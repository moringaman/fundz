"""Settings API routes – read / update application configuration.

Risk limits and trading preferences are persisted to the database so
they survive service restarts.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.config import settings as app_settings
from app.database import get_async_session

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


# ── Response / request schemas ────────────────────────────────────────────────

class ApiKeyStatus(BaseModel):
    has_phemex_key: bool = False
    phemex_testnet: bool = True
    key_hint: Optional[str] = None  # last 4 chars only


class RiskLimits(BaseModel):
    max_position_size_pct: float = Field(default=5.0, ge=0.1, le=100)
    max_daily_loss_pct: float = Field(default=5.0, ge=0.1, le=50)
    max_open_positions: int = Field(default=5, ge=1, le=50)
    default_stop_loss_pct: float = Field(default=3.5, ge=0.1, le=50)
    default_take_profit_pct: float = Field(default=7.0, ge=0.1, le=100)
    max_leverage: float = Field(default=1.0, ge=1.0, le=125)
    max_leveraged_notional_pct: float = Field(default=200.0, ge=10.0, le=1000.0)
    liquidation_buffer_pct: float = Field(default=12.5, ge=1.0, le=50.0)
    exposure_threshold_pct: float = Field(default=80.0, ge=10.0, le=100.0)


class TradingPreferences(BaseModel):
    default_symbol: str = "BTCUSDT"
    default_timeframe: str = "1h"
    paper_trading_default: bool = True
    auto_confirm_orders: bool = False
    default_order_type: str = "limit"
    trading_pairs: list[str] = Field(
        default=[
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
            "DOGEUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
            "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
        ]
    )


class LlmConfig(BaseModel):
    provider: str = "openrouter"
    model: str = "openai/gpt-4o-mini"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1000, ge=100, le=32000)
    has_openai_key: bool = False
    has_anthropic_key: bool = False
    has_openrouter_key: bool = False
    has_azure_key: bool = False


class TradingGates(BaseModel):
    """Configurable thresholds for all trading gates and filters."""
    # Entry confidence
    min_entry_confidence: float = Field(default=0.60, ge=0.1, le=1.0,
        description="Minimum signal confidence to enter a trade (0.0–1.0)")
    ta_veto_confidence: float = Field(default=0.75, ge=0.1, le=1.0,
        description="TA analyst must exceed this confidence to veto a signal")
    # MTF confluence
    mtf_confluence_block_score: float = Field(default=0.40, ge=0.0, le=1.0,
        description="Block trade when MTF alignment=mixed AND score below this")
    mtf_strong_alignment_score: float = Field(default=0.55, ge=0.0, le=1.0,
        description="Score above which aligned MTF boosts confidence")
    mtf_aligned_boost: float = Field(default=0.10, ge=0.0, le=0.5,
        description="Confidence multiplier bonus when MTF strongly aligned (+fraction)")
    mtf_opposed_penalty: float = Field(default=0.25, ge=0.0, le=0.9,
        description="Confidence multiplier penalty when MTF strongly opposes (fraction removed)")
    mtf_mixed_penalty: float = Field(default=0.20, ge=0.0, le=0.9,
        description="Confidence multiplier penalty when MTF alignment is mixed (fraction removed)")
    # HTF adjustments
    htf_aligned_boost: float = Field(default=0.15, ge=0.0, le=0.5,
        description="Confidence bonus when higher-TF trend aligns with signal (+fraction)")
    htf_opposed_penalty: float = Field(default=0.30, ge=0.0, le=0.9,
        description="Confidence penalty when higher-TF trend opposes signal (fraction removed). Lower values allow valid counter-trend setups to pass.")
    # Whale sentiment
    whale_entry_gate_enabled: bool = Field(default=True,
        description="When enabled, 70%+ opposing whale notional hard-blocks new entries. Disable to ignore whale positioning for trade entry decisions.")
    whale_caution_threshold: float = Field(default=0.75, ge=0.5, le=1.0,
        description="Fraction of whale notional SHORT to trigger CAUTION elevation")
    whale_info_threshold: float = Field(default=0.65, ge=0.5, le=1.0,
        description="Fraction of whale notional SHORT to add informational warning")
    whale_bull_threshold: float = Field(default=0.30, ge=0.0, le=0.5,
        description="Fraction SHORT below which broad bullish positioning is noted")
    # Agent lifecycle
    min_runs_before_disable: int = Field(default=15, ge=5, le=100,
        description="Minimum trade runs required before CIO or traders can disable an agent")
    # Circuit breaker
    circuit_breaker_max_trades: int = Field(default=20, ge=5, le=200,
        description="Maximum trades per day before halting all trading")
    # Correlation limits
    max_same_asset_positions: int = Field(default=2, ge=1, le=10,
        description="Maximum concurrent positions on the same symbol")
    max_directional_concentration_pct: float = Field(default=40.0, ge=10.0, le=100.0,
        description="Maximum % of capital in all-long or all-short positions")
    # Position sizing
    confidence_size_reference: float = Field(default=0.78, ge=0.3, le=1.0,
        description="Confidence level at which full position size is used (sizing reference)")
    confidence_size_floor: float = Field(default=0.25, ge=0.1, le=1.0,
        description="Minimum position size multiplier regardless of low confidence")
    # TA confidence boost/penalty
    ta_boost_multiplier: float = Field(default=0.20, ge=0.0, le=0.5,
        description="Confidence boost when TA agrees with signal (fraction added)")
    ta_penalty_multiplier: float = Field(default=0.40, ge=0.0, le=0.9,
        description="Confidence penalty when TA opposes signal (fraction removed)")
    ta_min_confidence: float = Field(default=0.60, ge=0.1, le=1.0,
        description="Minimum TA confidence required to apply boost or penalty")
    # Structural-level proximity gate (support/resistance)
    sr_proximity_block_pct: float = Field(default=0.005, ge=0.001, le=0.03,
        description="Block entries when price is within this fraction of opposing support/resistance (0.005 = 0.5%)")
    # ── US Market Open Management ─────────────────────────────────────────────
    # The 09:00 ET (13:00–13:30 UTC) open is the highest-volatility window of the
    # day. Institutional order flow, delta-hedging, and stop-hunting routinely
    # reverse the Asian/European trend. Three layers of protection:
    #   1. BLACKOUT (default 12:45–13:30 UTC): hard-block all new entries
    #   2. PRE-OPEN SWEEP (default 12:30 UTC): move profitable SLs to breakeven
    #   3. CONFIRMATION WINDOW (default 13:30–14:15 UTC): require elevated
    #      confidence before entering — direction must be proved, not guessed
    us_open_blackout_enabled: bool = Field(default=True,
        description="Block all new position entries during the US open chaos window")
    us_open_blackout_start_utc: int = Field(default=1245, ge=0, le=2359,
        description="Blackout start time in UTC HHMM format (default 12:45)")
    us_open_blackout_end_utc: int = Field(default=1330, ge=0, le=2359,
        description="Blackout end time in UTC HHMM format (default 13:30)")
    us_open_preopen_sl_tighten: bool = Field(default=True,
        description="Move profitable position SLs to breakeven before the US open")
    us_open_preopen_tighten_utc: int = Field(default=1230, ge=0, le=2359,
        description="Pre-open SL tighten sweep time in UTC HHMM format (default 12:30)")
    us_open_confirmation_end_utc: int = Field(default=1415, ge=0, le=2359,
        description="End of elevated-confidence confirmation window in UTC HHMM (default 14:15)")
    us_open_confirmation_confidence: float = Field(default=0.80, ge=0.5, le=1.0,
        description="Minimum confidence required to enter during the US open confirmation window")
    # ── London Open Fake-out Window ───────────────────────────────────────────
    # The London open (08:00 UTC) typically produces a sharp spike that reverses
    # before the real trend establishes (~09:00 UTC). Entering during 08:30–09:00
    # UTC risks getting caught in the fake-out. We apply a confidence penalty
    # rather than a full block (unlike US open) since crypto is less affected.
    london_open_fakeout_enabled: bool = Field(default=True,
        description="Apply confidence penalty during London open fake-out window (08:30–09:00 UTC)")
    london_open_fakeout_start_utc: int = Field(default=830, ge=0, le=2359,
        description="London fake-out penalty window start in UTC HHMM (default 08:30)")
    london_open_fakeout_end_utc: int = Field(default=900, ge=0, le=2359,
        description="London fake-out penalty window end in UTC HHMM (default 09:00)")
    london_open_fakeout_penalty: float = Field(default=0.15, ge=0.0, le=0.5,
        description="Confidence penalty (fraction) applied during London fake-out window")
    london_open_fakeout_min_confidence: float = Field(default=0.75, ge=0.5, le=1.0,
        description="Minimum confidence required to enter during London fake-out window (after penalty)")
    # ── Overnight Dead Zone ───────────────────────────────────────────────────
    # 20:00–00:00 UTC: NY session winding down, Asian session not yet active.
    # Volume is thin, spreads widen, signals are noise-heavy. Momentum and
    # breakout strategies are most vulnerable. Apply a confidence dampener.
    dead_zone_enabled: bool = Field(default=True,
        description="Apply confidence penalty during low-volume overnight window (20:00–00:00 UTC)")
    dead_zone_start_utc: int = Field(default=2000, ge=0, le=2359,
        description="Overnight dead zone start in UTC HHMM (default 20:00)")
    dead_zone_end_utc: int = Field(default=2359, ge=0, le=2359,
        description="Overnight dead zone end in UTC HHMM (default 23:59 — midnight threshold)")
    dead_zone_penalty: float = Field(default=0.15, ge=0.0, le=0.5,
        description="Confidence penalty (fraction) applied during overnight dead zone")
    dead_zone_min_confidence: float = Field(default=0.70, ge=0.5, le=1.0,
        description="Minimum confidence required to enter during dead zone (after penalty)")
    dead_zone_noop_enabled: bool = Field(default=True,
        description="When enabled, scheduler performs no-op during dead zone to conserve LLM/API tokens; only position monitoring continues")
    # ── Daily Fee Budget Circuit Breaker ──────────────────────────────────────
    # Hard stop that prevents new entries when cumulative daily fees exceed this
    # percentage of starting capital. Prevents fee bleed from consuming profits.
    max_daily_fees_pct: float = Field(default=0.5, ge=0.1, le=2.0,
        description="Hard circuit breaker: block all new entries when daily fees exceed this % of starting capital (0.5 = 50 bps). Prevents profit-eroding fee churn.")
    # ── Fee Coverage Guard ─────────────────────────────────────────────────────
    # Encourages selective trading by requiring realized edge to cover fees by a
    # minimum multiple. When below target, entry quality thresholds tighten.
    fee_coverage_guard_enabled: bool = Field(default=True,
        description="When enabled, tighten entry quality when realized PnL is not covering fees by the target multiple.")
    fee_coverage_min_ratio: float = Field(default=2.5, ge=0.5, le=10.0,
        description="Minimum realized PnL-to-fees coverage ratio target (2.5 = realized PnL should be 2.5x fees).")
    fee_coverage_min_fees_usd: float = Field(default=25.0, ge=0.0, le=5000.0,
        description="Minimum total fees before fee-coverage guard activates to avoid early-session noise.")
    fee_coverage_window_trades: int = Field(default=60, ge=5, le=500,
        description="Number of most recent closed trades used to compute fee coverage quality.")
    fee_coverage_min_closed_trades: int = Field(default=8, ge=1, le=200,
        description="Minimum closed trades required before fee-coverage guard can activate.")
    fee_coverage_include_slippage: bool = Field(default=True,
        description="Include estimated slippage costs in fee-coverage net-edge calculation.")
    fee_coverage_slippage_bps: float = Field(default=2.0, ge=0.0, le=50.0,
        description="Estimated slippage per execution leg in basis points (2.0 = 2 bps per entry and exit leg).")
    fee_coverage_include_funding: bool = Field(default=True,
        description="Include funding costs in fee-coverage net-edge calculation when available.")
    # ── Confidence-Gated Leverage ────────────────────────────────────────────
    leverage_enabled: bool = Field(default=True,
        description="Allow leverage only on high-confidence trades.")
    leverage_confidence_threshold: float = Field(default=0.75, ge=0.5, le=1.0,
        description="Minimum confidence required before any leverage above 1x is allowed.")
    leverage_tier_1_min_confidence: float = Field(default=0.75, ge=0.5, le=1.0,
        description="Minimum confidence for tier 1 leverage.")
    leverage_tier_1_max_confidence: float = Field(default=0.85, ge=0.5, le=1.0,
        description="Upper bound for tier 1 leverage confidence band.")
    leverage_tier_1_multiplier: float = Field(default=2.0, ge=1.0, le=5.0,
        description="Leverage multiplier for confidence tier 1.")
    leverage_tier_2_min_confidence: float = Field(default=0.85, ge=0.5, le=1.0,
        description="Minimum confidence for tier 2 leverage.")
    leverage_tier_2_max_confidence: float = Field(default=0.95, ge=0.5, le=1.0,
        description="Upper bound for tier 2 leverage confidence band.")
    leverage_tier_2_multiplier: float = Field(default=3.0, ge=1.0, le=5.0,
        description="Leverage multiplier for confidence tier 2.")
    leverage_tier_3_min_confidence: float = Field(default=0.95, ge=0.5, le=1.0,
        description="Minimum confidence for tier 3 leverage.")
    leverage_tier_3_multiplier: float = Field(default=5.0, ge=1.0, le=5.0,
        description="Leverage multiplier for highest-confidence trades.")



class GeneralSettings(BaseModel):
    app_name: str = "phemex-ai-trader"
    debug: bool = True
    rate_limit_per_minute: int = 120


class SettingsResponse(BaseModel):
    api_keys: ApiKeyStatus
    risk_limits: RiskLimits
    trading: TradingPreferences
    llm: LlmConfig
    general: GeneralSettings
    gates: TradingGates


class TradingGatesUpdateRequest(TradingGates):
    pass


class ApiKeySaveRequest(BaseModel):
    phemex_api_key: str
    phemex_api_secret: str
    phemex_testnet: bool = True


class RiskLimitsUpdateRequest(RiskLimits):
    pass


class TradingPreferencesUpdateRequest(TradingPreferences):
    pass


class LlmConfigUpdateRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=100, le=32000)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None

class TelegramSettingsModel(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False
    polling_enabled: bool = False   # Inbound command polling (getUpdates long-poll)
    trade_executed: bool = True
    trade_rejected: bool = True
    ta_veto: bool = True
    daily_loss_limit: bool = True
    position_closed: bool = True
    take_profit_hit: bool = True
    automation_start_stop: bool = True
    agent_error: bool = True
    api_error: bool = True
    daily_report: bool = True
    rebalance: bool = False


# ── DB-backed settings store ──────────────────────────────────────────────────
# Settings are loaded from the database on first access and cached in memory.
# Writes go to both the in-memory cache and the database.

_runtime_risk_limits: Optional[RiskLimits] = None
_runtime_trading_prefs: Optional[TradingPreferences] = None
_runtime_trading_gates: Optional[TradingGates] = None
_settings_loaded = False


async def _ensure_settings_table():
    """Create the app_settings table if it doesn't exist."""
    from app.database import engine
    from app.models import AppSetting
    async with engine.begin() as conn:
        await conn.run_sync(AppSetting.__table__.create, checkfirst=True)


