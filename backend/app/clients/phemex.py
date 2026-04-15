import hmac
import hashlib
import json
import time
import uuid
import logging
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


class PhemexClient:
    BASE_URL = "https://api.phemex.com"
    BASE_URL_TESTNET = "https://testnet-api.phemex.com"
    WS_URL = "wss://ws.phemex.com"
    WS_URL_TESTNET = "wss://testnet-api.phemex.com/ws"

    PRICE_SCALE = 10_000  # Phemex Ep scale factor

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = self.BASE_URL_TESTNET if testnet else self.BASE_URL
        self.ws_url = self.WS_URL_TESTNET if testnet else self.WS_URL
        self.client = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _generate_signature(self, method: str, path: str, params: Dict[str, Any], body: str = "") -> Dict[str, str]:
        if not self.api_key or not self.api_secret:
            return {}

        expiry = int(time.time()) + 60
        query_string = urlencode(sorted(params.items())) if params else ""
        message = f"{path}{query_string}{expiry}{body}"

        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return {
            "x-phemex-access-token": self.api_key,
            "x-phemex-request-expiry": str(expiry),
            "x-phemex-request-signature": signature,
        }

    async def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: str = "", base_url: str = None, _retries: int = 3) -> Dict[str, Any]:
        params = params or {}
        url_base = base_url or self.base_url
        url = f"{url_base}{path}"

        last_exc: Optional[Exception] = None
        for attempt in range(_retries):
            # Re-sign on every attempt — expiry is time-based
            headers = self._generate_signature(method, path, params, body)
            headers["Content-Type"] = "application/json"

            try:
                if method in ("POST", "PUT"):
                    response = await self.client.request(
                        method, url, headers=headers,
                        content=body if body else None,
                    )
                else:
                    response = await self.client.request(
                        method, url, params=params if method == "GET" else None,
                        headers=headers,
                    )

                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code < 500:
                    # 4xx errors are not retryable — alert on 401 before raising
                    if exc.response.status_code == 401:
                        try:
                            from app.services.telegram_service import telegram_service
                            import asyncio as _aio
                            _aio.create_task(
                                telegram_service.alert_api_error(
                                    context=f"{method} {path}",
                                    error=str(exc)[:300],
                                    status_code=401,
                                )
                            )
                        except Exception:
                            pass
                    raise
                logger.warning(
                    f"Phemex {exc.response.status_code} on {method} {path} "
                    f"(attempt {attempt + 1}/{_retries})"
                )
                if attempt < _retries - 1:
                    import asyncio
                    await asyncio.sleep(1.5 ** attempt)  # 1s, 1.5s, 2.25s
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                logger.warning(
                    f"Phemex connection issue on {method} {path}: {exc} "
                    f"(attempt {attempt + 1}/{_retries})"
                )
                if attempt < _retries - 1:
                    import asyncio
                    await asyncio.sleep(1.5 ** attempt)

        # ── Fire Telegram alert for critical API errors (500, 401) ────────
        if isinstance(last_exc, httpx.HTTPStatusError):
            _code = last_exc.response.status_code
            if _code in (500, 401):
                try:
                    from app.services.telegram_service import telegram_service
                    import asyncio
                    asyncio.create_task(
                        telegram_service.alert_api_error(
                            context=f"{method} {path}",
                            error=str(last_exc)[:300],
                            status_code=_code,
                        )
                    )
                except Exception:
                    pass  # Alert is best-effort — never block the raise

        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _to_ep(price: float) -> int:
        """Convert a float price to Phemex Ep (scaled integer)."""
        return int(round(price * PhemexClient.PRICE_SCALE))

    @staticmethod
    def _clord_id() -> str:
        return f"px-{uuid.uuid4().hex[:20]}"

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> List[Dict[str, Any]]:
        resolution_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1440"}
        resolution = resolution_map.get(interval, "60")
        
        if resolution not in ["60", "240", "1440"]:
            resolution = "60"
        
        try:
            path = "/exchange/public/md/v2/kline/last"
            params = {"symbol": symbol, "resolution": resolution, "size": limit}
            data = await self._request("GET", path, params)
            result = data.get("data", {})
            if result.get("rows") and len(result["rows"]) >= limit:
                return result["rows"]
        except Exception:
            pass
        
        return await self._get_binance_klines(symbol, interval, limit)

    async def _get_binance_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        """Fallback to Binance for historical data"""
        try:
            binance_interval = interval
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": symbol, "interval": binance_interval, "limit": limit}
                )
                data = response.json()
                
                result = []
                for k in data:
                    result.append([
                        int(k[0] / 1000),
                        "60",
                        k[1],
                        k[2],
                        k[3],
                        k[4],
                        k[5],
                        k[5],
                        symbol
                    ])
                return result
        except Exception:
            return []

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        path = "/md/v2/ticker/24hr"
        params = {"symbol": symbol}
        return await self._request("GET", path, params)

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        path = "/md/v2/depth"
        params = {"symbol": symbol, "limit": limit}
        return await self._request("GET", path, params)

    async def get_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        path = "/md/v2/trade"
        params = {"symbol": symbol, "limit": limit}
        data = await self._request("GET", path, params)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_balance(self) -> Dict[str, Any]:
        path = "/spot/wallets"
        return await self._request("GET", path)

    async def get_account_positions(self, currency: str = "USD") -> Dict[str, Any]:
        """Query trading account and positions (contract)."""
        path = "/accounts/accountPositions"
        params = {"currency": currency}
        return await self._request("GET", path, params)

    # ------------------------------------------------------------------
    # Order placement — Spot
    # ------------------------------------------------------------------

    async def place_order(self, symbol: str, side: str, quantity: float, order_type: str = "Limit", price: Optional[float] = None) -> Dict[str, Any]:
        """Place a spot order (legacy interface, kept for backward compat)."""
        path = "/spot/orders"
        body_obj: Dict[str, Any] = {
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "qtyType": "ByBase",
            "baseQtyEv": int(quantity * 1e8),
            "ordType": order_type,
            "clOrdID": self._clord_id(),
            "timeInForce": "GoodTillCancel",
        }
        if price:
            body_obj["priceEp"] = self._to_ep(price)
        body = json.dumps(body_obj)
        return await self._request("POST", path, body=body)

    async def place_spot_order_with_sl_tp(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place a spot order with optional SL/TP attached."""
        path = "/spot/orders"
        body_obj: Dict[str, Any] = {
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "qtyType": "ByBase",
            "baseQtyEv": int(quantity * 1e8),
            "ordType": order_type,
            "clOrdID": self._clord_id(),
            "timeInForce": "GoodTillCancel",
        }
        if price and order_type != "Market":
            body_obj["priceEp"] = self._to_ep(price)
        if stop_loss_price:
            body_obj["stopLossEp"] = self._to_ep(stop_loss_price)
        if take_profit_price:
            body_obj["takeProfitEp"] = self._to_ep(take_profit_price)
        body = json.dumps(body_obj)
        logger.info(f"Placing spot order: {side} {quantity} {symbol} @ {price or 'market'} SL={stop_loss_price} TP={take_profit_price}")
        return await self._request("POST", path, body=body)

    # ------------------------------------------------------------------
    # Order placement — Contract (futures/perpetual)
    # ------------------------------------------------------------------

    async def place_contract_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        reduce_only: bool = False,
        time_in_force: str = "GoodTillCancel",
    ) -> Dict[str, Any]:
        """Place a contract order with optional SL/TP."""
        path = "/orders"
        body_obj: Dict[str, Any] = {
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderQty": quantity,
            "ordType": order_type,
            "clOrdID": self._clord_id(),
            "timeInForce": time_in_force,
            "reduceOnly": reduce_only,
        }
        if price and order_type not in ("Market",):
            body_obj["priceEp"] = self._to_ep(price)
        if stop_loss_price:
            body_obj["stopLossEp"] = self._to_ep(stop_loss_price)
            body_obj["slTrigger"] = "ByLastPrice"
        if take_profit_price:
            body_obj["takeProfitEp"] = self._to_ep(take_profit_price)
            body_obj["tpTrigger"] = "ByLastPrice"
        body = json.dumps(body_obj)
        logger.info(f"Placing contract order: {side} {quantity} {symbol} @ {price or 'market'} SL={stop_loss_price} TP={take_profit_price}")
        return await self._request("POST", path, body=body)

    # ------------------------------------------------------------------
    # Conditional / Stop orders
    # ------------------------------------------------------------------

    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        trigger_price: float,
        order_type: str = "Stop",
        limit_price: Optional[float] = None,
        trigger_type: str = "ByLastPrice",
        close_on_trigger: bool = True,
    ) -> Dict[str, Any]:
        """Place a conditional stop or stop-limit order.

        Use ordType="Stop" for stop-market, "StopLimit" for stop-limit.
        """
        path = "/orders"
        body_obj: Dict[str, Any] = {
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderQty": quantity,
            "ordType": order_type,
            "stopPxEp": self._to_ep(trigger_price),
            "triggerType": trigger_type,
            "clOrdID": self._clord_id(),
            "timeInForce": "ImmediateOrCancel" if order_type == "Stop" else "GoodTillCancel",
            "closeOnTrigger": close_on_trigger,
        }
        if limit_price and order_type == "StopLimit":
            body_obj["priceEp"] = self._to_ep(limit_price)
        body = json.dumps(body_obj)
        logger.info(f"Placing stop order: {side} {quantity} {symbol} trigger={trigger_price} type={order_type}")
        return await self._request("POST", path, body=body)

    async def place_trailing_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        trailing_offset: float,
        activation_price: Optional[float] = None,
        trigger_type: str = "ByLastPrice",
    ) -> Dict[str, Any]:
        """Place a trailing stop order.

        Args:
            trailing_offset: Absolute price offset (positive for short, negative for long)
            activation_price: Optional price that must be reached before trailing activates
        """
        path = "/orders"
        body_obj: Dict[str, Any] = {
            "symbol": symbol,
            "side": "Buy" if side.lower() == "buy" else "Sell",
            "orderQty": quantity,
            "ordType": "Stop",
            "priceEp": 0,
            "triggerType": trigger_type,
            "clOrdID": self._clord_id(),
            "timeInForce": "ImmediateOrCancel",
            "closeOnTrigger": True,
            "pegPriceType": "TrailingStopPeg",
            "pegOffsetValueEp": self._to_ep(trailing_offset),
        }
        if activation_price:
            body_obj["stopPxEp"] = self._to_ep(activation_price)
            body_obj["pegPriceType"] = "TrailingTakeProfitPeg"
        body = json.dumps(body_obj)
        logger.info(f"Placing trailing stop: {side} {symbol} offset={trailing_offset}")
        return await self._request("POST", path, body=body)

    # ------------------------------------------------------------------
    # Amend / replace orders
    # ------------------------------------------------------------------

    async def amend_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        trailing_offset: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Amend an existing contract order's price, qty, SL, TP, or trailing offset."""
        path = "/orders/replace"
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderID"] = order_id
        if cl_ord_id:
            params["origClOrdID"] = cl_ord_id
        if price is not None:
            params["priceEp"] = self._to_ep(price)
        if quantity is not None:
            params["orderQty"] = quantity
        if stop_loss_price is not None:
            params["stopLossEp"] = self._to_ep(stop_loss_price)
        if take_profit_price is not None:
            params["takeProfitEp"] = self._to_ep(take_profit_price)
        if trailing_offset is not None:
            params["pegOffsetValueEp"] = self._to_ep(trailing_offset)

        logger.info(f"Amending order {order_id or cl_ord_id} on {symbol}: {params}")
        return await self._request("PUT", path, params)

    async def amend_spot_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Amend an existing spot order."""
        path = "/spot/orders"
        body_obj: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            body_obj["orderID"] = order_id
        if cl_ord_id:
            body_obj["origClOrdID"] = cl_ord_id
        if price is not None:
            body_obj["priceEp"] = self._to_ep(price)
        if quantity is not None:
            body_obj["baseQtyEv"] = int(quantity * 1e8)
        if stop_loss_price is not None:
            body_obj["stopLossEp"] = self._to_ep(stop_loss_price)
        if take_profit_price is not None:
            body_obj["takeProfitEp"] = self._to_ep(take_profit_price)
        body = json.dumps(body_obj)
        logger.info(f"Amending spot order {order_id or cl_ord_id} on {symbol}")
        return await self._request("PUT", path, body=body)

    # ------------------------------------------------------------------
    # Cancel orders
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        path = "/orders/cancel"
        params = {"symbol": symbol, "orderID": order_id}
        return await self._request("DELETE", path, params)

    async def cancel_all_orders(self, symbol: str, untriggered: bool = False) -> Dict[str, Any]:
        """Cancel all orders for a symbol. Set untriggered=True to cancel conditional orders."""
        path = "/orders/all"
        params: Dict[str, Any] = {"symbol": symbol, "untriggered": str(untriggered).lower()}
        return await self._request("DELETE", path, params)

    # ------------------------------------------------------------------
    # Query orders & positions
    # ------------------------------------------------------------------

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        path = "/orders/activeList"
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", path, params)
        rows = data.get("data", {})
        if isinstance(rows, dict):
            return rows.get("rows", [])
        return rows if isinstance(rows, list) else []

    async def get_positions(self) -> List[Dict[str, Any]]:
        path = "/accounts/accountPositions"
        params = {"currency": "USD"}
        data = await self._request("GET", path, params)
        return data.get("data", {}).get("positions", [])

    # ------------------------------------------------------------------
    # Leverage
    # ------------------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage for a contract symbol."""
        path = "/positions/leverage"
        params = {"symbol": symbol, "leverage": leverage}
        return await self._request("PUT", path, params)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self):
        await self.client.aclose()
