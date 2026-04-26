"""
Strategy sensitivity analysis — Phase 3 of quant rigour rollout.

Why this exists
---------------
Walk-forward (Phase 2) tells ye if the strategy degrades over time. Sensitivity
tells ye if the chosen parameters sit on a plateau or a knife-edge: do small
nudges to SL/TP/position-size produce small Sharpe changes, or does the surface
fall off a cliff a percentage point away from production config?

Methodology
-----------
- Sweep two axes (e.g. SL × TP) over user-specified value lists. Run a backtest
  per cell, returning Sharpe / net PnL / max DD / win rate / trade count.
- "Stability score" = std-dev of Sharpe across the immediate Moore neighbourhood
  (up to 8 cells around the chosen-param cell). Low std → plateau (good), high
  std → knife-edge (fragile). Returned alongside the chosen-cell metrics.
- Total cells capped at 25 (5×5) by default to bound runtime. Caller can raise
  the cap explicitly.

Hidden assumptions
------------------
- The two sweep axes correspond to fields `BacktestConfig` accepts: one of
  `position_size`, `stop_loss`, `take_profit`. Other axes raise ValueError.
- "Chosen params" snap to the nearest sweep value on each axis. If yer
  production param falls between sweep values, ye won't see exactly that cell;
  the star marker on the heatmap shows the closest.
- Stability uses Sharpe only. Adding a multi-metric stability score would be
  legitimate but is deferred — Sharpe is what the gate consumers use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.services.backtest import BacktestEngine

logger = logging.getLogger(__name__)


# Axes the engine actually consumes via BacktestConfig. Validate against this so
# typos surface as 400s instead of silent ignored-cell sweeps.
_SUPPORTED_AXES = {"position_size", "stop_loss", "take_profit"}
# Total grid size cap. 5×5 = 25 cells, ~ 25 backtests. At ~1s each this is the
# upper bound for an interactive request; raise via param if ye accept the wait.
_DEFAULT_MAX_CELLS = 25


@dataclass
class SensitivityResult:
    """Full sweep surface plus headline summary stats."""
    symbol: str
    strategy: str
    interval: str
    axis_x: str
    axis_y: str
    values_x: List[float]
    values_y: List[float]
    # Surface as row-major 2D list. cells[y_idx][x_idx] = full cell metrics dict
    # so the frontend can show whichever metric the user clicks.
    cells: List[List[Optional[Dict]]] = field(default_factory=list)
    # Chosen-param coordinates within the sweep grid (snapped to nearest axis value)
    chosen_x_idx: int = 0
    chosen_y_idx: int = 0
    chosen_x_value: float = 0.0
    chosen_y_value: float = 0.0
    # Headline stats
    chosen_sharpe: float = 0.0
    chosen_net_pnl: float = 0.0
    chosen_max_dd: float = 0.0
    # Stability: std-dev of Sharpe across the chosen cell's neighbourhood.
    # NaN when the chosen cell has zero valid neighbours.
    stability_score: float = 0.0
    stability_tier: str = "unknown"   # "plateau" | "moderate" | "knife_edge" | "unknown"
    n_cells_total: int = 0
    n_cells_valid: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


def _snap_to_axis(value: float, axis_values: List[float]) -> int:
    """Index of the axis value closest to `value`."""
    arr = np.asarray(axis_values, dtype=np.float64)
    return int(np.argmin(np.abs(arr - value)))


def _classify_stability(score: float, chosen_sharpe: float) -> str:
    """Bucket the stability score relative to the chosen cell's Sharpe.

    Stability is unitless std-dev of Sharpe; a 0.3 std-dev means very different
    things if chosen Sharpe is 0.5 vs 2.5. We normalise by max(|chosen|, 0.5)
    so the bands make sense across magnitudes.
    """
    if not np.isfinite(score):
        return "unknown"
    denom = max(abs(chosen_sharpe), 0.5)
    rel = score / denom
    if rel < 0.20:
        return "plateau"
    if rel < 0.50:
        return "moderate"
    return "knife_edge"


def _compute_stability(
    cells: List[List[Optional[Dict]]],
    chosen_y_idx: int,
    chosen_x_idx: int,
) -> Tuple[float, int]:
    """Std-dev of Sharpe across the Moore neighbourhood of the chosen cell.

    Returns (std_dev, n_neighbours). NaN std when fewer than 2 valid neighbours
    (std is undefined). Excludes the chosen cell itself — ye want neighbour
    spread, not "spread including the centre".
    """
    n_y = len(cells)
    n_x = len(cells[0]) if n_y > 0 else 0
    neighbours: List[float] = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            ny, nx = chosen_y_idx + dy, chosen_x_idx + dx
            if 0 <= ny < n_y and 0 <= nx < n_x:
                cell = cells[ny][nx]
                if cell is not None and 'sharpe' in cell:
                    neighbours.append(float(cell['sharpe']))
    if len(neighbours) < 2:
        return float('nan'), len(neighbours)
    return float(np.std(neighbours, ddof=1)), len(neighbours)


async def run_sensitivity_analysis(
    *,
    symbol: str,
    interval: str,
    strategy: str,
    axis_x: str,
    axis_y: str,
    values_x: List[float],
    values_y: List[float],
    chosen_x: float,
    chosen_y: float,
    candle_limit: int = 5000,
    max_cells: int = _DEFAULT_MAX_CELLS,
    engine: Optional[BacktestEngine] = None,
) -> SensitivityResult:
    """Run a 2D parameter sweep and return the surface + stability score."""
    from app.services.backtest import backtest_engine as default_engine
    engine = engine or default_engine

    if axis_x not in _SUPPORTED_AXES:
        raise ValueError(f"Unsupported axis_x '{axis_x}'. Use one of {sorted(_SUPPORTED_AXES)}")
    if axis_y not in _SUPPORTED_AXES:
        raise ValueError(f"Unsupported axis_y '{axis_y}'. Use one of {sorted(_SUPPORTED_AXES)}")
    if axis_x == axis_y:
        raise ValueError("axis_x and axis_y must differ")
    if not values_x or not values_y:
        raise ValueError("Both axes need at least one value")

    total_cells = len(values_x) * len(values_y)
    if total_cells > max_cells:
        raise ValueError(
            f"Grid has {total_cells} cells, exceeds max_cells={max_cells}. "
            f"Reduce axis lengths or raise max_cells."
        )

    # Build the grid as a flat dict for _run_param_grid, then refold into 2D.
    # Use a single-axis sweep for unmentioned params so the grid generator picks
    # a constant value (the chosen one) for each.
    static_params = {a for a in _SUPPORTED_AXES if a not in (axis_x, axis_y)}
    static_defaults = {
        "position_size": 0.1,
        "stop_loss": 0.02,
        "take_profit": 0.05,
    }
    ranges: Dict[str, List[float]] = {
        axis_x: list(values_x),
        axis_y: list(values_y),
    }
    for p in static_params:
        ranges[p] = [static_defaults[p]]

    flat_cells = await engine._run_param_grid(
        symbol=symbol,
        interval=interval,
        parameter_ranges=ranges,
        strategy=strategy,
        max_cells=total_cells,
        candle_limit=candle_limit,
    )

    # Refold into 2D: cells[y_idx][x_idx]. Some cells may have failed and be
    # absent from flat_cells — those slots stay None so the heatmap can render
    # a dead-cell marker rather than crashing.
    grid: List[List[Optional[Dict]]] = [
        [None for _ in values_x] for _ in values_y
    ]
    by_xy: Dict[Tuple[float, float], Dict] = {}
    for cell in flat_cells:
        params = cell['params']
        key = (float(params[axis_x]), float(params[axis_y]))
        by_xy[key] = cell
    for yi, yv in enumerate(values_y):
        for xi, xv in enumerate(values_x):
            cell = by_xy.get((float(xv), float(yv)))
            if cell is not None:
                grid[yi][xi] = cell

    chosen_x_idx = _snap_to_axis(chosen_x, values_x)
    chosen_y_idx = _snap_to_axis(chosen_y, values_y)
    chosen_cell = grid[chosen_y_idx][chosen_x_idx]
    chosen_sharpe = float(chosen_cell['sharpe']) if chosen_cell else 0.0
    chosen_net_pnl = float(chosen_cell['net_pnl']) if chosen_cell else 0.0
    chosen_max_dd = float(chosen_cell['max_dd']) if chosen_cell else 0.0

    stability, _ = _compute_stability(grid, chosen_y_idx, chosen_x_idx)
    tier = _classify_stability(stability, chosen_sharpe)

    return SensitivityResult(
        symbol=symbol,
        strategy=strategy,
        interval=interval,
        axis_x=axis_x,
        axis_y=axis_y,
        values_x=list(values_x),
        values_y=list(values_y),
        cells=grid,
        chosen_x_idx=chosen_x_idx,
        chosen_y_idx=chosen_y_idx,
        chosen_x_value=float(values_x[chosen_x_idx]),
        chosen_y_value=float(values_y[chosen_y_idx]),
        chosen_sharpe=chosen_sharpe,
        chosen_net_pnl=chosen_net_pnl,
        chosen_max_dd=chosen_max_dd,
        stability_score=stability,
        stability_tier=tier,
        n_cells_total=total_cells,
        n_cells_valid=len(flat_cells),
    )
