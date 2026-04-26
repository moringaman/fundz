"""
Regime service — orchestrates GMM fits, persistence, and per-symbol predictions.

Why this exists
---------------
`regime_model.py` is a pure stats module. This service wraps it with:
  - candle fetching (Phemex client)
  - DB persistence to RegimeStateRecord
  - in-process model cache
  - daily refit cadence guard

The research analyst calls `get_latest_prediction(symbol)` which is cheap (DB
or cache hit). The scheduler calls `refit_all(symbols)` once a day.

Hidden assumptions
------------------
- We refit on the most recent 5000 1h candles. ~7 months of context. Enough to
  capture multiple regime transitions; short enough that ancient bull markets
  don't dominate the fit.
- Refit cadence is daily. Faster doesn't help (regimes don't shift in hours);
  slower lets stale data hurt the prior.
- Anchor symbol is BTCUSDT. Other symbols get their own fits but the research
  analyst uses BTC as the macro prior — alts mostly follow BTC at the macro
  level so a per-alt regime call is noisier than useful for top-down assessment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import desc, select

from app.clients.phemex import PhemexClient
from app.config import settings
from app.database import get_async_session
from app.services import regime_model
from app.services.regime_model import (
    DEFAULT_VOL_WINDOW,
    FittedRegimeModel,
    MIN_FIT_CANDLES,
    REGIME_LABELS,
    fit_gmm,
    predict_regime,
)

logger = logging.getLogger(__name__)


# Refit cadence guard. We don't want a "missed yesterday's refit, do five today"
# stampede; this floor ensures at most one refit per symbol per ~23h window.
_MIN_REFIT_INTERVAL_HOURS = 23
_DEFAULT_FIT_CANDLES = 5000
_DEFAULT_TIMEFRAME = "1h"
# Symbols Marina narrates over. Keep this list short — each refit is a Phemex
# fetch + sklearn fit (a few hundred ms but compounds across symbols).
DEFAULT_REFIT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


class RegimeService:
    def __init__(self):
        # Lazy phemex client; only instantiated when we actually fetch candles
        # so unit tests can stub get_latest_prediction without provoking auth.
        self._phemex: Optional[PhemexClient] = None
        # Per-symbol last-refit timestamps for the cadence guard.
        self._last_refit: Dict[str, datetime] = {}

    @property
    def phemex(self) -> PhemexClient:
        if self._phemex is None:
            self._phemex = PhemexClient(
                api_key=settings.phemex_api_key,
                api_secret=settings.phemex_api_secret,
                testnet=settings.phemex_testnet,
            )
        return self._phemex

    async def _fetch_closes(
        self,
        symbol: str,
        timeframe: str = _DEFAULT_TIMEFRAME,
        limit: int = _DEFAULT_FIT_CANDLES,
    ) -> pd.Series:
        """Fetch close prices ascending; index by sequential int (timestamp not
        needed for the GMM features). Returns empty Series on failure so the
        caller can fall back to the default regime."""
        rows = await self.phemex.get_klines(symbol=symbol, interval=timeframe, limit=limit)
        if not rows:
            return pd.Series(dtype=float)
        # Phemex/Binance kline shape: index 4 is close. May be str or numeric.
        closes: List[float] = []
        for r in rows:
            try:
                closes.append(float(r[4]))
            except (IndexError, TypeError, ValueError):
                continue
        return pd.Series(closes, dtype=float)

    async def _load_prior_centroids(self, symbol: str) -> Optional[Dict[str, List[float]]]:
        """Load most recent label_centroids for warm-start label stability."""
        try:
            from app.models import RegimeStateRecord
            async with get_async_session() as session:
                q = (
                    select(RegimeStateRecord)
                    .where(RegimeStateRecord.symbol == symbol)
                    .order_by(desc(RegimeStateRecord.created_at))
                    .limit(1)
                )
                res = await session.execute(q)
                row = res.scalar_one_or_none()
                if row and row.label_centroids:
                    return dict(row.label_centroids)
        except Exception as e:
            logger.debug(f"Prior centroid load failed for {symbol}: {e}")
        return None

    async def refit_symbol(
        self,
        symbol: str,
        *,
        force: bool = False,
        timeframe: str = _DEFAULT_TIMEFRAME,
        limit: int = _DEFAULT_FIT_CANDLES,
    ) -> Optional[Dict]:
        """Refit GMM for `symbol`, predict latest regime, persist, return summary.

        Returns None when the cadence guard skips, fetch fails, or fit fails.
        Caller should treat None as "no new prediction; reuse latest persisted".
        """
        now = datetime.now(timezone.utc)
        last = self._last_refit.get(symbol)
        if not force and last is not None:
            hours_since = (now - last).total_seconds() / 3600.0
            if hours_since < _MIN_REFIT_INTERVAL_HOURS:
                logger.debug(
                    f"Regime refit skipped for {symbol}: only {hours_since:.1f}h since last refit"
                )
                return None

        closes = await self._fetch_closes(symbol, timeframe=timeframe, limit=limit)
        if len(closes) < MIN_FIT_CANDLES:
            logger.warning(
                f"Regime refit aborted for {symbol}: only {len(closes)} candles fetched, "
                f"need {MIN_FIT_CANDLES}"
            )
            return None

        prior_centroids = await self._load_prior_centroids(symbol)

        try:
            model = fit_gmm(
                closes,
                prior_centroids=prior_centroids,
                vol_window=DEFAULT_VOL_WINDOW,
            )
        except Exception as e:
            logger.error(f"GMM fit failed for {symbol}: {e}")
            return None

        try:
            prediction = predict_regime(model, closes, timestamp=now.isoformat())
        except Exception as e:
            logger.error(f"GMM predict failed for {symbol}: {e}")
            return None

        regime_model.cache_model(symbol, model)
        self._last_refit[symbol] = now

        await self._persist_state(symbol, timeframe, model, prediction)

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'regime_label': prediction.label,
            'confidence': prediction.confidence,
            'posteriors': prediction.posteriors,
            'fingerprint': model.fingerprint,
            'n_samples_fit': model.n_samples_fit,
        }

    async def _persist_state(
        self,
        symbol: str,
        timeframe: str,
        model: FittedRegimeModel,
        prediction,
    ) -> None:
        """Write a RegimeStateRecord row. Best-effort; swallows DB errors so a
        broken DB doesn't tank the scheduler tick."""
        try:
            from app.models import RegimeStateRecord
            async with get_async_session() as session:
                row = RegimeStateRecord(
                    symbol=symbol,
                    timeframe=timeframe,
                    regime_label=prediction.label,
                    confidence=prediction.confidence,
                    posteriors=prediction.posteriors,
                    label_centroids=model.label_centroids,
                    model_fingerprint=model.fingerprint,
                    n_samples_fit=model.n_samples_fit,
                    feature_window=model.feature_window,
                )
                session.add(row)
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist regime state for {symbol}: {e}")

    async def get_latest_prediction(self, symbol: str) -> Optional[Dict]:
        """Return the most recent persisted regime classification for `symbol`.

        Used by the research analyst as a prior. Cheap — single indexed DB
        lookup. Returns None when no fit has happened yet.
        """
        try:
            from app.models import RegimeStateRecord
            async with get_async_session() as session:
                q = (
                    select(RegimeStateRecord)
                    .where(RegimeStateRecord.symbol == symbol)
                    .order_by(desc(RegimeStateRecord.created_at))
                    .limit(1)
                )
                res = await session.execute(q)
                row = res.scalar_one_or_none()
                if row is None:
                    return None
                return {
                    'symbol': row.symbol,
                    'timeframe': row.timeframe,
                    'regime_label': row.regime_label,
                    'confidence': row.confidence,
                    'posteriors': dict(row.posteriors) if row.posteriors else {},
                    'fingerprint': row.model_fingerprint,
                    'created_at': row.created_at.isoformat() if row.created_at else None,
                }
        except Exception as e:
            logger.debug(f"get_latest_prediction failed for {symbol}: {e}")
            return None

    async def refit_all(
        self,
        symbols: Optional[List[str]] = None,
        *,
        force: bool = False,
    ) -> List[Dict]:
        """Refit every symbol in `symbols` (defaults to BTC + ETH). Sequential
        so we don't hammer the Phemex API; each refit is fast enough that
        parallelism isn't worth the complexity."""
        symbols = symbols or DEFAULT_REFIT_SYMBOLS
        results: List[Dict] = []
        for sym in symbols:
            try:
                summary = await self.refit_symbol(sym, force=force)
                if summary is not None:
                    results.append(summary)
            except Exception as e:
                logger.error(f"Refit loop failed for {sym}: {e}")
        return results


regime_service = RegimeService()
