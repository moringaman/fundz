"""
Phase 9.2 — Survival Bias: Drawdown Warnings & Trader "Firing"

Three-tier escalation based on lifetime drawdown from peak cumulative PnL:

  CAUTION    -5%   Elena warns in team chat; capital unchanged
  WARNING    -7%   "Pink Slip" injected into trader LLM prompt;
                   capital allocation cut 50%; Telegram alert
  TERMINATED -10%  Trader disabled; snapshot saved to TraderLegacy;
                   successor spawned with LLM-generated "What Not To Do"

"Lifetime drawdown" is peak-to-trough of the trader's cumulative closed PnL.
Peak is only ever updated upward. Drawdown = (peak - current) / (peak + seed_capital)
where seed_capital is used to normalise when peak is near zero.
"""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

CAUTION_THRESHOLD    = -0.05   # -5%
WARNING_THRESHOLD    = -0.07   # -7%
TERMINATED_THRESHOLD = -0.10   # -10%
SEED_CAPITAL         = 10_000  # normalisation floor when peak PnL is near zero

# Pink Slip text injected into the trader's system prompt at WARNING level
PINK_SLIP_TEXT = """
⚠️ FORMAL WARNING — CAPITAL REVIEW

Your portfolio has drawn down {drawdown_pct:.1f}% from its peak.
This is a formal warning: one more significant loss event will result in your termination.

You are now operating under SURVIVAL MODE rules:
- REDUCE position sizes by 50% until drawdown recovers above -5%
- AVOID low-conviction setups (confidence < 0.75)
- DO NOT average into losing positions
- PRIORITISE capital preservation over profit capture
- Any trade that pushes drawdown below -10% will result in your immediate termination

Your allocation has been reduced by 50% pending recovery. Act accordingly.
"""


@dataclass
class DrawdownStatus:
    trader_id: str
    trader_name: str
    current_pnl: float
    peak_pnl: float
    drawdown_pct: float          # positive = drawdown (e.g. 7.2 means -7.2%)
    warning_level: Optional[str] # None | "caution" | "warning" | "terminated"
    prev_warning_level: Optional[str]
    tier_changed: bool


async def update_trader_drawdown(
    trader_id: str,
    agent_ids: List[str],
    is_paper: bool = True,
) -> DrawdownStatus:
    """
    Recalculate the trader's lifetime drawdown from closed trades, update DB,
    check warning tiers, and trigger side effects (team chat, Telegram, termination).

    Safe to call after every trade close. Returns the current status.
    """
    from sqlalchemy import select
    from app.models import AgentRunRecord, Trader
    from app.database import get_async_session

    # ── Pull cumulative closed-trade PnL ─────────────────────────────────────
    current_pnl = 0.0
    if agent_ids:
        try:
            async with get_async_session() as db:
                result = await db.execute(
                    select(AgentRunRecord.pnl, AgentRunRecord.timestamp)
                    .where(
                        AgentRunRecord.agent_id.in_(agent_ids),
                        AgentRunRecord.pnl.is_not(None),
                        AgentRunRecord.use_paper == is_paper,
                    )
                    .order_by(AgentRunRecord.timestamp)
                )
                rows = result.fetchall()
                current_pnl = sum(r[0] for r in rows if r[0] is not None)
        except Exception as exc:
            logger.warning(f"DrawdownMonitor: failed to query PnL for {trader_id}: {exc}")

    # ── Load trader, compute drawdown ─────────────────────────────────────────
    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            return DrawdownStatus(
                trader_id=trader_id, trader_name="?", current_pnl=current_pnl,
                peak_pnl=0.0, drawdown_pct=0.0, warning_level=None,
                prev_warning_level=None, tier_changed=False,
            )

        peak = trader.lifetime_peak_balance or 0.0
        prev_level = trader.drawdown_warning_level
        trader_name = trader.name

        # Update peak if current PnL is a new high
        if current_pnl > peak:
            peak = current_pnl
            trader.lifetime_peak_balance = peak

        # Drawdown as a fraction of (peak + seed_capital) to handle near-zero peaks
        normaliser = abs(peak) + SEED_CAPITAL
        drawdown_pct = (peak - current_pnl) / normaliser * 100  # positive = loss from peak

        # Determine tier
        if drawdown_pct >= abs(TERMINATED_THRESHOLD) * 100:
            new_level: Optional[str] = "terminated"
        elif drawdown_pct >= abs(WARNING_THRESHOLD) * 100:
            new_level = "warning"
        elif drawdown_pct >= abs(CAUTION_THRESHOLD) * 100:
            new_level = "caution"
        else:
            new_level = None

        trader.lifetime_drawdown_pct = drawdown_pct
        trader.drawdown_warning_level = new_level
        await db.commit()

    tier_changed = new_level != prev_level
    status = DrawdownStatus(
        trader_id=trader_id,
        trader_name=trader_name,
        current_pnl=current_pnl,
        peak_pnl=peak,
        drawdown_pct=drawdown_pct,
        warning_level=new_level,
        prev_warning_level=prev_level,
        tier_changed=tier_changed,
    )

    # ── Trigger side effects only on tier change ──────────────────────────────
    if tier_changed:
        await _handle_tier_change(status, agent_ids, is_paper)

    return status


