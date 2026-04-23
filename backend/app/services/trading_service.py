"""
Trading Service — Unified Abstraction Layer
===========================================
Routes all trading calls to either PaperTradingService or LiveTradingService
based on the `paper_trading_default` setting read at call time.

All callers import `trading_service` and call the same API regardless of mode.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from app.models import OrderSide

logger = logging.getLogger(__name__)


def _is_paper_mode() -> bool:
    """Read paper_trading_default from settings at call time."""
    try:
        from app.api.routes.settings import get_trading_prefs
        return get_trading_prefs().paper_trading_default
    except Exception:
        return True  # fail safe — default to paper


class TradingService:
    """
    Unified trading interface.  Routes each call to paper or live service
    based on `paper_trading_default` setting evaluated at call time, so
    switching modes takes effect immediately on the next trade cycle.
    """

    # Per-agent venue cache populated by agent_scheduler on load/register.
    # Defaults to "phemex" for any agent not explicitly registered.
    _agent_venues: Dict[str, str] = {}

    def set_agent_venue(self, agent_id: str, venue: str) -> None:
        """Register an agent's trading venue. Called by agent_scheduler on load."""
        self._agent_venues[agent_id] = venue or "phemex"

    def _paper(self):
        from app.services.paper_trading import paper_trading
        return paper_trading

    def _live(self, agent_id: Optional[str] = None):
        venue = self._agent_venues.get(agent_id or "", "phemex")
        if venue == "hyperliquid":
            from app.services.hl_live_trading import hl_live_trading
            return hl_live_trading
        from app.services.live_trading import live_trading
        return live_trading

    def _backend(self, force_paper: Optional[bool] = None, agent_id: Optional[str] = None):
        is_paper = force_paper if force_paper is not None else _is_paper_mode()
        return self._paper() if is_paper else self._live(agent_id)

    # ── Core methods ───────────────────────────────────────────────────────

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
        force_paper: Optional[bool] = None,
    ):
        svc = self._backend(force_paper)
        mode = "paper" if _is_paper_mode() else "LIVE"
        logger.info(
            f"TradingService [{mode}]: place_order {side.value if hasattr(side,'value') else side} "
            f"{quantity:.6f} {symbol}"
        )
        return await svc.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            agent_id=agent_id,
            trader_id=trader_id,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            trailing_stop_pct=trailing_stop_pct,
        )

    async def close_position(
        self,
        position_id: str,
        force_paper: Optional[bool] = None,
    ):
        svc = await self._detect_backend(position_id, force_paper)
        return await svc.close_position(position_id)

    async def partial_close(
        self,
        position_id: str,
        close_pct: float,
        price: float,
        agent_id: Optional[str] = None,
        label: str = "scale-out",
        force_paper: Optional[bool] = None,
    ):
        svc = await self._detect_backend(position_id, force_paper)
        return await svc.partial_close(
            position_id=position_id,
            close_pct=close_pct,
            price=price,
            agent_id=agent_id,
            label=label,
        )

    async def update_position_sl_tp(
        self,
        position_id: str,
        stop_loss_price=...,
        take_profit_price=...,
        trailing_stop_pct=...,
        force_paper: Optional[bool] = None,
    ):
        svc = await self._detect_backend(position_id, force_paper)
        return await svc.update_position_sl_tp(
            position_id=position_id,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            trailing_stop_pct=trailing_stop_pct,
        )

    async def update_highest_price(
        self,
        position_id: str,
        current_price: float,
        is_short: bool = False,
        force_paper: Optional[bool] = None,
    ):
        svc = await self._detect_backend(position_id, force_paper)
        return await svc.update_highest_price(
            position_id=position_id,
            current_price=current_price,
            is_short=is_short,
        )

    async def get_positions(
        self,
        symbol: Optional[str] = None,
        agent_id: Optional[str] = None,
        include_live: bool = True,
    ):
        """Return positions from both paper and live backends."""
        paper_pos = await self._paper().get_positions(symbol=symbol, agent_id=agent_id)
        if include_live and not _is_paper_mode():
            try:
                live_pos = await self._live().get_positions(symbol=symbol, agent_id=agent_id)
                return list(paper_pos) + list(live_pos)
            except Exception as e:
                logger.warning(f"TradingService: live get_positions failed: {e}")
        return list(paper_pos)

    async def get_closed_trades(
        self,
        symbol: Optional[str] = None,
        limit: int = 100,
    ):
        paper_trades = await self._paper().get_closed_trades(symbol=symbol, limit=limit)
        if not _is_paper_mode():
            try:
                live_trades = await self._live().get_closed_trades(symbol=symbol, limit=limit)
                return list(paper_trades) + list(live_trades)
            except Exception as e:
                logger.warning(f"TradingService: live get_closed_trades failed: {e}")
        return list(paper_trades)

    async def fetch_current_price(self, symbol: str) -> float:
        return await self._paper().fetch_current_price(symbol)

    # ── Helpers ─────────────────────────────────────────────────────────────

    async def _detect_backend(self, position_id: str, force_paper: Optional[bool] = None):
        """Return the right service for a position — paper or the correct live venue."""
        if force_paper is True:
            return self._paper()
        try:
            from app.database import get_async_session
            from app.models import Position
            from sqlalchemy import select
            async with get_async_session() as db:
                pos = await db.scalar(select(Position).where(Position.id == position_id))
                if pos is not None:
                    if bool(pos.is_paper):
                        return self._paper()
                    return self._live(pos.agent_id)
        except Exception:
            pass
        return self._paper() if _is_paper_mode() else self._live()

    async def _detect_position_mode(self, position_id: str) -> bool:
        """Look up whether a position is paper or live from the DB."""
        try:
            from app.database import get_async_session
            from app.models import Position
            from sqlalchemy import select
            async with get_async_session() as db:
                pos = await db.scalar(select(Position).where(Position.id == position_id))
                if pos is not None:
                    return bool(pos.is_paper)
        except Exception:
            pass
        return _is_paper_mode()


# Singleton used throughout the system
trading_service = TradingService()