async def _load_setting(key: str) -> Optional[dict]:
    """Load a setting from the database."""
    try:
        await _ensure_settings_table()
        from app.models import AppSetting
        from sqlalchemy import select
        async with get_async_session() as db:
            row = await db.get(AppSetting, key)
            if row:
                return row.value
    except Exception as e:
        logger.warning(f"Failed to load setting '{key}' from DB: {e}")
    return None


async def _save_setting(key: str, value: dict):
    """Save a setting to the database."""
    try:
        await _ensure_settings_table()
        from app.models import AppSetting
        async with get_async_session() as db:
            row = await db.get(AppSetting, key)
            if row:
                row.value = value
            else:
                row = AppSetting(key=key, value=value)
                db.add(row)
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to save setting '{key}' to DB: {e}")


async def _load_all_settings():
    """Load risk limits, trading prefs and Telegram config from DB, fall back to defaults."""
    global _runtime_risk_limits, _runtime_trading_prefs, _runtime_trading_gates, _settings_loaded

    if _settings_loaded:
        return

    risk_data = await _load_setting("risk_limits")
    if risk_data:
        try:
            _runtime_risk_limits = RiskLimits(**risk_data)
            logger.info(f"Loaded risk limits from DB: {risk_data}")
        except Exception:
            _runtime_risk_limits = RiskLimits()
    else:
        _runtime_risk_limits = RiskLimits()

    trading_data = await _load_setting("trading_prefs")
    if trading_data:
        try:
            _runtime_trading_prefs = TradingPreferences(**trading_data)
            logger.info(f"Loaded trading prefs from DB: {trading_data}")
        except Exception:
            _runtime_trading_prefs = TradingPreferences()
    else:
        _runtime_trading_prefs = TradingPreferences()

    gates_data = await _load_setting("trading_gates")
    if gates_data:
        try:
            _runtime_trading_gates = TradingGates(**gates_data)
            logger.info("Loaded trading gates from DB")
        except Exception:
            _runtime_trading_gates = TradingGates()
    else:
        _runtime_trading_gates = TradingGates()

    # Load and apply Telegram settings
    telegram_data = await _load_setting("telegram")
    if telegram_data:
        try:
            _apply_telegram_config(TelegramSettingsModel(**telegram_data))
            logger.info("Loaded Telegram settings from DB")
        except Exception as e:
            logger.warning(f"Failed to apply Telegram settings: {e}")

    _settings_loaded = True


