"""
Phase 9.1 — Consistency-Gated Capital

Computes two institutional risk metrics per trader:

1. **40% Rule**: No single winning trade may represent >40% of the trader's total
   period profit.  A trader flagged INCONSISTENT has their capital increase blocked
   until they demonstrate broader distribution of gains.

2. **Rolling Sharpe Gate**: Capital multiplier based on the trader's live 20-trade
   Sharpe ratio.
       <0.5  → 0.7× multiplier (capital reduced)
       0.5–1.0 → 1.0× (unchanged)
       ≥1.0  → 1.3× (capital boosted, subject to MAX_ALLOCATION_PCT cap)

Data source: `AgentRunRecord.pnl` — set on each closed position, net of fees.
Requires at least MIN_TRADES_FOR_SCORING closed trades before flagging INCONSISTENT
(new traders default to INSUFFICIENT_DATA with neutral multiplier).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_SCORING = 5      # minimum closed trades before the 40% rule applies
SHARPE_WINDOW = 20              # rolling window of most-recent trades used for Sharpe


@dataclass
class ConsistencyResult:
    trader_id: str
    consistency_score: float        # 0.0–1.0; higher = more consistent
    consistency_flag: str           # "CONSISTENT" | "INCONSISTENT" | "INSUFFICIENT_DATA"
    max_single_trade_pnl: float     # largest single winning trade PnL in period
    total_pnl: float                # sum of all closed-trade PnL in period
    offending_trade_pct: float      # max_winning / total_pnl (0.0 if not applicable)
    sharpe: float                   # rolling Sharpe (raw, unannualised)
    sharpe_tier: str                # "high" | "medium" | "low"
    sharpe_multiplier: float        # 1.3 | 1.0 | 0.7
    trade_count: int                # number of closed trades used for scoring


async def compute_consistency(
    trader_id: str,
    agent_ids: List[str],
    is_paper: bool = True,
) -> ConsistencyResult:
    """Return consistency & Sharpe metrics for a trader.

    Queries the last 100 closed trades across all of the trader's agents.
    Safe to call every allocation cycle — no writes, read-only.
    """
    from sqlalchemy import select
    from app.models import AgentRunRecord
    from app.database import get_async_session

    _neutral = ConsistencyResult(
        trader_id=trader_id,
        consistency_score=0.5,
        consistency_flag="INSUFFICIENT_DATA",
        max_single_trade_pnl=0.0,
        total_pnl=0.0,
        offending_trade_pct=0.0,
        sharpe=0.0,
        sharpe_tier="medium",
        sharpe_multiplier=1.0,
        trade_count=0,
    )

    if not agent_ids:
        return _neutral

    try:
        async with get_async_session() as db:
            result = await db.execute(
                select(AgentRunRecord.pnl)
                .where(
                    AgentRunRecord.agent_id.in_(agent_ids),
                    AgentRunRecord.pnl.is_not(None),
                    AgentRunRecord.use_paper == is_paper,
                )
                .order_by(AgentRunRecord.timestamp.desc())
                .limit(100)
            )
            pnls: List[float] = [row[0] for row in result.fetchall() if row[0] is not None]
    except Exception as exc:
        logger.warning(f"ConsistencyScorer: DB query failed for trader {trader_id}: {exc}")
        return _neutral

    trade_count = len(pnls)

    # ── Rolling Sharpe (last SHARPE_WINDOW trades) ──────────────────────────
    window = pnls[:SHARPE_WINDOW]
    if len(window) >= 3:
        mean_pnl = statistics.mean(window)
        stdev = statistics.pstdev(window)   # population stdev (no Bessel correction)
        sharpe = mean_pnl / stdev if stdev > 0 else (10.0 if mean_pnl > 0 else 0.0)
    else:
        sharpe = 0.0

    if sharpe >= 1.0:
        sharpe_tier, sharpe_multiplier = "high", 1.3
    elif sharpe >= 0.5:
        sharpe_tier, sharpe_multiplier = "medium", 1.0
    else:
        sharpe_tier, sharpe_multiplier = "low", 0.7

    # ── 40% Rule ─────────────────────────────────────────────────────────────
    if trade_count < MIN_TRADES_FOR_SCORING:
        return ConsistencyResult(
            trader_id=trader_id,
            consistency_score=0.5,
            consistency_flag="INSUFFICIENT_DATA",
            max_single_trade_pnl=0.0,
            total_pnl=sum(pnls),
            offending_trade_pct=0.0,
            sharpe=round(sharpe, 3),
            sharpe_tier=sharpe_tier,
            sharpe_multiplier=sharpe_multiplier,
            trade_count=trade_count,
        )

    total_pnl = sum(pnls)
    winning_pnls = [p for p in pnls if p > 0]
    max_winning = max(winning_pnls, default=0.0)

    if total_pnl > 0 and winning_pnls:
        offending_pct = max_winning / total_pnl
        consistency_score = max(0.0, 1.0 - offending_pct)
        consistency_flag = "INCONSISTENT" if offending_pct > 0.40 else "CONSISTENT"
    else:
        # Net-losing trader: Sharpe gate still applies but 40% rule doesn't penalise further
        offending_pct = 0.0
        consistency_score = max(0.0, 0.3 + (trade_count / 100) * 0.2)
        consistency_flag = "CONSISTENT"

    logger.debug(
        f"ConsistencyScorer [{trader_id[:8]}]: flag={consistency_flag}, "
        f"score={consistency_score:.2f}, sharpe={sharpe:.2f} ({sharpe_tier}), "
        f"trades={trade_count}, max_win=${max_winning:.2f}, total_pnl=${total_pnl:.2f}"
    )

    return ConsistencyResult(
        trader_id=trader_id,
        consistency_score=round(consistency_score, 3),
        consistency_flag=consistency_flag,
        max_single_trade_pnl=round(max_winning, 2),
        total_pnl=round(total_pnl, 2),
        offending_trade_pct=round(offending_pct, 3),
        sharpe=round(sharpe, 3),
        sharpe_tier=sharpe_tier,
        sharpe_multiplier=sharpe_multiplier,
        trade_count=trade_count,
    )