async def _handle_tier_change(status: DrawdownStatus, agent_ids: List[str], is_paper: bool):
    """Fire team chat messages, Telegram alerts, and termination logic on tier change."""
    from app.services.team_chat import team_chat
    from app.services.telegram_service import telegram_service

    level = status.warning_level
    prev = status.prev_warning_level
    name = status.trader_name
    dd = status.drawdown_pct

    if level == "caution" and prev is None:
        # Elena: polite concern
        await team_chat.add_message(
            agent_role="executive_assistant",
            content=(
                f"⚠️ **Drawdown Alert — {name}**: portfolio has pulled back "
                f"**{dd:.1f}%** from its peak. Still within acceptable range, "
                f"but worth watching. Tightening risk posture would be wise."
            ),
            message_type="alert",
        )

    elif level == "warning" and prev in (None, "caution"):
        # Elena: serious warning + cut allocation 50%
        await team_chat.add_message(
            agent_role="executive_assistant",
            content=(
                f"🚨 **FORMAL WARNING — {name}**: drawdown has reached **{dd:.1f}%** "
                f"(threshold: {abs(WARNING_THRESHOLD)*100:.0f}%). "
                f"Pink Slip issued. Capital allocation cut 50%. "
                f"Operating under Survival Mode until drawdown recovers above "
                f"{abs(CAUTION_THRESHOLD)*100:.0f}%."
            ),
            message_type="alert",
        )
        await _cut_allocation_50pct(status.trader_id)
        # Telegram alert
        try:
            await telegram_service.send(
                f"🚨 *Trader Warning — Pink Slip Issued*\n\n"
                f"*{name}* is at *{dd:.1f}%* drawdown from peak.\n"
                f"Capital allocation cut 50%.\n"
                f"Termination threshold: {abs(TERMINATED_THRESHOLD)*100:.0f}%."
            )
        except Exception as exc:
            logger.warning(f"Telegram alert failed for WARNING tier: {exc}")

    elif level == "terminated":
        await _terminate_trader(status, agent_ids, is_paper)


async def _cut_allocation_50pct(trader_id: str):
    """Halve a trader's current allocation_pct after WARNING tier crossed."""
    from app.models import Trader
    from app.database import get_async_session

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if trader:
            new_pct = max(trader.allocation_pct * 0.5, 10.0)
            trader.allocation_pct = new_pct
            await db.commit()
            logger.info(f"9.2 Gold Slip: {trader.name} allocation cut to {new_pct:.1f}%")


