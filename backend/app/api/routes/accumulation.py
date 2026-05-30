"""Accumulation Fund API — DCA config, portfolio view, capital transfers."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel

from app.auth import get_optional_user_id
from app.services._acc import _acc as _acc, set_ctx_user_id as _acc_set_user

async def _inject_user(user_id: Optional[str] = Depends(get_optional_user_id)):
    _acc_set_user(user_id or "default-user")

router = APIRouter(
    prefix="/accumulation",
    tags=["accumulation"],
    dependencies=[Depends(_inject_user)],
)


@router.get("/portfolio")
async def get_portfolio():
    """Accumulation fund portfolio — positions, balances, unrealized P&L."""
    return await _acc.get_portfolio()


@router.get("/configs")
async def get_configs():
    """All accumulation strategy configs per asset."""
    return await _acc.get_configs()


class ConfigUpdate(BaseModel):
    asset: str
    enabled: Optional[bool] = None
    dca_enabled: Optional[bool] = None
    dca_amount_usd: Optional[float] = None
    dca_balance_pct: Optional[float] = None
    dca_interval_hours: Optional[int] = None
    va_enabled: Optional[bool] = None
    va_target_growth_rate: Optional[float] = None
    va_period_hours: Optional[int] = None
    dip_enabled: Optional[bool] = None
    dip_levels: Optional[list] = None
    scale_out_enabled: Optional[bool] = None
    scale_out_target_pct: Optional[float] = None
    scale_out_tranche_pct: Optional[float] = None
    scale_out_max_transfers: Optional[int] = None


@router.put("/configs")
async def upsert_config(req: ConfigUpdate):
    """Create or update accumulation strategy config for an asset."""
    data = req.model_dump(exclude_none=True)
    asset = data.pop("asset")
    return await _acc.upsert_config(asset, data)


@router.delete("/configs/{asset}")
async def delete_config(asset: str):
    """Remove accumulation config for an asset."""
    ok = await _acc.delete_config(asset)
    if not ok:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"status": "deleted"}


class TransferRequest(BaseModel):
    amount: float


@router.post("/deposit")
async def deposit_usdc(req: TransferRequest):
    """Deposit USDC into the accumulation fund (Hyperliquid DEX)."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    return await _acc.deposit_usdc(req.amount)


@router.post("/transfer-to-trading")
async def transfer_to_trading(req: TransferRequest):
    """Transfer USDC from accumulation fund to the trading fund."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    try:
        return await _acc.transfer_to_trading(req.amount)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/buy/{asset}")
async def buy_spot(asset: str, amount: float):
    """Execute a one-time spot buy into the accumulation fund (Hyperliquid)."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    try:
        return await _acc.buy_spot(asset, amount)
    except (ValueError, Exception) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/run-dca")
async def run_dca_manual():
    """Manually trigger DCA check (bypasses schedule, executes immediately)."""
    return {"results": await _acc.run_dca(force=True)}


@router.post("/run-scaleout")
async def run_scaleout_manual():
    """Manually trigger scale-out check."""
    return {"results": await _acc.check_scale_outs()}


@router.get("/executions")
async def get_executions(asset: Optional[str] = None, strategy: Optional[str] = None, limit: int = 100):
    """Historical accumulation execution records."""
    return await _acc.get_executions(asset=asset, strategy=strategy, limit=limit)


@router.get("/metrics")
async def get_metrics():
    """Aggregated execution counts per (asset, strategy) + portfolio stats."""
    return await _acc.get_metrics()


@router.get("/performance-chart")
async def get_performance_chart():
    """Time-series of portfolio value derived from execution history."""
    return await _acc.get_performance_chart()


@router.post("/sync-from-live")
async def sync_from_live():
    """Read current Hyperliquid accumulation state and write it to the paper DB.

    Use this after switching accumulation from live mode back to paper mode,
    so the paper DB reflects the actual on-chain holdings rather than stale data.
    """
    await _acc.sync_live_to_paper()
    return {"status": "synced"}


@router.post("/ack-low-balance")
async def ack_low_balance():
    """Acknowledge low balance alert — suppresses Telegram notifications for 24h."""
    _acc.ack_low_balance()
    return {"status": "acknowledged"}
