from typing import Optional, List, Dict
from datetime import datetime
import uuid
import logging
from contextvars import ContextVar

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


# Context var so route handlers can set the current user per-request without
# refactoring every service method.  Falls back to get_current_user_id().
_current_user_id: ContextVar[str] = ContextVar("_current_user_id", default=get_current_user_id())

def set_current_user_id(uid: str) -> None:
    _current_user_id.set(uid)

def get_current_user_id() -> str:
    return _current_user_id.get()

class PatternEntryDeferred(Exception):
    def __init__(self, symbol: str, order_type: str, entry_price: float,
                 current_price: float, pattern_type: Optional[str] = None):
        self.symbol = symbol
        self.order_type = order_type
        self.entry_price = entry_price
        self.current_price = current_price
        self.pattern_type = pattern_type
        super().__init__(
            f"Pattern entry deferred: {order_type} {symbol} @ {entry_price:.6g} "
            f"not yet triggered (current {current_price:.6g}, pattern={pattern_type or 'none'})"
        )


class PaperTradingService:
    # Fee rates — Phemex spot taker: 0.1%, perp taker (USDT- and coin-margined): 0.06%
    SPOT_FEE_RATE = 0.001    # 0.10% — spot only (not used in this system)
    CONTRACT_FEE_RATE = 0.0006  # 0.06% — all Phemex perpetual contracts (USDT- and coin-margined)
    HYPERLIQUID_FEE_RATE = 0.00035  # 0.035% HL taker — used for venue-accurate paper simulation
    ALPACA_FEE_RATE = 0.0  # $0 stock/ETF trades on Alpaca

    # Per-agent venue cache, populated by agent_scheduler on startup and toggle.
    _agent_venues: Dict[str, str] = {}

    @classmethod
    def set_agent_venue(cls, agent_id: str, venue: str) -> None:
        cls._agent_venues[agent_id] = venue or "phemex"

    @classmethod
    def _venue_for(cls, agent_id: Optional[str]) -> str:
        if agent_id and agent_id in cls._agent_venues:
            return cls._agent_venues[agent_id]
        return "hyperliquid"

    @classmethod
    def fee_rate_for(cls, symbol: str, venue: str = "hyperliquid") -> float:
        if venue == "hyperliquid":
            return cls.HYPERLIQUID_FEE_RATE
        if venue == "alpaca":
            return cls.ALPACA_FEE_RATE
        return cls.CONTRACT_FEE_RATE

    # ── Slippage model ─────────────────────────────────────────────────
    # Bps applied against the trader on every market fill so paper P&L
    # matches live execution. Without this, paper backtests routinely
    # over-state win rate by 5-8pts vs. live trading because real fills
    # cross the spread and walk the book.
    #   BUY  → fill higher (price * (1 + slip))
    #   SELL → fill lower  (price * (1 - slip))
    SLIPPAGE_BPS_NORMAL = 1.5      # 0.015% — typical mid-liquidity market entry
    SLIPPAGE_BPS_ADVERSE = 3.0     # 0.030% — wide-spread / band-extreme entries

    @classmethod
    def _apply_slippage(
        cls,
        price: float,
        side: str,
        adverse: bool = False,
    ) -> float:
        """Return fill price after applying execution slippage against the trader.

        side: "buy" or "sell" (case-insensitive).
        adverse: True when filling at a known-bad-liquidity zone (mean-reversion
                 entries at BB extremes, post-stop-out re-entries, etc.).
        """
        if price is None or price <= 0:
            return price
        bps = cls.SLIPPAGE_BPS_ADVERSE if adverse else cls.SLIPPAGE_BPS_NORMAL
        slip = bps / 10000.0
        s = (side.value if hasattr(side, "value") else str(side)).lower()
        if s == "buy":
            return price * (1.0 + slip)
        if s == "sell":
            return price * (1.0 - slip)
        return price

    @staticmethod
    def quote_currency(symbol: str) -> str:
        """Return the quote currency for a symbol.
        Crypto pairs end in USDT; stocks (alpaca) don't."""
        return "USDT" if symbol.endswith("USDT") else "USD"

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

    async def get_all_balances(self, tenant_id: str = None) -> List[PaperBalance]:
        tenant_id = tenant_id or get_current_user_id()
        """Return all paper-trading balances for the given tenant (trading fund only)."""
        async with get_async_session() as db:
            result = await db.execute(
                select(PaperBalance).where(
                    PaperBalance.fund_type != "accumulation",
                )
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
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.asset == asset,
                )
            )
            bal = result.scalars().first()
            if bal is None:
                if amount < 0:
                    raise ValueError(f"No {asset} balance exists to withdraw from")
                bal = PaperBalance(
                    user_id=get_current_user_id(),
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
        leverage: float = 1.0,
        margin_used: Optional[float] = None,
        liquidation_price: Optional[float] = None,
        order_type: str = "Market",
        entry_price: Optional[float] = None,
        pattern_type: Optional[str] = None,
        venue: Optional[str] = None,
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

        if order_type != "Market" and entry_price is not None and entry_price > 0:
            try:
                from app.services.pattern_entry import OrderPlan, can_fill_now
                from app.api.routes.settings import get_risk_limits as _gates_for_pattern
                _tol = float(getattr(_gates_for_pattern(), "pattern_entry_tolerance_pct", 0.30) or 0.30)
            except Exception:
                _tol = 0.30
                from app.services.pattern_entry import OrderPlan, can_fill_now
            _plan = OrderPlan(order_type=order_type, entry_price=float(entry_price), pattern_type=pattern_type)
            _side_str = side.value if hasattr(side, "value") else str(side)
            if not can_fill_now(_plan, _side_str, float(price), tolerance_pct=_tol):
                self.logger.info(
                    f"Paper {order_type} order deferred for {symbol}: "
                    f"current={price:.6g} not at entry={entry_price:.6g} "
                    f"(pattern={pattern_type or 'none'}, tol={_tol:.2f}%)"
                )
                # Persist pending order before raising so it's visible in the UI
                # But only if there isn't already an identical pending order — otherwise
                # every scheduler cycle creates a duplicate at the same price.
                try:
                    async with get_async_session() as _pending_db:
                        _dup_check = await _pending_db.execute(
                            select(PaperOrder).where(
                                PaperOrder.user_id == get_current_user_id(),
                                PaperOrder.agent_id == agent_id,
                                PaperOrder.symbol == symbol,
                                PaperOrder.side == side,
                                PaperOrder.price == float(entry_price),
                                PaperOrder.status == OrderStatus.PENDING,
                            )
                        )
                        if _dup_check.scalar_one_or_none() is not None:
                            self.logger.info(
                                f"Deferred order already exists for {symbol} at {entry_price:.6g} "
                                f"— skipping duplicate"
                            )
                        else:
                            _pending = PaperOrder(
                                id=str(uuid.uuid4()),
                                user_id=get_current_user_id(),
                                agent_id=agent_id,
                                trader_id=trader_id,
                                symbol=symbol,
                                side=side,
                                quantity=quantity,
                                price=float(entry_price),
                                total=0,
                                fee=0,
                                leverage=max(float(leverage or 1.0), 1.0),
                                margin_used=0,
                                status=OrderStatus.PENDING,
                                is_paper=True,
                                created_at=datetime.now(),
                                phemex_order_id=order_type,
                            )
                            _pending_db.add(_pending)
                            await _pending_db.commit()
                except Exception as _pe:
                    self.logger.warning(f"Failed to persist pending order: {_pe}")
                raise PatternEntryDeferred(
                    symbol=symbol,
                    order_type=order_type,
                    entry_price=float(entry_price),
                    current_price=float(price),
                    pattern_type=pattern_type,
                )
            price = float(entry_price)

        # ── Apply execution slippage on market fills ───────────────────
        # Limit / Stop orders (handled above) fill at the requested
        # entry_price by definition, so only Market orders take slippage.
        if order_type == "Market":
            price = self._apply_slippage(float(price), side, adverse=False)

        async with get_async_session() as db:
            _quote = self.quote_currency(symbol)
            usdt_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.asset == _quote,
                    PaperBalance.fund_type != "accumulation",
                )
            )
            if not usdt_balance:
                usdt_balance = PaperBalance(
                    user_id=get_current_user_id(), asset=_quote, available=50000.0, locked=0.0
                )
                db.add(usdt_balance)
                await db.flush()

            # Ensure base-asset balance exists (for sells / bookkeeping)
            base_asset = symbol.replace("USDT", "") if "USDT" in symbol else symbol
            base_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.asset == base_asset,
                )
            )
            if not base_balance:
                base_balance = PaperBalance(
                    user_id=get_current_user_id(), asset=base_asset, available=0.0, locked=0.0
                )
                db.add(base_balance)
                await db.flush()

            _venue = venue or self._venue_for(agent_id)
            notional = quantity * price
            fee = notional * self.fee_rate_for(symbol, _venue)
            leverage = max(float(leverage or 1.0), 1.0)
            effective_margin = margin_used if margin_used is not None else (notional / leverage)

            order = PaperOrder(
                id=str(uuid.uuid4()),
                user_id=get_current_user_id(),
                agent_id=agent_id,
                trader_id=trader_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                total=notional,
                fee=fee,
                leverage=leverage,
                margin_used=effective_margin,
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
                        PaperPosition.user_id == get_current_user_id(),
                        PaperPosition.symbol == symbol,
                        PaperPosition.agent_id == agent_id,
                        PaperPosition.side == OrderSide.SELL,
                    )
                )
                if short_position:
                    # Closing (covering) a short position
                    close_qty = min(quantity, short_position.quantity)
                    if (short_position.leverage or 1.0) > 1.0 or (short_position.margin_used or 0.0) > 0:
                        close_ratio = close_qty / max(short_position.quantity, 1e-12)
                        margin_release = (short_position.margin_used or 0.0) * close_ratio
                        # Fee already deducted at the top of place_order — do not subtract again here
                        realized = (short_position.entry_price - price) * close_qty
                        usdt_balance.available += margin_release + realized
                        short_position.margin_used = max((short_position.margin_used or 0.0) - margin_release, 0.0)
                    else:
                        realized = (short_position.entry_price - price) * close_qty
                        net_cost = max(-realized, 0)
                        if usdt_balance.available < net_cost:
                            raise ValueError(
                                f"Insufficient USDT balance to cover short loss: need {net_cost:.2f}, have {usdt_balance.available:.2f}"
                            )
                        usdt_balance.available += realized

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
                        extra_cost = (remainder * price) / leverage if leverage > 1.0 else remainder * price
                        if usdt_balance.available < extra_cost:
                            # Just close the short, skip remainder
                            await db.commit()
                            return order
                        usdt_balance.available -= extra_cost
                        new_pos = PaperPosition(
                            user_id=get_current_user_id(),
                            agent_id=agent_id,
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=remainder,
                            entry_price=price,
                            current_price=price,
                            unrealized_pnl=0.0,
                            realized_pnl=0.0,
                            leverage=leverage,
                            margin_used=extra_cost,
                            liquidation_price=liquidation_price,
                            stop_loss_price=stop_loss_price,
                            take_profit_price=take_profit_price,
                            highest_price=price,
                            trailing_stop_pct=trailing_stop_pct,
                            is_paper=True,
                        )
                        db.add(new_pos)
                else:
                    # Opening / adding to a LONG position
                    cost = effective_margin if leverage > 1.0 else quantity * price
                    if usdt_balance.available < cost:
                        raise ValueError(
                            f"Insufficient USDT balance: need {cost:.2f}, have {usdt_balance.available:.2f}"
                        )
                    usdt_balance.available -= cost

                    position = await db.scalar(
                        select(PaperPosition).where(
                            PaperPosition.user_id == get_current_user_id(),
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
                        position.leverage = max(float(position.leverage or 1.0), leverage)
                        position.margin_used = float(position.margin_used or 0.0) + cost
                        position.liquidation_price = liquidation_price or position.liquidation_price
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
                            user_id=get_current_user_id(),
                            agent_id=agent_id,
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=quantity,
                            entry_price=price,
                            current_price=price,
                            unrealized_pnl=0.0,
                            realized_pnl=0.0,
                            leverage=leverage,
                            margin_used=cost,
                            liquidation_price=liquidation_price,
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
                        PaperPosition.user_id == get_current_user_id(),
                        PaperPosition.symbol == symbol,
                        PaperPosition.agent_id == agent_id,
                        PaperPosition.side == OrderSide.BUY,
                    )
                )
                if long_position and long_position.quantity >= quantity - 1e-12:
                    # Closing a long position
                    if (long_position.leverage or 1.0) > 1.0 or (long_position.margin_used or 0.0) > 0:
                        close_ratio = quantity / max(long_position.quantity, 1e-12)
                        margin_release = (long_position.margin_used or 0.0) * close_ratio
                        # Fee already deducted at the top of place_order — do not subtract again here
                        realized = (price - long_position.entry_price) * quantity
                        usdt_balance.available += margin_release + realized
                        long_position.margin_used = max((long_position.margin_used or 0.0) - margin_release, 0.0)
                    else:
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
                    margin = effective_margin if leverage > 1.0 else quantity * price
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
                                PaperPosition.user_id == get_current_user_id(),
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
                            short_pos.leverage = max(float(short_pos.leverage or 1.0), leverage)
                            short_pos.margin_used = float(short_pos.margin_used or 0.0) + margin
                            short_pos.liquidation_price = liquidation_price or short_pos.liquidation_price
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
                                user_id=get_current_user_id(),
                                agent_id=agent_id,
                                symbol=symbol,
                                side=OrderSide.SELL,
                                quantity=remaining_short_qty,
                                entry_price=price,
                                current_price=price,
                                unrealized_pnl=0.0,
                                realized_pnl=0.0,
                                leverage=leverage,
                                margin_used=margin,
                                liquidation_price=liquidation_price,
                                stop_loss_price=stop_loss_price,
                                take_profit_price=take_profit_price,
                                highest_price=price,  # lowest price watermark for shorts
                                trailing_stop_pct=trailing_stop_pct,
                                is_paper=True,
                            )
                            db.add(short_pos)

            await db.commit()
            return order

    async def get_orders(self, symbol: Optional[str] = None, limit: int = 50, user_id: str = get_current_user_id()) -> List[PaperOrder]:
        async with get_async_session() as db:
            query = (
                select(PaperOrder)
                .where(PaperOrder.user_id == user_id)
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
            query = select(PaperPosition).where(PaperPosition.user_id == get_current_user_id())
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
            # Deduct only the entry fee already paid — exit fee is charged when the
            # position actually closes. Pre-charging it here caused trades to show
            # negative P&L the instant they opened (double-counted the entry fee).
            _fr = self.fee_rate_for(pos.symbol, self._venue_for(pos.agent_id))
            _est_fees = entry * qty * _fr
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
                "leverage": getattr(pos, 'leverage', 1.0) or 1.0,
                "margin_used": getattr(pos, 'margin_used', None),
                "liquidation_price": getattr(pos, 'liquidation_price', None),
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
            if not pos or pos.user_id != get_current_user_id():
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
            if not pos or pos.user_id != get_current_user_id():
                return None

            symbol = pos.symbol
            quantity = pos.quantity
            side = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            entry = pos.entry_price or 0
            _sl = getattr(pos, 'stop_loss_price', None)
            _tp = getattr(pos, 'take_profit_price', None)
            _trail = getattr(pos, 'trailing_stop_pct', None)

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
            stop_loss_price=_sl,
            take_profit_price=_tp,
            trailing_stop_pct=_trail,
        )

        # Cancel any pending orders for this agent/symbol — the thesis is done
        try:
            await self.cancel_pending_for_agent_symbol(pos.agent_id, symbol)
        except Exception:
            pass

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
            "leverage": getattr(pos, 'leverage', 1.0) or 1.0,
            "order": order,
        }

    async def cancel_pending_for_agent_symbol(self, agent_id: str, symbol: str) -> int:
        """Cancel all pending orders for an agent/symbol pair. Returns count cancelled."""
        cancelled = 0
        async with get_async_session() as db:
            orders = await db.execute(
                select(PaperOrder).where(
                    PaperOrder.user_id == get_current_user_id(),
                    PaperOrder.agent_id == agent_id,
                    PaperOrder.symbol == symbol,
                    PaperOrder.status == OrderStatus.PENDING,
                )
            )
            for o in orders.scalars().all():
                o.status = OrderStatus.CANCELLED
                cancelled += 1
            if cancelled:
                await db.commit()
                self.logger.info(f"Cancelled {cancelled} pending orders for {symbol} (agent {agent_id})")
        return cancelled

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

        async with get_async_session() as db:
            pos = await db.get(PaperPosition, position_id)
            if pos is None:
                return None

            _venue_for_close = self._venue_for(pos.agent_id)

            qty_to_close = pos.quantity * close_pct
            if qty_to_close < 1e-12:
                return None

            entry = pos.entry_price or price
            side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            is_long = side_str.upper() == "BUY"

            # Apply exit slippage: closing a long sells into the bid (lower fill);
            # closing a short buys the ask (higher fill).
            close_side_str = "sell" if is_long else "buy"
            price = self._apply_slippage(float(price), close_side_str, adverse=False)

            # Raw P&L on the slice
            if is_long:
                raw_pnl = qty_to_close * (price - entry)
            else:
                raw_pnl = qty_to_close * (entry - price)

            # Fee on the closing notional
            fr = self.fee_rate_for(pos.symbol, _venue_for_close)
            close_fee = qty_to_close * price * fr
            # Proportional entry fee for this slice (paid at open, allocated per unit)
            entry_fee = qty_to_close * entry * fr
            net_pnl = raw_pnl - close_fee - entry_fee

            # Reduce position quantity and accumulate realized P&L
            original_quantity = pos.quantity
            pos.quantity -= qty_to_close
            pos.realized_pnl = (pos.realized_pnl or 0.0) + net_pnl
            margin_release = 0.0
            if (pos.leverage or 1.0) > 1.0 or (pos.margin_used or 0.0) > 0:
                close_ratio = qty_to_close / max(original_quantity, 1e-12)
                margin_release = (pos.margin_used or 0.0) * close_ratio
                pos.margin_used = max((pos.margin_used or 0.0) - margin_release, 0.0)

            # Credit quote balance with the proceeds from closing this slice
            _close_quote = self.quote_currency(pos.symbol)
            usdt_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == pos.user_id,
                    PaperBalance.asset == _close_quote,
                    PaperBalance.fund_type != "accumulation",
                )
            )
            if usdt_balance is not None:
                if (pos.leverage or 1.0) > 1.0 or margin_release > 0:
                    usdt_balance.available += margin_release + raw_pnl - close_fee
                else:
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
                leverage=pos.leverage or 1.0,
                margin_used=margin_release,
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

    # ── Pending Orders (limit/stop orders waiting to fill) ───────────────

    async def get_pending_orders(self, agent_id: Optional[str] = None) -> List[PaperOrder]:
        """Return all pending (unfilled) limit/stop orders."""
        async with get_async_session() as db:
            q = select(PaperOrder).where(
                PaperOrder.status == OrderStatus.PENDING,
                PaperOrder.is_paper == True,
            ).order_by(PaperOrder.created_at.desc())
            if agent_id:
                q = q.where(PaperOrder.agent_id == agent_id)
            result = await db.execute(q)
            return list(result.scalars().all())

    async def cancel_pending_order(self, order_id: str) -> bool:
        """Cancel a pending order by ID. Returns True if cancelled."""
        async with get_async_session() as db:
            order = await db.get(PaperOrder, order_id)
            if order is None or order.status != OrderStatus.PENDING:
                return False
            order.status = OrderStatus.CANCELLED
            await db.commit()
            return True

    async def cleanup_stale_pending_orders(self, max_age_minutes: int = 120) -> int:
        """Cancel pending orders older than *max_age_minutes*. Returns count removed."""
        import datetime as _dt
        cutoff = _dt.datetime.now() - _dt.timedelta(minutes=max_age_minutes)
        async with get_async_session() as db:
            stale = await db.execute(
                select(PaperOrder).where(
                    PaperOrder.status == OrderStatus.PENDING,
                    PaperOrder.is_paper == True,
                    PaperOrder.created_at < cutoff,
                )
            )
            count = 0
            for o in stale.scalars().all():
                o.status = OrderStatus.CANCELLED
                count += 1
            await db.commit()
            return count

    async def fill_pending_orders(self) -> int:
        """Check all pending orders against current market price and fill those
        whose entry price has been reached. Returns number of orders filled."""
        filled = 0
        orders = await self.get_pending_orders()
        for o in orders:
            try:
                ticker = await self.phemex_client.get_ticker(o.symbol)
                market_price = float(ticker.get("result", {}).get("closeRp", 0))
            except Exception:
                continue
            if market_price <= 0:
                continue
            # Determine whether the order should fill at this price.
            # Respect the original order type (Limit vs Stop) stored in phemex_order_id.
            _side = o.side.value if hasattr(o.side, "value") else str(o.side)
            _entry = o.price
            _order_type = getattr(o, 'phemex_order_id', 'Limit') or 'Limit'
            _tol = _entry * 0.003  # 0.3% tolerance
            should_fill = False
            if _order_type == "Limit":
                # Limit: price must REACH the entry level
                if _side.lower() == "buy":
                    if market_price <= _entry + _tol:
                        should_fill = True
                else:
                    if market_price >= _entry - _tol:
                        should_fill = True
            else:
                # Stop: price must BREAK THROUGH the entry level
                if _side.lower() == "buy":
                    if market_price >= _entry - _tol:
                        should_fill = True
                else:
                    if market_price <= _entry + _tol:
                        should_fill = True
            if not should_fill:
                continue
            # Fill the order — execute the same logic as place_order would
            try:
                await self._execute_pending_fill(o, market_price)
                filled += 1
            except Exception as _fill_err:
                self.logger.warning(f"Failed to fill pending order {o.id}: {_fill_err}")
        return filled

    async def _execute_pending_fill(self, order: PaperOrder, fill_price: float) -> None:
        """Execute a pending order at *fill_price* — creates position, deducts balance, etc."""
        from app.models import Position as PaperPosition, Balance as PaperBalance
        _pend_quote = self.quote_currency(order.symbol)
        async with get_async_session() as db:
            usdt_balance = await db.scalar(
                select(PaperBalance).where(
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.asset == _pend_quote,
                    PaperBalance.fund_type != "accumulation",
                )
            )
            if not usdt_balance:
                usdt_balance = PaperBalance(
                    user_id=get_current_user_id(), asset=_pend_quote, available=50000.0, locked=0.0
                )
                db.add(usdt_balance)
                await db.flush()

            _venue = self._venue_for(order.agent_id)
            notional = order.quantity * fill_price
            fee = notional * self.fee_rate_for(order.symbol, _venue)

            # Update the order record
            order.price = fill_price
            order.total = notional
            order.fee = fee
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now()

            # Deduct notional + fee from quote balance
            # Guard: if balance can't cover notional + fee, cancel instead of going negative
            if usdt_balance.available < notional + fee:
                self.logger.warning(
                    f"Insufficient balance to fill pending {order.symbol} {order.side}: "
                    f"need ${notional + fee:.2f}, have ${usdt_balance.available:.2f}. Cancelling."
                )
                order.status = OrderStatus.CANCELLED
                await db.commit()
                return
            usdt_balance.available -= notional
            usdt_balance.available -= fee

            side_str = order.side.value if hasattr(order.side, "value") else str(order.side)

            if side_str.upper() == "BUY":
                short_pos = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == get_current_user_id(),
                        PaperPosition.symbol == order.symbol,
                        PaperPosition.agent_id == order.agent_id,
                        PaperPosition.side == OrderSide.SELL,
                    )
                )
                if short_pos:
                    close_qty = min(order.quantity, short_pos.quantity)
                    realized = (short_pos.entry_price - fill_price) * close_qty
                    usdt_balance.available += realized
                    if short_pos.quantity <= close_qty + 1e-12:
                        await db.delete(short_pos)
                    else:
                        short_pos.quantity -= close_qty
                    remaining = order.quantity - close_qty
                    if remaining > 1e-12:
                        existing = await db.scalar(
                            select(PaperPosition).where(
                                PaperPosition.user_id == get_current_user_id(),
                                PaperPosition.symbol == order.symbol,
                                PaperPosition.agent_id == order.agent_id,
                                PaperPosition.side == OrderSide.BUY,
                            )
                        )
                        if existing:
                            existing.quantity += remaining
                        else:
                            db.add(PaperPosition(
                                id=str(uuid.uuid4()),
                                user_id=get_current_user_id(), agent_id=order.agent_id,
                                symbol=order.symbol, side=OrderSide.BUY,
                                quantity=remaining, entry_price=fill_price,
                                leverage=order.leverage, is_paper=True,
                            ))
                else:
                    existing = await db.scalar(
                        select(PaperPosition).where(
                            PaperPosition.user_id == get_current_user_id(),
                            PaperPosition.symbol == order.symbol,
                            PaperPosition.agent_id == order.agent_id,
                            PaperPosition.side == OrderSide.BUY,
                        )
                    )
                    if existing:
                        existing.quantity += order.quantity
                    else:
                        db.add(PaperPosition(
                            id=str(uuid.uuid4()),
                            user_id=get_current_user_id(), agent_id=order.agent_id,
                            symbol=order.symbol, side=OrderSide.BUY,
                            quantity=order.quantity, entry_price=fill_price,
                            leverage=order.leverage, is_paper=True,
                        ))
            else:
                long_pos = await db.scalar(
                    select(PaperPosition).where(
                        PaperPosition.user_id == get_current_user_id(),
                        PaperPosition.symbol == order.symbol,
                        PaperPosition.agent_id == order.agent_id,
                        PaperPosition.side == OrderSide.BUY,
                    )
                )
                if long_pos:
                    close_qty = min(order.quantity, long_pos.quantity)
                    realized = (fill_price - long_pos.entry_price) * close_qty
                    usdt_balance.available += realized + (long_pos.margin_used or 0.0) * (close_qty / max(long_pos.quantity, 1e-12))
                    if long_pos.quantity <= close_qty + 1e-12:
                        await db.delete(long_pos)
                    else:
                        long_pos.quantity -= close_qty
                    remaining = order.quantity - close_qty
                    if remaining > 1e-12:
                        existing = await db.scalar(
                            select(PaperPosition).where(
                                PaperPosition.user_id == get_current_user_id(),
                                PaperPosition.symbol == order.symbol,
                                PaperPosition.agent_id == order.agent_id,
                                PaperPosition.side == OrderSide.SELL,
                            )
                        )
                        if existing:
                            existing.quantity += remaining
                        else:
                            db.add(PaperPosition(
                                id=str(uuid.uuid4()),
                                user_id=get_current_user_id(), agent_id=order.agent_id,
                                symbol=order.symbol, side=OrderSide.SELL,
                                quantity=remaining, entry_price=fill_price,
                                leverage=order.leverage, is_paper=True,
                            ))
                else:
                    existing = await db.scalar(
                        select(PaperPosition).where(
                            PaperPosition.user_id == get_current_user_id(),
                            PaperPosition.symbol == order.symbol,
                            PaperPosition.agent_id == order.agent_id,
                            PaperPosition.side == OrderSide.SELL,
                        )
                    )
                    if existing:
                        existing.quantity += order.quantity
                    else:
                        db.add(PaperPosition(
                            id=str(uuid.uuid4()),
                            user_id=get_current_user_id(), agent_id=order.agent_id,
                            symbol=order.symbol, side=OrderSide.SELL,
                            quantity=order.quantity, entry_price=fill_price,
                            leverage=order.leverage, is_paper=True,
                        ))

            await db.commit()
            self.logger.info(f"Pending order {order.id} filled: {order.symbol} @ {fill_price:.6f}")


    async def get_closed_trades(self, symbol: Optional[str] = None, limit: int = 100, include_archived: bool = False, agent_id: Optional[str] = None) -> List[dict]:
        """Return completed round-trip trades with realised P&L.

        Uses FIFO matching: for longs, each sell is matched against earliest
        unfilled buy. For shorts, each buy is matched against earliest
        unfilled sell.

        When *include_archived* is True, also includes trades from the
        archived_trades table (preserved across paper trading resets).
        """
        async with get_async_session() as db:
            query = (
                select(PaperOrder)
                .where(
                    PaperOrder.user_id == get_current_user_id(),
                    PaperOrder.status == OrderStatus.FILLED,
                )
                .order_by(PaperOrder.created_at.asc())
            )
            if symbol:
                query = query.where(PaperOrder.symbol == symbol)
            if agent_id:
                query = query.where(PaperOrder.agent_id == agent_id)
            result = await db.execute(query)
            orders = list(result.scalars().all())

            # ── Include archived trades if requested ────────────────────────
            if include_archived:
                try:
                    from app.models import ArchivedTrade
                    from sqlalchemy import select as _arch_sel
                    _arch_query = _arch_sel(ArchivedTrade).order_by(ArchivedTrade.created_at.asc().nullsfirst())
                    if symbol:
                        _arch_query = _arch_query.where(ArchivedTrade.symbol == symbol)
                    if agent_id:
                        _arch_query = _arch_query.where(ArchivedTrade.agent_id == agent_id)
                    _arch_result = await db.execute(_arch_query)
                    _arch_rows = _arch_result.scalars().all()
                    # Wrap ArchivedTrade rows as order-like objects for FIFO matching
                    _FakeOrder = type("FakeOrder", (), {
                        "id": property(lambda self: self._id),
                        "symbol": property(lambda self: self._sym),
                        "agent_id": property(lambda self: self._aid),
                        "side": property(lambda self: self._side),
                        "quantity": property(lambda self: self._qty),
                        "price": property(lambda self: self._prc),
                        "fee": property(lambda self: self._fee),
                        "created_at": property(lambda self: self._cat),
                        "status": property(lambda self: self._st),
                    })
                    for _ar in _arch_rows:
                        _o = _FakeOrder()
                        _o._id = _ar.id
                        _o._sym = _ar.symbol
                        _o._aid = _ar.agent_id
                        _o._side = OrderSide.BUY if str(_ar.side).lower() == "buy" else OrderSide.SELL
                        _o._qty = _ar.quantity
                        _o._prc = _ar.price
                        _o._fee = _ar.fee
                        _o._cat = _ar.created_at or _ar.archived_at
                        _o._st = OrderStatus.FILLED
                        orders.append(_o)
                except Exception as _arch_err:
                    logger.debug(f"Archived trade query failed (non-fatal): {_arch_err}")

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
            # Arrr, the old two-pass (long pass + independent short pass) was a leaky
            # ship — a BUY used to cover a short could still be consumed by the long
            # pass, creating ghost long trades with swapped entry/exit and identical P&L.
            # The fix: one chronological FIFO pass with explicit long/short inventory.
            # A BUY first covers any open shorts; remainder opens a long.
            # A SELL first closes any open longs; remainder opens a short.
            # Only completed round-trips land in `closed`. No order is double-counted.
            all_orders_sorted = sorted(
                [(o, "buy") for o in sides["buys"]] + [(o, "sell") for o in sides["sells"]],
                key=lambda x: x[0].created_at,
            )

            # Each entry: {"price": float, "qty": float, "order": o}
            long_inv: list[dict] = []
            short_inv: list[dict] = []

            def _make_record(direction: str, entry_o, exit_o, fill: float) -> dict:
                if direction == "long":
                    pnl = fill * (exit_o.price - entry_o.price)
                else:
                    pnl = fill * (entry_o.price - exit_o.price)
                fee = (
                    (entry_o.fee or 0) * (fill / entry_o.quantity)
                    + (exit_o.fee or 0) * (fill / exit_o.quantity)
                )
                net_pnl = pnl - fee
                entry_notional = fill * entry_o.price
                pnl_pct = (net_pnl / entry_notional * 100) if entry_notional else 0
                return {
                    "symbol": sym,
                    "agent_id": agent_id if agent_id != "__none__" else None,
                    "side": direction,
                    "quantity": round(fill, 8),
                    "entry_price": entry_o.price,
                    "exit_price": exit_o.price,
                    "entry_time": entry_o.created_at.isoformat() if entry_o.created_at else None,
                    "exit_time": exit_o.created_at.isoformat() if exit_o.created_at else None,
                    "gross_pnl": round(pnl, 6),
                    "fee": round(fee, 6),
                    "net_pnl": round(net_pnl, 6),
                    "pnl_pct": round(pnl_pct, 4),
                    "result": "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "breakeven"),
                }

            for o, side_str in all_orders_sorted:
                remaining = o.quantity

                if side_str == "buy":
                    # Cover open shorts first (FIFO)
                    while remaining > 1e-12 and short_inv:
                        slot = short_inv[0]
                        fill = min(remaining, slot["qty"])
                        closed.append(_make_record("short", slot["order"], o, fill))
                        remaining -= fill
                        slot["qty"] -= fill
                        if slot["qty"] <= 1e-12:
                            short_inv.pop(0)
                    # Remainder is a new long position
                    if remaining > 1e-12:
                        long_inv.append({"price": o.price, "qty": remaining, "order": o})

                else:  # sell
                    # Close open longs first (FIFO)
                    while remaining > 1e-12 and long_inv:
                        slot = long_inv[0]
                        fill = min(remaining, slot["qty"])
                        closed.append(_make_record("long", slot["order"], o, fill))
                        remaining -= fill
                        slot["qty"] -= fill
                        if slot["qty"] <= 1e-12:
                            long_inv.pop(0)
                    # Remainder opens a new short position
                    if remaining > 1e-12:
                        short_inv.append({"price": o.price, "qty": remaining, "order": o})

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
                    PaperOrder.user_id == get_current_user_id(),
                    PaperOrder.status == OrderStatus.FILLED,
                )
            )
            orders = orders_result.scalars().all()

            positions_result = await db.execute(
                select(PaperPosition).where(PaperPosition.user_id == get_current_user_id())
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

        # ── Match trades via FIFO to compute closed PnL ──────────────────────
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

        # Total fees paid across all filled orders (lifetime)
        total_fees = sum(o.fee or 0 for o in orders)

        # UTC-day paid fees for daily budget tracking
        utc_today = datetime.utcnow().date()
        daily_fees_paid = sum(
            (o.fee or 0)
            for o in orders
            if o.created_at and o.created_at.date() == utc_today
        )

        # Estimated exit fees for open positions (not yet paid)
        open_exit_fees = 0.0
        for pos in positions:
            cp = current_prices.get(pos.symbol, pos.entry_price or 0.0)
            open_exit_fees += (pos.quantity or 0) * cp * self.fee_rate_for(pos.symbol, self._venue_for(pos.agent_id))

        # ── Total P&L from closed trades (FIFO-matched) ──────────────────────
        # Use get_closed_trades which properly matches BUYs and SELLs.
        # The old formula (closed_pnl + unrealized - total_fees) double-counted
        # fees from orphan orders (SELLs with no matching BUY from the broken close).
        _closed_trades = await self.get_closed_trades(limit=9999)
        _closed_pnl = sum(t.get("net_pnl", 0) for t in _closed_trades)
        _closed_gross = sum(t.get("gross_pnl", 0) for t in _closed_trades)
        _closed_fees = sum(abs(t.get("fee", 0)) for t in _closed_trades)

        return {
            "total_pnl": _closed_pnl + unrealized_pnl - open_exit_fees,
            "realized_pnl": _closed_gross,
            "unrealized_pnl": unrealized_pnl - open_exit_fees,
            "total_fees": _closed_fees + open_exit_fees,
            "daily_fees_paid": daily_fees_paid,
            "daily_fees_with_estimated_exit": daily_fees_paid + open_exit_fees,
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
        """Archive all paper trades and positions, then wipe and re-seed balances."""
        async with get_async_session() as db:
            # ── Archive trades before deleting ──────────────────────────────
            try:
                from app.models import ArchivedTrade, ArchivedPosition
                from sqlalchemy import select
                import uuid as _arch_uuid

                trades_to_archive = (await db.execute(
                    select(PaperOrder).where(PaperOrder.user_id == get_current_user_id())
                )).scalars().all()
                for t in trades_to_archive:
                    db.add(ArchivedTrade(
                        id=str(_arch_uuid.uuid4()),
                        original_id=t.id,
                        user_id=t.user_id,
                        agent_id=t.agent_id,
                        trader_id=t.trader_id,
                        symbol=t.symbol,
                        side=t.side.value if hasattr(t.side, "value") else str(t.side),
                        quantity=t.quantity,
                        price=t.price,
                        total=t.total,
                        fee=t.fee,
                        leverage=t.leverage,
                        margin_used=t.margin_used,
                        status=t.status.value if hasattr(t.status, "value") else str(t.status),
                        phemex_order_id=t.phemex_order_id,
                        is_paper=t.is_paper,
                        created_at=t.created_at,
                        filled_at=t.filled_at,
                    ))

                positions_to_archive = (await db.execute(
                    select(PaperPosition).where(PaperPosition.user_id == get_current_user_id())
                )).scalars().all()
                for p in positions_to_archive:
                    db.add(ArchivedPosition(
                        id=str(_arch_uuid.uuid4()),
                        original_id=p.id,
                        user_id=p.user_id,
                        agent_id=p.agent_id,
                        symbol=p.symbol,
                        side=p.side.value if hasattr(p.side, "value") else str(p.side),
                        quantity=p.quantity,
                        entry_price=p.entry_price,
                        current_price=p.current_price,
                        unrealized_pnl=p.unrealized_pnl,
                        realized_pnl=p.realized_pnl,
                        leverage=p.leverage,
                        margin_used=p.margin_used,
                        liquidation_price=p.liquidation_price,
                        stop_loss_price=p.stop_loss_price,
                        take_profit_price=p.take_profit_price,
                        highest_price=p.highest_price,
                        trailing_stop_pct=p.trailing_stop_pct,
                        is_paper=p.is_paper,
                        phemex_order_id=p.phemex_order_id,
                        scale_out_levels=p.scale_out_levels,
                        entry_indicators=p.entry_indicators,
                    ))
            except Exception as _arch_err:
                logger.warning(f"Trade archival failed (proceeding with reset): {_arch_err}")

            await db.execute(
                delete(PaperOrder).where(PaperOrder.user_id == get_current_user_id())
            )
            await db.execute(
                delete(PaperPosition).where(PaperPosition.user_id == get_current_user_id())
            )
            # Preserve accumulation fund balances — don't wipe on trading reset.
            _acc_balances = {}
            for _ab in (await db.execute(
                select(PaperBalance).where(
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.fund_type == "accumulation",
                )
            )).scalars().all():
                _acc_balances[_ab.asset] = {"available": _ab.available, "locked": _ab.locked}
            await db.execute(
                delete(PaperBalance).where(
                    PaperBalance.user_id == get_current_user_id(),
                    PaperBalance.fund_type != "accumulation",
                )
            )
            # Re-seed trading balances
            for asset, amount in [("BTC", 1.0), ("USDT", 50000.0), ("ETH", 10.0), ("SOL", 100.0), ("USD", 50000.0)]:
                db.add(
                    PaperBalance(
                        user_id=get_current_user_id(), asset=asset, available=amount, locked=0.0
                    )
                )
            # Restore accumulation balances
            for _asset, _bal in _acc_balances.items():
                db.add(
                    PaperBalance(
                        user_id=get_current_user_id(), asset=_asset,
                        available=_bal["available"], locked=_bal["locked"],
                        fund_type="accumulation",
                    )
                )
            await db.commit()
            logger.info(f"Paper trading reset: archived {len(trades_to_archive)} trades and {len(positions_to_archive)} positions")


paper_trading = PaperTradingService()