def _apply_telegram_config(model: TelegramSettingsModel) -> None:
    """Push a TelegramSettingsModel into the telegram_service singleton."""
    from app.services.telegram_service import TelegramConfig, telegram_service
    telegram_service.configure(TelegramConfig(**model.model_dump()))


def get_risk_limits() -> RiskLimits:
    """Access current risk limits from other modules (sync)."""
    if _runtime_risk_limits is None:
        return RiskLimits()
    return _runtime_risk_limits


def get_trading_prefs() -> TradingPreferences:
    """Access current trading preferences from other modules (sync)."""
    if _runtime_trading_prefs is None:
        return TradingPreferences()
    return _runtime_trading_prefs


def get_trading_gates() -> TradingGates:
    """Access current trading gate thresholds from other modules (sync)."""
    if _runtime_trading_gates is None:
        return TradingGates()
    return _runtime_trading_gates


def _mask_key(key: Optional[str]) -> Optional[str]:
    """Return last 4 characters of a key, or None."""
    if not key or len(key) < 8:
        return None
    return f"...{key[-4:]}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return all current settings (secrets are masked)."""
    await _load_all_settings()
    return SettingsResponse(
        api_keys=ApiKeyStatus(
            has_phemex_key=bool(app_settings.phemex_api_key),
            phemex_testnet=app_settings.phemex_testnet,
            key_hint=_mask_key(app_settings.phemex_api_key),
        ),
        risk_limits=_runtime_risk_limits,
        trading=_runtime_trading_prefs,
        llm=LlmConfig(
            provider=app_settings.llm_provider,
            model=app_settings.llm_model,
            temperature=app_settings.llm_temperature,
            max_tokens=app_settings.llm_max_tokens,
            has_openai_key=bool(app_settings.openai_api_key),
            has_anthropic_key=bool(app_settings.anthropic_api_key),
            has_openrouter_key=bool(app_settings.openrouter_api_key),
            has_azure_key=bool(app_settings.azure_openai_key),
        ),
        general=GeneralSettings(
            app_name=app_settings.app_name,
            debug=app_settings.debug,
            rate_limit_per_minute=app_settings.rate_limit_per_minute,
        ),
        gates=_runtime_trading_gates,
    )


