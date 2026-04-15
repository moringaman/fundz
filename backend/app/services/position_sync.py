"""
Position Sync Service
=====================
Periodically syncs live Phemex positions to the local database.
Detects externally closed positions (exchange SL/TP hit server-side).
Handles partial fills and quantity drift.
Logs sync summaries to team chat every 5 minutes.

Wired into agent_scheduler._scheduler_loop() on a 30-second cadence.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.database import get_async_session
from app.models import Position, Trade, OrderSide, OrderStatus

logger = logging.getLogger(__name__)

_DEFAULT_USER = "00000000-0000-0000-0000-000000000001"


def _gen_id() -> str:
    return str(uuid.uuid4())


def _ep_to_float(val, divisor: float = 1e8) -> float:
    try:
        v = float(val)
        return v / divisor if abs(v) > 1e6 else v
    except (TypeError, ValueError):
        return 0.0


class PositionSyncService:
    def __init__(self) -> None:
        self._phemex = None
        self._last_team_log: Optional[datetime] = None

    def _get_phemex(self):
        if self._phemex is None:
            from app.clients.phemex import PhemexClient
            from app.config import settings
            self._phemex = PhemexClient(
                api_key=settings.phemex_api_key,
                api_secret=settings.phemex_api_secret,
                testnet=getattr(settings, "phemex_testnet", False),
            )
        return self._phemex

    def _is_live_mode(self) -> bool:
        try:
            from app.api.routes.settings import get_trading_prefs
            return not get_trading_prefs().paper_trading_default
        except Exception:
            return False

    async def sync_once(self) -> dict:
        if not self._is_live_mode():
            return {"skipped": True, "reason": "paper mode active"}

        phemex = self._get_phemex()
        summary = {"synced": 0, "created": 0, "externally_closed": 0, "partial_fills": 0, "errors": []}

        try:
            raw = await phemex.get_positions()
            positions_data = raw if isinstance(raw, list) else raw.get("data", [])
        except Exception as e:
            logger.error(f"PositionSync: failed to fetch Phemex positions: {e}")
            summary["errors"].append(str(e))
            return summary

        live_symbols_by_side: dict = {}
        for p in positions_data:
            sym = p.get("symbol", "")
            size = _ep_to_float(p.get("size", 0) or p.get("qty", 0))
            if size == 0:
                continue
            side_raw = (p.get("side", "") or p.get("posSide", "")).lower()
            side = OrderSide.BUY if side_raw in ("long", "buy") else OrderSide.SELL
            live_symbols_by_side[(sym, side)] = p

        async with get_async_session() as db:
            existing = list((await db.scalars(
                select(Position).where(Position.is_paper == False)  # noqa: E712
            )).all())
            existing_map = {(p.symbol, p.side): p for p in existing}

            for (sym, side), phemex_pos in live_symbols_by_side.items():
                qty = _ep_to_float(phemex_pos.get("size", 0) or phemex_pos.get("qty", 0))
                entry = _ep_to_float(phemex_pos.get("avgEntryPrice", 0) or phemex_pos.get("avgEntryPriceEp", 0))
                mark = _ep_to_float(phemex_pos.get("markPrice", 0) or phemex_pos.get("markPriceEp", 0))
                upnl = _ep_to_float(phemex_pos.get("unrealisedPnl", 0) or phemex_pos.get("unrealisedPnlEv", 0))

                db_pos = existing_map.get((sym, side))
                if db_pos:
                    if abs(db_pos.quantity - qty) > 1e-8:
                        summary["partial_fills"] += 1
                        logger.info(f"PositionSync: qty drift {sym} {side.value}: local={db_pos.quantity:.6f} phemex={qty:.6f}")
                    db_pos.quantity = qty
                    db_pos.entry_price = entry or db_pos.entry_price
                    db_pos.current_price = mark or db_pos.current_price
                    db_pos.unrealized_pnl = upnl
                    summary["synced"] += 1
                else:
                    new_pos = Position(
                        id=_gen_id(), user_id=_DEFAULT_USER,
                        symbol=sym, side=side, quantity=qty,
                        entry_price=entry, current_price=mark,
                        unrealized_pnl=upnl, is_paper=False,
                    )
                    db.add(new_pos)
                    summary["created"] += 1
                    logger.info(f"PositionSync: new live position {sym} {side.value} qty={qty:.6f}")

            live_keys = set(live_symbols_by_side.keys())
            for (sym, side), db_pos in existing_map.items():
                if (sym, side) not in live_keys and db_pos.quantity > 0:
                    logger.info(f"PositionSync: external close detected {sym} {side.value}")
                    trade = Trade(
                        id=_gen_id(), user_id=_DEFAULT_USER, agent_id=db_pos.agent_id,
                        symbol=sym, side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
                        quantity=db_pos.quantity, price=db_pos.current_price or 0,
                        total=(db_pos.quantity * (db_pos.current_price or 0)),
                        fee=0.0, status=OrderStatus.FILLED,
                        phemex_order_id="external-close", is_paper=False,
                        filled_at=datetime.utcnow(),
                    )
                    db.add(trade)
                    await db.delete(db_pos)
                    summary["externally_closed"] += 1

            await db.commit()

        now = datetime.utcnow()
        if self._last_team_log is None or (now - self._last_team_log).total_seconds() >= 300:
            self._last_team_log = now
            total = summary["synced"] + summary["created"] + summary["externally_closed"]
            if total > 0:
                try:
                    from app.services.team_chat import team_chat
                    content = (
                        f"🔄 Live position sync: {summary['synced']} active, "
                        f"{summary['created']} new, {summary['externally_closed']} externally closed"
                    )
                    if summary["partial_fills"]:
                        content += f", {summary['partial_fills']} qty drift(s) detected"
                    await team_chat.add_message(agent_role="system", content=content, message_type="analysis")
                except Exception:
                    pass

        return summary


position_sync_service = PositionSyncService()


async def start_position_sync(app):
    """Legacy startup handler — kept for backward compatibility."""
    return app
