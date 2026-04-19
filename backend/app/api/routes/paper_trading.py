import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from collections import defaultdict

from app.database import get_db
from app.services.paper_trading import paper_trading
from app.models import OrderSide, OrderStatus, Balance as PaperBalance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/paper", tags=["paper_trading"])


class OrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: Optional[float] = None
    agent_id: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    status: str
    created_at: str
    agent_id: Optional[str] = None


class BalanceResponse(BaseModel):
    asset: str
    available: float
    locked: float


class PositionResponse(BaseModel):
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    updated_at: Optional[str] = None
    agent_id: Optional[str] = None


@router.get("/status")
async def get_paper_status(db: AsyncSession = Depends(get_db)):
    try:
        query = select(PaperBalance).where(PaperBalance.user_id == "default-user")
        result = await db.execute(query)
        balances = result.scalars().all()

        if not balances:
            # If no balances exist, create default balances
            default_balances = [
                PaperBalance(user_id="default-user", asset="BTC", available=1.0, locked=0.0),
                PaperBalance(user_id="default-user", asset="USDT", available=50000.0, locked=0.0),
                PaperBalance(user_id="default-user", asset="ETH", available=10.0, locked=0.0)
            ]
            for balance in default_balances:
                db.add(balance)
            await db.commit()
            balances = default_balances

        return {
            "enabled": True,
            "balances": [
                {"asset": b.asset, "available": b.available, "locked": b.locked}
                for b in balances
            ]
        }
    except SQLAlchemyError as e:
        logger.error(f"Database error in paper trading status: {e}")
        raise HTTPException(status_code=500, detail="Database error occurred") from e
    except Exception as e:
        logger.error(f"Unexpected error in paper trading status: {e}")
        raise HTTPException(status_code=500, detail="Unexpected server error") from e


@router.post("/enable")
async def enable_paper_trading():
    paper_trading.enable()
    return {"enabled": True, "message": "Paper trading enabled"}


@router.post("/disable")
async def disable_paper_trading():
    paper_trading.disable()
    return {"enabled": False, "message": "Paper trading disabled"}


@router.post("/reset")
async def reset_paper_trading():
    await paper_trading.reset()
    return {"message": "Paper trading account reset"}


@router.get("/balance", response_model=List[BalanceResponse])
async def get_paper_balance():
    balances = await paper_trading.get_all_balances()
    return [
        BalanceResponse(asset=b.asset, available=b.available, locked=b.locked)
        for b in balances
    ]


class AdjustBalanceRequest(BaseModel):
    asset: str = "USDT"
    amount: float


