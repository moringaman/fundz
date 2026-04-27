"""
Signal fusion: probabilistic ensemble over independent evidence streams.

Replaces the binary "gate chain" approach where every input is a hard pass/fail
veto. Instead, each evidence source contributes signed log-odds proportional to
its directional confidence and per-source weight; the sigmoid of the sum gives
the fused probability of the signal direction being correct.

Phase 1 (current): fusion *modulates* the strategy-emitted confidence — it does
not override the strategy's chosen direction. A signal where many independent
oracles agree gets a confidence boost; one where sources actively contradict
gets penalised. The strategy still selects direction.

Phase 2 (future, requires offline weight tuning against historical PnL):
  - argmax over fused long/short/flat probabilities replaces strategy direction
  - weights re-fit nightly via logistic regression on persisted evidence vectors
  - per-strategy weight overlays (e.g. mean-reversion downweights momentum-style
    evidence and vice versa)

Default weights are seeded from architectural intuition (README hierarchy +
PnL impact estimate). Every signal's evidence vector is persisted to JSONL so
weights can be empirically refit once enough trades have been logged.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1

EVIDENCE_LOG_PATH = os.environ.get(
    "SIGNAL_FUSION_LOG_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "logs", "evidence_vectors.jsonl"),
)


DEFAULT_WEIGHTS: Dict[str, float] = {
    "strategy_signal":   1.50,
    "ta_confluence":     1.00,
    "regime_alignment":  0.60,
    "htf_trend":         0.50,
    "pattern_strength":  0.60,
    "divergence":        0.40,
    "whale_flow":        0.30,
    "sentiment":         0.20,
}


@dataclass
class EvidenceVector:
    """
    Captures all evidence streams that bear on a single position-opening decision.

    Direction encoding for every field: +1 = bullish, -1 = bearish, 0 = neutral.
    Magnitude encoding: confidence in [0.0, 1.0].

    Each (direction, confidence) pair is converted to a signed log-odds
    contribution `direction * confidence * weight` during fusion.
    """
    agent_id: str
    symbol: str
    strategy: str
    timeframe: str

    strategy_signal:   tuple = (0, 0.0)
    ta_confluence:     tuple = (0, 0.0)
    regime_alignment:  tuple = (0, 0.0)
    htf_trend:         tuple = (0, 0.0)
    pattern_strength:  tuple = (0, 0.0)
    divergence:        tuple = (0, 0.0)
    whale_flow:        tuple = (0, 0.0)
    sentiment:         tuple = (0, 0.0)

    extra: Dict[str, tuple] = field(default_factory=dict)
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_components(self) -> Dict[str, tuple]:
        return {
            "strategy_signal":  self.strategy_signal,
            "ta_confluence":    self.ta_confluence,
            "regime_alignment": self.regime_alignment,
            "htf_trend":        self.htf_trend,
            "pattern_strength": self.pattern_strength,
            "divergence":       self.divergence,
            "whale_flow":       self.whale_flow,
            "sentiment":        self.sentiment,
            **self.extra,
        }


@dataclass
class FusedSignal:
    direction: int
    fused_confidence: float
    raw_confidence: float
    log_odds: float
    contributions: Dict[str, float]
    agreement_score: float


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _direction_from_signal(signal_str: Optional[str]) -> int:
    if not signal_str:
        return 0
    s = signal_str.lower()
    if s == "buy":
        return 1
    if s == "sell":
        return -1
    return 0


def _direction_from_label(label: Optional[str], bullish_labels: tuple, bearish_labels: tuple) -> int:
    if not label:
        return 0
    s = label.lower()
    if s in bullish_labels:
        return 1
    if s in bearish_labels:
        return -1
    return 0


def build_evidence_vector(
    *,
    agent_id: str,
    symbol: str,
    strategy: str,
    timeframe: str,
    signal: str,
    raw_confidence: float,
    market_context: Optional[Dict] = None,
    indicators: Optional[Dict] = None,
) -> EvidenceVector:
    """
    Construct an EvidenceVector from data already gathered by run_agent.

    No new fetches: this consumes only what the agent loop already has.
    Missing inputs degrade gracefully to (0, 0.0) — neutral, zero-weight.
    """
    market_context = market_context or {}
    indicators = indicators or {}

    strat_dir = _direction_from_signal(signal)
    ev = EvidenceVector(
        agent_id=agent_id,
        symbol=symbol,
        strategy=strategy,
        timeframe=timeframe,
        strategy_signal=(strat_dir, float(max(0.0, min(1.0, raw_confidence)))),
    )

    ta_score = float(market_context.get("ta_confluence_score") or 0.0)
    ta_dir_label = market_context.get("ta_signal_direction") or market_context.get("ta_direction")
    ta_dir = _direction_from_signal(ta_dir_label) if isinstance(ta_dir_label, str) else 0
    if ta_dir == 0 and ta_score:
        ta_dir = strat_dir
    ev.ta_confluence = (ta_dir, max(0.0, min(1.0, ta_score)))

    regime = (market_context.get("regime") or "").lower()
    if regime in ("trending_up",):
        ev.regime_alignment = (1, 0.7)
    elif regime in ("trending_down",):
        ev.regime_alignment = (-1, 0.7)
    elif regime in ("volatility_compression", "pre_breakout", "consolidating"):
        ev.regime_alignment = (strat_dir, 0.4)
    elif regime in ("ranging", "low_volatility"):
        ev.regime_alignment = (0, 0.0)
    elif regime in ("high_volatility", "volatile"):
        ev.regime_alignment = (strat_dir, 0.2)

    htf = market_context.get("htf_trend")
    htf_dir = _direction_from_label(htf, bullish_labels=("bullish", "up"), bearish_labels=("bearish", "down"))
    ev.htf_trend = (htf_dir, 0.6 if htf_dir != 0 else 0.0)

    pat = market_context.get("pattern") or indicators.get("pattern")
    if isinstance(pat, dict):
        pat_dir = _direction_from_label(
            pat.get("direction") or pat.get("bias"),
            bullish_labels=("bullish", "up", "long"),
            bearish_labels=("bearish", "down", "short"),
        )
        pat_conf = float(pat.get("confidence") or pat.get("strength") or 0.0)
        ev.pattern_strength = (pat_dir, max(0.0, min(1.0, pat_conf)))

    bull_div = bool(indicators.get("bullish_divergence"))
    bear_div = bool(indicators.get("bearish_divergence"))
    div_weight = float(indicators.get("divergence_weight") or 0.0)
    if bull_div and not bear_div:
        ev.divergence = (1, max(0.0, min(1.0, div_weight or 0.6)))
    elif bear_div and not bull_div:
        ev.divergence = (-1, max(0.0, min(1.0, div_weight or 0.6)))

    whale = market_context.get("whale_flow") or market_context.get("whale_intelligence")
    if isinstance(whale, dict):
        whale_dir = _direction_from_label(
            whale.get("net_direction") or whale.get("direction"),
            bullish_labels=("inflow", "accumulation", "bullish"),
            bearish_labels=("outflow", "distribution", "bearish"),
        )
        whale_conf = float(whale.get("confidence") or 0.0)
        ev.whale_flow = (whale_dir, max(0.0, min(1.0, whale_conf)))

    sent = market_context.get("sentiment")
    if isinstance(sent, dict):
        sent_dir = _direction_from_label(
            sent.get("label") or sent.get("polarity"),
            bullish_labels=("bullish", "positive"),
            bearish_labels=("bearish", "negative"),
        )
        sent_score = float(sent.get("score") or 0.0)
        ev.sentiment = (sent_dir, max(0.0, min(1.0, abs(sent_score))))

    return ev


def fuse(
    evidence: EvidenceVector,
    weights: Optional[Dict[str, float]] = None,
) -> FusedSignal:
    """
    Aggregate evidence streams into a calibrated probability and final direction.

    Algorithm:
        1. For each source, contribution = direction * confidence * weight.
        2. Sum contributions → log-odds (centred at 0 = no info).
        3. Two sigmoids: P(long) = sigmoid(log_odds), P(short) = sigmoid(-log_odds).
        4. Final direction = sign(log_odds); fused_confidence = max(P(long), P(short)).
        5. Floor fused_confidence at the strategy's raw confidence to ensure fusion
           never DOWNGRADES a high-conviction strategy signal when the other sources
           are merely silent (zero contribution). Fusion only attenuates when sources
           actively disagree (negative contribution).
    """
    w = weights or DEFAULT_WEIGHTS
    components = evidence.as_components()

    contributions: Dict[str, float] = {}
    log_odds = 0.0
    agreeing_weight = 0.0
    total_active_weight = 0.0
    strat_dir = evidence.strategy_signal[0]

    for source_name, (direction, confidence) in components.items():
        weight = float(w.get(source_name, 0.0))
        if weight == 0.0 or direction == 0 or confidence <= 0.0:
            contributions[source_name] = 0.0
            continue
        contribution = float(direction) * float(confidence) * weight
        log_odds += contribution
        contributions[source_name] = contribution

        if source_name != "strategy_signal" and strat_dir != 0:
            total_active_weight += weight * confidence
            if direction == strat_dir:
                agreeing_weight += weight * confidence

    p_long = _sigmoid(log_odds)
    p_short = 1.0 - p_long

    if log_odds > 0:
        direction = 1
        fused_confidence = p_long
    elif log_odds < 0:
        direction = -1
        fused_confidence = p_short
    else:
        direction = strat_dir
        fused_confidence = evidence.strategy_signal[1]

    raw_conf = float(evidence.strategy_signal[1] or 0.0)
    if direction == strat_dir and raw_conf > fused_confidence:
        fused_confidence = raw_conf

    fused_confidence = max(0.0, min(1.0, fused_confidence))

    agreement_score = (agreeing_weight / total_active_weight) if total_active_weight > 0 else 0.5

    return FusedSignal(
        direction=direction,
        fused_confidence=round(fused_confidence, 4),
        raw_confidence=round(raw_conf, 4),
        log_odds=round(log_odds, 4),
        contributions={k: round(v, 4) for k, v in contributions.items()},
        agreement_score=round(agreement_score, 4),
    )


def persist_evidence(evidence: EvidenceVector, fused: FusedSignal, log_path: Optional[str] = None) -> None:
    """
    Append a single evidence + fusion record to JSONL for offline weight tuning.
    Failures are swallowed and logged at DEBUG: this must never block trading.
    """
    path = log_path or EVIDENCE_LOG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {
            "schema_version": SCHEMA_VERSION,
            "evidence": asdict(evidence),
            "fused": asdict(fused),
        }
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.debug(f"Evidence vector persistence skipped: {e}")