@router.put("/api-keys")
async def update_api_keys(req: ApiKeySaveRequest):
    """Save Phemex API credentials (updates runtime config)."""
    if not req.phemex_api_key or not req.phemex_api_secret:
        raise HTTPException(status_code=400, detail="Both API key and secret are required")

    app_settings.phemex_api_key = req.phemex_api_key
    app_settings.phemex_api_secret = req.phemex_api_secret
    app_settings.phemex_testnet = req.phemex_testnet

    return {
        "status": "ok",
        "message": "API keys updated",
        "key_hint": _mask_key(req.phemex_api_key),
    }


@router.put("/risk-limits", response_model=RiskLimits)
async def update_risk_limits(req: RiskLimitsUpdateRequest):
    """Update risk management parameters (persisted to DB)."""
    global _runtime_risk_limits
    _runtime_risk_limits = RiskLimits(**req.model_dump())
    await _save_setting("risk_limits", req.model_dump())
    return _runtime_risk_limits


@router.put("/trading", response_model=TradingPreferences)
async def update_trading_prefs(req: TradingPreferencesUpdateRequest):
    """Update trading preferences (persisted to DB)."""
    global _runtime_trading_prefs
    _runtime_trading_prefs = TradingPreferences(**req.model_dump())
    await _save_setting("trading_prefs", req.model_dump())
    return _runtime_trading_prefs


