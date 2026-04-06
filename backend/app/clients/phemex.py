import hmac
import hashlib
import time
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import httpx


class PhemexClient:
    BASE_URL = "https://api.phemex.com"
    BASE_URL_TESTNET = "https://api-testnet.phemex.com"
    WS_URL = "wss://stream.phemex.com"
    WS_URL_TESTNET = "wss://stream-testnet.phemex.com"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = self.BASE_URL_TESTNET if testnet else self.BASE_URL
        self.ws_url = self.WS_URL_TESTNET if testnet else self.WS_URL
        self.client = httpx.AsyncClient(timeout=30.0)

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

    async def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: str = "", base_url: str = None) -> Dict[str, Any]:
        params = params or {}
        headers = self._generate_signature(method, path, params, body)
        headers["Content-Type"] = "application/json"
        
        url_base = base_url or self.base_url
        response = await self.client.request(method, f"{url_base}{path}", params=params if method == "GET" else None, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_klines(self, symbol: str, interval: str = "1h", limit: int = 100) -> List[Dict[str, Any]]:
        resolution_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "1440"}
        resolution = resolution_map.get(interval, "60")
        
        # Only 1h+ is reliably supported by Phemex
        if resolution not in ["60", "240", "1440"]:
            resolution = "60"
        
        try:
            path = "/exchange/public/md/v2/kline/last"
            params = {"symbol": symbol, "resolution": resolution, "size": limit}
            data = await self._request("GET", path, params)
            result = data.get("data", {})
            if result.get("rows") and len(result["rows"]) >= limit:
                return result["rows"]
        except:
            pass
        
        # Phemex returns limited data - use Binance fallback for more candles
        return await self._get_binance_klines(symbol, interval, limit)

    async def _get_binance_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        """Fallback to Binance for historical data"""
        try:
            binance_interval = interval
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.binance.com/api/v3/klines",
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

    async def get_account_balance(self) -> Dict[str, Any]:
        path = "/spot/wallets"
        return await self._request("GET", path)

    async def place_order(self, symbol: str, side: str, quantity: float, order_type: str = "Limit", price: Optional[float] = None) -> Dict[str, Any]:
        import json
        path = "/v1/orders"
        body = json.dumps({
            "symbol": symbol,
            "side": side,
            "orderQty": quantity,
            "ordType": order_type,
        })
        if price:
            body_obj = json.loads(body)
            body_obj["priceEp"] = int(price * 10000)
            body = json.dumps(body_obj)
        return await self._request("POST", path, body=body)

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        path = f"/v1/orders/{order_id}"
        params = {"symbol": symbol}
        return await self._request("DELETE", path, params)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        path = "/v1/orders/active"
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._request("GET", path, params)
        return data.get("data", [])

    async def get_positions(self) -> List[Dict[str, Any]]:
        path = "/v1/positions"
        data = await self._request("GET", path)
        return data.get("data", [])

    async def close(self):
        await self.client.aclose()
