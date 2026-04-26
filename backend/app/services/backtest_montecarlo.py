"""
Monte Carlo bootstrap of backtest trade PnL — Phase 1 of quant rigour rollout.

Why this exists
---------------
The deterministic backtest in `BacktestEngine` produces a single equity path. That
path's final Sharpe and max drawdown are point estimates with zero uncertainty
quantification — a "Sharpe 1.8" strategy could just as easily be the lucky tail
of a true Sharpe 0.4 distribution. This module resamples the realised per-trade
PnL with replacement, replays N equity curves, and reports the percentile band.

Methodology
-----------
- Trade-level bootstrap (not bar-level). We assume trade outcomes are
  exchangeable. This is the standard approach for trade-frequency backtests
  and avoids re-running the strategy logic N times. Arrr — the cost is that
  it ignores serial correlation between trades; for highly path-dependent
  strategies (e.g. trailing stops conditioned on prior wins) this is an
  optimistic uncertainty estimate. Documented limitation, not a bug.
- Sharpe ratio computed on per-trade percentage returns (PnL / prior equity)
  and scaled by sqrt(n_trades), so it's directly comparable across simulations
  but NOT comparable to the annualised Sharpe in `_calculate_metrics`.
  Percentile ranks across simulations are what matter, not absolute level.

Hidden assumption: the trade list passed in must be the deterministic backtest's
EXIT records (each with a `net_pnl` or `pnl`). Sequence order is irrelevant —
bootstrap doesn't care.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# Cap total samples cells (n_simulations × n_trades) to avoid blowing memory on
# pathological backtests with thousands of trades. 10M floats ≈ 80MB per array,
# and we hold two simultaneously (samples + paths). Tuned for backend RAM budget.
_MAX_SAMPLE_CELLS = 10_000_000
_DEFAULT_BAND_POINTS = 200


def _adaptive_n_simulations(n_trades: int) -> int:
    """Scale simulation count to trade count.

    Few trades → less information per resample → fewer sims add little.
    Many trades → already low variance per path → cap at 10k for runtime.
    Floor of 1000 so even tiny backtests get usable percentiles.
    """
    return int(min(10_000, max(1_000, 100 * n_trades)))


def bootstrap_equity_paths(
    trades: List[Dict],
    initial_balance: float,
    n_simulations: Optional[int] = None,
    seed: Optional[int] = None,
    band_points: int = _DEFAULT_BAND_POINTS,
) -> Optional[Dict]:
    """Resample trade PnLs with replacement and return percentile-banded equity paths.

    Parameters
    ----------
    trades : list of trade dicts
        Both ENTRY and EXIT records accepted; only EXITs are used. Each EXIT
        must carry a numeric `net_pnl` (preferred) or `pnl`.
    initial_balance : float
        Starting equity for each simulated path.
    n_simulations : int, optional
        Number of bootstrap paths. Defaults to `_adaptive_n_simulations(n_trades)`.
    seed : int, optional
        Seed for `numpy.random.default_rng`. Provide for reproducible tests.
    band_points : int
        Number of evenly-spaced points along the trade axis to keep in the
        returned equity bands. Storage/transport optimisation — full N×T arrays
        are too fat for JSON.

    Returns
    -------
    dict | None
        None when there are zero EXIT trades (caller handles). Otherwise a dict
        with `final_pnl`, `max_drawdown`, `sharpe` percentile maps and an
        `equity_bands` block containing trade-axis indices and p5/p25/p50/p75/p95
        equity values. Final structure is JSON-serialisable.
    """
    exit_trades = [t for t in trades if t.get('type') == 'EXIT']
    pnls = np.array(
        [float(t.get('net_pnl', t.get('pnl', 0.0))) for t in exit_trades],
        dtype=np.float64,
    )
    n = len(pnls)
    if n == 0:
        return None

    if n_simulations is None or n_simulations <= 0:
        n_simulations = _adaptive_n_simulations(n)

    # Memory guard — degrade simulation count rather than OOM the backend.
    # We'd rather report MC on 2k sims than skip it entirely.
    if n_simulations * n > _MAX_SAMPLE_CELLS:
        capped = max(1_000, _MAX_SAMPLE_CELLS // n)
        logger.warning(
            "Bootstrap memory cap hit: n_trades=%d, requested n_sim=%d, capping to %d",
            n, n_simulations, capped,
        )
        n_simulations = capped

    rng = np.random.default_rng(seed)
    # samples shape: (n_simulations, n_trades)
    samples = rng.choice(pnls, size=(n_simulations, n), replace=True)

    # paths shape: (n_simulations, n_trades + 1) — column 0 is the starting balance
    paths = np.empty((n_simulations, n + 1), dtype=np.float64)
    paths[:, 0] = initial_balance
    paths[:, 1:] = initial_balance + np.cumsum(samples, axis=1)

    final_balances = paths[:, -1]
    final_pnl_arr = final_balances - initial_balance

    # Max drawdown per simulation: peak-to-trough as a fraction of the running peak.
    # Floor running-max at 1.0 to avoid div-by-zero if a simulation hits zero/negative
    # equity. Ye treat insolvent sims as 100% drawdown anyway, which the formula
    # produces correctly when peak still exceeds current.
    running_max = np.maximum.accumulate(paths, axis=1)
    safe_peak = np.where(running_max > 0, running_max, 1.0)
    drawdowns = (running_max - paths) / safe_peak
    max_dd_per_sim = drawdowns.max(axis=1)

    # Per-trade Sharpe-like statistic: mean_return / std_return × sqrt(n).
    # NOT comparable to annualised Sharpe — this is purely a within-bootstrap
    # ranking metric. Documented in module docstring.
    prev_equity = paths[:, :-1]
    safe_prev = np.where(prev_equity > 0, prev_equity, 1.0)
    per_trade_returns = samples / safe_prev
    mean_r = per_trade_returns.mean(axis=1)
    if n > 1:
        std_r = per_trade_returns.std(axis=1, ddof=1)
    else:
        # Single-trade backtest — Sharpe undefined, return zeros across the board
        std_r = np.zeros(n_simulations, dtype=np.float64)
    # np.where evaluates BOTH branches eagerly; the false branch divides by zero
    # std_r values that the mask discards. Suppress the warning rather than
    # restructure into a masked compute, which would cost more than it's worth.
    with np.errstate(divide='ignore', invalid='ignore'):
        sharpe_per_sim = np.where(std_r > 0, mean_r / std_r * np.sqrt(n), 0.0)

    pcts = [5, 25, 50, 75, 95]
    final_pcts = np.percentile(final_pnl_arr, pcts)
    dd_pcts = np.percentile(max_dd_per_sim, pcts)
    sharpe_pcts = np.percentile(sharpe_per_sim, pcts)

    # Downsample equity bands along the trade axis.
    # If trade count is small, send every point; otherwise pick evenly-spaced indices.
    if n + 1 <= band_points:
        idx = np.arange(n + 1)
    else:
        idx = np.linspace(0, n, band_points, dtype=np.int64)
    sampled_paths = paths[:, idx]
    band_pcts = np.percentile(sampled_paths, pcts, axis=0)

    def _pct_dict(arr: np.ndarray) -> Dict[str, float]:
        return {f"p{p}": float(v) for p, v in zip(pcts, arr)}

    return {
        'n_simulations': int(n_simulations),
        'n_trades': int(n),
        'seed': seed,
        'final_pnl':    _pct_dict(final_pcts),
        'max_drawdown': _pct_dict(dd_pcts),
        'sharpe':       _pct_dict(sharpe_pcts),
        'equity_bands': {
            'trade_index': idx.tolist(),
            'p5':  band_pcts[0].tolist(),
            'p25': band_pcts[1].tolist(),
            'p50': band_pcts[2].tolist(),
            'p75': band_pcts[3].tolist(),
            'p95': band_pcts[4].tolist(),
        },
    }