@router.get("/gates", response_model=TradingGates)
async def get_gates():
    """Return current trading gate thresholds."""
    await _load_all_settings()
    return _runtime_trading_gates


@router.put("/gates", response_model=TradingGates)
async def update_trading_gates(req: TradingGatesUpdateRequest):
    """Update trading gate thresholds (persisted to DB, applied immediately)."""
    global _runtime_trading_gates
    _runtime_trading_gates = TradingGates(**req.model_dump())
    await _save_setting("trading_gates", req.model_dump())
    return _runtime_trading_gates


# ── Gate Autopilot ────────────────────────────────────────────────────────────

class AutopilotToggleRequest(BaseModel):
    enabled: bool


@router.get("/gates/autopilot")
async def get_gate_autopilot_status():
    """Return the current state of the gate autopilot (enabled, regime, last run)."""
    from app.services.gate_autopilot import gate_autopilot
    return gate_autopilot.status()


@router.post("/gates/autopilot")
async def set_gate_autopilot(req: AutopilotToggleRequest):
    """Enable or disable the gate autopilot.  Returns the updated status."""
    from app.services.gate_autopilot import gate_autopilot
    return await gate_autopilot.set_enabled(req.enabled)


@router.post("/gates/autopilot/run")
async def run_gate_autopilot_now():
    """Trigger an immediate autopilot evaluation (for manual testing)."""
    from app.services.gate_autopilot import gate_autopilot
    return await gate_autopilot.run_once()


