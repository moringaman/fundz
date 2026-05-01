"""
Strategy Learner — Bayesian Parameter Optimization + RL Dataset Builder.

Two feedback loops:
  1. BAYESIAN TUNING — maintains Beta(α, β) posteriors over win rate for
     each (strategy, regime, parameter_range) bucket, then shifts agent
     configs toward the highest-probability parameter set.
  2. RL DATASET    — collects (decision_context, action, reward) triples
     from AgentRunRecord for export as a fine-tuning dataset.
"""

from __future__ import annotations

import json
import logging
import math
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# ── Beta-Bernoulli helpers ────────────────────────────────────────────


def beta_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5


def beta_sample(alpha: float, beta: float) -> float:
    """Thompson sample from Beta(α, β)."""
    return random.betavariate(alpha, beta)


# ── Outcome Bucket ────────────────────────────────────────────────────


@classmethod
def _sl_bucket(cls, sl_pct: float) -> str:
    if sl_pct <= 1.5: return "tight"
    if sl_pct <= 3.0: return "normal"
    return "wide"


@classmethod
def _tp_bucket(cls, tp_pct: float) -> str:
    if tp_pct <= 3.0: return "tight"
    if tp_pct <= 7.0: return "normal"
    return "wide"


class StrategyLearner:
    """Bayesian optimizer that tunes agent parameters from live trade outcomes.

    Maintains Beta(α, β) posteriors for win rate per
    (strategy, regime, sl_bucket, tp_bucket) combination and
    periodically shifts agent configs toward optimal buckets.
    """

    def __init__(self):
        self._posteriors: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"alpha": 1.0, "beta": 1.0}
        )

    # ── Bucket key ────────────────────────────────────────────────────

    def _bucket_key(
        self,
        strategy: str,
        regime: str,
        sl_pct: float,
        tp_pct: float,
        confidence: float,
    ) -> str:
        sl_b = _sl_bucket(sl_pct)
        tp_b = _tp_bucket(tp_pct)
        conf_b = "high" if confidence >= 0.7 else ("mid" if confidence >= 0.4 else "low")
        return f"{strategy}|{regime}|{sl_b}|{tp_b}|{conf_b}"

    # ── Ingestion ─────────────────────────────────────────────────────

    def observe(self, strategy: str, regime: str, sl_pct: float,
                tp_pct: float, confidence: float, pnl: float) -> None:
        """Record one trade outcome into the posterior."""
        key = self._bucket_key(strategy, regime, sl_pct, tp_pct, confidence)
        won = pnl > 0
        if won:
            self._posteriors[key]["alpha"] += 1.0
        else:
            self._posteriors[key]["beta"] += 1.0

    # ── Query ─────────────────────────────────────────────────────────

    def win_rate_for(self, strategy: str, regime: str,
                     sl_pct: float, tp_pct: float,
                     confidence: float) -> Optional[float]:
        """Expected win rate for a given parameter set."""
        key = self._bucket_key(strategy, regime, sl_pct, tp_pct, confidence)
        if key not in self._posteriors:
            return None
        p = self._posteriors[key]
        return beta_mean(p["alpha"], p["beta"])

    def best_parameters(self, strategy: str, regime: str,
                        confidence: float) -> Optional[Dict[str, Any]]:
        """Thompson-sample all buckets for this (strategy, regime, confidence)
        and return the parameter set with the highest sampled win rate."""
        prefix = f"{strategy}|{regime}|"
        candidates = []
        for key, p in self._posteriors.items():
            if not key.startswith(prefix):
                continue
            parts = key.split("|")
            if len(parts) != 5:
                continue
            _, _, sl_b, tp_b, conf_b = parts
            if conf_b != self._confidence_bucket(confidence):
                continue
            sampled = beta_sample(p["alpha"], p["beta"])
            candidates.append((sampled, sl_b, tp_b))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        _, best_sl_b, best_tp_b = candidates[0]

        sl_pct_map = {"tight": 1.5, "normal": 2.5, "wide": 4.0}
        tp_pct_map = {"tight": 3.0, "normal": 5.5, "wide": 8.0}

        return {
            "stop_loss_pct": sl_pct_map.get(best_sl_b, 2.5),
            "take_profit_pct": tp_pct_map.get(best_tp_b, 5.5),
            "confidence_threshold": confidence,
        }

    # ── Bulk ingest from DB ───────────────────────────────────────────

    async def ingest_from_db(self, hours: int = 168) -> int:
        """Load recent AgentRunRecord rows and update posteriors.
        Returns count of records ingested."""
        from app.database import get_async_session
        from app.models import AgentRunRecord
        from sqlalchemy import select

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        count = 0
        async with get_async_session() as db:
            rows = await db.execute(
                select(AgentRunRecord).where(
                    AgentRunRecord.timestamp >= cutoff,
                    AgentRunRecord.pnl.isnot(None),
                    AgentRunRecord.executed == True,
                )
            )
            for rec in rows.scalars().all():
                # Parse regime and parameter info from stored data
                # (regime is not stored on AgentRunRecord; we use a fallback)
                strategy = rec.strategy_type or "unknown"
                regime = "unknown"  # would need a regime lookup
                sl_pct = 2.5  # default, ideally from agent config
                tp_pct = 5.5
                conf = rec.confidence or 0.5
                self.observe(strategy, regime, sl_pct, tp_pct, conf, rec.pnl or 0.0)
                count += 1
        logger.info(f"Ingested {count} trade outcomes into Bayesian posteriors")
        return count

    # ── Apply optimal params to agents ────────────────────────────────

    async def tune_agents(self, hours: int = 168) -> List[str]:
        """Ingest recent outcomes, find optimal parameters per strategy,
        and update agent configs in the DB. Returns list of changes applied."""
        await self.ingest_from_db(hours=hours)

        from app.database import get_async_session
        from app.models import Agent as DBAgent
        from sqlalchemy import select
        from app.api.routes.settings import get_trading_prefs

        changes = []
        async with get_async_session() as db:
            agents = await db.execute(select(DBAgent).where(DBAgent.is_enabled == True))
            for agent in agents.scalars().all():
                strategy = agent.strategy_type
                conf = agent.config.get("confidence_threshold", 0.5) if isinstance(agent.config, dict) else 0.5
                optimal = self.best_parameters(strategy, "unknown", conf)
                if optimal is None:
                    continue

                config = agent.config if isinstance(agent.config, dict) else {}
                old_sl = config.get("stop_loss_pct", 2.5)
                old_tp = config.get("take_profit_pct", 5.5)
                new_sl = optimal["stop_loss_pct"]
                new_tp = optimal["take_profit_pct"]

                if abs(old_sl - new_sl) > 0.3 or abs(old_tp - new_tp) > 0.3:
                    config["stop_loss_pct"] = new_sl
                    config["take_profit_pct"] = new_tp
                    agent.config = config
                    changes.append(
                        f"{agent.name}: SL {old_sl}%→{new_sl}%, TP {old_tp}%→{new_tp}%"
                    )
            if changes:
                await db.commit()
                logger.info(f"Tuned {len(changes)} agents: {', '.join(changes)}")
        return changes

    def _confidence_bucket(self, conf: float) -> str:
        if conf >= 0.7: return "high"
        if conf >= 0.4: return "mid"
        return "low"

    def summary(self) -> List[Dict[str, Any]]:
        """Return posterior stats for the learning dashboard."""
        rows = []
        for key, p in sorted(self._posteriors.items()):
            parts = key.split("|")
            if len(parts) != 5:
                continue
            strategy, regime, sl_b, tp_b, conf_b = parts
            total = p["alpha"] + p["beta"] - 2
            if total < 3:
                continue
            rows.append({
                "strategy": strategy,
                "regime": regime,
                "sl_bucket": sl_b,
                "tp_bucket": tp_b,
                "confidence_bucket": conf_b,
                "wins": int(p["alpha"] - 1),
                "losses": int(p["beta"] - 1),
                "total": int(total),
                "win_rate": round(beta_mean(p["alpha"], p["beta"]) * 100, 1),
            })
        return rows


