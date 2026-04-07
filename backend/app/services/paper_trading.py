from typing import Optional, List
from datetime import datetime
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_async_session
from app.models import (
    Trade as PaperOrder,
    Position as PaperPosition,
    Balance as PaperBalance,
    OrderSide,
    OrderStatus,
)
from app.clients.phemex import PhemexClient
from app.config import settings


class PaperTradingService:
    def __init__(self, phemex_client: Optional[PhemexClient] = None):
        self.logger = logging.getLogger(__name__)
        self._enabled = True
        self.phemex_client = phemex_client or PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet,
        )

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    async def reset(self):
        await self.reset_trading_session()

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_all_balances(self) -> List[PaperBalance]:
        """Return all paper-trading balances for the default user."""
        async with get_async_session() as db:
            result = await db.execute(
                select(PaperBalance).where(PaperBalance.user_id == "default-user")
            )
            return result.scalars().all()

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: Optional[float] = None,
        agent_id: Optional[str] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
    ):
        """Place a paper trading order using real market price when price is omitted."""
        if not self._enabled:
            raise ValueError("Paper trading is currently disabled")

        if price is None:
            try:
                ticker = await self.phemex_client.get_ticker(symbol)
                price = float(ticker.get("result", {}).get("closeRp", 0))
            except Exception as e:
                self.logger.error(f"Failed to fetch market price: {e}")
                raise ValueError("Unable to fetch current market price")

        async with get_async_session() as db:
            # Ensure USDT balance exists
            usdt_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == "default-user",
                    PaperBalance.asset == "USDT",
                )
            )
            if not usdt_balance:
                usdt_balance = PaperBalance(
                    user_id="default-user", asset="USDT", available=50000.0, locked=0.0
                )
                db.add(usdt_balance)
                await db.flush()

            # Ensure base-asset balance exists (for sells / bookkeeping)
            base_asset = symbol.replace("USDT", "") if "USDT" in symbol else symbol
            base_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == "default-user",
                    PaperBalance.asset == base_asset,
                )
            )
            if not base_balance:
                base_balance = PaperBalance(
                    user_id="default-user", asset=base_asset, available=0.0, locked=0.0
                )
                db.add(base_balance)
                await db.flush()

            order = PaperOrder(
                id=str(uuid.uuid4()),
                user_id="default-user",
                agent_id=agent_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                total=quantity * price,
                fee=quantity * price * 0.001,
                status=OrderStatus.FILLED,
                created_at=datetime.now(),
                filled_at=datetime.now(),
            )
            db.add(order)

            if side == OrderSide.BUY:
                cost = quantity * price
                if usdt_balance.available < cost:
                    raise ValueError(
                        f"Insufficient USDT balance: need {cost:.2f}, have {usdt_balance.available:.2f}"
                    )
                usdt_balance.available -= cost

                position = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == "default-user",
                        PaperPosition.symbol == symbol,
                    )
                )
                if position:
                    total_qty = position.quantity + quantity
                    position.entry_price = (
                        position.entry_price * position.quantity + price * quantity
                    ) / total_qty
                    position.quantity = total_qty
                    position.current_price = price
                    # Update highest_price watermark
                    if position.highest_price is None or price > position.highest_price:
                        position.highest_price = price
                    if stop_loss_price is not None:
                        position.stop_loss_price = stop_loss_price
                    if take_profit_price is not None:
                        position.take_profit_price = take_profit_price
                    if trailing_stop_pct is not None:
                        position.trailing_stop_pct = trailing_stop_pct
                else:
                    position = PaperPosition(
                        user_id="default-user",
                        agent_id=agent_id,
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=quantity,
                        entry_price=price,
                        current_price=price,
                        unrealized_pnl=0.0,
                        realized_pnl=0.0,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        highest_price=price,
                        trailing_stop_pct=trailing_stop_pct,
                    )
                    db.add(position)

            elif side == OrderSide.SELL:
                position = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == "default-user",
                        PaperPosition.symbol == symbol,
                    )
                )
                if not position or position.quantity < quantity:
                    raise ValueError("Insufficient position to sell")

                realized = (price - position.entry_price) * quantity
                usdt_balance.available += quantity * price

                if position.quantity == quantity:
                    await db.delete(position)
                else:
                    position.quantity -= quantity
                    position.realized_pnl = (position.realized_pnl or 0.0) + realized
                    position.current_price = price

            await db.commit()
            return order

    async def get_orders(self, symbol: Optional[str] = None, limit: int = 50) -> List[PaperOrder]:
        """Return paper trade order history for the default user."""
        async with get_async_session() as db:
            query = (
                select(PaperOrder)
                .where(PaperOrder.user_id == "default-user")
                .order_by(PaperOrder.created_at.desc())
                .limit(limit)
            )
            if symbol:
                query = query.where(PaperOrder.symbol == symbol)
            result = await db.execute(query)
            return result.scalars().all()

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending paper order."""
        async with get_async_session() as db:
            order = await db.scalar(
                select(PaperOrder).where(
                    PaperOrder.id == order_id,
                    PaperOrder.status == OrderStatus.PENDING,
                )
            )
            if order:
                order.status = OrderStatus.CANCELLED
                await db.commit()
                return True
            return False

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_positions(self, symbol: Optional[str] = None) -> List[PaperPosition]:
        """Return open paper trading positions."""
        async with get_async_session() as db:
            query = select(PaperPosition).where(PaperPosition.user_id == "default-user")
            if symbol:
                query = query.where(PaperPosition.symbol == symbol)
            result = await db.execute(query)
            return result.scalars().all()

    async def get_positions_live(self, symbol: Optional[str] = None) -> list:
        """Return open positions with live market prices and unrealized P&L."""
        positions = await self.get_positions(symbol)
        live_positions = []
        for pos in positions:
            try:
                current_price = await self.fetch_current_price(pos.symbol)
            except Exception:
                current_price = pos.current_price or pos.entry_price or 0.0
            entry = pos.entry_price or 0.0
            unrealized = (current_price - entry) * (pos.quantity or 0)
            unrealized_pct = ((current_price - entry) / entry * 100) if entry > 0 else 0.0
            live_positions.append({
                "symbol": pos.symbol,
                "side": pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                "quantity": pos.quantity,
                "entry_price": entry,
                "current_price": current_price,
                "unrealized_pnl": round(unrealized, 4),
                "unrealized_pnl_pct": round(unrealized_pct, 2),
                "stop_loss_price": getattr(pos, 'stop_loss_price', None),
                "take_profit_price": getattr(pos, 'take_profit_price', None),
                "highest_price": getattr(pos, 'highest_price', None),
                "trailing_stop_pct": getattr(pos, 'trailing_stop_pct', None),
                "updated_at": pos.updated_at.isoformat() if pos.updated_at else None,
                "agent_id": pos.agent_id,
            })
        return live_positions

    async def update_highest_price(self, position_id: str, current_price: float):
        """Update the highest price watermark for trailing stop tracking."""
        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if pos:
                if pos.highest_price is None or current_price > pos.highest_price:
                    pos.highest_price = current_price
                    await db.commit()

    # ------------------------------------------------------------------
    # P&L
    # ------------------------------------------------------------------

    async def calculate_pnl(self) -> dict:
        """Calculate paper trading performance metrics."""
        async with get_async_session() as db:
            orders_result = await db.execute(
                select(PaperOrder).where(
                    PaperOrder.user_id == "default-user",
                    PaperOrder.status == OrderStatus.FILLED,
                )
            )
            orders = orders_result.scalars().all()

            positions_result = await db.execute(
                select(PaperPosition).where(PaperPosition.user_id == "default-user")
            )
            positions = positions_result.scalars().all()

        buy_volume = sum(o.quantity * o.price for o in orders if o.side == OrderSide.BUY)
        sell_volume = sum(o.quantity * o.price for o in orders if o.side == OrderSide.SELL)

        # Realized P&L: sum of realized_pnl recorded when positions are closed
        # Each sell records realized_pnl = (sell_price - avg_entry) * qty
        realized_pnl = sum(pos.realized_pnl or 0 for pos in positions)
        # Also count fully closed positions (no longer in DB) via sell orders
        # matched against corresponding buys per symbol
        symbol_buys: dict = {}
        symbol_sells: dict = {}
        for o in sorted(orders, key=lambda x: x.created_at or datetime.min):
            sym = o.symbol
            if o.side == OrderSide.BUY:
                symbol_buys.setdefault(sym, []).append((o.quantity, o.price))
            else:
                symbol_sells.setdefault(sym, []).append((o.quantity, o.price))

        closed_pnl = 0.0
        for sym, sells in symbol_sells.items():
            buys = list(symbol_buys.get(sym, []))
            buy_idx = 0
            buy_remaining = buys[0][0] if buys else 0
            for sell_qty, sell_price in sells:
                remaining = sell_qty
                while remaining > 0 and buy_idx < len(buys):
                    fill = min(remaining, buy_remaining)
                    buy_price = buys[buy_idx][1]
                    closed_pnl += fill * (sell_price - buy_price)
                    remaining -= fill
                    buy_remaining -= fill
                    if buy_remaining <= 1e-12:
                        buy_idx += 1
                        buy_remaining = buys[buy_idx][0] if buy_idx < len(buys) else 0

        # Fetch live prices for unrealized PnL
        current_prices: dict = {}
        for pos in positions:
            try:
                current_prices[pos.symbol] = await self.fetch_current_price(pos.symbol)
            except Exception as e:
                self.logger.error(f"Failed to fetch price for {pos.symbol}: {e}")
                current_prices[pos.symbol] = pos.entry_price or 0.0

        unrealized_pnl = sum(
            (current_prices.get(pos.symbol, pos.entry_price or 0.0) - (pos.entry_price or 0.0))
            * pos.quantity
            for pos in positions
        )

        return {
            "total_pnl": closed_pnl + unrealized_pnl,
            "realized_pnl": closed_pnl,
            "unrealized_pnl": unrealized_pnl,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "trade_count": len(orders),
            "open_positions": len(positions),
        }

    async def fetch_current_price(self, symbol: str) -> float:
        """Fetch live market price for a symbol."""
        try:
            ticker = await self.phemex_client.get_ticker(symbol)
            return float(ticker.get("result", {}).get("closeRp", 0))
        except Exception as e:
            self.logger.error(f"Failed to fetch price for {symbol}: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    async def reset_trading_session(self):
        """Wipe all paper trading data and re-seed default balances."""
        async with get_async_session() as db:
            await db.execute(
                delete(PaperOrder).where(PaperOrder.user_id == "default-user")
            )
            await db.execute(
                delete(PaperPosition).where(PaperPosition.user_id == "default-user")
            )
            await db.execute(
                delete(PaperBalance).where(PaperBalance.user_id == "default-user")
            )
            for asset, amount in [("BTC", 1.0), ("USDT", 50000.0), ("ETH", 10.0), ("SOL", 100.0)]:
                db.add(
                    PaperBalance(
                        user_id="default-user", asset=asset, available=amount, locked=0.0
                    )
                )
            await db.commit()


paper_trading = PaperTradingService()
