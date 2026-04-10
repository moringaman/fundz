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
    exposure_threshold_pct: float = Field(default=80.0, ge=10.0, le=100.0)


class TradingPreferences(BaseModel):
    default_symbol: str = "BTCUSDT"
    default_timeframe: str = "1h"
    paper_trading_default: bool = True
    auto_confirm_orders: bool = False
    default_order_type: str = "limit"
    trading_pairs: list[str] = Field(
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]
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
    global _runtime_risk_limits, _runtime_trading_prefs, _settings_loaded

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
