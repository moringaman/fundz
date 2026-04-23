"""
Hyperliquid Live Trading Service
=================================
Mirrors LiveTradingService's public API but routes all calls to Hyperliquid.
Maintains local DB records (is_paper=False) for monitoring.

Used by agent_scheduler when an agent's venue is set to "hyperliquid".
Credentials are read from env vars at first use (lazy init):
  HYPERLIQUID_WALLET_ADDRESS — public wallet address
  HYPERLIQUID_WALLET_KEY     — private key (hex, 0x-prefixed or bare)

SL/TP enforcement: Hyperliquid does not have exchange-native SL/TP on perp
orders the same way Phemex does.  We store SL/TP in the local DB and rely on
the agent_scheduler monitoring loop to close positions when prices are hit,
exactly as paper trading does.  This keeps the two venues behaving identically
for the monitoring / team-chat logic.
"""

from __future__ import annotations

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


def _to_hl_coin(symbol: str) -> str:
    """Translate exchange symbol to Hyperliquid coin name.

    Examples:
        BTCUSDT  → BTC
        ETHUSDT  → ETH
        SOLUSDT  → SOL
    Hyperliquid perps trade in coin-denominated contracts, not USDT pairs.
    """
    symbol = symbol.upper()
    for suffix in ("USDT", "BUSD", "USD"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


class HyperliquidLiveTradingService:
    """Live trading service that executes real Hyperliquid perpetual orders."""

    # Hyperliquid taker fee: 0.035% (maker: 0.01%)
    DEFAULT_FEE_RATE = 0.00035

    def __init__(self) -> None:
        self._exchange = None   # hyperliquid.exchange.Exchange — lazily initialised
        self._info_client = None  # HyperliquidClient — lazily initialised

    # ── Client accessors ───────────────────────────────────────────────────

    def _get_exchange(self):
        """Lazily initialise the authenticated Hyperliquid exchange client."""
        if self._exchange is None:
            from eth_account import Account  # type: ignore
            from hyperliquid.exchange import Exchange  # type: ignore
            from app.config import settings as _s
            if not _s.hyperliquid_wallet_key:
                raise RuntimeError(
                    "HYPERLIQUID_WALLET_KEY is not configured. "
                    "Set it in Settings → API Keys or as an environment variable."
                )
            wallet = Account.from_key(_s.hyperliquid_wallet_key)
            self._exchange = Exchange(wallet, "https://api.hyperliquid.xyz")
        return self._exchange

    def _get_info(self):
        """Lazily initialise the read-only Hyperliquid info client."""
        if self._info_client is None:
            from app.clients.hyperliquid import HyperliquidClient
            self._info_client = HyperliquidClient()
        return self._info_client

    # ── Helpers ────────────────────────────────────────────────────────────

    def _fee_rate(self, symbol: str) -> float:  # noqa: ARG002
        return self.DEFAULT_FEE_RATE

    async def _fetch_price(self, symbol: str) -> float:
        coin = _to_hl_coin(symbol)
        try:
            mids = await self._get_info().get_all_mids()
            if isinstance(mids, dict):
                val = mids.get(coin)
                if val:
                    return float(val)
        except Exception as exc:
            logger.warning(f"HLTrading: failed to fetch price for {coin}: {exc}")
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
        Place a real Hyperliquid market-open order.
        Creates a local Position record with is_paper=False.
        SL/TP are stored in DB and enforced by the monitoring loop.
        Returns an order result dict or None on failure.
        """
        coin = _to_hl_coin(symbol)
        side_str = side.value if hasattr(side, "value") else str(side)
        is_buy = side_str.lower() == "buy"

        if price is None:
            price = await self._fetch_price(symbol)
        if price <= 0:
            logger.error(f"HLTrading: cannot place order — bad price for {coin}")
            return None

        exchange = self._get_exchange()

        # ── Safety: verify account balance ────────────────────────────────
        try:
            bal = await self.get_balance()
            available = float(bal.get("available", 0))
            required = quantity * price * 1.05  # 5% buffer for fees/slippage
            if available > 0 and available < required:
                logger.error(
                    f"HLTrading: insufficient balance for {coin} — "
                    f"need ~${required:.2f}, have ${available:.2f}"
                )
                return None
        except Exception as exc:
            logger.warning(f"HLTrading: balance check failed (proceeding): {exc}")

        # ── Place Hyperliquid market order ─────────────────────────────────
        try:
            result = exchange.market_open(coin, is_buy, quantity, None, slippage=0.01)
            status = (result or {}).get("status", "")
            if status != "ok":
                logger.error(f"HLTrading: order rejected for {coin}: {result}")
                return None
            statuses = (
                result.get("response", {}).get("data", {}).get("statuses", [{}])
            )
            filled = statuses[0].get("filled", {}) if statuses else {}
            hl_order_id = str(filled.get("oid", ""))
            fill_price = float(filled.get("avgPx", price) or price)
        except Exception as exc:
            logger.error(f"HLTrading: order placement failed for {coin}: {exc}")
            return None

        fee_rate = self._fee_rate(symbol)
        fee = quantity * fill_price * fee_rate

        # ── Persist DB records ──────────────────────────────────────────────
        async with get_async_session() as db:
            trade = Trade(
                id=_gen_id(),
                user_id=self._default_user_id(),
                agent_id=agent_id,
                trader_id=trader_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=fill_price,
                total=quantity * fill_price,
                fee=fee,
                leverage=leverage,
                margin_used=margin_used or ((quantity * fill_price) / max(leverage, 1.0)),
                status=OrderStatus.FILLED,
                phemex_order_id=hl_order_id,  # reuse field — stores HL order ID
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)

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
                    entry_price=fill_price,
                    current_price=fill_price,
                    leverage=leverage,
                    margin_used=margin_used or ((quantity * fill_price) / max(leverage, 1.0)),
                    liquidation_price=liquidation_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    trailing_stop_pct=trailing_stop_pct,
                    highest_price=fill_price,
                    is_paper=False,
                    phemex_order_id=hl_order_id,
                )
                db.add(pos)
            else:
                # Average into existing position
                total_qty = pos.quantity + quantity
                pos.entry_price = (
                    pos.entry_price * pos.quantity + fill_price * quantity
                ) / total_qty
                pos.quantity = total_qty
                pos.current_price = fill_price
                pos.leverage = max(float(pos.leverage or 1.0), leverage)
                pos.margin_used = float(pos.margin_used or 0.0) + (
                    margin_used or ((quantity * fill_price) / max(leverage, 1.0))
                )
                pos.liquidation_price = liquidation_price or pos.liquidation_price
                if stop_loss_price:
                    pos.stop_loss_price = stop_loss_price
                if take_profit_price:
                    pos.take_profit_price = take_profit_price
                pos.phemex_order_id = hl_order_id

            await db.commit()
            await db.refresh(pos)
            position_id = pos.id

        logger.info(
            f"HLTrading: placed {side_str} {quantity} {coin} @ {fill_price:.4f} "
            f"SL={stop_loss_price} TP={take_profit_price} hl_order_id={hl_order_id}"
        )
        return {
            "id": position_id,
            "symbol": symbol,
            "side": side_str,
            "quantity": quantity,
            "price": fill_price,
            "leverage": leverage,
            "margin_used": margin_used or ((quantity * fill_price) / max(leverage, 1.0)),
            "liquidation_price": liquidation_price,
            "phemex_order_id": hl_order_id,
            "is_paper": False,
            "venue": "hyperliquid",
        }

    async def close_position(self, position_id: str) -> Optional[dict]:
        """Close a Hyperliquid live position via market_close."""
        exchange = self._get_exchange()

        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                logger.warning(f"HLTrading: position {position_id} not found")
                return None

            current_price = await self._fetch_price(pos.symbol)
            if current_price <= 0:
                current_price = pos.current_price or pos.entry_price or 0.0

            is_long = (
                pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            ).lower() == "buy"
            coin = _to_hl_coin(pos.symbol)

            try:
                result = exchange.market_close(coin, None, None, slippage=0.01)
                status = (result or {}).get("status", "")
                if status != "ok":
                    logger.error(f"HLTrading: close rejected for {coin}: {result}")
                    return None
                statuses = (
                    result.get("response", {}).get("data", {}).get("statuses", [{}])
                )
                filled = statuses[0].get("filled", {}) if statuses else {}
                close_order_id = str(filled.get("oid", ""))
                fill_price = float(filled.get("avgPx", current_price) or current_price)
            except Exception as exc:
                logger.error(
                    f"HLTrading: failed to close position {position_id}: {exc}"
                )
                return None

            fee_rate = self._fee_rate(pos.symbol)
            entry_fee = pos.entry_price * pos.quantity * fee_rate
            exit_fee = fill_price * pos.quantity * fee_rate
            raw_pnl = (
                (fill_price - pos.entry_price) * pos.quantity
                if is_long
                else (pos.entry_price - fill_price) * pos.quantity
            )
            final_pnl = raw_pnl - entry_fee - exit_fee + (pos.realized_pnl or 0)

            trade = Trade(
                id=_gen_id(),
                user_id=pos.user_id,
                agent_id=pos.agent_id,
                symbol=pos.symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=pos.quantity,
                price=fill_price,
                total=fill_price * pos.quantity,
                fee=exit_fee,
                status=OrderStatus.FILLED,
                phemex_order_id=close_order_id,
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)

            result_dict = {
                "closed": True,
                "position_id": position_id,
                "symbol": pos.symbol,
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "exit_price": fill_price,
                "pnl": round(final_pnl, 4),
                "leverage": pos.leverage or 1.0,
                "phemex_order_id": close_order_id,
                "is_paper": False,
                "venue": "hyperliquid",
            }

            await db.delete(pos)
            await db.commit()

        logger.info(
            f"HLTrading: closed position {position_id} {pos.symbol} "
            f"P&L=${final_pnl:+.2f} hl_order={close_order_id}"
        )
        return result_dict

    async def partial_close(
        self,
        position_id: str,
        close_pct: float,
        price: float,
        agent_id: Optional[str] = None,
        label: str = "scale-out",
    ) -> Optional[dict]:
        """Close a percentage of a Hyperliquid position (scale-out)."""
        exchange = self._get_exchange()

        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                return None

            qty_to_close = round(pos.quantity * close_pct, 8)
            if qty_to_close <= 0:
                return None

            is_long = (
                pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            ).lower() == "buy"
            coin = _to_hl_coin(pos.symbol)

            try:
                # Reduce-only IOC (acts as a market order on HL)
                limit_px = (
                    price * 0.99 if is_long else price * 1.01
                )  # 1% slippage tolerance
                result = exchange.order(
                    coin,
                    not is_long,  # opposite side to reduce
                    qty_to_close,
                    limit_px,
                    {"limit": {"tif": "Ioc"}},
                    reduce_only=True,
                )
                status = (result or {}).get("status", "")
                statuses = (
                    result.get("response", {}).get("data", {}).get("statuses", [{}])
                    if status == "ok"
                    else [{}]
                )
                filled = statuses[0].get("filled", {}) if statuses else {}
                close_order_id = str(
                    filled.get("oid", f"{label}-{position_id[:8]}")
                )
                fill_price = float(filled.get("avgPx", price) or price)
            except Exception as exc:
                logger.error(
                    f"HLTrading: partial_close failed for {position_id}: {exc}"
                )
                return None

            fee_rate = self._fee_rate(pos.symbol)
            entry_fee_slice = qty_to_close * (pos.entry_price or price) * fee_rate
            close_fee = qty_to_close * fill_price * fee_rate
            raw_pnl = (
                (fill_price - pos.entry_price) * qty_to_close
                if is_long
                else (pos.entry_price - fill_price) * qty_to_close
            )
            slice_pnl = raw_pnl - entry_fee_slice - close_fee

            remaining_qty = pos.quantity - qty_to_close
            pos.quantity = max(remaining_qty, 0)
            pos.realized_pnl = (pos.realized_pnl or 0) + slice_pnl

            trade = Trade(
                id=_gen_id(),
                user_id=pos.user_id,
                agent_id=pos.agent_id or agent_id,
                symbol=pos.symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                quantity=qty_to_close,
                price=fill_price,
                total=qty_to_close * fill_price,
                fee=close_fee,
                status=OrderStatus.FILLED,
                phemex_order_id=close_order_id,
                is_paper=False,
                filled_at=datetime.utcnow(),
            )
            db.add(trade)
            await db.commit()

        logger.info(
            f"HLTrading: partial_close {label} position={position_id} "
            f"qty={qty_to_close:.6f} pnl=${slice_pnl:+.4f}"
        )
        return {
            "realized_pnl": round(slice_pnl, 4),
            "quantity_closed": qty_to_close,
            "remaining_quantity": max(remaining_qty, 0),
            "close_price": fill_price,
            "leverage": pos.leverage or 1.0,
            "phemex_order_id": close_order_id,
            "is_paper": False,
            "venue": "hyperliquid",
        }

    async def update_position_sl_tp(
        self,
        position_id: str,
        stop_loss_price=_SENTINEL,
        take_profit_price=_SENTINEL,
        trailing_stop_pct=_SENTINEL,
    ) -> Optional[dict]:
        """
        Update SL/TP on a Hyperliquid position.
        DB-only — the monitoring loop reads these values and calls close_position
        when prices are hit (same mechanism as paper trading).
        """
        async with get_async_session() as db:
            pos = await self._get_position(db, position_id)
            if pos is None:
                return None

            new_sl = pos.stop_loss_price if stop_loss_price is _SENTINEL else stop_loss_price
            new_tp = pos.take_profit_price if take_profit_price is _SENTINEL else take_profit_price
            new_trail = pos.trailing_stop_pct if trailing_stop_pct is _SENTINEL else trailing_stop_pct

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
        """Update the trailing stop watermark for a Hyperliquid position."""
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
        """Return open Hyperliquid live positions from local DB."""
        async with get_async_session() as db:
            q = select(Position).where(Position.is_paper == False)  # noqa: E712
            if symbol:
                q = q.where(Position.symbol == symbol)
            if agent_id:
                q = q.where(Position.agent_id == agent_id)
            return list((await db.scalars(q)).all())

    async def get_closed_trades(
        self,
        symbol: Optional[str] = None,
        limit: int = 100,
    ):
        """Return closed Hyperliquid trade records from DB."""
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

    async def get_closed_positions(
        self,
        lookback_hours: int = 48,
        limit: int = 200,
    ) -> list:
        """Return closed Hyperliquid positions in the same dict shape as
        paper_trading.get_closed_trades() so TradeRetrospectiveService can
        treat both venues identically.

        A position is considered closed when realized_pnl is non-zero and
        updated_at falls within the lookback window.  The scheduler's
        _monitor_open_positions writes realized_pnl at close time.
        """
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
        async with get_async_session() as db:
            q = (
                select(Position)
                .where(
                    Position.is_paper == False,  # noqa: E712
                    Position.realized_pnl != 0,
                    Position.updated_at >= cutoff,
                )
                .order_by(Position.updated_at.desc())
                .limit(limit)
            )
            positions = list((await db.scalars(q)).all())

        result = []
        for pos in positions:
            if pos.entry_price is None or pos.current_price is None:
                continue
            net_pnl = pos.realized_pnl or 0.0
            entry_price = pos.entry_price
            exit_price = pos.current_price  # last price recorded at close
            pnl_pct = (net_pnl / (entry_price * pos.quantity)) * 100 if entry_price and pos.quantity else 0.0
            result.append({
                "symbol": pos.symbol,
                "agent_id": pos.agent_id,
                "side": "long" if pos.side == OrderSide.BUY else "short",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_time": pos.updated_at.isoformat(),  # Arrr, no open_at field — use updated_at as proxy
                "exit_time": pos.updated_at.isoformat(),
                "net_pnl": net_pnl,
                "pnl_pct": round(pnl_pct, 4),
                "result": "win" if net_pnl > 0 else "loss",
                "entry_indicators": getattr(pos, "entry_indicators", None),
            })
        return result

    async def fetch_current_price(self, symbol: str) -> float:
        return await self._fetch_price(symbol)

    async def get_balance(self) -> dict:
        """Return USDC account value from Hyperliquid clearinghouse state."""
        from app.config import settings as _s
        addr = _s.hyperliquid_wallet_address
        if not addr:
            return {
                "available": 0.0,
                "total": 0.0,
                "currency": "USDC",
                "error": "HYPERLIQUID_WALLET_ADDRESS not configured",
            }
        try:
            state = await self._get_info().get_clearinghouse_state(addr)
            summary = state.get("marginSummary", {})
            total = float(summary.get("accountValue", 0))
            available = float(summary.get("withdrawable", total) or total)
            return {"available": available, "total": total, "currency": "USDC"}
        except Exception as exc:
            logger.warning(f"HLTrading: get_balance failed: {exc}")
            return {"available": 0.0, "total": 0.0, "currency": "USDC", "error": str(exc)}

    @classmethod
    def fee_rate_for(cls, symbol: str) -> float:  # noqa: ARG003
        """Hyperliquid taker fee: 0.035%."""
        return cls.DEFAULT_FEE_RATE


# Singleton used throughout the system
hl_live_trading = HyperliquidLiveTradingService()
