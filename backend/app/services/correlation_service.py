"""
Cross-symbol correlation service for portfolio risk gating.

Why this exists
---------------
The existing per-direction concentration check sums all longs (or shorts)
across the fund and gates on raw notional. It treats $X long in BTC + ETH +
SOL identically to $X long across uncorrelated assets — but BTC/ETH/SOL daily
correlation is ~0.85, so three "independent" trades at $X each are
effectively one ~3X-sized bet on crypto beta.

This module turns that flat sum into a **principal-component-equivalent**
exposure using the standard portfolio-variance formula

        σ²_portfolio = Σᵢ Σⱼ ρᵢⱼ · wᵢ · wⱼ

where wᵢ is signed fractional notional (positive long, negative short) and ρ
is the rolling correlation matrix of daily log-returns. The square root of
that sum is the **effective concentration** as a fraction of capital — it
collapses to Σ|wᵢ| when correlations are 1.0 (one giant bet) and shrinks
toward √Σwᵢ² as correlations approach 0 (genuinely diversified).

Refresh cadence: daily. Crypto correlation regimes shift across weeks/months,
not hours, and each refit is N×Phemex fetches + a small numpy op.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


CORRELATION_REFRESH_HOURS = 23
CORRELATION_LOOKBACK_DAYS = 90
DEFAULT_TIMEFRAME = "1d"
MIN_RETURN_OBSERVATIONS = 30
DEFAULT_FALLBACK_CORRELATION = 0.65


@dataclass
class CorrelationMatrix:
    symbols: List[str]
    matrix: np.ndarray
    last_refresh: datetime
    observations: int

    def correlation(self, sym1: str, sym2: str) -> Optional[float]:
        if sym1 == sym2:
            return 1.0
        try:
            i = self.symbols.index(sym1)
            j = self.symbols.index(sym2)
        except ValueError:
            return None
        return float(self.matrix[i, j])


@dataclass
class CorrelatedExposure:
    effective_pct: float
    raw_long_pct: float
    raw_short_pct: float
    weighted_pairs: List[Tuple[str, str, float, float]]


class CorrelationService:
    def __init__(self):
        self._matrix: Optional[CorrelationMatrix] = None
        self._refresh_lock = asyncio.Lock()
        self._phemex = None

    @property
    def matrix(self) -> Optional[CorrelationMatrix]:
        return self._matrix

    def _get_phemex(self):
        if self._phemex is None:
            from app.clients.phemex import PhemexClient
            from app.config import settings as _cfg
            self._phemex = PhemexClient(
                api_key=_cfg.phemex_api_key,
                api_secret=_cfg.phemex_api_secret,
                testnet=_cfg.phemex_testnet,
            )
        return self._phemex

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        if self._matrix is None:
            return True
        ref_now = now or datetime.now(timezone.utc)
        age = ref_now - self._matrix.last_refresh
        return age >= timedelta(hours=CORRELATION_REFRESH_HOURS)

    async def _fetch_closes(self, symbol: str) -> List[float]:
        try:
            rows = await self._get_phemex().get_klines(
                symbol=symbol,
                interval=DEFAULT_TIMEFRAME,
                limit=CORRELATION_LOOKBACK_DAYS + 5,
            )
        except Exception as e:
            logger.debug(f"correlation: kline fetch failed for {symbol}: {e}")
            return []
        if not rows:
            return []
        closes: List[float] = []
        for r in rows:
            for idx in (5, 4):
                try:
                    closes.append(float(r[idx]))
                    break
                except (IndexError, TypeError, ValueError):
                    continue
        return closes

    @staticmethod
    def _log_returns(closes: List[float]) -> np.ndarray:
        if len(closes) < 2:
            return np.array([], dtype=float)
        arr = np.asarray(closes, dtype=float)
        arr = arr[arr > 0]
        if arr.size < 2:
            return np.array([], dtype=float)
        return np.diff(np.log(arr))

    async def refresh(self, symbols: Iterable[str], force: bool = False) -> Optional[CorrelationMatrix]:
        symbols = sorted({s for s in symbols if s})
        if not symbols:
            return self._matrix

        async with self._refresh_lock:
            if not force and not self.is_stale():
                return self._matrix

            tasks = [self._fetch_closes(s) for s in symbols]
            closes_per_symbol = await asyncio.gather(*tasks, return_exceptions=True)

            return_series: Dict[str, np.ndarray] = {}
            for symbol, closes in zip(symbols, closes_per_symbol):
                if isinstance(closes, Exception):
                    continue
                rets = self._log_returns(closes)
                if rets.size >= MIN_RETURN_OBSERVATIONS:
                    return_series[symbol] = rets

            if len(return_series) < 2:
                logger.warning(
                    f"correlation: insufficient data to build matrix "
                    f"({len(return_series)} symbols had ≥{MIN_RETURN_OBSERVATIONS} obs)"
                )
                return self._matrix

            min_len = min(r.size for r in return_series.values())
            ordered_syms = sorted(return_series.keys())
            data = np.vstack([return_series[s][-min_len:] for s in ordered_syms])
            try:
                corr = np.corrcoef(data)
            except Exception as e:
                logger.warning(f"correlation: np.corrcoef failed: {e}")
                return self._matrix

            corr = np.where(np.isnan(corr), 0.0, corr)
            self._matrix = CorrelationMatrix(
                symbols=ordered_syms,
                matrix=corr,
                last_refresh=datetime.now(timezone.utc),
                observations=min_len,
            )
            logger.info(
                f"correlation: refreshed matrix for {len(ordered_syms)} symbols "
                f"with {min_len} daily-return observations"
            )
            return self._matrix

    def lookup(self, sym1: str, sym2: str, fallback: float = DEFAULT_FALLBACK_CORRELATION) -> float:
        if sym1 == sym2:
            return 1.0
        if self._matrix is None:
            return fallback
        rho = self._matrix.correlation(sym1, sym2)
        return rho if rho is not None else fallback

    def compute_correlated_exposure(
        self,
        positions: List[Dict],
        intended_trade: Optional[Dict] = None,
        total_capital: float = 0.0,
        fallback_correlation: float = DEFAULT_FALLBACK_CORRELATION,
    ) -> CorrelatedExposure:
        if total_capital <= 0:
            return CorrelatedExposure(
                effective_pct=0.0, raw_long_pct=0.0, raw_short_pct=0.0, weighted_pairs=[]
            )

        weights: Dict[str, float] = {}
        long_notional = 0.0
        short_notional = 0.0

        for p in positions:
            symbol = p.get("symbol")
            if not symbol:
                continue
            side = (p.get("side") or "").lower()
            margin = p.get("margin_used")
            if margin is None or margin <= 0:
                qty = p.get("quantity") or 0
                price = p.get("current_price") or p.get("entry_price") or 0
                lev = max(p.get("leverage") or 1.0, 1.0)
                notional = qty * price
                margin = notional / lev
            if margin <= 0:
                continue
            sign = 1.0 if side in ("buy", "long") else (-1.0 if side in ("sell", "short") else 0.0)
            if sign == 0.0:
                continue
            weights[symbol] = weights.get(symbol, 0.0) + sign * margin
            if sign > 0:
                long_notional += margin
            else:
                short_notional += margin

        if intended_trade:
            symbol = intended_trade.get("symbol")
            margin = float(intended_trade.get("margin", 0.0) or 0.0)
            side = (intended_trade.get("side") or "").lower()
            sign = 1.0 if side in ("buy", "long") else (-1.0 if side in ("sell", "short") else 0.0)
            if symbol and margin > 0 and sign != 0.0:
                weights[symbol] = weights.get(symbol, 0.0) + sign * margin
                if sign > 0:
                    long_notional += margin
                else:
                    short_notional += margin

        if not weights:
            return CorrelatedExposure(
                effective_pct=0.0, raw_long_pct=0.0, raw_short_pct=0.0, weighted_pairs=[]
            )

        symbols = list(weights.keys())
        w_vec = np.array([weights[s] / total_capital for s in symbols], dtype=float)

        n = len(symbols)
        rho = np.eye(n)
        weighted_pairs: List[Tuple[str, str, float, float]] = []
        for i in range(n):
            for j in range(i + 1, n):
                r = self.lookup(symbols[i], symbols[j], fallback=fallback_correlation)
                rho[i, j] = r
                rho[j, i] = r
                weighted_pairs.append((symbols[i], symbols[j], r, w_vec[i] * w_vec[j]))

        variance = float(w_vec @ rho @ w_vec)
        variance = max(variance, 0.0)
        effective_pct = math.sqrt(variance) * 100.0

        return CorrelatedExposure(
            effective_pct=effective_pct,
            raw_long_pct=long_notional / total_capital * 100.0,
            raw_short_pct=short_notional / total_capital * 100.0,
            weighted_pairs=weighted_pairs,
        )


correlation_service = CorrelationService()