@router.post("/balance/adjust")
async def adjust_paper_balance(req: AdjustBalanceRequest):
    """Deposit or withdraw funds from the paper trading wallet."""
    try:
        updated = await paper_trading.adjust_balance(req.asset, req.amount)
        action = "deposited" if req.amount > 0 else "withdrew"
        return {
            "message": f"Successfully {action} {abs(req.amount)} {req.asset}",
            **updated,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/portfolio")
async def get_paper_portfolio():
    """Canonical portfolio summary — single source of truth for balances,
    positions value, total capital, and exposure."""
    balances = await paper_trading.get_all_balances()
    positions = await paper_trading.get_positions_live()

    # Cash balances
    balance_list = [
        {"asset": b.asset, "available": b.available, "locked": b.locked}
        for b in balances
    ]
    usdt_bal = next((b for b in balances if b.asset == "USDT"), None)
    usdt_total = (usdt_bal.available + usdt_bal.locked) if usdt_bal else 0.0

    # Open positions value
    positions_value = sum(
        p.get("quantity", 0) * p.get("current_price", p.get("entry_price", 0))
        for p in positions
    )

    total_capital = usdt_total + positions_value

    # Exposure = positions value / total capital (same as risk manager)
    exposure_pct = (positions_value / total_capital * 100) if total_capital > 0 else 0.0

    # Concentration
    largest_position = None
    concentration = "low"
    if positions:
        largest = max(positions, key=lambda p: p.get("quantity", 0) * p.get("current_price", p.get("entry_price", 0)))
        largest_val = largest.get("quantity", 0) * largest.get("current_price", largest.get("entry_price", 0))
        largest_pct = (largest_val / positions_value * 100) if positions_value > 0 else 0
        largest_position = {"symbol": largest.get("symbol"), "value": largest_val, "pct": largest_pct}
        concentration = "high" if largest_pct > 40 else ("medium" if largest_pct > 25 else "low")

    return {
        "balances": balance_list,
        "positions_count": len(positions),
        "positions_value": positions_value,
        "usdt_available": usdt_bal.available if usdt_bal else 0.0,
        "usdt_total": usdt_total,
        "total_capital": total_capital,
        "exposure_pct": exposure_pct,
        "concentration": concentration,
        "largest_position": largest_position,
    }


@router.post("/order", response_model=OrderResponse)
async def place_paper_order(order: OrderRequest):
    try:
        order_side = OrderSide.BUY if order.side.lower() == 'buy' else OrderSide.SELL
        result = await paper_trading.place_order(
            symbol=order.symbol,
            side=order_side,
            quantity=order.quantity,
            price=order.price,
            agent_id=order.agent_id
        )
        return OrderResponse(
            order_id=result.id,
            symbol=result.symbol,
            side=result.side.value,
            quantity=result.quantity,
            price=result.price,
            status=result.status.value,
            created_at=result.created_at.isoformat(),
            agent_id=result.agent_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/order/{order_id}")
async def cancel_paper_order(order_id: str):
    success = await paper_trading.cancel_order(order_id)
    if not success:
        raise HTTPException(status_code=404, detail="Order not found or cannot be cancelled")
    return {"status": "cancelled", "order_id": order_id}


@router.get("/orders")
async def get_paper_orders(symbol: Optional[str] = None, limit: int = 50):
    orders = await paper_trading.get_orders(symbol=symbol, limit=limit)
    return [
        {
            "id": o.id,
            "symbol": o.symbol,
            "side": o.side.value,
            "quantity": o.quantity,
            "price": o.price,
            "total": o.total,
            "fee": o.fee or 0,
            "leverage": o.leverage or 1.0,
            "margin_used": o.margin_used or 0.0,
            "status": o.status.value,
            "created_at": o.created_at.isoformat(),
            "agent_id": o.agent_id,
        }
        for o in orders
    ]


@router.get("/closed-trades")
async def get_closed_trades(symbol: Optional[str] = None, limit: int = 100):
    """Return FIFO-matched buy→sell round-trips with realised P&L."""
    return await paper_trading.get_closed_trades(symbol=symbol, limit=limit)


@router.get("/positions")
async def get_paper_positions(symbol: Optional[str] = None):
    return await paper_trading.get_positions_live(symbol)


@router.get("/pnl")
async def get_paper_pnl():
    return await paper_trading.calculate_pnl()


class UpdatePositionRequest(BaseModel):
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_pct: Optional[float] = None


@router.patch("/positions/{position_id}")
async def update_position_sl_tp(position_id: str, body: UpdatePositionRequest):
    """Update stop-loss, take-profit, or trailing-stop on an open position."""
    kwargs: dict = {}
    if body.stop_loss_price is not None:
        kwargs["stop_loss_price"] = body.stop_loss_price
    if body.take_profit_price is not None:
        kwargs["take_profit_price"] = body.take_profit_price
    if body.trailing_stop_pct is not None:
        kwargs["trailing_stop_pct"] = body.trailing_stop_pct

    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await paper_trading.update_position_sl_tp(position_id, **kwargs)
    if result is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return result


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str):
    """Close an open position at the current market price."""
    try:
        result = await paper_trading.close_position(position_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return result


@router.get("/performance-chart")
async def get_performance_chart(limit: int = 2000):
    """Return daily-aggregated performance metrics for charting.

    Each entry covers one calendar day and includes cumulative P&L,
    rolling win rate, trade count, average win, and average loss.
    """
    trades = await paper_trading.get_closed_trades(limit=limit)

    # Sort ascending by exit time
    def _exit_ts(t: dict) -> str:
        return t.get("exit_time") or ""

    trades_sorted = sorted(
        [t for t in trades if t.get("exit_time")],
        key=_exit_ts,
    )

    if not trades_sorted:
        return []

    # Group trades by UTC date
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades_sorted:
        try:
            dt = datetime.fromisoformat(t["exit_time"].replace("Z", "+00:00"))
            day_key = dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
        by_day[day_key].append(t)

    rows = []
    cum_pnl = 0.0
    total_wins = 0
    total_losses = 0
    sum_win_pnl = 0.0
    sum_loss_pnl = 0.0  # stored as negative values

    for day in sorted(by_day.keys()):
        day_trades = by_day[day]
        day_pnl = sum(t.get("net_pnl", 0) for t in day_trades)
        cum_pnl += day_pnl

        for t in day_trades:
            pnl = t.get("net_pnl", 0)
            if pnl > 0:
                total_wins += 1
                sum_win_pnl += pnl
            elif pnl < 0:
                total_losses += 1
                sum_loss_pnl += pnl  # negative

        total_trades = total_wins + total_losses
        win_rate = (total_wins / total_trades * 100) if total_trades else 0
        avg_win = (sum_win_pnl / total_wins) if total_wins else 0
        avg_loss = (sum_loss_pnl / total_losses) if total_losses else 0  # negative

        rows.append({
            "date": day,
            "cumulative_pnl": round(cum_pnl, 2),
            "win_rate": round(win_rate, 1),
            "daily_trades": len(day_trades),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        })

    return rows
