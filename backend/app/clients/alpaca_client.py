"""
Alpaca Markets REST client — stocks/ETFs trading via Alpaca's Broker API.

Uses the `alpaca-trade-api` Python SDK under the hood. Supports both
paper (free) and live modes. Market data comes from Alpaca's free API.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


class AlpacaClientError(Exception):
    pass


class AlpacaClient:
    """Thin wrapper around alpaca-trade-api SDK for stock/ETF trading."""

    def __init__(self, api_key: str = "", secret_key: str = "", paper: bool = True):
        self._api_key = api_key or settings.alpaca_api_key or ""
        self._secret_key = secret_key or settings.alpaca_secret_key or ""
        self._paper = paper
        self._trade_api = None  # lazy init

    @property
    def _api(self):
        if self._trade_api is None:
            try:
                from alpaca.trading.client import TradingClient
                self._trade_api = TradingClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    paper=self._paper,
                )
            except ImportError:
                raise AlpacaClientError(
                    "alpaca-trade-api not installed. Run: pip install alpaca-trade-api"
                )
        return self._trade_api

    @property
    def _data_api(self):
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            return StockHistoricalDataClient(self._api_key, self._secret_key)
        except ImportError:
            raise AlpacaClientError("alpaca-trade-api not installed")

    # ── Account ────────────────────────────────────────────────────────

    def get_account(self) -> Dict[str, Any]:
        acct = self._api.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "status": acct.status,
            "currency": "USD",
        }

    # ── Orders ─────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,              # "buy" | "sell"
        quantity: float,
        order_type: str = "market",  # "market" | "limit" | "stop"
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Dict[str, Any]:
        """Place a stock/ETF order through Alpaca."""
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, OrderClass

        side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force.lower() == "day" else TimeInForce.GTC

        qty = abs(quantity)

        if order_type == "market":
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif,
                order_class=OrderClass.SIMPLE,
            )
        elif order_type == "limit":
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif,
                limit_price=Decimal(str(limit_price)) if limit_price else None,
            )
        elif order_type == "stop":
            req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=tif,
                stop_price=Decimal(str(stop_price)) if stop_price else None,
            )
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        order = self._api.submit_order(req)
        return {
            "id": order.id,
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": float(order.qty),
            "filled_qty": float(order.filled_qty or 0),
            "filled_avg_price": float(order.filled_avg_price or 0),
            "status": order.status,
            "created_at": str(order.created_at),
        }

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._api.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.warning(f"Alpaca cancel order failed: {e}")
            return False

    def get_orders(self, status: str = "open", limit: int = 50) -> List[Dict[str, Any]]:
        from alpaca.trading.enums import QueryOrderStatus
        orders = self._api.get_orders(
            status=QueryOrderStatus.OPEN if status == "open" else QueryOrderStatus.CLOSED,
            limit=limit,
        )
        return [
            {
                "id": o.id,
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "filled_qty": float(o.filled_qty or 0),
                "filled_avg_price": float(o.filled_avg_price or 0),
                "status": o.status,
                "created_at": str(o.created_at),
            }
            for o in orders
        ]

    # ── Positions ──────────────────────────────────────────────────────

    def get_positions(self) -> List[Dict[str, Any]]:
        positions = self._api.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_pl_pct": float(p.unrealized_plpc),
                "cost_basis": float(p.cost_basis),
            }
            for p in positions
        ]

    def close_position(self, symbol: str, qty: Optional[float] = None) -> bool:
        try:
            if qty:
                self._api.close_position(symbol, qty_to_close=qty)
            else:
                self._api.close_position(symbol)
            return True
        except Exception as e:
            logger.warning(f"Alpaca close position failed: {e}")
            return False

    # ── Market data ────────────────────────────────────────────────────

    def get_stock_price(self, symbol: str) -> Optional[float]:
        """Get latest trade price for a stock/ETF."""
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            resp = self._data_api.get_stock_latest_trade(req)
            trade = resp.get(symbol)
            return float(trade.price) if trade else None
        except Exception as e:
            logger.warning(f"Alpaca price fetch failed for {symbol}: {e}")
            return None

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get OHLCV bars for a stock/ETF."""
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            tf_map = {
                "1Min": TimeFrame(1, TimeFrameUnit.Minute),
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
                "1Day": TimeFrame(1, TimeFrameUnit.Day),
                "1Week": TimeFrame(1, TimeFrameUnit.Week),
            }
            tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Day))
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                limit=limit,
            )
            resp = self._data_api.get_stock_bars(req)
            bars = resp.get(symbol, [])
            return [
                {
                    "timestamp": str(b.timestamp),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": b.volume,
                }
                for b in bars
            ]
        except Exception as e:
            logger.warning(f"Alpaca bars fetch failed for {symbol}: {e}")
            return []

    # ── Multi-asset helpers ────────────────────────────────────────────

    def get_market_status(self) -> Dict[str, Any]:
        """Check if US markets are currently open."""
        try:
            from alpaca.trading.enums import MarketStatus
            clock = self._api.get_clock()
            return {
                "is_open": clock.is_open,
                "next_open": str(clock.next_open),
                "next_close": str(clock.next_close),
            }
        except Exception as e:
            logger.warning(f"Alpaca clock fetch failed: {e}")
            return {"is_open": False}
