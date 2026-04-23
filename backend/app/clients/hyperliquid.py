"""
Hyperliquid API Client — public endpoints only, no auth required.

All Hyperliquid info endpoints share a single POST interface at
https://api.hyperliquid.xyz/info with a JSON body specifying the query type.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class HyperliquidClient:
    BASE_URL = "https://api.hyperliquid.xyz/info"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _post(self, payload: Dict[str, Any]) -> Any:
        """Single POST wrapper — all /info endpoints share this interface."""
        resp = await self._client.post(self.BASE_URL, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_clearinghouse_state(self, address: str) -> Dict[str, Any]:
        """
        Returns positions, margin, leverage, unrealizedPnl for an address.
        Schema: { assetPositions: [{ position: { coin, szi, entryPx,
                  unrealizedPnl, leverage, positionValue } }], marginSummary: {...} }
        """
        return await self._post({"type": "clearinghouseState", "user": address})

    async def get_leaderboard(self) -> List[Dict[str, Any]]:
        """
        Fetch the Hyperliquid leaderboard from stats-data endpoint.
        Returns list of {ethAddress, accountValue, windowPerformances, displayName}.
        Uses a separate stats-data host — not the /info endpoint.
        """
        resp = await self._client.get(
            "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("leaderboardRows", [])

    async def get_user_fills(self, address: str) -> List[Dict[str, Any]]:
        """Returns up to 2000 recent fills: coin, px, sz, side, pnl, fee, time."""
        return await self._post({"type": "userFills", "user": address})

    async def get_portfolio(self, address: str) -> Dict[str, Any]:
        """Returns PnL history: day / week / month / allTime."""
        return await self._post({"type": "portfolio", "user": address})

    async def get_open_orders(self, address: str) -> List[Dict[str, Any]]:
        """Returns open orders: coin, side, sz, limitPx, orderType."""
        return await self._post({"type": "frontendOpenOrders", "user": address})

    async def get_all_mids(self) -> Dict[str, str]:
        """Returns mid prices for all assets as {coin: price_str}, e.g. {"BTC": "50000.0"}."""
        return await self._post({"type": "allMids"})

    async def close(self) -> None:
        await self._client.aclose()
