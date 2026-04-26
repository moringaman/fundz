"""Tests for the trade-bootstrap Monte Carlo module.

These be smoke + invariant tests \u2014 they don't pretend to validate that the
percentiles are statistically correct (that's `numpy.percentile`'s job), but
they catch regressions in shape, ordering, determinism, and edge cases.
"""

from __future__ import annotations

import pytest

from app.services.backtest_montecarlo import (
    bootstrap_equity_paths,
    _adaptive_n_simulations,
)


def _make_exit(net_pnl: float, idx: int = 0) -> dict:
    return {
        'time': idx,
        'type': 'EXIT',
        'side': 'buy',
        'price': 100.0,
        'quantity': 1.0,
        'pnl': net_pnl,
        'net_pnl': net_pnl,
        'fee': 0.0,
        'balance': 0.0,
    }


class TestBootstrapBasics:
    def test_zero_trades_returns_none(self):
        # No EXITs in the list \u2014 nothing to resample, so caller gets None
        # rather than a dict full of NaNs.
        assert bootstrap_equity_paths([], initial_balance=10_000.0) is None
        assert bootstrap_equity_paths(
            [{'type': 'ENTRY'}], initial_balance=10_000.0
        ) is None

    def test_constant_winning_trades_have_zero_spread(self):
        # If every trade pays exactly +$10, every bootstrap path is identical,
        # so p5 == p50 == p95 and the band collapses to a line.
        trades = [_make_exit(10.0, i) for i in range(20)]
        out = bootstrap_equity_paths(
            trades, initial_balance=10_000.0, n_simulations=500, seed=42
        )
        assert out is not None
        assert out['final_pnl']['p5'] == pytest.approx(out['final_pnl']['p95'])
        assert out['final_pnl']['p50'] == pytest.approx(200.0)  # 20 \xd7 $10
        # Max drawdown is zero on a strictly increasing path
        assert out['max_drawdown']['p95'] == pytest.approx(0.0, abs=1e-9)

    def test_percentile_ordering_invariant(self):
        # For mixed wins/losses, p5 \u2264 p25 \u2264 p50 \u2264 p75 \u2264 p95 must hold
        # in every output block. Catches accidental key swaps.
        trades = [_make_exit(pnl, i) for i, pnl in enumerate(
            [50, -30, 20, -10, 80, -40, 15, -25, 60, -50]
        )]
        out = bootstrap_equity_paths(
            trades, initial_balance=10_000.0, n_simulations=2_000, seed=1
        )
        assert out is not None
        for block in ('final_pnl', 'max_drawdown', 'sharpe'):
            ps = [out[block][f'p{p}'] for p in (5, 25, 50, 75, 95)]
            assert ps == sorted(ps), f"{block} percentiles not monotonic: {ps}"

    def test_seed_reproducibility(self):
        # Same seed \u2192 same output, byte for byte. If this breaks, ye've changed
        # the RNG path and the tests downstream will go flaky.
        trades = [_make_exit(pnl, i) for i, pnl in enumerate(
            [10, -5, 15, -8, 12, -3, 20, -10]
        )]
        a = bootstrap_equity_paths(trades, 10_000.0, n_simulations=500, seed=123)
        b = bootstrap_equity_paths(trades, 10_000.0, n_simulations=500, seed=123)
        assert a == b

    def test_seed_changes_output(self):
        # Sanity: different seeds produce different paths on volatile input.
        trades = [_make_exit(pnl, i) for i, pnl in enumerate(
            [50, -30, 20, -10, 80, -40, 15, -25]
        )]
        a = bootstrap_equity_paths(trades, 10_000.0, n_simulations=500, seed=1)
        b = bootstrap_equity_paths(trades, 10_000.0, n_simulations=500, seed=2)
        assert a['equity_bands']['p50'] != b['equity_bands']['p50']


class TestEquityBands:
    def test_band_length_matches_band_points(self):
        # When trade count > band_points, we downsample to exactly band_points
        trades = [_make_exit(1.0, i) for i in range(500)]
        out = bootstrap_equity_paths(
            trades, 10_000.0, n_simulations=200, seed=7, band_points=50
        )
        assert out is not None
        for key in ('p5', 'p25', 'p50', 'p75', 'p95'):
            assert len(out['equity_bands'][key]) == 50
        assert len(out['equity_bands']['trade_index']) == 50

    def test_band_length_when_trades_below_band_points(self):
        # When trade count + 1 \u2264 band_points, we keep every point (no downsampling)
        trades = [_make_exit(1.0, i) for i in range(10)]
        out = bootstrap_equity_paths(
            trades, 10_000.0, n_simulations=200, seed=7, band_points=200
        )
        assert out is not None
        # n_trades + 1 = 11 starting balance + 10 trades
        assert len(out['equity_bands']['p50']) == 11

    def test_first_point_is_initial_balance(self):
        # Every percentile band should start at the initial balance \u2014
        # bootstrap can't affect the starting point.
        trades = [_make_exit(pnl, i) for i, pnl in enumerate([10, -5, 8, -3])]
        out = bootstrap_equity_paths(trades, 10_000.0, n_simulations=300, seed=42)
        for key in ('p5', 'p50', 'p95'):
            assert out['equity_bands'][key][0] == pytest.approx(10_000.0)


class TestAdaptiveSizing:
    def test_floor_at_1000(self):
        # Even with one trade, ye get the 1000-sim floor
        assert _adaptive_n_simulations(1) == 1_000

    def test_ceiling_at_10000(self):
        # Cap at 10k regardless of trade count
        assert _adaptive_n_simulations(500) == 10_000
        assert _adaptive_n_simulations(5_000) == 10_000

    def test_scales_linearly_in_middle(self):
        # 100\xd7 trade count when in the linear band
        assert _adaptive_n_simulations(50) == 5_000

    def test_default_is_used_when_n_simulations_none(self):
        # Confirm the wiring \u2014 no n_simulations arg \u2192 adaptive default
        trades = [_make_exit(1.0, i) for i in range(20)]
        out = bootstrap_equity_paths(trades, 10_000.0, seed=1)
        # 100 \xd7 20 = 2000, between floor and ceiling
        assert out['n_simulations'] == 2_000


class TestEdgeCases:
    def test_negative_path_drawdown_is_capped_sensibly(self):
        # All-loss trades \u2014 path goes negative, drawdown should still be a
        # finite positive number, not NaN or inf
        trades = [_make_exit(-100.0, i) for i in range(50)]
        out = bootstrap_equity_paths(trades, 1_000.0, n_simulations=500, seed=9)
        assert out is not None
        for p in (5, 25, 50, 75, 95):
            dd = out['max_drawdown'][f'p{p}']
            assert dd == dd  # not NaN
            assert dd >= 0.0
            assert dd < 1e6  # not inf

    def test_single_trade_does_not_crash(self):
        # n=1: std-dev is undefined; we degrade to zero Sharpe rather than NaN
        trades = [_make_exit(50.0, 0)]
        out = bootstrap_equity_paths(trades, 10_000.0, n_simulations=200, seed=3)
        assert out is not None
        assert out['n_trades'] == 1
        # All Sharpe percentiles should be exactly zero per the n==1 branch
        for p in (5, 50, 95):
            assert out['sharpe'][f'p{p}'] == pytest.approx(0.0)
