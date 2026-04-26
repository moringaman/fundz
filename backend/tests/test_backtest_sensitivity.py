"""Tests for the strategy sensitivity analysis module.

Stub the engine so we can exercise grid building, refolding, snap-to-axis,
stability classification, and the validation guards without hitting Phemex.
"""

from __future__ import annotations

from typing import Dict, List
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.services.backtest_sensitivity import (
    SensitivityResult,
    _classify_stability,
    _compute_stability,
    _snap_to_axis,
    run_sensitivity_analysis,
)


def _stub_cell(params: Dict, sharpe: float, net_pnl: float = 100.0) -> Dict:
    """Mimic the dict shape `_run_param_grid` returns."""
    return {
        'params': params,
        'score': sharpe * 0.4,
        'win_rate': 0.55,
        'net_pnl': net_pnl,
        'sharpe': sharpe,
        'max_dd': 0.05,
        'trades': 25,
        'profit_factor': 1.4,
    }


class TestSnapToAxis:
    def test_exact_match(self):
        assert _snap_to_axis(0.05, [0.01, 0.03, 0.05, 0.07]) == 2

    def test_between_values_picks_closest(self):
        # 0.04 is closer to 0.03 than 0.05
        assert _snap_to_axis(0.04, [0.01, 0.03, 0.05, 0.07]) == 1

    def test_below_range_clamps_to_first(self):
        assert _snap_to_axis(-1.0, [0.01, 0.03, 0.05]) == 0

    def test_above_range_clamps_to_last(self):
        assert _snap_to_axis(99.0, [0.01, 0.03, 0.05]) == 2


class TestStabilityComputation:
    def test_uniform_neighbours_zero_std(self):
        # All neighbours identical Sharpe → std = 0 → plateau
        cells = [
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
        ]
        std, n = _compute_stability(cells, 1, 1)
        assert std == pytest.approx(0.0)
        assert n == 8

    def test_volatile_neighbours_high_std(self):
        # Wide spread of Sharpes around the chosen cell → high std
        cells = [
            [_stub_cell({}, -1.0), _stub_cell({}, 2.0), _stub_cell({}, -1.0)],
            [_stub_cell({}, 2.0),  _stub_cell({}, 0.5), _stub_cell({}, 2.0)],
            [_stub_cell({}, -1.0), _stub_cell({}, 2.0), _stub_cell({}, -1.0)],
        ]
        std, n = _compute_stability(cells, 1, 1)
        assert std > 1.0
        assert n == 8

    def test_corner_cell_uses_only_valid_neighbours(self):
        # Top-left corner has only 3 neighbours
        cells = [
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
        ]
        std, n = _compute_stability(cells, 0, 0)
        assert n == 3
        assert std == pytest.approx(0.0)

    def test_skips_none_cells(self):
        # Failed cells (None) should be excluded from the neighbourhood
        cells = [
            [None, _stub_cell({}, 1.0), None],
            [_stub_cell({}, 1.0), _stub_cell({}, 1.0), _stub_cell({}, 1.0)],
            [None, _stub_cell({}, 1.0), None],
        ]
        std, n = _compute_stability(cells, 1, 1)
        assert n == 4  # only the 4 cardinal neighbours valid

    def test_too_few_neighbours_returns_nan(self):
        # Only 1 valid neighbour → std undefined
        cells = [[_stub_cell({}, 1.0), _stub_cell({}, 1.0)]]
        std, n = _compute_stability(cells, 0, 0)
        assert n == 1
        assert np.isnan(std)


class TestStabilityClassification:
    def test_plateau_low_relative_std(self):
        # std=0.05, chosen Sharpe=1.0 → rel=0.05 → plateau
        assert _classify_stability(0.05, 1.0) == "plateau"

    def test_moderate_mid_relative_std(self):
        # std=0.3, chosen Sharpe=1.0 → rel=0.3 → moderate
        assert _classify_stability(0.3, 1.0) == "moderate"

    def test_knife_edge_high_relative_std(self):
        # std=0.7, chosen Sharpe=1.0 → rel=0.7 → knife_edge
        assert _classify_stability(0.7, 1.0) == "knife_edge"

    def test_low_chosen_sharpe_uses_floor(self):
        # Without the floor, std=0.1 / |chosen|=0.1 = 1.0 → knife_edge.
        # With the floor at 0.5, rel = 0.1/0.5 = 0.2 → "moderate".
        # Confirms low-Sharpe agents don't get spuriously labelled fragile.
        assert _classify_stability(0.1, 0.1) == "moderate"
        # And without the floor it would be knife_edge — confirmed via
        # high-Sharpe equivalent: same std=0.1 against Sharpe=2.0 → rel=0.05 → plateau
        assert _classify_stability(0.1, 2.0) == "plateau"

    def test_nan_returns_unknown(self):
        assert _classify_stability(float('nan'), 1.0) == "unknown"