@router.put("/llm")
async def update_llm_config(req: LlmConfigUpdateRequest):
    """Update LLM provider configuration."""
    if req.provider:
        app_settings.llm_provider = req.provider
    if req.model:
        app_settings.llm_model = req.model
    if req.temperature is not None:
        app_settings.llm_temperature = req.temperature
    if req.max_tokens is not None:
        app_settings.llm_max_tokens = req.max_tokens
    if req.openai_api_key:
        app_settings.openai_api_key = req.openai_api_key
    if req.anthropic_api_key:
        app_settings.anthropic_api_key = req.anthropic_api_key
    if req.openrouter_api_key:
        app_settings.openrouter_api_key = req.openrouter_api_key

    return {
        "status": "ok",
        "message": "LLM configuration updated",
        "provider": app_settings.llm_provider,
        "model": app_settings.llm_model,
    }


# ── Email test ────────────────────────────────────────────────────────────────

@router.post("/test-email")
async def send_test_email():
    """Send a test daily summary email to verify the email pipeline."""
    from app.services.email_service import email_service

    if not app_settings.mail_server_api_key:
        raise HTTPException(
            status_code=400,
            detail="MAIL_SERVER_API_KEY not configured",
        )

    ok = await email_service.send_test_email()
    if ok:
        return {"status": "ok", "message": f"Test email sent to {app_settings.mail_to_address}"}
    raise HTTPException(status_code=502, detail="Email delivery failed — check server logs")


