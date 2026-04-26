"""
Walk-forward analysis — Phase 2 of quant rigour rollout.

Why this exists
---------------
A single backtest over 5000 candles produces one Sharpe and one PnL. If the
strategy was hand-tuned (by humans choosing SL/TP/strategy_type) on roughly the
same period, its in-sample numbers are inflated. Walk-forward splits the data
into sequential train/test chunks and re-runs the backtest per chunk, exposing
how the strategy actually performs in periods the developer didn't see when
choosing parameters.

Methodology
-----------
- **Anchored (expanding)** mode: window i uses candles [0 : (i+1)·chunk] as
  IS and [(i+1)·chunk : (i+2)·chunk] as OOS. The IS window grows; the OOS
  window steps forward in non-overlapping chunks. Standard for trading
  strategies where more history is generally better for indicator warmup.
- **Rolling** mode: window i uses [i·chunk : (i+1)·chunk] as IS — fixed
  width, walks forward. Catches regime changes that anchored mode would
  smooth over.
- Parameters are held constant across windows. We do NOT refit per window;
  that would require defining a tuning procedure and is a separate phase.
  The "degradation ratio" therefore measures stability of a fixed strategy
  across time, not generalisation of a tuning procedure. Documented
  limitation, not a bug.

Hidden assumption
-----------------
Each window needs ≥500 candles to satisfy `BacktestEngine`'s indicator warmup
requirement. If the requested `n_windows` would produce thinner chunks, we
auto-reduce. If even `n_windows=1` can't meet the floor, we raise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Literal, Optional

import numpy as np

from app.services.backtest import BacktestConfig, BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


# Backtest engine needs ~150 warmup candles for long-period indicators. We use
# 500 as the operational minimum for "real" Sharpe stability; below that the
# per-window numbers be too noisy to compare.
_MIN_CANDLES_PER_WINDOW = 500


@dataclass
class WindowResult:
    """Per-window IS + OOS metrics. JSON-serialisable via asdict."""
    window_index: int
    is_start: int          # candle index (inclusive)
    is_end: int            # candle index (exclusive)
    oos_start: int
    oos_end: int
    is_sharpe: float
    is_net_pnl: float
    is_win_rate: float
    is_total_trades: int
    is_max_drawdown: float
    oos_sharpe: float
    oos_net_pnl: float
    oos_win_rate: float
    oos_total_trades: int
    oos_max_drawdown: float
    # Concatenated OOS equity tail — useful for stitching a global OOS curve.
    # Stored downsampled to the last value only; full curve is not persisted
    # per window to keep the JSON blob sane.
    oos_final_balance: float = 0.0


@dataclass
class WalkForwardResult:
    """Aggregate output. `windows` carries per-window detail, the rest is summary."""
    mode: str                          # "anchored" | "rolling"
    n_windows: int
    chunk_size: int                    # candles per chunk
    total_candles: int
    windows: List[WindowResult] = field(default_factory=list)
    # Mean Sharpe across windows. NaN when zero windows produced trades.
    mean_is_sharpe: float = 0.0
    mean_oos_sharpe: float = 0.0
    # OOS / IS Sharpe ratio. The headline number. >0.7 green, 0.4-0.7 amber,
    # <0.4 red. NaN-safe via guard.
    degradation_ratio: float = 0.0
    # Stitched OOS equity curve (concatenated across windows, normalised so the
    # first window's starting balance is config.initial_balance and subsequent
    # windows compound off the prior OOS final balance).
    oos_equity_curve: List[float] = field(default_factory=list)
    # OOS max drawdown across the stitched curve — different from per-window
    # max DD because it captures cross-window drawdowns the windows wouldn't
    # see in isolation.
    oos_max_drawdown: float = 0.0

    def to_dict(self) -> Dict:
        out = asdict(self)
        # asdict already converts WindowResult dataclasses; nothing else needed
        return out


def _compute_chunking(
    n_candles: int,
    n_windows: int,
) -> tuple[int, int]:
    """Return (effective_n_windows, chunk_size). Auto-degrade rather than fail.

    We need n_windows OOS chunks plus at least one IS chunk → n_windows+1 chunks.
    Each chunk must be ≥ _MIN_CANDLES_PER_WINDOW. Reduce n_windows until it fits.
    """
    if n_candles < _MIN_CANDLES_PER_WINDOW * 2:
        raise ValueError(
            f"Need at least {_MIN_CANDLES_PER_WINDOW * 2} candles for walk-forward, "
            f"got {n_candles}"
        )

    while n_windows >= 1:
        chunk = n_candles // (n_windows + 1)
        if chunk >= _MIN_CANDLES_PER_WINDOW:
            return n_windows, chunk
        n_windows -= 1

    # Fallback — even 1 window can't meet the floor. Shouldn't happen given
    # the upfront check, but defend anyway.
    raise ValueError(
        f"Cannot fit any walk-forward window of {_MIN_CANDLES_PER_WINDOW}+ candles in {n_candles}"
    )


def _aggregate_windows(
    windows: List[WindowResult],
    initial_balance: float,
) -> tuple[float, float, float, List[float], float]:
    """Compute mean Sharpes, degradation ratio, stitched OOS curve and DD.

    Stitched OOS equity: each window starts from the prior window's final
    balance, so the curve compounds chronologically. First window starts at
    initial_balance.

    Returns (mean_is, mean_oos, degradation, oos_curve, oos_max_dd).
    """
    if not windows:
        return 0.0, 0.0, 0.0, [], 0.0

    is_sharpes = np.array([w.is_sharpe for w in windows], dtype=np.float64)
    oos_sharpes = np.array([w.oos_sharpe for w in windows], dtype=np.float64)
    mean_is = float(np.nanmean(is_sharpes)) if is_sharpes.size else 0.0
    mean_oos = float(np.nanmean(oos_sharpes)) if oos_sharpes.size else 0.0

    mean_is = mean_is if np.isfinite(mean_is) else 0.0
    mean_oos = mean_oos if np.isfinite(mean_oos) else 0.0

    # Degradation ratio: positive IS Sharpe is the "claim", OOS Sharpe is the
    # "delivery". Negative or zero IS Sharpe makes the ratio meaningless.
    if mean_is > 0:
        degradation = mean_oos / mean_is
    else:
        degradation = 0.0

    degradation = degradation if np.isfinite(degradation) else 0.0

    # Stitched OOS curve: walk through window OOS final balances, scaling each
    # to start where the previous one ended. Each window ran on initial_balance
    # in isolation, so we apply the per-window return ratio.
    # Arrr — this assumes each window had at least one trade; if a window
    # produced zero trades, its final balance equals initial_balance and the
    # ratio is 1.0, which is correct (flat line for that window).
    oos_curve: List[float] = [initial_balance]
    for w in windows:
        ratio = (w.oos_final_balance / initial_balance) if initial_balance > 0 else 1.0
        next_balance = oos_curve[-1] * ratio
        if np.isfinite(next_balance):
            oos_curve.append(next_balance)

    # Max DD on the stitched curve
    if len(oos_curve) > 1:
        arr = np.array(oos_curve, dtype=np.float64)
        arr = arr[np.isfinite(arr)] if len(arr[np.isfinite(arr)]) > 0 else np.array([initial_balance])
        running_max = np.maximum.accumulate(arr)
        safe_peak = np.where(running_max > 0, running_max, 1.0)
        oos_max_dd = float(((running_max - arr) / safe_peak).max())
    else:
        oos_max_dd = 0.0

    oos_max_dd = oos_max_dd if np.isfinite(oos_max_dd) else 0.0

    return mean_is, mean_oos, degradation, oos_curve, oos_max_dd


async def run_walk_forward(
    config: BacktestConfig,
    n_windows: int = 6,
    mode: Literal["anchored", "rolling"] = "anchored",
    engine: Optional[BacktestEngine] = None,
) -> WalkForwardResult:
    """Run walk-forward analysis. Fetches candles once, slices per window.

    Parameters
    ----------
    config : BacktestConfig
        Strategy + data window. MC bootstrap inside per-window backtests is
        force-disabled — uncertainty bands per window add noise without
        signal. The full-period MC stays available via the standard backtest.
    n_windows : int
        Requested OOS window count. Auto-degraded if data is too thin.
    mode : "anchored" | "rolling"
        See module docstring.
    engine : BacktestEngine, optional
        Inject for tests. Defaults to the singleton from `app.services.backtest`.
    """
    from app.services.backtest import backtest_engine as default_engine
    engine = engine or default_engine

    # Fetch once. Per-window backtests reuse this list.
    klines = await engine._fetch_historical_data(config)
    if not klines:
        raise ValueError(f"No historical data returned for {config.symbol} {config.interval}")

    n_candles = len(klines)
    eff_windows, chunk = _compute_chunking(n_candles, n_windows)
    if eff_windows < n_windows:
        logger.warning(
            "Walk-forward auto-reduced windows from %d to %d due to candle count %d",
            n_windows, eff_windows, n_candles,
        )

    # Disable MC inside per-window runs — we want speed, not nested uncertainty.
    # The caller's MC settings still apply to the full-period backtest if they
    # run that separately.
    window_config = BacktestConfig(**{**config.__dict__, 'run_montecarlo': False})

    windows: List[WindowResult] = []
    for i in range(eff_windows):
        if mode == "anchored":
            is_start = 0
            is_end = (i + 1) * chunk
        else:  # rolling
            is_start = i * chunk
            is_end = (i + 1) * chunk
        oos_start = is_end
        oos_end = oos_start + chunk
        # Last window: take all remaining candles for OOS so we don't drop tail data
        if i == eff_windows - 1:
            oos_end = n_candles

        is_slice = klines[is_start:is_end]
        oos_slice = klines[oos_start:oos_end]

        try:
            is_result = await engine.run_backtest(window_config, _klines=is_slice)
            oos_result = await engine.run_backtest(window_config, _klines=oos_slice)
        except ValueError as exc:
            # Window too thin or other backtest issue — record zeros and continue.
            # Better than aborting the whole walk-forward over one bad chunk.
            logger.warning("Walk-forward window %d failed: %s", i, exc)
            windows.append(WindowResult(
                window_index=i,
                is_start=is_start, is_end=is_end,
                oos_start=oos_start, oos_end=oos_end,
                is_sharpe=0.0, is_net_pnl=0.0, is_win_rate=0.0,
                is_total_trades=0, is_max_drawdown=0.0,
                oos_sharpe=0.0, oos_net_pnl=0.0, oos_win_rate=0.0,
                oos_total_trades=0, oos_max_drawdown=0.0,
                oos_final_balance=config.initial_balance,
            ))
            continue

        oos_final = (
            oos_result.equity_curve[-1] if oos_result.equity_curve else config.initial_balance
        )
        windows.append(WindowResult(
            window_index=i,
            is_start=is_start, is_end=is_end,
            oos_start=oos_start, oos_end=oos_end,
            is_sharpe=is_result.sharpe_ratio,
            is_net_pnl=is_result.net_pnl,
            is_win_rate=is_result.win_rate,
            is_total_trades=is_result.total_trades,
            is_max_drawdown=is_result.max_drawdown,
            oos_sharpe=oos_result.sharpe_ratio,
            oos_net_pnl=oos_result.net_pnl,
            oos_win_rate=oos_result.win_rate,
            oos_total_trades=oos_result.total_trades,
            oos_max_drawdown=oos_result.max_drawdown,
            oos_final_balance=oos_final,
        ))

    mean_is, mean_oos, degradation, oos_curve, oos_max_dd = _aggregate_windows(
        windows, config.initial_balance
    )

    return WalkForwardResult(
        mode=mode,
        n_windows=eff_windows,
        chunk_size=chunk,
        total_candles=n_candles,
        windows=windows,
        mean_is_sharpe=mean_is,
        mean_oos_sharpe=mean_oos,
        degradation_ratio=degradation,
        oos_equity_curve=oos_curve,
        oos_max_drawdown=oos_max_dd,
    )
