"""
Alpaca Live Trading Service — stocks/ETFs execution via the Alpaca API.

Designed for longer-term / lower-frequency trading:
- 1h and 1d timeframes (not 5m scalping)
- Wider default SL/TP to accommodate lower stock volatility
- Fee rate: $0 stock trades / 0% crypto on Alpaca
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.clients.alpaca_client import AlpacaClient
from app.config import settings
from app.models import OrderSide, OrderStatus

logger = logging.getLogger(__name__)


class AlpacaLiveTradingService:
    """Live trading service for Alpaca Markets (stocks/ETFs)."""

    DEFAULT_FEE_RATE = 0.0  # Alpaca charges $0 for stock/ETF trades

    def __init__(self):
        self._client = AlpacaClient(
            api_key=settings.alpaca_api_key or "",
            secret_key=settings.alpaca_secret_key or "",
            paper=settings.alpaca_paper if hasattr(settings, 'alpaca_paper') else True,
        )
        self.logger = logging.getLogger(__name__)

    # ── Fee ────────────────────────────────────────────────────────────

    @classmethod
    def fee_rate_for(cls, symbol: str) -> float:
        return cls.DEFAULT_FEE_RATE

    # ── Orders ─────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        agent_id: Optional[str] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        try:
            result = self._client.place_order(
                symbol=symbol,
                side=side,
                quantity=abs(quantity),
                order_type="market",
            )
            self.logger.info(
                f"Alpaca {side.upper()} {quantity:.4f} {symbol} @ ${result.get('filled_avg_price', 0):.2f}"
            )
            return result
        except Exception as e:
            self.logger.error(f"Alpaca place_order failed: {e}")
            return None

    async def close_position(
        self,
        position_id: str,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Close a position by symbol (position_id is the Alpaca symbol)."""
        try:
            ok = self._client.close_position(position_id)
            if ok:
                self.logger.info(f"Alpaca closed position {position_id}")
                return {"status": "closed", "symbol": position_id}
            return None
        except Exception as e:
            self.logger.error(f"Alpaca close_position failed: {e}")
            return None

    # ── Positions ──────────────────────────────────────────────────────

    async def get_positions(
        self,
        symbol: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            positions = self._client.get_positions()
            if symbol:
                positions = [p for p in positions if p["symbol"] == symbol]
            return positions
        except Exception as e:
            self.logger.error(f"Alpaca get_positions failed: {e}")
            return []

    async def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single position by symbol."""
        positions = await self.get_positions(symbol=position_id)
        return positions[0] if positions else None

    # ── Account ────────────────────────────────────────────────────────

    async def get_account(self) -> Dict[str, Any]:
        try:
            return self._client.get_account()
        except Exception as e:
            self.logger.error(f"Alpaca get_account failed: {e}")
            return {}

    # ── Update SL/TP (no-op — Alpaca doesn't support native OCO brackets
    #     via the simple API; we'd need to manage these server-side.) ─────

    async def update_position_sl_tp(
        self,
        position_id: str,
        stop_loss_price: Any = None,
        take_profit_price: Any = None,
        trailing_stop_pct: Any = None,
    ) -> None:
        self.logger.debug(
            f"Alpaca SL/TP update skipped — not supported natively "
            f"(position={position_id})"
        )
        pass

    async def update_highest_price(
        self,
        position_id: str,
        current_price: float,
        is_short: bool = False,
    ) -> None:
        pass


alpaca_live_trading = AlpacaLiveTradingService()