@router.get("/trading-pairs")
async def get_trading_pairs():
    """Return the configured trading pairs list."""
    await _load_all_settings()
    return {"pairs": _runtime_trading_prefs.trading_pairs}


# ── Telegram settings ─────────────────────────────────────────────────────────

@router.get("/telegram", response_model=TelegramSettingsModel)
async def get_telegram_settings():
    """Return current Telegram settings (token masked)."""
    await _load_all_settings()
    from app.services.telegram_service import telegram_service
    cfg = telegram_service.get_config()
    data = {
        "bot_token": f"...{cfg.bot_token[-6:]}" if len(cfg.bot_token) > 6 else ("***" if cfg.bot_token else ""),
        "chat_id": cfg.chat_id,
        "enabled": cfg.enabled,
        "polling_enabled": cfg.polling_enabled,
        "trade_executed": cfg.trade_executed,
        "trade_rejected": cfg.trade_rejected,
        "ta_veto": cfg.ta_veto,
        "daily_loss_limit": cfg.daily_loss_limit,
        "position_closed": cfg.position_closed,
        "take_profit_hit": cfg.take_profit_hit,
        "automation_start_stop": cfg.automation_start_stop,
        "agent_error": cfg.agent_error,
        "api_error": cfg.api_error,
        "daily_report": cfg.daily_report,
        "rebalance": cfg.rebalance,
    }
    return TelegramSettingsModel(**data)


@router.put("/telegram", response_model=TelegramSettingsModel)
async def update_telegram_settings(req: TelegramSettingsModel):
    """Save Telegram settings (persisted to DB and applied immediately)."""
    await _load_all_settings()
    from app.services.telegram_service import telegram_service

    # If token is masked placeholder, keep existing token
    existing_cfg = telegram_service.get_config()
    token = req.bot_token
    if token.startswith("...") or token == "***":
        token = existing_cfg.bot_token

    model_with_real_token = TelegramSettingsModel(**{**req.model_dump(), "bot_token": token})
    await _save_setting("telegram", model_with_real_token.model_dump())
    _apply_telegram_config(model_with_real_token)
    logger.info(f"Telegram settings updated — enabled={req.enabled}")

    # Return with masked token
    masked = model_with_real_token.model_dump()
    masked["bot_token"] = f"...{token[-6:]}" if len(token) > 6 else ("***" if token else "")
    return TelegramSettingsModel(**masked)


@router.post("/test-telegram")
async def test_telegram(req: TelegramSettingsModel):
    """Send a test message to verify bot token + chat ID (does not save)."""
    from app.services.telegram_service import telegram_service

    # Use saved token if placeholder passed
    token = req.bot_token
    if token.startswith("...") or token == "***":
        token = telegram_service.get_config().bot_token

    if not token or not req.chat_id:
        raise HTTPException(status_code=400, detail="Bot token and chat ID are required")

    result = await telegram_service.test_connection(token, req.chat_id)
    if result["ok"]:
        return {"status": "ok", "message": result["message"]}
    raise HTTPException(status_code=502, detail=result["message"])