# ── Singleton ─────────────────────────────────────────────────────────

strategy_learner = StrategyLearner()


# ═══════════════════════════════════════════════════════════════════════
# RL Dataset Builder
# ═══════════════════════════════════════════════════════════════════════


def _is_profitable(pnl: float) -> str:
    return "positive" if pnl > 0 else "negative"


async def build_rl_dataset(
    days: int = 30,
    min_confidence: float = 0.0,
    output_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build a fine-tuning dataset from AgentRunRecord entries.

    Each record is a (system_prompt, agent_response, reward) triple:
      - system_prompt: truncated context + instruction
      - agent_response: the JSON action the LLM returned
      - reward: +1 for profitable trades, -1 for unprofitable, 0 for holds

    Returns the dataset as a list of dicts. If *output_path* is given,
    also writes JSONL to that path.
    """
    from app.database import get_async_session
    from app.models import AgentRunRecord
    from sqlalchemy import select

    cutoff = datetime.utcnow() - timedelta(days=days)
    dataset: List[Dict[str, Any]] = []

    async with get_async_session() as db:
        rows = await db.execute(
            select(AgentRunRecord).where(
                AgentRunRecord.timestamp >= cutoff,
                AgentRunRecord.llm_reasoning.isnot(None),
                AgentRunRecord.llm_reasoning != "",
            ).order_by(AgentRunRecord.timestamp.desc())
        )
        for rec in rows.scalars().all():
            if rec.confidence is not None and rec.confidence < min_confidence:
                continue

            # Reward: +1 for profitable executed trades, -1 for unprofitable, 0 otherwise
            if rec.executed and rec.pnl is not None:
                reward = 1.0 if rec.pnl > 0 else -1.0
            else:
                reward = 0.0

            entry = {
                "system_prompt": (
                    f"You are a trading agent managing a {rec.strategy_type or 'unknown'} strategy "
                    f"on {rec.symbol}. Your task is to decide: buy, sell, or hold. "
                    f"Output JSON: {{\"action\":\"buy|sell|hold\",\"confidence\":0.0-1.0,\"reasoning\":\"...\"}}"
                ),
                "agent_response": rec.llm_reasoning,
                "reward": reward,
                "signal": rec.signal,
                "confidence": rec.confidence,
                "pnl": rec.pnl,
                "symbol": rec.symbol,
                "strategy": rec.strategy_type,
                "timestamp": rec.timestamp.isoformat() if rec.timestamp else None,
            }
            dataset.append(entry)

    logger.info(f"Built RL dataset: {len(dataset)} records ({days}d window)")

    if output_path:
        with open(output_path, "w") as f:
            for entry in dataset:
                f.write(json.dumps(entry) + "\n")
        logger.info(f"Wrote RL dataset to {output_path}")

    return dataset


async def export_openai_finetuning_jsonl(
    days: int = 30,
    output_path: str = "/tmp/rl_dataset.jsonl",
    min_confidence: float = 0.5,
) -> int:
    """Export a dataset in OpenAI fine-tuning JSONL format.

    Each line:
      {"messages": [
        {"role": "system", "content": "<system prompt>"},
        {"role": "assistant", "content": "<agent response>"}
      ]}

    Only exports trades with |reward| > 0.5 (meaningful profit or loss)
    and confidence >= min_confidence.
    """
    import json

    dataset = await build_rl_dataset(days=days, min_confidence=min_confidence)
    count = 0
    with open(output_path, "w") as f:
        for entry in dataset:
            if abs(entry["reward"]) < 0.5:
                continue
            # For positive examples, use the response as-is
            # For negative examples, prepend a "what not to do" prefix
            content = entry["agent_response"]
            if entry["reward"] < 0:
                content = f"[UNPROFITABLE TRADE - LEARN FROM THIS MISTAKE]\n{content}"
            else:
                content = f"[PROFITABLE TRADE]\n{content}"

            line = {
                "messages": [
                    {"role": "system", "content": entry["system_prompt"]},
                    {"role": "assistant", "content": content},
                ]
            }
            f.write(json.dumps(line) + "\n")
            count += 1

    logger.info(f"Exported {count} examples to {output_path} for OpenAI fine-tuning")
    return count
