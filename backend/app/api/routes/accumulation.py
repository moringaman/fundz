"""Accumulation Fund API — DCA config, portfolio view, capital transfers."""

from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel

from app.services.accumulation_service import accumulation_service

router = APIRouter(prefix="/accumulation", tags=["accumulation"])


@router.get("/portfolio")
async def get_portfolio():
    """Accumulation fund portfolio — positions, balances, unrealized P&L."""
    return await accumulation_service.get_portfolio()


@router.get("/configs")
async def get_configs():
    """All accumulation strategy configs per asset."""
    return await accumulation_service.get_configs()


class ConfigUpdate(BaseModel):
    asset: str
    enabled: Optional[bool] = None
    dca_enabled: Optional[bool] = None
    dca_amount_usd: Optional[float] = None
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
    return await accumulation_service.upsert_config(asset, data)


@router.delete("/configs/{asset}")
async def delete_config(asset: str):
    """Remove accumulation config for an asset."""
    ok = await accumulation_service.delete_config(asset)
    if not ok:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"status": "deleted"}


class TransferRequest(BaseModel):
    amount: float


@router.post("/deposit")
async def deposit_usdt(req: TransferRequest):
    """Deposit USDT into the accumulation fund."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    return await accumulation_service.deposit_usdt(req.amount)


@router.post("/transfer-to-trading")
async def transfer_to_trading(req: TransferRequest):
    """Transfer USDT from accumulation fund to the trading fund."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    try:
        return await accumulation_service.transfer_to_trading(req.amount)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/buy/{asset}")
async def buy_spot(asset: str, amount: float):
    """Execute a one-time spot buy into the accumulation fund."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    try:
        return await accumulation_service.buy_spot(asset, amount)
    except (ValueError, Exception) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/run-dca")
async def run_dca_manual():
    """Manually trigger DCA check."""
    return {"results": await accumulation_service.run_dca()}


@router.post("/run-scaleout")
async def run_scaleout_manual():
    """Manually trigger scale-out check."""
    return {"results": await accumulation_service.check_scale_outs()}


@router.post("/ack-low-balance")
async def ack_low_balance():
    """Acknowledge low balance alert — suppresses Telegram notifications for 24h."""
    accumulation_service.ack_low_balance()
    return {"status": "acknowledged"}
