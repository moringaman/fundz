import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

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
            "status": o.status.value,
            "created_at": o.created_at.isoformat(),
            "agent_id": o.agent_id,
        }
        for o in orders
    ]


@router.get("/positions")
async def get_paper_positions(symbol: Optional[str] = None):
    positions = await paper_trading.get_positions(symbol)
    return [
        {
            "symbol": pos.symbol,
            "side": pos.side.value,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "current_price": pos.current_price,
            "unrealized_pnl": pos.unrealized_pnl or 0,
            "updated_at": pos.updated_at.isoformat() if pos.updated_at else None,
            "agent_id": pos.agent_id,
        }
        for pos in positions
    ]


@router.get("/pnl")
async def get_paper_pnl():
    return await paper_trading.calculate_pnl()
