from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from decimal import Decimal
import uuid

from app.database import get_db
from app.clients.phemex import PhemexClient
from app.clients.hyperliquid import HyperliquidClient
from app.config import settings
from app.models import (
    Trade as DBTrade, 
    Balance as DBBalance, 
    Position as DBPosition,
    OrderSide, 
    OrderStatus
)

router = APIRouter(prefix="/trading", tags=["trading"])
hyperliquid_client = HyperliquidClient()

phemex_client = PhemexClient(
    api_key=settings.phemex_api_key,
    api_secret=settings.phemex_api_secret,
    testnet=settings.phemex_testnet
)


class OrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    price: Optional[float] = None
    order_type: str = "Limit"


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    status: str


class TradeResponse(BaseModel):
    id: str
    symbol: str
    side: str
    quantity: float
    price: float
    total: float
    fee: float
    leverage: float = 1.0
    margin_used: float = 0.0
    status: str
    created_at: str
    phemex_order_id: Optional[str] = None


class BalanceResponse(BaseModel):
    asset: str
    available: float
    locked: float


class PositionResponse(BaseModel):
    id: Optional[str] = None
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    opened_at: Optional[datetime] = None
    margin_type: Optional[str] = 'cross'  # or 'isolated'
    leverage: Optional[float] = 1.0
    margin_used: Optional[float] = None
    liquidation_price: Optional[float] = None
    risk_level: Optional[str] = None


def trade_to_response(db_trade: DBTrade) -> TradeResponse:
    return TradeResponse(
        id=db_trade.id,
        symbol=db_trade.symbol,
        side=db_trade.side.value,
        quantity=db_trade.quantity,
        price=db_trade.price,
        total=db_trade.total,
        fee=db_trade.fee,
        leverage=db_trade.leverage or 1.0,
        margin_used=db_trade.margin_used or 0.0,
        status=db_trade.status.value,
        created_at=db_trade.created_at.isoformat() if db_trade.created_at else "",
        phemex_order_id=db_trade.phemex_order_id
    )


@router.post("/order")
async def place_order(order: OrderRequest, db: AsyncSession = Depends(get_db)):
    if not settings.phemex_api_key or not settings.phemex_api_secret:
        raise HTTPException(status_code=401, detail="API credentials not configured")
    
    result = await phemex_client.place_order(
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        order_type=order.order_type,
        price=order.price
    )
    
    price = order.price or 0
    total = order.quantity * price
    
    db_trade = DBTrade(
        id=result.get("orderID", ""),
        user_id="default-user",
        symbol=order.symbol,
        side=OrderSide.BUY if order.side.lower() == "buy" else OrderSide.SELL,
        quantity=order.quantity,
        price=price,
        total=total,
        fee=total * 0.001,
        status=OrderStatus.PENDING,
        phemex_order_id=result.get("orderID"),
        is_paper=False,
    )
    db.add(db_trade)
    await db.commit()
    
    return result


@router.delete("/order/{order_id}")
async def cancel_order(order_id: str, symbol: str = Query(...), db: AsyncSession = Depends(get_db)):
    if not settings.phemex_api_key or not settings.phemex_api_secret:
        raise HTTPException(status_code=401, detail="API credentials not configured")
    
    result = await phemex_client.cancel_order(order_id, symbol)
    
    query = await db.execute(select(DBTrade).where(DBTrade.phemex_order_id == order_id))
    db_trade = query.scalar_one_or_none()
    if db_trade:
        db_trade.status = OrderStatus.CANCELLED
        await db.commit()
    
    return result


