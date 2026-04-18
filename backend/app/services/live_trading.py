"""
Live Trading Service
====================
Mirrors PaperTradingService's public API but routes all calls to the real
Phemex exchange. Maintains local DB records (is_paper=False) for monitoring.

Used by TradingService (trading_service.py) when paper_trading_default=False.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.database import get_async_session
from app.models import Position, Trade, OrderSide, OrderStatus

logger = logging.getLogger(__name__)

_SENTINEL = object()


def _gen_id() -> str:
    return str(uuid.uuid4())


class LiveTradingService:
    """Live trading service that executes real Phemex orders."""

    # Default Phemex perpetual taker fee rate
    DEFAULT_FEE_RATE = 0.0006  # 0.06%

    def __init__(self) -> None:
        self._phemex: Optional[object] = None

    def _get_phemex(self):
        if self._phemex is None:
            from app.clients.phemex import PhemexClient
            self._phemex = PhemexClient()
        return self._phemex

    # ── Helpers ────────────────────────────────────────────────────────────

    def _fee_rate(self, symbol: str) -> float:
        return self.DEFAULT_FEE_RATE

    async def _fetch_price(self, symbol: str) -> float:
        try:
            phemex = self._get_phemex()
            ticker = await phemex.get_ticker(symbol)
            if isinstance(ticker, dict):
                data = ticker.get("result", ticker)
                last = data.get("lastEp") or data.get("last") or data.get("lastPrice")
                if last:
                    # Phemex prices may be scaled by 1e8 (Ep format)
                    val = float(last)
                    return val / 1e8 if val > 1e7 else val
        except Exception as e:
            logger.warning(f"LiveTrading: failed to fetch price for {symbol}: {e}")
        return 0.0

    async def _get_position(self, db, position_id: str) -> Optional[Position]:
        return await db.scalar(
            select(Position).where(
                Position.id == position_id,
                Position.is_paper == False,  # noqa: E712
            )
        )

    def _default_user_id(self) -> str:
        return "00000000-0000-0000-0000-000000000001"

    # ── Core trading methods ───────────────────────────────────────────────

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
    ) -> Optional[dict]:
        """
        Place a real Phemex contract order with SL/TP.
        Creates a local Position record with is_paper=False.
        Returns an order result dict or None on failure.
        """
        phemex = self._get_phemex()
        side_str = side.value if hasattr(side, "value") else str(side)

        if price is None:
            price = await self._fetch_price(symbol)
        if price <= 0:
            logger.error(f"LiveTrading: cannot place order — bad price for {symbol}")
            return None

        # ── Safety: verify account balance ────────────────────────────────
        try:
            balance_resp = await phemex.get_account_balance()
            bal_data = (balance_resp or {}).get("result", balance_resp) or {}
            usdt_bal = float(bal_data.get("accountBalanceEv", 0) or 0) / 1e8
            required = quantity * price * 1.05  # 5% buffer for fees/slippage
            if usdt_bal > 0 and usdt_bal < required:
                logger.error(
                    f"LiveTrading: insufficient balance for {symbol} — "
                    f"need ~${required:.2f}, have ${usdt_bal:.2f}"
                )
                return None
        except Exception as be:
            logger.warning(f"LiveTrading: balance check failed (proceeding): {be}")

        # ── Place Phemex order ─────────────────────────────────────────────
        try:
            if leverage > 1.0:
                await phemex.set_leverage(symbol, max(1, int(round(leverage))))
            resp = await phemex.place_contract_order(
                symbol=symbol,
                side=side_str,
                quantity=quantity,
                order_type="Market",
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                reduce_only=False,
            )
            order_data = (resp or {}).get("result", resp) or {}
            phemex_order_id = str(order_data.get("orderID", order_data.get("clOrdID", "")))
        except Exception as e:
            logger.error(f"LiveTrading: order placement failed for {symbol}: {e}")
            return None

        fee_rate = self._fee_rate(symbol)
        fee = quantity * price * fee_rate

        # ── Persist Trade record ───────────────────────────────────────────
        async with get_async_session() as db:
            trade = Trade(
                id=_gen_id(),
                user_id=self._default_user_id(),
                agent_id=agent_id,
                trader_id=trader_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                total=quantity * price,
                fee=fee,
                leverage=leverage,
                margin_used=margin_used or ((quantity * price) / max(leverage, 1.0)),
                status=OrderStatus.FILLED,
                phemex_order_id=phemex_order_id,
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)

            # ── Create / update Position record ───────────────────────────
            pos = await db.scalar(
                select(Position).where(
                    Position.agent_id == agent_id,
                    Position.symbol == symbol,
                    Position.is_paper == False,  # noqa: E712
                )
            )
            if pos is None:
                pos = Position(
                    id=_gen_id(),
                    user_id=self._default_user_id(),
                    agent_id=agent_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=price,
                    current_price=price,
                    leverage=leverage,
                    margin_used=margin_used or ((quantity * price) / max(leverage, 1.0)),
                    liquidation_price=liquidation_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    trailing_stop_pct=trailing_stop_pct,
                    highest_price=price,
                    is_paper=False,
                    phemex_order_id=phemex_order_id,
                )
                db.add(pos)
            else:
                # Average into existing position
                total_qty = pos.quantity + quantity
                pos.entry_price = (pos.entry_price * pos.quantity + price * quantity) / total_qty
                pos.quantity = total_qty
                pos.current_price = price
                pos.leverage = max(float(pos.leverage or 1.0), leverage)
                pos.margin_used = float(pos.margin_used or 0.0) + (margin_used or ((quantity * price) / max(leverage, 1.0)))
                pos.liquidation_price = liquidation_price or pos.liquidation_price
                if stop_loss_price:
                    pos.stop_loss_price = stop_loss_price
                if take_profit_price:
                    pos.take_profit_price = take_profit_price
                pos.phemex_order_id = phemex_order_id

            await db.commit()
            await db.refresh(pos)
            position_id = pos.id

        logger.info(
            f"LiveTrading: placed {side_str} {quantity} {symbol} @ {price:.4f} "
            f"SL={stop_loss_price} TP={take_profit_price} order_id={phemex_order_id}"
        )
        return {
            "id": position_id,
            "symbol": symbol,
            "side": side_str,
            "quantity": quantity,
            "price": price,
            "leverage": leverage,
            "margin_used": margin_used or ((quantity * price) / max(leverage, 1.0)),
            "liquidation_price": liquidation_price,
            "phemex_order_id": phemex_order_id,
            "is_paper": False,
        }

    async def close_position(self, position_id: str) -> Optional[dict]:
        """Close a live position with a reduce-only market order."""
        phemex = self._get_phemex()
        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                logger.warning(f"LiveTrading: position {position_id} not found")
                return None

            current_price = await self._fetch_price(pos.symbol)
            if current_price <= 0:
                current_price = pos.current_price or pos.entry_price or 0.0

            is_long = (pos.side.value if hasattr(pos.side, "value") else str(pos.side)).lower() == "buy"
            exit_side = "Sell" if is_long else "Buy"

            # Place reduce-only order
            try:
                resp = await phemex.place_contract_order(
                    symbol=pos.symbol,
                    side=exit_side,
                    quantity=pos.quantity,
                    order_type="Market",
                    reduce_only=True,
                )
                order_data = (resp or {}).get("result", resp) or {}
                close_order_id = str(order_data.get("orderID", ""))
            except Exception as e:
                logger.error(f"LiveTrading: failed to close position {position_id}: {e}")
                return None

            fee_rate = self._fee_rate(pos.symbol)
            entry_fee = pos.entry_price * pos.quantity * fee_rate
            exit_fee = current_price * pos.quantity * fee_rate
            raw_pnl = (current_price - pos.entry_price) * pos.quantity if is_long else (pos.entry_price - current_price) * pos.quantity
            final_pnl = raw_pnl - entry_fee - exit_fee + (pos.realized_pnl or 0)

            # Record close trade
            trade = Trade(
                id=_gen_id(),
                user_id=pos.user_id,
                agent_id=pos.agent_id,
                symbol=pos.symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=pos.quantity,
                price=current_price,
                total=current_price * pos.quantity,
                fee=exit_fee,
                status=OrderStatus.FILLED,
                phemex_order_id=close_order_id,
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)

            result = {
                "closed": True,
                "position_id": position_id,
                "symbol": pos.symbol,
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "exit_price": current_price,
                "pnl": round(final_pnl, 4),
                "leverage": pos.leverage or 1.0,
                "phemex_order_id": close_order_id,
                "is_paper": False,
            }

            await db.delete(pos)
            await db.commit()

        logger.info(
            f"LiveTrading: closed position {position_id} {result['symbol']} "
            f"P&L=${final_pnl:+.2f} close_order={close_order_id}"
        )
        return result

    async def partial_close(
        self,
        position_id: str,
        close_pct: float,
        price: float,
        agent_id: Optional[str] = None,
        label: str = "scale-out",
    ) -> Optional[dict]:
        """Close a percentage of a live position (scale-out). Mirrors paper logic."""
        phemex = self._get_phemex()
        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                return None

            qty_to_close = round(pos.quantity * close_pct, 8)
            if qty_to_close <= 0:
                return None

            is_long = (pos.side.value if hasattr(pos.side, "value") else str(pos.side)).lower() == "buy"
            exit_side = "Sell" if is_long else "Buy"

            try:
                resp = await phemex.place_contract_order(
                    symbol=pos.symbol,
                    side=exit_side,
                    quantity=qty_to_close,
                    order_type="Market",
                    reduce_only=True,
                )
                order_data = (resp or {}).get("result", resp) or {}
                close_order_id = str(order_data.get("orderID", f"{label}-{position_id[:8]}"))
            except Exception as e:
                logger.error(f"LiveTrading: partial_close failed for {position_id}: {e}")
                return None

            fee_rate = self._fee_rate(pos.symbol)
            entry_fee_slice = qty_to_close * (pos.entry_price or price) * fee_rate
            close_fee = qty_to_close * price * fee_rate

            raw_pnl = (price - pos.entry_price) * qty_to_close if is_long else (pos.entry_price - price) * qty_to_close
            slice_pnl = raw_pnl - entry_fee_slice - close_fee

            remaining_qty = pos.quantity - qty_to_close
            pos.quantity = max(remaining_qty, 0)
            pos.realized_pnl = (pos.realized_pnl or 0) + slice_pnl

            # Audit trade record
            trade = Trade(
                id=_gen_id(),
                user_id=pos.user_id,
                agent_id=pos.agent_id or agent_id,
                symbol=pos.symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=qty_to_close,
                price=price,
                total=qty_to_close * price,
                fee=close_fee,
                status=OrderStatus.FILLED,
                phemex_order_id=close_order_id,
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)
            await db.commit()

        logger.info(
            f"LiveTrading: partial_close {label} position={position_id} "
            f"qty={qty_to_close:.6f} pnl=${slice_pnl:+.4f}"
        )
        return {
            "realized_pnl": round(slice_pnl, 4),
            "quantity_closed": qty_to_close,
            "remaining_quantity": max(remaining_qty, 0),
            "close_price": price,
            "leverage": pos.leverage or 1.0,
            "phemex_order_id": close_order_id,
            "is_paper": False,
        }

    async def update_position_sl_tp(
        self,
        position_id: str,
        stop_loss_price=_SENTINEL,
        take_profit_price=_SENTINEL,
        trailing_stop_pct=_SENTINEL,
    ) -> Optional[dict]:
        """
        Amend SL/TP on a live position: updates the Phemex server-side conditional
        orders AND the local DB record.
        """
        phemex = self._get_phemex()
        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                return None

            new_sl = pos.stop_loss_price if stop_loss_price is _SENTINEL else stop_loss_price
            new_tp = pos.take_profit_price if take_profit_price is _SENTINEL else take_profit_price
            new_trail = pos.trailing_stop_pct if trailing_stop_pct is _SENTINEL else trailing_stop_pct

            # Amend the live order on Phemex
            if pos.phemex_order_id:
                try:
                    await phemex.amend_order(
                        symbol=pos.symbol,
                        order_id=pos.phemex_order_id,
                        stop_loss_price=new_sl,
                        take_profit_price=new_tp,
                    )
                except Exception as e:
                    logger.warning(
                        f"LiveTrading: amend_order failed for {pos.symbol} "
                        f"order={pos.phemex_order_id}: {e} — updating DB only"
                    )

            # Update local DB regardless (monitoring loop reads from here)
            if stop_loss_price is not _SENTINEL:
                pos.stop_loss_price = stop_loss_price
            if take_profit_price is not _SENTINEL:
                pos.take_profit_price = take_profit_price
            if trailing_stop_pct is not _SENTINEL:
                pos.trailing_stop_pct = trailing_stop_pct

            await db.commit()

        return {
            "id": position_id,
            "symbol": pos.symbol,
            "stop_loss_price": new_sl,
            "take_profit_price": new_tp,
            "trailing_stop_pct": new_trail,
        }

    async def update_highest_price(
        self, position_id: str, current_price: float, is_short: bool = False
    ):
        """Update the trailing stop watermark for a live position."""
        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                return
            current_hw = pos.highest_price or current_price
            if is_short:
                if current_hw == 0 or current_price < current_hw:
                    pos.highest_price = current_price
            else:
                if current_price > current_hw:
                    pos.highest_price = current_price
            await db.commit()

    async def get_positions(
        self,
        symbol: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """Return open live positions from local DB (populated by position sync)."""
        async with get_async_session() as db:
            q = select(Position).where(Position.is_paper == False)  # noqa: E712
            if symbol:
                q = q.where(Position.symbol == symbol)
            if agent_id:
                q = q.where(Position.agent_id == agent_id)
            return list((await db.scalars(q)).all())

    async def get_position(self, position_id: str):
        """Fetch a single live position by ID from DB (always fresh)."""
        async with get_async_session() as db:
            return await db.get(Position, position_id)

    async def get_closed_trades(
        self,
        symbol: Optional[str] = None,
        limit: int = 100,
    ):
        """Return closed live trade records from DB."""
        async with get_async_session() as db:
            q = (
                select(Trade)
                .where(Trade.is_paper == False, Trade.status == OrderStatus.FILLED)  # noqa: E712
                .order_by(Trade.filled_at.desc())
                .limit(limit)
            )
            if symbol:
                q = q.where(Trade.symbol == symbol)
            trades = list((await db.scalars(q)).all())
        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                "quantity": t.quantity,
                "price": t.price,
                "total": t.total,
                "fee": t.fee,
                "leverage": t.leverage or 1.0,
                "margin_used": t.margin_used or 0.0,
                "phemex_order_id": t.phemex_order_id,
                "filled_at": t.filled_at.isoformat() if t.filled_at else None,
            }
            for t in trades
        ]

    async def fetch_current_price(self, symbol: str) -> float:
        return await self._fetch_price(symbol)

    async def get_balance(self) -> dict:
        """Return USDT balance from Phemex."""
        try:
            phemex = self._get_phemex()
            account = await phemex.get_account_info()
            if isinstance(account, dict):
                data = account.get("data", account)
                assets = data.get("assets", [])
                for asset in assets:
                    if asset.get("currency", "").upper() == "USDT":
                        av = float(asset.get("availableBalanceEv", 0)) / 1e8
                        total = float(asset.get("accountBalanceEv", 0)) / 1e8
                        return {"available": av, "total": total, "currency": "USDT"}
            return {"available": 0.0, "total": 0.0, "currency": "USDT", "error": "parse_failed"}
        except Exception as e:
            logger.warning(f"LiveTrading: get_balance failed: {e}")
            return {"available": 0.0, "total": 0.0, "currency": "USDT", "error": str(e)}

    @classmethod
    def fee_rate_for(cls, symbol: str) -> float:
        """Phemex perpetual contract taker fee: 0.06%."""
        return 0.0006


# Singleton
live_trading = LiveTradingService()
