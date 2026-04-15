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
    # Fee rates — Phemex spot taker: 0.1%, contract taker: 0.06%
    SPOT_FEE_RATE = 0.001    # 0.10% for USDT spot pairs
    CONTRACT_FEE_RATE = 0.0006  # 0.06% for coin-margined contracts

    @classmethod
    def fee_rate_for(cls, symbol: str) -> float:
        return cls.SPOT_FEE_RATE if symbol.endswith("USDT") else cls.CONTRACT_FEE_RATE

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

    async def adjust_balance(self, asset: str, amount: float) -> dict:
        """Add or subtract funds from a paper-trading balance.

        *amount* is signed: positive = deposit, negative = withdraw.
        Returns the updated balance dict.
        """
        asset = asset.upper()
        async with get_async_session() as db:
            result = await db.execute(
                select(PaperBalance).where(
                    PaperBalance.user_id == "default-user",
                    PaperBalance.asset == asset,
                )
            )
            bal = result.scalar_one_or_none()
            if bal is None:
                if amount < 0:
                    raise ValueError(f"No {asset} balance exists to withdraw from")
                bal = PaperBalance(
                    user_id="default-user",
                    asset=asset,
                    available=amount,
                    locked=0.0,
                )
                db.add(bal)
            else:
                if bal.available + amount < 0:
                    raise ValueError(
                        f"Insufficient {asset} balance: have {bal.available}, "
                        f"tried to withdraw {abs(amount)}"
                    )
                bal.available += amount
            await db.commit()
            await db.refresh(bal)
            return {"asset": bal.asset, "available": bal.available, "locked": bal.locked}

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
        trader_id: Optional[str] = None,
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

            notional = quantity * price
            fee = notional * self.fee_rate_for(symbol)

            order = PaperOrder(
                id=str(uuid.uuid4()),
                user_id="default-user",
                agent_id=agent_id,
                trader_id=trader_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                total=notional,
                fee=fee,
                status=OrderStatus.FILLED,
                is_paper=True,
                created_at=datetime.now(),
                filled_at=datetime.now(),
            )
            db.add(order)

            # Deduct fee from balance immediately
            usdt_balance.available -= fee

            if side == OrderSide.BUY:
                # Check if closing an existing SHORT position first
                short_position = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == "default-user",
                        PaperPosition.symbol == symbol,
                        PaperPosition.agent_id == agent_id,
                        PaperPosition.side == OrderSide.SELL,
                    )
                )
                if short_position:
                    # Closing (covering) a short position
                    close_qty = min(quantity, short_position.quantity)
                    # Short P&L: entry(sell) - exit(buy)
                    realized = (short_position.entry_price - price) * close_qty
                    # Net cost is only the loss (if any) — margin from original short is returned
                    net_cost = max(-realized, 0)  # positive when trade lost money
                    if usdt_balance.available < net_cost:
                        raise ValueError(
                            f"Insufficient USDT balance to cover short loss: need {net_cost:.2f}, have {usdt_balance.available:.2f}"
                        )
                    # Apply net settlement: return margin + realized P&L
                    usdt_balance.available += realized  # positive if profitable, negative if loss

                    if short_position.quantity <= close_qty + 1e-12:
                        await db.delete(short_position)
                    else:
                        short_position.quantity -= close_qty
                        short_position.realized_pnl = (short_position.realized_pnl or 0.0) + realized
                        short_position.current_price = price

                    try:
                        from app.services.risk_manager import risk_manager
                        risk_manager.record_pnl(realized)
                    except Exception:
                        pass

                    # If buy quantity exceeds short, open a long with remainder
                    remainder = quantity - close_qty
                    if remainder > 1e-12:
                        extra_cost = remainder * price
                        if usdt_balance.available < extra_cost:
                            # Just close the short, skip remainder
                            await db.commit()
                            return order
                        usdt_balance.available -= extra_cost
                        new_pos = PaperPosition(
                            user_id="default-user",
                            agent_id=agent_id,
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=remainder,
                            entry_price=price,
                            current_price=price,
                            unrealized_pnl=0.0,
                            realized_pnl=0.0,
                            stop_loss_price=stop_loss_price,
                            take_profit_price=take_profit_price,
                            highest_price=price,
                            trailing_stop_pct=trailing_stop_pct,
                            is_paper=True,
                        )
                        db.add(new_pos)
                else:
                    # Opening / adding to a LONG position
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
                            PaperPosition.agent_id == agent_id,
                            PaperPosition.side == OrderSide.BUY,
                        )
                    )
                    if position:
                        total_qty = position.quantity + quantity
                        position.entry_price = (
                            position.entry_price * position.quantity + price * quantity
                        ) / total_qty
                        position.quantity = total_qty
                        position.current_price = price
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
                            is_paper=True,
                        )
                        db.add(position)

            elif side == OrderSide.SELL:
                # Check if closing an existing LONG position first
                long_position = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == "default-user",
                        PaperPosition.symbol == symbol,
                        PaperPosition.agent_id == agent_id,
                        PaperPosition.side == OrderSide.BUY,
                    )
                )
                if long_position and long_position.quantity >= quantity - 1e-12:
                    # Closing a long position
                    realized = (price - long_position.entry_price) * quantity
                    usdt_balance.available += quantity * price

                    if long_position.quantity <= quantity + 1e-12:
                        await db.delete(long_position)
                    else:
                        long_position.quantity -= quantity
                        long_position.realized_pnl = (long_position.realized_pnl or 0.0) + realized
                        long_position.current_price = price

                    try:
                        from app.services.risk_manager import risk_manager
                        risk_manager.record_pnl(realized)
                    except Exception:
                        pass
                else:
                    # Opening / adding to a SHORT position
                    # Margin requirement: lock USDT equal to position value
                    margin = quantity * price
                    if usdt_balance.available < margin:
                        raise ValueError(
                            f"Insufficient USDT margin for short: need {margin:.2f}, have {usdt_balance.available:.2f}"
                        )
                    usdt_balance.available -= margin

                    # Close any remaining long first
                    if long_position and long_position.quantity > 0:
                        realized = (price - long_position.entry_price) * long_position.quantity
                        usdt_balance.available += long_position.quantity * price
                        remaining_short_qty = quantity - long_position.quantity
                        await db.delete(long_position)
                        try:
                            from app.services.risk_manager import risk_manager
                            risk_manager.record_pnl(realized)
                        except Exception:
                            pass
                    else:
                        remaining_short_qty = quantity

                    if remaining_short_qty > 1e-12:
                        short_pos = await db.scalar(
                            select(PaperPosition).where(
                                PaperPosition.user_id == "default-user",
                                PaperPosition.symbol == symbol,
                                PaperPosition.agent_id == agent_id,
                                PaperPosition.side == OrderSide.SELL,
                            )
                        )
                        if short_pos:
                            total_qty = short_pos.quantity + remaining_short_qty
                            short_pos.entry_price = (
                                short_pos.entry_price * short_pos.quantity + price * remaining_short_qty
                            ) / total_qty
                            short_pos.quantity = total_qty
                            short_pos.current_price = price
                            # For shorts, lowest_price is the watermark (stored in highest_price field)
                            if short_pos.highest_price is None or price < short_pos.highest_price:
                                short_pos.highest_price = price
                            if stop_loss_price is not None:
                                short_pos.stop_loss_price = stop_loss_price
                            if take_profit_price is not None:
                                short_pos.take_profit_price = take_profit_price
                            if trailing_stop_pct is not None:
                                short_pos.trailing_stop_pct = trailing_stop_pct
                        else:
                            short_pos = PaperPosition(
                                user_id="default-user",
                                agent_id=agent_id,
                                symbol=symbol,
                                side=OrderSide.SELL,
                                quantity=remaining_short_qty,
                                entry_price=price,
                                current_price=price,
                                unrealized_pnl=0.0,
                                realized_pnl=0.0,
                                stop_loss_price=stop_loss_price,
                                take_profit_price=take_profit_price,
                                highest_price=price,  # lowest price watermark for shorts
                                trailing_stop_pct=trailing_stop_pct,
                                is_paper=True,
                            )
                            db.add(short_pos)

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

    async def get_positions(self, symbol: Optional[str] = None, agent_id: Optional[str] = None) -> List[PaperPosition]:
        """Return open paper trading positions, optionally filtered by symbol and/or agent."""
        async with get_async_session() as db:
            query = select(PaperPosition).where(PaperPosition.user_id == "default-user")
            if symbol:
                query = query.where(PaperPosition.symbol == symbol)
            if agent_id:
                query = query.where(PaperPosition.agent_id == agent_id)
            result = await db.execute(query)
            return result.scalars().all()

    async def get_position(self, position_id: str) -> Optional[PaperPosition]:
        """Fetch a single position by ID from DB (always fresh, bypasses in-memory cache)."""
        async with get_async_session() as db:
            return await db.get(PaperPosition, position_id)

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
            pos_side = pos.side.value if hasattr(pos.side, 'value') else str(pos.side)
            is_short = pos_side.lower() == 'sell'
            qty = pos.quantity or 0
            # Round-trip fee estimate so unrealized P&L matches closed-trade net P&L
            _fr = self.fee_rate_for(pos.symbol)
            _est_fees = (entry * qty * _fr) + (current_price * qty * _fr)
            if is_short:
                unrealized = (entry - current_price) * qty - _est_fees
                unrealized_pct = (unrealized / (entry * qty) * 100) if entry * qty > 0 else 0.0
            else:
                unrealized = (current_price - entry) * qty - _est_fees
                unrealized_pct = (unrealized / (entry * qty) * 100) if entry * qty > 0 else 0.0
            sl_price = getattr(pos, 'stop_loss_price', None)
            tp_price = getattr(pos, 'take_profit_price', None)

            # Distance from current price to SL — positive = still safe, negative = already past SL
            distance_to_sl_pct: Optional[float] = None
            if sl_price and current_price:
                if is_short:
                    distance_to_sl_pct = round((sl_price - current_price) / current_price * 100, 2)
                else:
                    distance_to_sl_pct = round((current_price - sl_price) / current_price * 100, 2)

            # Distance from current price to TP
            distance_to_tp_pct: Optional[float] = None
            if tp_price and current_price:
                if is_short:
                    distance_to_tp_pct = round((current_price - tp_price) / current_price * 100, 2)
                else:
                    distance_to_tp_pct = round((tp_price - current_price) / current_price * 100, 2)

            # Is the SL below entry (long) or above entry (short)?
            # If true the position would close at a loss if the stop fires.
            sl_below_entry: bool = False
            pnl_at_sl: Optional[float] = None
            if sl_price and entry:
                if is_short:
                    sl_below_entry = sl_price > entry   # short SL above entry = loss
                else:
                    sl_below_entry = sl_price < entry   # long SL below entry = loss
                qty = pos.quantity or 0
                if is_short:
                    pnl_at_sl = round((entry - sl_price) * qty, 2)
                else:
                    pnl_at_sl = round((sl_price - entry) * qty, 2)

            # Danger tiers:
            #   critical  — within 1% of SL (immediate stop-out risk)
            #   warning   — within 2.5% of SL (approaching danger)
            #   safe      — more than 2.5% away (or no SL set)
            if distance_to_sl_pct is not None and distance_to_sl_pct >= 0:
                if distance_to_sl_pct <= 1.0:
                    sl_danger = "critical"
                elif distance_to_sl_pct <= 2.5:
                    sl_danger = "warning"
                else:
                    sl_danger = "safe"
            elif distance_to_sl_pct is not None and distance_to_sl_pct < 0:
                sl_danger = "critical"  # Already past SL
            else:
                sl_danger = "safe"

            live_positions.append({
                "id": pos.id,
                "symbol": pos.symbol,
                "side": pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                "quantity": pos.quantity,
                "entry_price": entry,
                "current_price": current_price,
                "unrealized_pnl": round(unrealized, 4),
                "unrealized_pnl_pct": round(unrealized_pct, 2),
                "stop_loss_price": sl_price,
                "take_profit_price": tp_price,
                "distance_to_sl_pct": distance_to_sl_pct,
                "distance_to_tp_pct": distance_to_tp_pct,
                "sl_below_entry": sl_below_entry,
                "pnl_at_sl": pnl_at_sl,
                "sl_danger": sl_danger,
                "highest_price": getattr(pos, 'highest_price', None),
                "trailing_stop_pct": getattr(pos, 'trailing_stop_pct', None),
                "updated_at": pos.updated_at.isoformat() if pos.updated_at else None,
                "agent_id": pos.agent_id,
                "is_paper": getattr(pos, 'is_paper', True),
            })
        return live_positions

    async def update_position_sl_tp(
        self,
        position_id: str,
        stop_loss_price: Optional[float] = ...,
        take_profit_price: Optional[float] = ...,
        trailing_stop_pct: Optional[float] = ...,
    ) -> Optional[dict]:
        """Update stop-loss, take-profit, or trailing-stop on an open position.

        Pass ``None`` to clear a field. Omit (leave as ``...``) to leave unchanged.
        """
        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if not pos or pos.user_id != "default-user":
                return None

            if stop_loss_price is not ...:
                pos.stop_loss_price = stop_loss_price
            if take_profit_price is not ...:
                pos.take_profit_price = take_profit_price
            if trailing_stop_pct is not ...:
                pos.trailing_stop_pct = trailing_stop_pct

            await db.commit()
            await db.refresh(pos)

            self.logger.info(
                "Position %s (%s) SL/TP updated → SL=%s  TP=%s  trail=%s",
                pos.symbol, position_id[:8],
                pos.stop_loss_price, pos.take_profit_price, pos.trailing_stop_pct,
            )

            return {
                "id": pos.id,
                "symbol": pos.symbol,
                "stop_loss_price": pos.stop_loss_price,
                "take_profit_price": pos.take_profit_price,
                "trailing_stop_pct": pos.trailing_stop_pct,
            }

    async def update_highest_price(self, position_id: str, current_price: float, is_short: bool = False):
        """Update the price watermark for trailing stop tracking.
        
        For longs: tracks the highest price (sell exit if price drops from peak).
        For shorts: tracks the lowest price (buy exit if price rises from trough).
        """
        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if pos:
                if is_short:
                    if pos.highest_price is None or current_price < pos.highest_price:
                        pos.highest_price = current_price
                        await db.commit()
                else:
                    if pos.highest_price is None or current_price > pos.highest_price:
                        pos.highest_price = current_price
                        await db.commit()

    async def close_position(self, position_id: str) -> Optional[dict]:
        """Close an open position at current market price.

        Places an opposite-side order for the full quantity, which the
        normal ``place_order`` logic will match against the existing position
        and record the closed trade.
        """
        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if not pos or pos.user_id != "default-user":
                return None

            symbol = pos.symbol
            quantity = pos.quantity
            side = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            entry = pos.entry_price or 0

        # Fetch live price
        try:
            current_price = await self.fetch_current_price(symbol)
        except Exception:
            current_price = None

        # Opposite side to close
        close_side = "buy" if side.lower() == "sell" else "sell"

        order = await self.place_order(
            symbol=symbol,
            side=close_side,
            quantity=quantity,
            price=current_price,
            agent_id=pos.agent_id,
        )

        is_short = side.lower() == "sell"
        if is_short:
            pnl = (entry - (current_price or entry)) * quantity
        else:
            pnl = ((current_price or entry) - entry) * quantity

        return {
            "closed": True,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry,
            "exit_price": current_price,
            "pnl": round(pnl, 4),
            "order": order,
        }

    async def partial_close(
        self,
        position_id: str,
        close_pct: float,
        price: float,
        agent_id: Optional[str] = None,
        label: str = "scale-out",
    ) -> Optional[dict]:
        """
        Close a fraction of an open position at the given price.

        close_pct: 0.0–1.0 (e.g. 0.33 = close 33% of remaining quantity)

        Returns a summary dict with realized_pnl, quantity_closed, and
        remaining_quantity, or None if the position was not found.

        The position record is updated in-place (quantity reduced, realized_pnl
        accumulated).  A Trade record is written for the audit trail.
        """
        close_pct = max(0.0, min(close_pct, 1.0))
        if close_pct == 0.0:
            return None

        fee_rate = self.fee_rate_for
        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if pos is None:
                return None

            qty_to_close = pos.quantity * close_pct
            if qty_to_close < 1e-12:
                return None

            entry = pos.entry_price or price
            side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            is_long = side_str.upper() == "BUY"

            # Raw P&L on the slice
            if is_long:
                raw_pnl = qty_to_close * (price - entry)
            else:
                raw_pnl = qty_to_close * (entry - price)

            # Fee on the closing notional
            fr = self.fee_rate_for(pos.symbol)
            close_fee = qty_to_close * price * fr
            # Proportional entry fee for this slice (paid at open, allocated per unit)
            entry_fee = qty_to_close * entry * fr
            net_pnl = raw_pnl - close_fee - entry_fee

            # Reduce position quantity and accumulate realized P&L
            pos.quantity -= qty_to_close
            pos.realized_pnl = (pos.realized_pnl or 0.0) + net_pnl

            # Credit USDT balance with the proceeds from closing this slice
            usdt_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == pos.user_id,
                    PaperBalance.asset == "USDT",
                )
            )
            if usdt_balance is not None:
                usdt_balance.available += qty_to_close * price - close_fee

            # Write an audit trade record
            close_side = OrderSide.SELL if is_long else OrderSide.BUY
            import uuid as _uuid
            trade = PaperOrder(
                id=str(_uuid.uuid4()),
                user_id=pos.user_id,
                agent_id=agent_id or pos.agent_id,
                symbol=pos.symbol,
                side=close_side,
                quantity=qty_to_close,
                price=price,
                total=qty_to_close * price,
                fee=close_fee,
                status=OrderStatus.FILLED,
                is_paper=True,
                phemex_order_id=f"{label}-{str(_uuid.uuid4())[:8]}",
            )
            db.add(trade)

            # If position is fully closed, remove it
            if pos.quantity < 1e-12:
                await db.delete(pos)
                remaining = 0.0
            else:
                remaining = pos.quantity

            await db.commit()

        return {
            "label": label,
            "quantity_closed": qty_to_close,
            "remaining_quantity": remaining,
            "price": price,
            "raw_pnl": raw_pnl,
            "net_pnl": net_pnl,
            "close_fee": close_fee,
            "entry_fee": entry_fee,
            "total_fee": close_fee + entry_fee,
        }

    # ------------------------------------------------------------------
    # Closed Trades (FIFO-matched buy→sell pairs with realised P&L)
    # ------------------------------------------------------------------

    async def get_closed_trades(self, symbol: Optional[str] = None, limit: int = 100) -> List[dict]:
        """Return completed round-trip trades with realised P&L.

        Uses FIFO matching: for longs, each sell is matched against earliest
        unfilled buy. For shorts, each buy is matched against earliest
        unfilled sell.
        """
        async with get_async_session() as db:
            query = (
                select(PaperOrder)
                .where(
                    PaperOrder.user_id == "default-user",
                    PaperOrder.status == OrderStatus.FILLED,
                )
                .order_by(PaperOrder.created_at.asc())
            )
            if symbol:
                query = query.where(PaperOrder.symbol == symbol)
            result = await db.execute(query)
            orders = result.scalars().all()

        # Group by (symbol, agent_id) for per-agent isolation
        groups: dict[tuple, dict] = {}
        for o in orders:
            key = (o.symbol, o.agent_id or "__none__")
            groups.setdefault(key, {"buys": [], "sells": []})
            if o.side == OrderSide.BUY:
                groups[key]["buys"].append(o)
            else:
                groups[key]["sells"].append(o)

        closed: list[dict] = []
        for (sym, agent_id), sides in groups.items():
            # --- LONG trades: buy→sell FIFO ---
            buys = list(sides["buys"])
            buy_idx = 0
            buy_remaining = buys[0].quantity if buys else 0

            for sell in sides["sells"]:
                remaining = sell.quantity
                while remaining > 1e-12 and buy_idx < len(buys):
                    fill = min(remaining, buy_remaining)
                    buy = buys[buy_idx]
                    pnl = fill * (sell.price - buy.price)
                    fee = (buy.fee or 0) * (fill / buy.quantity) + (sell.fee or 0) * (fill / sell.quantity)
                    net_pnl = pnl - fee
                    entry_notional = fill * buy.price
                    pnl_pct = (net_pnl / entry_notional * 100) if entry_notional else 0

                    closed.append({
                        "symbol": sym,
                        "agent_id": agent_id if agent_id != "__none__" else None,
                        "side": "long",
                        "quantity": round(fill, 8),
                        "entry_price": buy.price,
                        "exit_price": sell.price,
                        "entry_time": buy.created_at.isoformat() if buy.created_at else None,
                        "exit_time": sell.created_at.isoformat() if sell.created_at else None,
                        "gross_pnl": round(pnl, 6),
                        "fee": round(fee, 6),
                        "net_pnl": round(net_pnl, 6),
                        "pnl_pct": round(pnl_pct, 4),
                        "result": "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "breakeven"),
                    })

                    remaining -= fill
                    buy_remaining -= fill
                    if buy_remaining <= 1e-12:
                        buy_idx += 1
                        buy_remaining = buys[buy_idx].quantity if buy_idx < len(buys) else 0

            # --- SHORT trades: sell→buy FIFO ---
            # Unmatched sells (after long matching) are short entries
            # Match remaining sells against buys that follow them chronologically
            unmatched_sells = []
            for s in sides["sells"]:
                unmatched_sells.append({"order": s, "remaining": s.quantity})
            # Subtract fills already used by long matching
            for entry in unmatched_sells:
                entry["remaining"] = 0  # reset — we'll rebuild from scratch

            # Rebuild: sells that come BEFORE any matching buy = short entries
            sell_list = list(sides["sells"])
            buy_list = list(sides["buys"])
            # Short detection: sells that weren't preceded by enough buys
            cum_bought = 0.0
            cum_sold = 0.0
            all_orders = sorted(
                [(o, "buy") for o in buy_list] + [(o, "sell") for o in sell_list],
                key=lambda x: x[0].created_at
            )
            short_entries: list = []  # (order, qty)
            short_remaining: list = []

            for o, side_str in all_orders:
                if side_str == "buy":
                    cum_bought += o.quantity
                    # Check if this buy covers any open shorts
                    for sr in short_remaining:
                        if sr["remaining"] <= 1e-12:
                            continue
                        cover = min(o.quantity, sr["remaining"])
                        if cover > 1e-12:
                            entry_order = sr["order"]
                            pnl = cover * (entry_order.price - o.price)  # short P&L
                            fee = (entry_order.fee or 0) * (cover / entry_order.quantity) + (o.fee or 0) * (cover / o.quantity)
                            net_pnl = pnl - fee
                            entry_notional = cover * entry_order.price
                            pnl_pct = (net_pnl / entry_notional * 100) if entry_notional else 0

                            closed.append({
                                "symbol": sym,
                                "agent_id": agent_id if agent_id != "__none__" else None,
                                "side": "short",
                                "quantity": round(cover, 8),
                                "entry_price": entry_order.price,
                                "exit_price": o.price,
                                "entry_time": entry_order.created_at.isoformat() if entry_order.created_at else None,
                                "exit_time": o.created_at.isoformat() if o.created_at else None,
                                "gross_pnl": round(pnl, 6),
                                "fee": round(fee, 6),
                                "net_pnl": round(net_pnl, 6),
                                "pnl_pct": round(pnl_pct, 4),
                                "result": "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "breakeven"),
                            })
                            sr["remaining"] -= cover
                else:
                    cum_sold += o.quantity
                    if cum_sold > cum_bought + 1e-12:
                        short_qty = min(o.quantity, cum_sold - cum_bought)
                        short_remaining.append({"order": o, "remaining": short_qty})

        # ── Consolidate FIFO slices that share the same exit order ────────
        # When multiple entry orders are closed by a single exit (e.g. 3
        # scale-in entries closed by 1 buy), the FIFO matcher creates one
        # row per entry. Merge them into a single round-trip row so the
        # user sees one trade with the correct total P&L.
        merged: list[dict] = []
        merge_map: dict[str, int] = {}   # (agent_id, side, exit_time) → index
        for t in closed:
            key = f"{t['agent_id']}|{t['side']}|{t['exit_time']}"
            if key in merge_map:
                idx = merge_map[key]
                m = merged[idx]
                old_qty = m["quantity"]
                new_qty = old_qty + t["quantity"]
                # weighted-average entry price
                m["entry_price"] = (m["entry_price"] * old_qty + t["entry_price"] * t["quantity"]) / new_qty
                m["quantity"] = round(new_qty, 8)
                m["gross_pnl"] = round(m["gross_pnl"] + t["gross_pnl"], 6)
                m["fee"] = round(m["fee"] + t["fee"], 6)
                m["net_pnl"] = round(m["net_pnl"] + t["net_pnl"], 6)
                entry_notional = m["quantity"] * m["entry_price"]
                m["pnl_pct"] = round((m["net_pnl"] / entry_notional * 100) if entry_notional else 0, 4)
                m["result"] = "win" if m["net_pnl"] > 0 else ("loss" if m["net_pnl"] < 0 else "breakeven")
            else:
                merge_map[key] = len(merged)
                merged.append(dict(t))    # shallow copy

        # Sort by exit time descending (most recent first) and limit
        merged.sort(key=lambda t: t["exit_time"] or "", reverse=True)
        return merged[:limit]

    async def get_agent_performance_from_db(self) -> dict[str, dict]:
        """Return per-agent net P&L, win rate, and trade count from closed DB trades.

        This is the authoritative source for leaderboard metrics — it uses the
        actual matched round-trip trades (net of fees) rather than the in-memory
        buffer which is pruned and mixes bootstrap backtest data with live trades.

        Returns: {agent_id: {"net_pnl": float, "win_rate": float, "total_trades": int}}
        """
        # Fetch all closed trades with no limit (we need full history for accuracy)
        all_closed = await self.get_closed_trades(limit=999_999)
        perf: dict[str, dict] = {}
        for trade in all_closed:
            aid = trade.get("agent_id")
            if not aid:
                continue
            if aid not in perf:
                perf[aid] = {"net_pnl": 0.0, "wins": 0, "total": 0}
            perf[aid]["net_pnl"] += trade.get("net_pnl", 0.0)
            perf[aid]["total"] += 1
            if trade.get("net_pnl", 0.0) > 0:
                perf[aid]["wins"] += 1
        return {
            aid: {
                "net_pnl": v["net_pnl"],
                "win_rate": v["wins"] / v["total"] if v["total"] > 0 else None,
                "total_trades": v["total"],
            }
            for aid, v in perf.items()
        }



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

        unrealized_pnl = 0.0
        for pos in positions:
            current_price = current_prices.get(pos.symbol, pos.entry_price or 0.0)
            entry = pos.entry_price or 0.0
            qty = pos.quantity or 0
            pos_side = pos.side.value if hasattr(pos.side, 'value') else str(pos.side)
            if pos_side.lower() == 'sell':
                unrealized_pnl += (entry - current_price) * qty
            else:
                unrealized_pnl += (current_price - entry) * qty

        # Total fees paid across all filled orders
        total_fees = sum(o.fee or 0 for o in orders)

        # Estimated exit fees for open positions (not yet paid)
        open_exit_fees = 0.0
        for pos in positions:
            cp = current_prices.get(pos.symbol, pos.entry_price or 0.0)
            open_exit_fees += (pos.quantity or 0) * cp * self.fee_rate_for(pos.symbol)

        return {
            "total_pnl": closed_pnl + unrealized_pnl - total_fees - open_exit_fees,
            "realized_pnl": closed_pnl,
            "unrealized_pnl": unrealized_pnl - open_exit_fees,
            "total_fees": total_fees + open_exit_fees,
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
