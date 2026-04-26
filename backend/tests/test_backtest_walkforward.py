"""Tests for the walk-forward analysis module.

We can't hit Phemex from CI, so these tests stub `BacktestEngine` with a fake
that returns deterministic per-slice results. The fake lets us assert on
chunking, aggregation, degradation ratio, and edge cases without exercising
the real strategy loop (which is covered elsewhere).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.services.backtest import BacktestConfig, BacktestResult
from app.services.backtest_walkforward import (
    WalkForwardResult,
    WindowResult,
    _aggregate_windows,
    _compute_chunking,
    run_walk_forward,
)


def _stub_result(sharpe: float, net_pnl: float, n_trades: int = 10, initial: float = 10_000.0) -> BacktestResult:
    """Build a minimal BacktestResult with just the fields walk-forward reads."""
    return BacktestResult(
        total_trades=n_trades,
        winning_trades=int(n_trades * 0.6),
        losing_trades=n_trades - int(n_trades * 0.6),
        win_rate=0.6,
        total_pnl=net_pnl,
        net_pnl=net_pnl,
        total_fees=0.0,
        max_drawdown=0.05,
        sharpe_ratio=sharpe,
        avg_trade_pnl=net_pnl / max(n_trades, 1),
        profit_factor=1.5,
        avg_win=10.0,
        avg_loss=-5.0,
        max_consecutive_wins=3,
        max_consecutive_losses=2,
        trades=[],
        equity_curve=[initial, initial + net_pnl],
        drawdown_curve=[0.0, 0.05],
    )


class TestChunking:
    def test_normal_chunking(self):
        # 5000 candles / (6+1) = 714 per chunk, well above 500 floor
        n_eff, chunk = _compute_chunking(5_000, 6)
        assert n_eff == 6
        assert chunk == 714

    def test_auto_degrades_when_thin(self):
        # 2500 / 7 = 357 (< 500), so 6 windows can't fit. Reduce to 4 → 500 each.
        n_eff, chunk = _compute_chunking(2_500, 6)
        assert n_eff == 4
        assert chunk == 500

    def test_raises_when_far_too_thin(self):
        with pytest.raises(ValueError, match="at least"):
            _compute_chunking(800, 6)

    def test_minimum_two_chunks(self):
        # Exactly 1000 candles → 1 IS + 1 OOS, both 500 each
        n_eff, chunk = _compute_chunking(1_000, 1)
        assert n_eff == 1
        assert chunk == 500


class TestAggregation:
    def test_zero_windows_returns_nans(self):
        mean_is, mean_oos, deg, curve, dd = _aggregate_windows([], 10_000.0)
        assert np.isnan(mean_is)
        assert np.isnan(mean_oos)
        assert np.isnan(deg)
        assert curve == []
        assert dd == 0.0

    def test_perfect_consistency_degradation_one(self):
        # IS Sharpe == OOS Sharpe across all windows → degradation ratio = 1.0
        windows = [
            WindowResult(
                window_index=i, is_start=0, is_end=100, oos_start=100, oos_end=200,
                is_sharpe=1.5, is_net_pnl=100, is_win_rate=0.6, is_total_trades=10,
                is_max_drawdown=0.05,
                oos_sharpe=1.5, oos_net_pnl=100, oos_win_rate=0.6, oos_total_trades=10,
                oos_max_drawdown=0.05, oos_final_balance=10_100,
            )
            for i in range(3)
        ]
        _, _, deg, _, _ = _aggregate_windows(windows, 10_000.0)
        assert deg == pytest.approx(1.0)

    def test_overfit_degradation_below_one(self):
        # IS Sharpe 2.0, OOS Sharpe 0.4 → degradation = 0.2 (red zone)
        windows = [
            WindowResult(
                window_index=i, is_start=0, is_end=100, oos_start=100, oos_end=200,
                is_sharpe=2.0, is_net_pnl=200, is_win_rate=0.7, is_total_trades=10,
                is_max_drawdown=0.05,
                oos_sharpe=0.4, oos_net_pnl=20, oos_win_rate=0.5, oos_total_trades=10,
                oos_max_drawdown=0.10, oos_final_balance=10_020,
            )
            for i in range(3)
        ]
        _, _, deg, _, _ = _aggregate_windows(windows, 10_000.0)
        assert deg == pytest.approx(0.2)

    def test_negative_is_sharpe_yields_nan_degradation(self):
        # Strategy losing money in-sample — degradation ratio is meaningless
        windows = [
            WindowResult(
                window_index=0, is_start=0, is_end=100, oos_start=100, oos_end=200,
                is_sharpe=-0.5, is_net_pnl=-50, is_win_rate=0.4, is_total_trades=10,
                is_max_drawdown=0.10,
                oos_sharpe=0.3, oos_net_pnl=30, oos_win_rate=0.5, oos_total_trades=10,
                oos_max_drawdown=0.05, oos_final_balance=10_030,
            ),
        ]
        _, _, deg, _, _ = _aggregate_windows(windows, 10_000.0)
        assert np.isnan(deg)

    def test_stitched_curve_compounds(self):
        # Each window earns 1% on initial balance → stitched curve compounds
        windows = [
            WindowResult(
                window_index=i, is_start=0, is_end=100, oos_start=100, oos_end=200,
                is_sharpe=1.0, is_net_pnl=100, is_win_rate=0.6, is_total_trades=5,
                is_max_drawdown=0.0,
                oos_sharpe=1.0, oos_net_pnl=100, oos_win_rate=0.6, oos_total_trades=5,
                oos_max_drawdown=0.0, oos_final_balance=10_100,  # +1%
            )
            for i in range(3)
        ]
        _, _, _, curve, _ = _aggregate_windows(windows, 10_000.0)
        # 4 points: initial + 3 windows
        assert len(curve) == 4
        assert curve[0] == pytest.approx(10_000.0)
        assert curve[1] == pytest.approx(10_100.0)
        assert curve[2] == pytest.approx(10_201.0)  # 10_100 × 1.01
        assert curve[3] == pytest.approx(10_303.01, abs=0.01)

    def test_stitched_curve_drawdown(self):
        # First window +5%, second -10% → DD on stitched curve
        windows = [
            WindowResult(
                window_index=0, is_start=0, is_end=100, oos_start=100, oos_end=200,
                is_sharpe=1.0, is_net_pnl=500, is_win_rate=0.7, is_total_trades=5,
                is_max_drawdown=0.0,
                oos_sharpe=1.0, oos_net_pnl=500, oos_win_rate=0.7, oos_total_trades=5,
                oos_max_drawdown=0.0, oos_final_balance=10_500,  # +5%
            ),
            WindowResult(
                window_index=1, is_start=0, is_end=200, oos_start=200, oos_end=300,
                is_sharpe=1.0, is_net_pnl=-1000, is_win_rate=0.3, is_total_trades=5,
                is_max_drawdown=0.0,
                oos_sharpe=-0.5, oos_net_pnl=-1000, oos_win_rate=0.3, oos_total_trades=5,
                oos_max_drawdown=0.10, oos_final_balance=9_000,  # -10%
            ),
        ]
        _, _, _, curve, dd = _aggregate_windows(windows, 10_000.0)
        # Curve: [10000, 10500, 9450]; peak 10500, trough 9450 → DD = 0.10
        assert curve[1] == pytest.approx(10_500.0)
        assert curve[2] == pytest.approx(9_450.0)
        assert dd == pytest.approx(0.10)


class TestRunWalkForward:
    @pytest.mark.asyncio
    async def test_anchored_window_slicing(self):
        # 3500 candles, 6 requested windows → 6 windows of 500 each
        # (3500 / 7 = 500). Verify the fake engine receives the correct slices.
        from app.services import backtest_walkforward as wf_mod

        klines = [{'time': i, 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1.0}
                  for i in range(3500)]

        fake_engine = AsyncMock()
        fake_engine._fetch_historical_data.return_value = klines
        # Return the same stub for both IS and OOS calls
        fake_engine.run_backtest.return_value = _stub_result(sharpe=1.0, net_pnl=100)

        cfg = BacktestConfig(
            symbol="BTCUSDT", strategy="momentum", candle_limit=3500,
            run_montecarlo=False,
        )
        result = await run_walk_forward(cfg, n_windows=6, mode="anchored", engine=fake_engine)

        assert isinstance(result, WalkForwardResult)
        assert result.mode == "anchored"
        assert result.n_windows == 6
        assert result.chunk_size == 500
        # 6 windows × 2 calls (IS + OOS) = 12 backtest invocations
        assert fake_engine.run_backtest.await_count == 12
        # Anchored: window 0 IS = candles[0:500], OOS = [500:1000]
        first_call_kwargs = fake_engine.run_backtest.await_args_list[0].kwargs
        assert len(first_call_kwargs['_klines']) == 500
        # Window 0 OOS slice
        second_call_kwargs = fake_engine.run_backtest.await_args_list[1].kwargs
        assert len(second_call_kwargs['_klines']) == 500

    @pytest.mark.asyncio
    async def test_rolling_mode_uses_fixed_width(self):
        from app.services import backtest_walkforward as wf_mod

        klines = [{'time': i, 'close': 100} for i in range(3500)]
        fake_engine = AsyncMock()
        fake_engine._fetch_historical_data.return_value = klines
        fake_engine.run_backtest.return_value = _stub_result(sharpe=1.0, net_pnl=100)

        cfg = BacktestConfig(symbol="BTCUSDT", strategy="momentum", candle_limit=3500, run_montecarlo=False)
        result = await run_walk_forward(cfg, n_windows=3, mode="rolling", engine=fake_engine)

        # Rolling: each window's IS is fixed-width chunk (3500/4 = 875)
        assert result.chunk_size == 875
        assert result.n_windows == 3

    @pytest.mark.asyncio
    async def test_disables_mc_in_per_window_runs(self):
        # Confirm the per-window config has run_montecarlo forced off, even if
        # the parent config requested it.
        klines = [{'time': i, 'close': 100} for i in range(2000)]
        fake_engine = AsyncMock()
        fake_engine._fetch_historical_data.return_value = klines
        fake_engine.run_backtest.return_value = _stub_result(sharpe=1.0, net_pnl=100)

        cfg = BacktestConfig(
            symbol="BTCUSDT", strategy="momentum", candle_limit=2000,
            run_montecarlo=True,  # parent says yes
        )
        await run_walk_forward(cfg, n_windows=3, engine=fake_engine)

        # Every backtest invocation should have received run_montecarlo=False
        for call in fake_engine.run_backtest.await_args_list:
            passed_cfg = call.args[0] if call.args else call.kwargs.get('config')
            assert passed_cfg.run_montecarlo is False

    @pytest.mark.asyncio
    async def test_handles_window_failure_gracefully(self):
        # If one window's backtest raises ValueError, the walk-forward should
        # log and continue with a zero-filled WindowResult rather than abort.
        klines = [{'time': i, 'close': 100} for i in range(2000)]
        fake_engine = AsyncMock()
        fake_engine._fetch_historical_data.return_value = klines

        good = _stub_result(sharpe=1.0, net_pnl=100)
        # Sequence: window 0 IS ok, OOS ok, window 1 IS RAISES, window 2 IS ok, OOS ok
        # The exception during window 1 should produce a zeroed WindowResult.
        fake_engine.run_backtest.side_effect = [
            good, good,                          # window 0: IS, OOS
            ValueError("simulated bad window"),  # window 1: IS fails
            good, good,                          # window 2: IS, OOS
        ]

        cfg = BacktestConfig(symbol="BTCUSDT", strategy="momentum", candle_limit=2000, run_montecarlo=False)
        result = await run_walk_forward(cfg, n_windows=3, engine=fake_engine)

        assert len(result.windows) == 3
        # Window 1 should be the zero-filled fallback
        assert result.windows[1].is_total_trades == 0
        assert result.windows[1].oos_sharpe == 0.0
