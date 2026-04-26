"""
Fear & Greed sentiment service.

Fetches the Alternative.me Crypto Fear & Greed Index — a free, unauthenticated
endpoint that returns a 0-100 score updated daily.  Score legend:
  0-24   → Extreme Fear   (retail panic; historically a contrarian buy zone)
  25-49  → Fear
  50-74  → Greed
  75-100 → Extreme Greed  (over-leveraged euphoria; elevated correction risk)

Cache TTL: 1 hour.  The index updates daily, so a 1-hour cache is already
over-fetching.  Fetch errors degrade gracefully: callers receive None-valued
dicts and skip the sentiment block in prompts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Arrr — the endpoint is free, has no auth, and is CORS-open. No .env entry needed.
_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_CACHE_TTL = timedelta(hours=1)
_REQUEST_TIMEOUT = 10.0  # seconds — slow DNS in container environments is the enemy


class SentimentService:
    """Wraps the Alternative.me Fear & Greed index with a simple in-memory cache."""

    def __init__(self) -> None:
        self._cached: Optional[dict] = None
        self._cached_at: Optional[datetime] = None

    async def fetch_fear_greed(self) -> dict:
        """Return the latest Fear & Greed data.

        Returns a dict guaranteed to have keys: ``value`` (int | None),
        ``label`` (str), ``timestamp`` (int | None).  On any failure the
        values are None/unknown so callers don't need to guard individually.
        """
        _empty = {"value": None, "label": "unknown", "timestamp": None}

        # Serve from cache if fresh enough
        if (
            self._cached is not None
            and self._cached_at is not None
            and (datetime.utcnow() - self._cached_at) < _CACHE_TTL
        ):
            return self._cached

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                resp = await client.get(_FNG_URL)
                resp.raise_for_status()
                payload = resp.json()

            entry = payload.get("data", [{}])[0]
            result = {
                "value": int(entry.get("value", 0)),
                "label": entry.get("value_classification", "unknown"),
                "timestamp": int(entry.get("timestamp", 0)) if entry.get("timestamp") else None,
            }
            self._cached = result
            self._cached_at = datetime.utcnow()
            logger.debug(
                f"F&G index fetched: {result['value']}/100 ({result['label']})"
            )
            return result

        except Exception as exc:
            # Arrr — degrade gracefully rather than blowing up the team analysis cycle.
            # The F&G block is additive; the system runs fine without it.
            logger.warning(f"Fear & Greed fetch failed ({exc}); sentiment block skipped")
            return _empty

    def build_llm_context_block(self, fg_data: dict) -> str:
        """Format Fear & Greed data as a prompt-ready context block.

        Returns an empty string when the value is unavailable so callers can
        safely concatenate without adding blank sections to prompts.
        """
        value = fg_data.get("value")
        label = fg_data.get("label", "unknown")

        if value is None:
            return ""

        # Contextual interpretation — give the LLM a head start, don't just
        # dump the raw number and expect it to reason about it unprompted.
        if value < 20:
            interpretation = (
                "⚠️ EXTREME FEAR: Retail panic-selling. Historically a contrarian long zone "
                "— track for capitulation signatures (volume spike + RSI extreme) before entering."
            )
        elif value < 40:
            interpretation = (
                "Market fear elevated. Expect weak hands exiting; potential accumulation by institutions. "
                "Mean-reversion and Wyckoff spring setups are higher-probability in this regime."
            )
        elif value < 60:
            interpretation = "Sentiment neutral. No strong behavioural bias; let technical signals lead."
        elif value < 80:
            interpretation = (
                "Greed present. Trend-following works, but watch for over-extension. "
                "Tighten take-profit levels and avoid chasing breakouts at resistance."
            )
        else:
            interpretation = (
                "⚠️ EXTREME GREED: Over-leveraged euphoria. Elevated risk of sharp correction or "
                "long-squeeze. Favour short or mean-reversion setups; use tighter stops on longs."
            )

        return (
            f"\nFEAR & GREED INDEX: {value}/100 — {label}\n"
            f"{interpretation}\n"
        )


# Module-level singleton — the scheduler and services import this instance directly
sentiment_service = SentimentService()
