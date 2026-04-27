import os
import json
import math
import tempfile

from app.services.signal_fusion import (
    EvidenceVector,
    FusedSignal,
    DEFAULT_WEIGHTS,
    build_evidence_vector,
    fuse,
    persist_evidence,
)


class TestBuildEvidenceVector:
    def test_buy_with_full_corroboration(self):
        ev = build_evidence_vector(
            agent_id="a1",
            symbol="BTCUSDT",
            strategy="momentum",
            timeframe="1h",
            signal="buy",
            raw_confidence=0.55,
            market_context={
                "ta_confluence_score": 0.8,
                "ta_signal_direction": "buy",
                "regime": "trending_up",
                "htf_trend": "bullish",
            },
            indicators={"bullish_divergence": True, "divergence_weight": 0.7},
        )
        assert ev.strategy_signal == (1, 0.55)
        assert ev.ta_confluence == (1, 0.8)
        assert ev.regime_alignment == (1, 0.7)
        assert ev.htf_trend == (1, 0.6)
        assert ev.divergence == (1, 0.7)

    def test_sell_with_full_opposition(self):
        ev = build_evidence_vector(
            agent_id="a1",
            symbol="BTCUSDT",
            strategy="momentum",
            timeframe="1h",
            signal="sell",
            raw_confidence=0.6,
            market_context={
                "ta_confluence_score": 0.7,
                "ta_signal_direction": "sell",
                "regime": "trending_down",
                "htf_trend": "bearish",
            },
            indicators={"bearish_divergence": True, "divergence_weight": 0.5},
        )
        assert ev.strategy_signal == (-1, 0.6)
        assert ev.ta_confluence == (-1, 0.7)
        assert ev.regime_alignment == (-1, 0.7)
        assert ev.htf_trend == (-1, 0.6)
        assert ev.divergence == (-1, 0.5)

    def test_silent_context_yields_zero_evidence(self):
        ev = build_evidence_vector(
            agent_id="a1", symbol="BTCUSDT", strategy="momentum", timeframe="1h",
            signal="buy", raw_confidence=0.7,
            market_context={}, indicators={},
        )
        assert ev.strategy_signal == (1, 0.7)
        assert ev.ta_confluence == (0, 0.0)
        assert ev.regime_alignment == (0, 0.0)
        assert ev.htf_trend == (0, 0.0)
        assert ev.divergence == (0, 0.0)
        assert ev.pattern_strength == (0, 0.0)

    def test_pattern_evidence_extracted(self):
        ev = build_evidence_vector(
            agent_id="a1", symbol="BTCUSDT", strategy="breakout", timeframe="1h",
            signal="buy", raw_confidence=0.5,
            market_context={"pattern": {"direction": "bullish", "confidence": 0.85}},
        )
        assert ev.pattern_strength == (1, 0.85)


class TestFuse:
    def test_full_agreement_boosts_confidence(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.55),
            ta_confluence=(1, 0.8),
            regime_alignment=(1, 0.7),
            htf_trend=(1, 0.6),
        )
        fused = fuse(ev)
        assert fused.direction == 1
        assert fused.fused_confidence > 0.55, "Agreement must boost confidence above raw"
        assert fused.agreement_score == 1.0
        assert fused.log_odds > 0

    def test_full_opposition_flips_direction(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.55),
            ta_confluence=(-1, 0.9),
            regime_alignment=(-1, 0.8),
            htf_trend=(-1, 0.7),
            divergence=(-1, 0.7),
        )
        fused = fuse(ev)
        assert fused.direction == -1, "Overwhelming opposing evidence must flip direction"
        assert fused.log_odds < 0

    def test_silent_evidence_does_not_downgrade(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.7),
        )
        fused = fuse(ev)
        assert fused.direction == 1
        assert fused.fused_confidence >= 0.7, (
            "Silent supporting evidence must never downgrade strategy confidence"
        )

    def test_hold_signal_propagates(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(0, 0.0),
        )
        fused = fuse(ev)
        assert fused.direction == 0

    def test_contributions_signed_and_summed(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.5),
            ta_confluence=(-1, 0.5),
        )
        fused = fuse(ev)
        assert fused.contributions["strategy_signal"] == 0.5 * DEFAULT_WEIGHTS["strategy_signal"]
        assert fused.contributions["ta_confluence"] == -0.5 * DEFAULT_WEIGHTS["ta_confluence"]
        expected_log_odds = (
            0.5 * DEFAULT_WEIGHTS["strategy_signal"]
            - 0.5 * DEFAULT_WEIGHTS["ta_confluence"]
        )
        assert math.isclose(fused.log_odds, expected_log_odds, abs_tol=1e-4)

    def test_zero_weight_source_does_not_contribute(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.6),
            sentiment=(-1, 0.9),
        )
        weights = {**DEFAULT_WEIGHTS, "sentiment": 0.0}
        fused = fuse(ev, weights=weights)
        assert fused.contributions["sentiment"] == 0.0
        assert fused.direction == 1

    def test_fused_confidence_bounded_in_unit_interval(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 1.0),
            ta_confluence=(1, 1.0),
            regime_alignment=(1, 1.0),
            htf_trend=(1, 1.0),
            divergence=(1, 1.0),
            pattern_strength=(1, 1.0),
            whale_flow=(1, 1.0),
            sentiment=(1, 1.0),
        )
        fused = fuse(ev)
        assert 0.0 <= fused.fused_confidence <= 1.0

    def test_agreement_score_when_split(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.5),
            ta_confluence=(1, 0.5),
            regime_alignment=(-1, 0.5),
        )
        fused = fuse(ev)
        assert 0.0 < fused.agreement_score < 1.0


class TestPersistEvidence:
    def test_persist_writes_jsonl(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.6),
        )
        fused = fuse(ev)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "evidence.jsonl")
            persist_evidence(ev, fused, log_path=log_path)
            with open(log_path) as f:
                line = f.readline()
            record = json.loads(line)
            assert record["schema_version"] == 1
            assert record["evidence"]["agent_id"] == "a"
            assert record["fused"]["direction"] == 1

    def test_persist_failure_is_silent(self):
        ev = EvidenceVector(
            agent_id="a", symbol="BTC", strategy="momentum", timeframe="1h",
            strategy_signal=(1, 0.6),
        )
        fused = fuse(ev)
        persist_evidence(ev, fused, log_path="/nonexistent/dir/that/does/not/exist/x.jsonl")
