"""Settings API routes – read / update application configuration."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.config import settings as app_settings

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Response / request schemas ────────────────────────────────────────────────

class ApiKeyStatus(BaseModel):
    has_phemex_key: bool = False
    phemex_testnet: bool = True
    key_hint: Optional[str] = None  # last 4 chars only


class RiskLimits(BaseModel):
    max_position_size_pct: float = Field(default=5.0, ge=0.1, le=100)
    max_daily_loss_pct: float = Field(default=5.0, ge=0.1, le=50)
    max_open_positions: int = Field(default=5, ge=1, le=50)
    default_stop_loss_pct: float = Field(default=2.0, ge=0.1, le=50)
    default_take_profit_pct: float = Field(default=4.0, ge=0.1, le=100)
    max_leverage: float = Field(default=1.0, ge=1.0, le=125)


class TradingPreferences(BaseModel):
    default_symbol: str = "BTCUSDT"
    default_timeframe: str = "1h"
    paper_trading_default: bool = True
    auto_confirm_orders: bool = False
    default_order_type: str = "limit"


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


# ── In-memory settings store (persisted per server lifetime) ──────────────────
# In production these would live in the DB; for MVP we keep them in memory,
# seeded from environment variables on first access.

_runtime_risk_limits = RiskLimits()
_runtime_trading_prefs = TradingPreferences()


def _mask_key(key: Optional[str]) -> Optional[str]:
    """Return last 4 characters of a key, or None."""
    if not key or len(key) < 8:
        return None
    return f"...{key[-4:]}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Return all current settings (secrets are masked)."""
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
    """Update risk management parameters."""
    global _runtime_risk_limits
    _runtime_risk_limits = RiskLimits(**req.model_dump())
    return _runtime_risk_limits


@router.put("/trading", response_model=TradingPreferences)
async def update_trading_prefs(req: TradingPreferencesUpdateRequest):
    """Update trading preferences."""
    global _runtime_trading_prefs
    _runtime_trading_prefs = TradingPreferences(**req.model_dump())
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