class TestRunSensitivity:
    @pytest.mark.asyncio
    async def test_validates_axes(self):
        with pytest.raises(ValueError, match="Unsupported axis_x"):
            await run_sensitivity_analysis(
                symbol="BTCUSDT", interval="1h", strategy="momentum",
                axis_x="bogus", axis_y="stop_loss",
                values_x=[0.01], values_y=[0.05],
                chosen_x=0.01, chosen_y=0.05,
            )

    @pytest.mark.asyncio
    async def test_validates_distinct_axes(self):
        with pytest.raises(ValueError, match="must differ"):
            await run_sensitivity_analysis(
                symbol="BTCUSDT", interval="1h", strategy="momentum",
                axis_x="stop_loss", axis_y="stop_loss",
                values_x=[0.01, 0.02], values_y=[0.01, 0.02],
                chosen_x=0.02, chosen_y=0.02,
            )

    @pytest.mark.asyncio
    async def test_enforces_max_cells(self):
        # 6×6 = 36 > default 25
        with pytest.raises(ValueError, match="exceeds max_cells"):
            await run_sensitivity_analysis(
                symbol="BTCUSDT", interval="1h", strategy="momentum",
                axis_x="stop_loss", axis_y="take_profit",
                values_x=[0.01, 0.015, 0.02, 0.025, 0.03, 0.035],
                values_y=[0.02, 0.03, 0.04, 0.05, 0.06, 0.07],
                chosen_x=0.02, chosen_y=0.05,
            )

    @pytest.mark.asyncio
    async def test_full_sweep_refolds_and_snaps(self):
        # 3×3 sweep, fake engine returns Sharpe = sl + tp so we know the surface
        fake_engine = AsyncMock()

        async def _grid(**kwargs):
            ranges = kwargs['parameter_ranges']
            cells: List[Dict] = []
            for sl in ranges['stop_loss']:
                for tp in ranges['take_profit']:
                    for ps in ranges['position_size']:
                        cells.append(_stub_cell(
                            {'stop_loss': sl, 'take_profit': tp, 'position_size': ps},
                            sharpe=sl * 100 + tp * 10,  # deterministic for assertions
                        ))
            return cells

        fake_engine._run_param_grid.side_effect = _grid

        result = await run_sensitivity_analysis(
            symbol="BTCUSDT", interval="1h", strategy="momentum",
            axis_x="stop_loss", axis_y="take_profit",
            values_x=[0.01, 0.02, 0.03],
            values_y=[0.04, 0.05, 0.06],
            chosen_x=0.02,  # exactly on grid
            chosen_y=0.058,  # closer to 0.06 than 0.05 (no tie)
            engine=fake_engine,
        )

        assert isinstance(result, SensitivityResult)
        # Snap to axis: 0.02 is exact (idx 1); 0.058 closer to 0.06 (idx 2)
        assert result.chosen_x_idx == 1
        assert result.chosen_x_value == pytest.approx(0.02)
        assert result.chosen_y_idx == 2
        assert result.chosen_y_value == pytest.approx(0.06)
        # Cell at chosen position: sl=0.02, tp=0.06 → Sharpe = 2 + 0.6 = 2.6
        chosen_cell = result.cells[2][1]
        assert chosen_cell is not None
        assert chosen_cell['sharpe'] == pytest.approx(2.6)
        assert result.chosen_sharpe == pytest.approx(2.6)
        # All 9 cells should be populated
        assert result.n_cells_total == 9
        assert result.n_cells_valid == 9
        # stability_tier should be one of the four labels
        assert result.stability_tier in {"plateau", "moderate", "knife_edge", "unknown"}

    @pytest.mark.asyncio
    async def test_handles_partial_grid_failure(self):
        # Engine returns only 2 of 4 expected cells — refold leaves None slots
        fake_engine = AsyncMock()
        fake_engine._run_param_grid.return_value = [
            _stub_cell({'stop_loss': 0.01, 'take_profit': 0.05, 'position_size': 0.1}, sharpe=1.0),
            _stub_cell({'stop_loss': 0.02, 'take_profit': 0.06, 'position_size': 0.1}, sharpe=2.0),
        ]

        result = await run_sensitivity_analysis(
            symbol="BTCUSDT", interval="1h", strategy="momentum",
            axis_x="stop_loss", axis_y="take_profit",
            values_x=[0.01, 0.02],
            values_y=[0.05, 0.06],
            chosen_x=0.01, chosen_y=0.05,
            engine=fake_engine,
        )

        assert result.n_cells_valid == 2
        assert result.n_cells_total == 4
        # Only the (sl=0.01, tp=0.05) and (sl=0.02, tp=0.06) cells populated
        assert result.cells[0][0] is not None
        assert result.cells[0][1] is None  # sl=0.02, tp=0.05 missing
        assert result.cells[1][0] is None  # sl=0.01, tp=0.06 missing
        assert result.cells[1][1] is not None
