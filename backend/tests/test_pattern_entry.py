from dataclasses import dataclass

import pytest

from app.services.pattern_entry import OrderPlan, can_fill_now, select_pattern_entry


@dataclass
class _Pat:
    pattern_type: str
    direction: str
    confidence: float
    entry_price: float


class TestSelectPatternEntry:
    def test_bull_flag_above_current_routes_to_stop_buy(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[_Pat("bull_flag", "bullish", 0.78, 50_200)],
            min_confidence=0.65,
        )
        assert plan is not None
        assert plan.order_type == "Stop"
        assert plan.entry_price == 50_200
        assert plan.pattern_type == "bull_flag"

    def test_bull_flag_pullback_routes_to_limit_buy(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_300,
            patterns=[_Pat("bull_flag", "bullish", 0.78, 50_100)],
            min_confidence=0.65,
        )
        assert plan is not None
        assert plan.order_type == "Limit"
        assert plan.entry_price == 50_100

    def test_bear_flag_breakdown_routes_to_stop_sell(self):
        plan = select_pattern_entry(
            side="sell",
            current_price=2_000,
            patterns=[_Pat("bear_flag", "bearish", 0.78, 1_980)],
            min_confidence=0.65,
        )
        assert plan is not None
        assert plan.order_type == "Stop"

    def test_sell_above_current_routes_to_limit_sell(self):
        plan = select_pattern_entry(
            side="sell",
            current_price=1_980,
            patterns=[_Pat("rising_wedge", "bearish", 0.72, 2_010)],
            min_confidence=0.65,
        )
        assert plan is not None
        assert plan.order_type == "Limit"
        assert plan.entry_price == 2_010

    def test_no_aligned_pattern_returns_none(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[_Pat("bear_flag", "bearish", 0.9, 49_500)],
            min_confidence=0.65,
        )
        assert plan is None

    def test_below_min_confidence_returns_none(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[_Pat("bull_flag", "bullish", 0.5, 50_200)],
            min_confidence=0.65,
        )
        assert plan is None

    def test_disabled_returns_none(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[_Pat("bull_flag", "bullish", 0.9, 50_200)],
            min_confidence=0.65,
            enabled=False,
        )
        assert plan is None

    def test_picks_highest_confidence_when_multiple(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[
                _Pat("bull_flag", "bullish", 0.70, 50_200),
                _Pat("cup_handle", "bullish", 0.85, 50_300),
                _Pat("ascending_triangle", "bullish", 0.68, 50_150),
            ],
            min_confidence=0.65,
        )
        assert plan is not None
        assert plan.pattern_type == "cup_handle"
        assert plan.entry_price == 50_300

    def test_zero_or_negative_entry_price_rejected(self):
        plan = select_pattern_entry(
            side="buy",
            current_price=50_000,
            patterns=[_Pat("bull_flag", "bullish", 0.9, 0.0)],
            min_confidence=0.65,
        )
        assert plan is None


class TestCanFillNow:
    def test_market_always_fillable(self):
        plan = OrderPlan(order_type="Market", entry_price=100)
        assert can_fill_now(plan, "buy", 50_000, tolerance_pct=0.30)

    def test_stop_buy_triggers_above_level(self):
        plan = OrderPlan(order_type="Stop", entry_price=50_200)
        assert can_fill_now(plan, "buy", 50_300, tolerance_pct=0.0)
        assert not can_fill_now(plan, "buy", 50_000, tolerance_pct=0.0)

    def test_stop_buy_with_tolerance_below_level(self):
        plan = OrderPlan(order_type="Stop", entry_price=50_200)
        assert can_fill_now(plan, "buy", 50_100, tolerance_pct=0.30), \
            "0.3% tolerance on 50200 = 150 band; 50100 within tolerance"

    def test_limit_buy_triggers_at_or_below_level(self):
        plan = OrderPlan(order_type="Limit", entry_price=50_100)
        assert can_fill_now(plan, "buy", 50_000, tolerance_pct=0.0)
        assert not can_fill_now(plan, "buy", 50_300, tolerance_pct=0.0)

    def test_stop_sell_triggers_below_level(self):
        plan = OrderPlan(order_type="Stop", entry_price=1_980)
        assert can_fill_now(plan, "sell", 1_950, tolerance_pct=0.0)
        assert not can_fill_now(plan, "sell", 2_010, tolerance_pct=0.0)

    def test_limit_sell_triggers_at_or_above_level(self):
        plan = OrderPlan(order_type="Limit", entry_price=2_010)
        assert can_fill_now(plan, "sell", 2_020, tolerance_pct=0.0)
        assert not can_fill_now(plan, "sell", 1_990, tolerance_pct=0.0)


class TestPatternEntryDeferredException:
    def test_exception_carries_diagnostic_fields(self):
        from app.services.paper_trading import PatternEntryDeferred
        exc = PatternEntryDeferred(
            symbol="BTCUSDT", order_type="Stop", entry_price=50_200,
            current_price=50_000, pattern_type="bull_flag",
        )
        assert exc.symbol == "BTCUSDT"
        assert exc.order_type == "Stop"
        assert exc.entry_price == 50_200
        assert exc.pattern_type == "bull_flag"
        assert "Stop" in str(exc)
        assert "BTCUSDT" in str(exc)
        assert "50200" in str(exc) or "50_200" in str(exc) or "50,200" in str(exc) or "5e+04" in str(exc)


class TestTradingSignalSchema:
    def test_optional_pattern_fields_default_none(self):
        from app.services.indicators import TradingSignal, Signal
        s = TradingSignal(signal=Signal.BUY, confidence=0.7, price=100, indicators={}, reasoning="")
        assert s.entry_type is None
        assert s.entry_price is None
        assert s.pattern_type is None

    def test_pattern_fields_persist_when_set(self):
        from app.services.indicators import TradingSignal, Signal
        s = TradingSignal(
            signal=Signal.SELL, confidence=0.8, price=2_000, indicators={}, reasoning="",
            entry_type="Stop", entry_price=1_980, pattern_type="bear_flag",
        )
        assert s.entry_type == "Stop"
        assert s.entry_price == 1_980
        assert s.pattern_type == "bear_flag"