async def _terminate_trader(status: DrawdownStatus, agent_ids: List[str], is_paper: bool):
    """
    Disable the trader, snapshot to TraderLegacy, spawn successor.
    """
    from sqlalchemy import select
    from app.models import Trader, TraderLegacy, AgentRunRecord, Agent
    from app.database import get_async_session
    from app.services.team_chat import team_chat
    from app.services.telegram_service import telegram_service

    logger.warning(f"9.2 Termination: {status.trader_name} reached {status.drawdown_pct:.1f}% drawdown")

    # Fetch worst 5 trades for evolutionary context
    worst_summary = ""
    try:
        worst_summary = await _generate_worst_trades_summary(
            status.trader_id, agent_ids, is_paper
        )
    except Exception as exc:
        logger.warning(f"Worst trades summary failed: {exc}")
        worst_summary = "Insufficient trade data for analysis."

    async with get_async_session() as db:
        trader = await db.get(Trader, status.trader_id)
        if not trader:
            return

        # Aggregate closed-trade stats for snapshot
        total_trades = 0
        win_trades = 0
        if agent_ids:
            result = await db.execute(
                select(AgentRunRecord.pnl)
                .where(
                    AgentRunRecord.agent_id.in_(agent_ids),
                    AgentRunRecord.pnl.is_not(None),
                    AgentRunRecord.use_paper == is_paper,
                )
            )
            pnls = [r[0] for r in result.fetchall() if r[0] is not None]
            total_trades = len(pnls)
            win_trades = sum(1 for p in pnls if p > 0)

        win_rate = win_trades / total_trades if total_trades > 0 else 0.0

        legacy = TraderLegacy(
            original_trader_id=trader.id,
            name=trader.name,
            llm_model=trader.llm_model,
            total_pnl=status.current_pnl,
            total_trades=total_trades,
            win_rate=win_rate,
            lifetime_peak_balance=status.peak_pnl,
            lifetime_drawdown_pct=status.drawdown_pct,
            termination_reason=f"Drawdown -{status.drawdown_pct:.1f}% exceeded -{abs(TERMINATED_THRESHOLD)*100:.0f}% threshold",
            worst_trades_summary=worst_summary,
            config_snapshot=dict(trader.config or {}),
        )
        db.add(legacy)

        # Disable the trader
        trader.is_enabled = False
        trader.drawdown_warning_level = "terminated"
        await db.commit()

    # Team chat + Telegram
    await team_chat.add_message(
        agent_role="executive_assistant",
        content=(
            f"🔴 **TERMINATED — {status.trader_name}**: drawdown reached "
            f"**{status.drawdown_pct:.1f}%** (limit {abs(TERMINATED_THRESHOLD)*100:.0f}%). "
            f"All positions will be closed. A successor trader will be onboarded with "
            f"the lessons learned from this run."
        ),
        message_type="alert",
    )
    try:
        await telegram_service.send(
            f"🔴 *Trader Terminated*\n\n"
            f"*{status.trader_name}* has been removed from the fund.\n"
            f"Final drawdown: *{status.drawdown_pct:.1f}%*\n"
            f"Successor trader spawning..."
        )
    except Exception as exc:
        logger.warning(f"Telegram termination alert failed: {exc}")

    # Spawn successor
    try:
        await _spawn_successor(status, worst_summary)
    except Exception as exc:
        logger.error(f"Failed to spawn successor for {status.trader_name}: {exc}")


