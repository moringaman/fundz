import math
from datetime import datetime, timezone, timedelta

import numpy as np
import pytest

from app.services.correlation_service import (
    CorrelationMatrix,
    CorrelationService,
    DEFAULT_FALLBACK_CORRELATION,
)
from app.services.risk_manager import RiskManager


def _matrix(symbols, rho_off_diag, observations=200):
    n = len(symbols)
    m = np.full((n, n), rho_off_diag, dtype=float)
    np.fill_diagonal(m, 1.0)
    return CorrelationMatrix(
        symbols=list(symbols),
        matrix=m,
        last_refresh=datetime.now(timezone.utc),
        observations=observations,
    )


class TestCorrelatedExposureFormula:
    def test_perfectly_correlated_collapses_to_sum(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT", "ETHUSDT", "SOLUSDT"], rho_off_diag=1.0)
        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "SOLUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        assert math.isclose(exp.effective_pct, 30.0, abs_tol=0.01), (
            "All-correlated longs must equal raw notional sum"
        )
        assert math.isclose(exp.raw_long_pct, 30.0, abs_tol=0.01)

    def test_uncorrelated_yields_quadrature_sum(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT", "ETHUSDT", "SOLUSDT"], rho_off_diag=0.0)
        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "SOLUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        expected = math.sqrt(3 * (0.10 ** 2)) * 100
        assert math.isclose(exp.effective_pct, expected, abs_tol=0.01)

    def test_realistic_crypto_correlation_inflates_exposure(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT", "ETHUSDT", "SOLUSDT"], rho_off_diag=0.85)
        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "SOLUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        assert exp.effective_pct > 25.0, "highly-correlated longs should be near raw sum"
        assert exp.effective_pct < 30.5, "should be slightly below perfect-correlation case"

    def test_offsetting_long_short_reduces_exposure(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT", "ETHUSDT"], rho_off_diag=1.0)
        positions = [
            {"symbol": "BTCUSDT", "side": "buy",  "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "sell", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        assert exp.effective_pct < 1.0, "offsetting long+short on perfectly correlated assets nets near-zero"

    def test_intended_trade_added(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT", "ETHUSDT"], rho_off_diag=1.0)
        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp_before = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        exp_after = svc.compute_correlated_exposure(
            positions=positions,
            intended_trade={"symbol": "ETHUSDT", "side": "buy", "margin": 1000},
            total_capital=10000,
        )
        assert exp_after.effective_pct > exp_before.effective_pct
        assert math.isclose(exp_after.effective_pct, 20.0, abs_tol=0.01)

    def test_zero_capital_returns_zero(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT"], rho_off_diag=0.0)
        exp = svc.compute_correlated_exposure(
            positions=[{"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1}],
            total_capital=0,
        )
        assert exp.effective_pct == 0.0

    def test_no_matrix_uses_fallback(self):
        svc = CorrelationService()
        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "buy", "quantity": 1, "entry_price": 1000, "current_price": 1000, "leverage": 1},
        ]
        exp = svc.compute_correlated_exposure(positions=positions, total_capital=10000)
        expected = math.sqrt(2 * 0.10 ** 2 + 2 * DEFAULT_FALLBACK_CORRELATION * 0.10 * 0.10) * 100
        assert math.isclose(exp.effective_pct, expected, abs_tol=0.01)


class TestStaleness:
    def test_no_matrix_is_stale(self):
        svc = CorrelationService()
        assert svc.is_stale()

    def test_fresh_matrix_not_stale(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT"], rho_off_diag=0.0)
        assert not svc.is_stale()

    def test_old_matrix_is_stale(self):
        svc = CorrelationService()
        svc._matrix = _matrix(["BTCUSDT"], rho_off_diag=0.0)
        svc._matrix.last_refresh = datetime.now(timezone.utc) - timedelta(hours=24)
        assert svc.is_stale()


class TestLogReturns:
    def test_log_returns_basic(self):
        svc = CorrelationService()
        rets = svc._log_returns([100.0, 110.0, 121.0])
        assert rets.size == 2
        assert math.isclose(rets[0], math.log(110 / 100), abs_tol=1e-6)

    def test_log_returns_filters_zero_prices(self):
        svc = CorrelationService()
        rets = svc._log_returns([100.0, 0.0, 110.0])
        assert rets.size == 1
        assert math.isclose(rets[0], math.log(110 / 100), abs_tol=1e-6)

    def test_log_returns_handles_empty(self):
        svc = CorrelationService()
        assert svc._log_returns([]).size == 0
        assert svc._log_returns([100.0]).size == 0


class TestRefreshFromSyntheticReturns:
    @pytest.mark.asyncio
    async def test_refresh_builds_matrix_from_correlated_series(self, monkeypatch):
        svc = CorrelationService()

        rng = np.random.default_rng(42)
        n = 100
        common = rng.normal(0, 0.02, n)
        btc_close = 50000.0 * np.exp(np.cumsum(common + rng.normal(0, 0.005, n)))
        eth_close = 3000.0 * np.exp(np.cumsum(common + rng.normal(0, 0.005, n)))
        random_close = 1.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))

        async def fake_fetch(symbol):
            if symbol == "BTCUSDT":
                return list(btc_close)
            if symbol == "ETHUSDT":
                return list(eth_close)
            if symbol == "RNDUSDT":
                return list(random_close)
            return []

        monkeypatch.setattr(svc, "_fetch_closes", fake_fetch)
        matrix = await svc.refresh(["BTCUSDT", "ETHUSDT", "RNDUSDT"])
        assert matrix is not None
        rho_btc_eth = matrix.correlation("BTCUSDT", "ETHUSDT")
        rho_btc_rnd = matrix.correlation("BTCUSDT", "RNDUSDT")
        assert rho_btc_eth > 0.6, f"shared driver should give high correlation, got {rho_btc_eth}"
        assert abs(rho_btc_rnd) < 0.4, f"unrelated series should be weakly correlated, got {rho_btc_rnd}"

    @pytest.mark.asyncio
    async def test_refresh_skips_symbols_with_insufficient_data(self, monkeypatch):
        svc = CorrelationService()

        async def fake_fetch(symbol):
            if symbol == "BTCUSDT":
                return [100.0 + i for i in range(60)]
            if symbol == "ETHUSDT":
                return [3000.0 + i for i in range(60)]
            if symbol == "TINYUSDT":
                return [1.0, 1.1, 1.2]
            return []

        monkeypatch.setattr(svc, "_fetch_closes", fake_fetch)
        matrix = await svc.refresh(["BTCUSDT", "ETHUSDT", "TINYUSDT"])
        assert matrix is not None
        assert "TINYUSDT" not in matrix.symbols
        assert "BTCUSDT" in matrix.symbols
        assert "ETHUSDT" in matrix.symbols


class TestRiskManagerGate:
    def test_gate_disabled_when_limit_at_100(self):
        rm = RiskManager()
        result = rm.check_correlation_concentration(
            intended_trade={"symbol": "BTCUSDT", "side": "buy", "margin": 1000},
            current_positions=[],
            total_capital=10000,
            max_correlated_exposure_pct=100.0,
        )
        assert result.allowed

    def test_gate_blocks_when_correlated_exposure_exceeds_limit(self, monkeypatch):
        rm = RiskManager()

        from app.services import correlation_service as cs_module
        cs_module.correlation_service._matrix = _matrix(
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"], rho_off_diag=0.9
        )

        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 1500, "current_price": 1500, "leverage": 1},
            {"symbol": "ETHUSDT", "side": "buy", "quantity": 1, "entry_price": 1500, "current_price": 1500, "leverage": 1},
        ]
        result = rm.check_correlation_concentration(
            intended_trade={"symbol": "SOLUSDT", "side": "buy", "margin": 1500},
            current_positions=positions,
            total_capital=10000,
            max_correlated_exposure_pct=30.0,
        )
        assert not result.allowed
        assert "Correlation-weighted" in result.reason

    def test_gate_allows_diversified_book(self, monkeypatch):
        rm = RiskManager()

        from app.services import correlation_service as cs_module
        cs_module.correlation_service._matrix = _matrix(
            ["BTCUSDT", "ETHUSDT"], rho_off_diag=1.0
        )

        positions = [
            {"symbol": "BTCUSDT", "side": "buy", "quantity": 1, "entry_price": 500, "current_price": 500, "leverage": 1},
        ]
        result = rm.check_correlation_concentration(
            intended_trade={"symbol": "ETHUSDT", "side": "sell", "margin": 500},
            current_positions=positions,
            total_capital=10000,
            max_correlated_exposure_pct=30.0,
        )
        assert result.allowed