@router.get("/orders")
async def get_open_orders(symbol: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    if not settings.phemex_api_key or not settings.phemex_api_secret:
        raise HTTPException(status_code=401, detail="API credentials not configured")
    
    orders = await phemex_client.get_open_orders(symbol)
    return {"data": orders}


@router.get("/positions", response_model=List[PositionResponse])
async def get_positions(db: AsyncSession = Depends(get_db)):
    if not settings.phemex_api_key or not settings.phemex_api_secret:
        raise HTTPException(status_code=401, detail="API credentials not configured")
    
    # First, get current price from Phemex
    try:
        current_prices = {}
        for symbol in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT']:  # Add your tracked symbols
            ticker = await phemex_client.get_ticker(symbol)
            current_prices[symbol] = float(ticker.get('result', {}).get('closeRp', 0))
    except Exception as e:
        current_prices = {}
        print(f"Error fetching current prices: {e}")
    
    # Fetch live positions only from database
    query = select(DBPosition).where(
        DBPosition.user_id == "default-user",
        DBPosition.is_paper == False,  # noqa: E712
    )
    result = await db.execute(query)
    db_positions = result.scalars().all()
    
    # Convert to response model
    enhanced_positions = []
    for pos in db_positions:
        # Use current price from Phemex if available
        current_price = current_prices.get(pos.symbol, pos.current_price or 0)
        
        # Calculate risk metrics
        entry_price = pos.entry_price or 0
        risk_level = 'low'
        if entry_price > 0 and current_price > 0:
            pct_change = abs((current_price - entry_price) / entry_price * 100)
            if pct_change > 5:
                risk_level = 'high'
            elif pct_change > 2:
                risk_level = 'medium'
        
        enhanced_positions.append(PositionResponse(
            id=pos.id,
            symbol=pos.symbol,
            side=pos.side.value,
            quantity=pos.quantity,
            entry_price=entry_price,
            current_price=current_price,
            unrealized_pnl=pos.unrealized_pnl or 0,
            margin_type='cross',
            risk_level=risk_level,
            # Optional: Add agent details if relevant
            # agent_id=pos.agent_id,
            # agent_name=pos.agent_name if relevant
            opened_at=pos.updated_at
        ))
    
    return enhanced_positions


@router.get("/balance", response_model=List[BalanceResponse])
async def get_balance(db: AsyncSession = Depends(get_db)):
    if not settings.phemex_api_key or not settings.phemex_api_secret:
        return get_demo_balances()
    
    try:
        balance = await phemex_client.get_account_balance()
        balances = balance.get("data", [])
        
        result = []
        for b in balances:
            asset = b.get("currency", "")
            available = parse_phemex_value(b.get("balanceEv"))
            locked = parse_phemex_value(b.get("lockedTradingBalanceEv"))

            stmt = pg_insert(DBBalance).values(
                id=str(uuid.uuid4()),
                user_id="default-user",
                asset=asset,
                available=available,
                locked=locked,
            ).on_conflict_do_update(
                index_elements=["user_id", "asset"],
                set_={"available": available, "locked": locked},
            )
            await db.execute(stmt)

            result.append(BalanceResponse(
                asset=asset,
                available=available,
                locked=locked
            ))

        await db.commit()
        return result
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "IP mismatch" in error_msg or "Unauthorized" in error_msg:
            return get_demo_balances()
        raise HTTPException(status_code=500, detail=f"Phemex API error: {error_msg}")


def get_demo_balances():
    return [
        BalanceResponse(asset="BTC", available=0.5, locked=0.0),
        BalanceResponse(asset="USDT", available=10000.0, locked=0.0),
        BalanceResponse(asset="ETH", available=10.0, locked=0.0),
    ]


class HyperliquidBalanceResponse(BaseModel):
    account_value: float
    margin_used: float
    free_margin: float
    unrealized_pnl: float
    positions: List[dict]


@router.get("/hl-balance", response_model=HyperliquidBalanceResponse)
async def get_hyperliquid_balance():
    """Fetch Hyperliquid account balance and open positions for the configured wallet."""
    address = settings.hyperliquid_wallet_address
    if not address:
        raise HTTPException(
            status_code=404,
            detail="Hyperliquid wallet address not configured",
        )
    try:
        state = await hyperliquid_client.get_clearinghouse_state(address)
        summary = state.get("marginSummary", {})
        account_value = float(summary.get("accountValue", 0))
        total_margin_used = float(summary.get("totalMarginUsed", 0))
        total_raw_usd = float(summary.get("totalRawUsd", 0))
        unrealized_pnl = float(summary.get("totalUnrealizedPnl", 0))
        free_margin = account_value - total_margin_used

        positions = []
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            size = float(pos.get("szi", 0))
            if size == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "size": size,
                "entry_price": float(pos.get("entryPx") or 0),
                "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
                "leverage": pos.get("leverage", {}).get("value", 1),
                "position_value": float(pos.get("positionValue") or 0),
            })

        return HyperliquidBalanceResponse(
            account_value=account_value,
            margin_used=total_margin_used,
            free_margin=free_margin,
            unrealized_pnl=unrealized_pnl,
            positions=positions,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Hyperliquid API error: {e}")


def parse_phemex_value(value) -> float:
    if value is None:
        return 0.0
    return float(value) / 100000000


@router.get("/history", response_model=List[TradeResponse])
async def get_trade_history(
    symbol: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(DBTrade)
        .where(DBTrade.is_paper == False)  # noqa: E712
        .order_by(desc(DBTrade.created_at))
        .limit(limit)
    )
    if symbol:
        query = query.where(DBTrade.symbol == symbol)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return [trade_to_response(t) for t in trades]


@router.get("/pnl")
async def get_pnl(db: AsyncSession = Depends(get_db)):
    # Calculate PNL from live positions only
    query = select(DBPosition).where(
        DBPosition.user_id == "default-user",
        DBPosition.is_paper == False,  # noqa: E712
    )
    result = await db.execute(query)
    positions = result.scalars().all()
    
    total_unrealized_pnl = sum(pos.unrealized_pnl or 0 for pos in positions)
    
    # Calculate total trades PNL from live trades only
    trade_query = select(DBTrade).where(
        DBTrade.status == OrderStatus.FILLED,
        DBTrade.is_paper == False,  # noqa: E712
    )
    trade_result = await db.execute(trade_query)
    trades = trade_result.scalars().all()
    
    buy_value = 0.0
    sell_value = 0.0
    
    for trade in trades:
        if trade.side == OrderSide.BUY:
            buy_value += trade.total
        else:
            sell_value += trade.total
    
    realized_pnl = sell_value - buy_value
    
    return {
        "total_pnl": total_unrealized_pnl + realized_pnl,
        "unrealized_pnl": total_unrealized_pnl,
        "realized_pnl": realized_pnl,
        "buy_volume": buy_value,
        "sell_volume": sell_value,
        "trade_count": len(trades),
        "position_count": len(positions)
    }
