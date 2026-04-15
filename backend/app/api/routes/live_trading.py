"""
Live Trading API Routes (/live/*)
===================================
Mirrors the paper trading API for real Phemex order execution.
Only active when paper_trading_default = False.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.live_trading import live_trading
from app.models import OrderSide

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live_trading"])


# ── Request / response models ─────────────────────────────────────────────────

class LiveOrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: Optional[float] = None
    agent_id: Optional[str] = None
    trader_id: Optional[str] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_pct: Optional[float] = None


class SLTPUpdateRequest(BaseModel):
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_pct: Optional[float] = None


class PartialCloseRequest(BaseModel):
    close_pct: float
    price: float
    label: str = "manual-close"


def _guard_live_mode() -> None:
    """Raise 403 if system is in paper trading mode."""
    try:
        from app.api.routes.settings import get_trading_prefs
        if get_trading_prefs().paper_trading_default:
            raise HTTPException(
                status_code=403,
                detail="Live trading is disabled. Switch to live mode in Settings first.",
            )
    except HTTPException:
        raise
    except Exception:
        pass


# ── Status ─────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_live_status():
    """Return live trading mode and current Phemex balance."""
    try:
        from app.api.routes.settings import get_trading_prefs
        prefs = get_trading_prefs()
        is_live = not prefs.paper_trading_default
    except Exception:
        is_live = False

    result = {
        "mode": "live" if is_live else "paper",
        "live_enabled": is_live,
        "balance": None,
        "positions_count": 0,
    }

    if is_live:
        try:
            balance = await live_trading.get_balance()
            positions = await live_trading.get_positions()
            result["balance"] = balance
            result["positions_count"] = len(positions) if positions else 0
        except Exception as e:
            result["balance_error"] = str(e)

    return result


# ── Positions ──────────────────────────────────────────────────────────────────

@router.get("/positions")
async def get_live_positions(symbol: Optional[str] = None, agent_id: Optional[str] = None):
    _guard_live_mode()
    try:
        positions = await live_trading.get_positions(symbol=symbol, agent_id=agent_id)
        return [
            {
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side.value if hasattr(p.side, "value") else p.side,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "unrealized_pnl": p.unrealized_pnl,
                "stop_loss_price": getattr(p, "stop_loss_price", None),
                "take_profit_price": getattr(p, "take_profit_price", None),
                "agent_id": p.agent_id,
                "is_paper": False,
            }
            for p in (positions or [])
        ]
    except Exception as e:
        logger.error(f"GET /live/positions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Orders ─────────────────────────────────────────────────────────────────────

@router.post("/orders")
async def place_live_order(req: LiveOrderRequest):
    _guard_live_mode()
    try:
        side = OrderSide(req.side.lower()) if req.side.lower() in ("buy", "sell") else OrderSide.BUY
        result = await live_trading.place_order(
            symbol=req.symbol,
            side=side,
            quantity=req.quantity,
            price=req.price,
            agent_id=req.agent_id,
            trader_id=req.trader_id,
            stop_loss_price=req.stop_loss_price,
            take_profit_price=req.take_profit_price,
            trailing_stop_pct=req.trailing_stop_pct,
        )
        return result
    except Exception as e:
        logger.error(f"POST /live/orders error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/positions/{position_id}")
async def close_live_position(position_id: str):
    _guard_live_mode()
    try:
        result = await live_trading.close_position(position_id)
        return result
    except Exception as e:
        logger.error(f"DELETE /live/positions/{position_id} error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/positions/{position_id}/partial-close")
async def partial_close_live_position(position_id: str, req: PartialCloseRequest):
    _guard_live_mode()
    try:
        result = await live_trading.partial_close(
            position_id=position_id,
            close_pct=req.close_pct,
            price=req.price,
            label=req.label,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/positions/{position_id}/sl-tp")
async def update_live_sl_tp(position_id: str, req: SLTPUpdateRequest):
    _guard_live_mode()
    try:
        result = await live_trading.update_position_sl_tp(
            position_id=position_id,
            stop_loss_price=req.stop_loss_price,
            take_profit_price=req.take_profit_price,
            trailing_stop_pct=req.trailing_stop_pct,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Trade history ──────────────────────────────────────────────────────────────

@router.get("/trades")
async def get_live_trades(symbol: Optional[str] = None, limit: int = 100):
    _guard_live_mode()
    try:
        trades = await live_trading.get_closed_trades(symbol=symbol, limit=limit)
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Sync ──────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def trigger_position_sync():
    """Manually trigger a position sync from Phemex."""
    _guard_live_mode()
    try:
        from app.services.position_sync import position_sync_service
        result = await position_sync_service.sync_once()
        return {"status": "ok", "summary": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Emergency stop ─────────────────────────────────────────────────────────────

@router.post("/emergency-stop")
async def emergency_stop():
    """
    Emergency stop: close ALL live positions immediately at market price.
    This is a destructive, irreversible action.
    """
    _guard_live_mode()
    try:
        positions = await live_trading.get_positions()
        closed = []
        errors = []
        for pos in (positions or []):
            try:
                await live_trading.close_position(pos.id)
                closed.append(pos.symbol)
            except Exception as e:
                errors.append({"symbol": pos.symbol, "error": str(e)})

        from app.services.team_chat import team_chat
        await team_chat.add_message(
            agent_role="risk_manager",
            content=(
                f"🛑 **EMERGENCY STOP** executed. Closed {len(closed)} live positions: "
                f"{', '.join(closed) or 'none'}. "
                f"Errors: {len(errors)}."
            ),
            message_type="alert",
        )

        return {
            "status": "emergency_stop_executed",
            "closed": closed,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Emergency stop failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