async def _generate_worst_trades_summary(
    trader_id: str, agent_ids: List[str], is_paper: bool
) -> str:
    """Ask the LLM to summarise the 5 worst trades as evolutionary guidance."""
    from sqlalchemy import select
    from app.models import AgentRunRecord
    from app.database import get_async_session
    from app.services.llm import LLMService

    if not agent_ids:
        return "No trade history available."

    async with get_async_session() as db:
        result = await db.execute(
            select(AgentRunRecord.symbol, AgentRunRecord.signal,
                   AgentRunRecord.pnl, AgentRunRecord.timestamp, AgentRunRecord.error)
            .where(
                AgentRunRecord.agent_id.in_(agent_ids),
                AgentRunRecord.pnl.is_not(None),
                AgentRunRecord.use_paper == is_paper,
            )
            .order_by(AgentRunRecord.pnl)
            .limit(5)
        )
        worst = result.fetchall()

    if not worst:
        return "No trade history available."

    trade_lines = "\n".join(
        f"  {i+1}. {r[0]} {r[1].upper()} — P&L ${r[2]:+.2f} on {r[3].strftime('%Y-%m-%d %H:%M') if r[3] else '?'}"
        + (f" | Error: {r[4][:80]}" if r[4] else "")
        for i, r in enumerate(worst)
    )

    prompt = f"""You are a senior risk officer writing a brief "What Not To Do" memo for a replacement trader.

These are the 5 worst trades from the terminated trader:
{trade_lines}

Write 3-5 concise bullet points (max 200 words total) that a successor trader must avoid.
Focus on patterns, not individual incidents. Be direct and actionable."""

    try:
        llm = LLMService()
        await llm.initialize()
        resp = await llm._call_llm(prompt)
        return resp.content[:800]
    except Exception as exc:
        logger.warning(f"LLM worst-trades summary failed: {exc}")
        return f"Top 5 worst trades by P&L:\n{trade_lines}"


async def _spawn_successor(status: DrawdownStatus, worst_trades_summary: str):
    """Create a new Trader in the DB as the successor, inheriting no baggage."""
    import random
    from app.models import Trader
    from app.database import get_async_session
    from app.services.trader_service import DEFAULT_TRADERS
    from app.services.team_chat import team_chat

    # Pick a different LLM model to the terminated trader's last-known model
    all_models = [d["llm_model"] for d in DEFAULT_TRADERS]
    all_providers = [d["llm_provider"] for d in DEFAULT_TRADERS]
    idx = random.randint(0, len(DEFAULT_TRADERS) - 1)

    # Successor name: add generation suffix
    base_name = status.trader_name.split(" [")[0]
    gen_num = 2
    while True:
        candidate = f"{base_name} [Gen {gen_num}]"
        async with get_async_session() as db:
            from sqlalchemy import select
            r = await db.execute(select(Trader).where(Trader.name == candidate))
            if not r.scalar_one_or_none():
                break
        gen_num += 1

    succession_context = (
        f"You are a successor trader, appointed after your predecessor was terminated "
        f"for excessive drawdown. Study these lessons from their worst trades and do NOT "
        f"repeat these mistakes:\n\n{worst_trades_summary}"
    )

    async with get_async_session() as db:
        defn = DEFAULT_TRADERS[idx]
        successor = Trader(
            name=candidate,
            llm_provider=defn["llm_provider"],
            llm_model=defn["llm_model"],
            allocation_pct=33.3 / 3,   # start with minimal allocation
            is_enabled=True,
            config={
                **defn["config"],
                "succession_context": succession_context,
                "avatar": "🌱",
                "bio": (
                    f"Successor to {status.trader_name}, who was terminated at "
                    f"{status.drawdown_pct:.1f}% drawdown. Operating with heightened caution."
                ),
            },
            performance_metrics={},
            successor_of=status.trader_id,
        )
        db.add(successor)
        await db.commit()
        await db.refresh(successor)
        new_id = successor.id
        new_name = successor.name

    await team_chat.add_message(
        agent_role="executive_assistant",
        content=(
            f"🌱 **New Trader Onboarded — {new_name}**: successor to {status.trader_name}. "
            f"Starting with a reduced allocation of {33.3/3:.1f}% while building track record. "
            f"Evolution context from predecessor has been loaded."
        ),
        message_type="system",
    )

    logger.info(f"9.2 Successor spawned: {new_name} ({new_id}) — replacing {status.trader_name}")
    return new_id


def get_pink_slip_text(drawdown_pct: float) -> str:
    """Return the Pink Slip warning text to inject into a WARNING-tier trader's system prompt."""
    return PINK_SLIP_TEXT.format(drawdown_pct=drawdown_pct).strip()
