"""
Team Chat Service — Logs inter-agent conversations during the team analysis cycle.

Each time a team agent completes its analysis, a human-readable chat message
is generated summarising its findings and (where relevant) referencing previous
agents' outputs.  Messages are stored in a bounded in-memory ring buffer,
persisted to the database, and broadcast to connected WebSocket clients.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from typing import Any, Callable, Coroutine, Dict, List, Optional
from collections import deque
import json
import logging

logger = logging.getLogger(__name__)

# Max messages kept in memory (roughly 2 hours of 5-min cycles × 6 agents)
MAX_MESSAGES = 200


@dataclass
class ChatMessage:
    id: str
    agent_id: str
    agent_name: str
    agent_role: str
    avatar: str
    content: str
    message_type: str          # analysis, decision, warning, recommendation, greeting
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mentions: List[str] = field(default_factory=list)  # agent_roles mentioned
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# Map chat role keys to LLMRegistry keys (they differ for CIO)
_ROLE_TO_REGISTRY: Dict[str, str] = {
    "research_analyst": "research_analyst",
    "portfolio_manager": "portfolio_manager",
    "risk_manager": "risk_manager",
    "execution_coordinator": "execution_coordinator",
    "cio": "cio_agent",
    "cio_agent": "cio_agent",
    "technical_analyst": "technical_analyst",
}

# Emoji fallbacks keyed by registry role
_ROLE_EMOJI: Dict[str, str] = {
    "research_analyst": "🔬",
    "technical_analyst": "📈",
    "portfolio_manager": "💼",
    "risk_manager": "🛡️",
    "execution_coordinator": "⚡",
    "cio_agent": "🎯",
}


def _get_agent_profile(agent_role: str) -> Dict[str, str]:
    """Resolve agent profile from LLMRegistry (single source of truth)."""
    from app.services.llm import LLMRegistry

    registry_key = _ROLE_TO_REGISTRY.get(agent_role, agent_role)
    info = LLMRegistry.get_agent_info(registry_key)
    return {
        "name": info.get("name", agent_role),
        "role": agent_role,
        "title": info.get("title", agent_role),
        "avatar": _ROLE_EMOJI.get(registry_key, "🤖"),
    }


# Keep AGENT_PROFILES as a lazy cache so existing imports still work
AGENT_PROFILES: Dict[str, Dict[str, str]] = {}


def _ensure_profiles_loaded() -> None:
    if AGENT_PROFILES:
        return
    for role in _ROLE_TO_REGISTRY:
        AGENT_PROFILES[role] = _get_agent_profile(role)


class TeamChatService:
    """Manages the in-memory conversation log between fund team agents."""

    def __init__(self) -> None:
        self._messages: deque[ChatMessage] = deque(maxlen=MAX_MESSAGES)
        self._counter = 0
        self._broadcast_fn: Optional[Callable[[dict], Coroutine]] = None

    def set_broadcast(self, fn: Callable[[dict], Coroutine]) -> None:
        """Register the WS broadcast coroutine (called from main.py)."""
        self._broadcast_fn = fn

    def _next_id(self) -> str:
        self._counter += 1
        return f"msg-{self._counter}"

    async def add_message(
        self,
        agent_role: str,
        content: str,
        message_type: str = "analysis",
        mentions: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ChatMessage:
        _ensure_profiles_loaded()
        profile = AGENT_PROFILES.get(agent_role) or _get_agent_profile(agent_role)
        msg = ChatMessage(
            id=self._next_id(),
            agent_id=agent_role,
            agent_name=profile["name"],
            agent_role=profile["role"],
            avatar=profile["avatar"],
            content=content,
            message_type=message_type,
            mentions=mentions or [],
            metadata=metadata or {},
        )
        self._messages.append(msg)
        logger.info(f"TeamChat [{profile['title']}]: {content[:80]}…" if len(content) > 80 else f"TeamChat [{profile['title']}]: {content}")

        # Persist to database
        await self._persist_message(msg)

        if self._broadcast_fn:
            try:
                await self._broadcast_fn({
                    "type": "team_chat",
                    "data": msg.to_dict(),
                })
            except Exception as e:
                logger.debug(f"TeamChat broadcast failed: {e}")

        return msg

    async def _persist_message(self, msg: ChatMessage) -> None:
        """Write a chat message to the database."""
        try:
            from app.database import get_async_session
            from app.models import TeamChatMessageRecord

            async with get_async_session() as db:
                record = TeamChatMessageRecord(
                    agent_id=msg.agent_id,
                    agent_name=msg.agent_name,
                    agent_role=msg.agent_role,
                    avatar=msg.avatar,
                    content=msg.content,
                    message_type=msg.message_type,
                    mentions=msg.mentions,
                    extra_metadata=msg.metadata,
                )
                db.add(record)
                await db.commit()
        except Exception as e:
            logger.debug(f"Failed to persist chat message: {e}")

    async def get_messages_for_date(self, target_date: Optional[date] = None) -> List[dict]:
        """Retrieve all chat messages for a given date from the database."""
        try:
            from app.database import get_async_session
            from app.models import TeamChatMessageRecord
            from sqlalchemy import select, cast, Date

            target = target_date or date.today()

            async with get_async_session() as db:
                result = await db.execute(
                    select(TeamChatMessageRecord)
                    .where(cast(TeamChatMessageRecord.created_at, Date) == target)
                    .order_by(TeamChatMessageRecord.created_at.asc())
                )
                records = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "agent_id": r.agent_id,
                        "agent_name": r.agent_name,
                        "agent_role": r.agent_role,
                        "avatar": r.avatar,
                        "content": r.content,
                        "message_type": r.message_type,
                        "timestamp": r.created_at.isoformat() if r.created_at else "",
                        "mentions": r.mentions or [],
                        "metadata": r.metadata or {},
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"Failed to fetch messages for {target_date}: {e}")
            return []

    def get_messages(self, limit: int = 50, since: Optional[str] = None) -> List[dict]:
        msgs = list(self._messages)
        if since:
            msgs = [m for m in msgs if m.timestamp > since]
        return [m.to_dict() for m in msgs[-limit:]]

    def clear(self) -> None:
        self._messages.clear()

    # ── Helper generators – produce conversational messages from agent outputs ──

    async def log_analyst_report(self, report: Any) -> None:
        """Generate a chat message from Research Analyst output."""
        try:
            regime = report.market_regime
            opps = report.opportunities or []
            top_opp = opps[0] if opps else None

            parts = [
                f"Team, I've completed my market scan across {len(report.symbols_analyzed)} symbols.",
                f"Market regime: **{regime.regime}** (confidence {(regime.regime_confidence or 0):.0%}).",
                f"Sentiment is **{regime.sentiment}**, volatility is **{regime.volatility_regime}**.",
            ]
            if top_opp:
                parts.append(
                    f"Top opportunity: **{top_opp.symbol}** — {top_opp.opportunity_type} setup "
                    f"(confidence {(top_opp.confidence or 0):.0%}). {top_opp.reasoning[:100]}"
                )
            parts.append(f"My recommendation: **{report.analyst_recommendation}**.")

            await self.add_message(
                agent_role="research_analyst",
                content=" ".join(parts),
                message_type="analysis",
                metadata={"regime": regime.regime, "sentiment": regime.sentiment},
            )
        except Exception as e:
            logger.debug(f"Failed to log analyst report: {e}")

    async def log_portfolio_decision(self, decision: Any, agents_list: list) -> None:
        """Generate a chat message from Portfolio Manager output."""
        try:
            alloc = decision.allocation_pct or {}
            top_allocs = sorted(alloc.items(), key=lambda x: x[1], reverse=True)[:3]

            agent_names = {a.get("id", ""): a.get("name", a.get("id", "")) for a in agents_list}

            parts = [
                f"Thanks @research_analyst. Based on the market analysis and agent performance, I've adjusted allocations.",
            ]
            if top_allocs:
                alloc_strs = [f"{agent_names.get(aid, aid)}: {pct:.0f}%" for aid, pct in top_allocs]
                parts.append(f"Top allocations → {', '.join(alloc_strs)}.")
            if decision.expected_return_pct:
                parts.append(f"Expected return: **{decision.expected_return_pct:+.1f}%**.")
            if decision.reasoning:
                parts.append(decision.reasoning[:150])

            await self.add_message(
                agent_role="portfolio_manager",
                content=" ".join(parts),
                message_type="decision",
                mentions=["research_analyst"],
                metadata={"allocation": alloc},
            )
        except Exception as e:
            logger.debug(f"Failed to log portfolio decision: {e}")

    async def log_risk_assessment(self, assessment: Any) -> None:
        """Generate a chat message from Risk Manager output."""
        try:
            level = assessment.risk_level
            pnl = assessment.daily_pnl
            exposure = getattr(assessment, "exposure_pct_of_capital", 0)

            if level == "danger":
                tone = "⚠️ **ALERT** — I'm flagging DANGER risk level."
            elif level == "caution":
                tone = "Heads up team — risk level is at **CAUTION**."
            else:
                tone = "All clear from risk — portfolio is within safe parameters."

            parts = [tone]
            parts.append(f"Daily P&L: **${(pnl or 0):+.2f}**. Exposure: {(exposure or 0):.1f}% of capital.")

            recs = getattr(assessment, "recommendations", []) or []
            if recs:
                parts.append(f"Recommendations: {'; '.join(str(r) for r in recs[:2])}.")

            if level == "danger":
                parts.append("@portfolio_manager — I suggest halting new positions until risk subsides.")

            await self.add_message(
                agent_role="risk_manager",
                content=" ".join(parts),
                message_type="warning" if level == "danger" else "analysis",
                mentions=["portfolio_manager"] if level != "safe" else [],
                metadata={"risk_level": level, "daily_pnl": pnl},
            )
        except Exception as e:
            logger.debug(f"Failed to log risk assessment: {e}")

    async def log_execution_plan(self, plan: Any) -> None:
        """Generate a chat message from Execution Coordinator output."""
        try:
            pending = plan.pending_orders_count
            action = plan.recommended_action

            if pending == 0:
                content = "No pending orders in the queue. Standing by for new signals from the team."
            else:
                content = (
                    f"I have **{pending}** pending orders queued. "
                    f"Recommended action: **{action.replace('_', ' ')}**. "
                    f"Estimated slippage: {(plan.aggregate_slippage_estimate or 0):.3f}%."
                )

            await self.add_message(
                agent_role="execution_coordinator",
                content=content,
                message_type="decision",
                metadata={"pending_orders": pending, "action": action},
            )
        except Exception as e:
            logger.debug(f"Failed to log execution plan: {e}")

    async def log_cio_report(self, report: Any) -> None:
        """Generate a chat message from CIO Agent output."""
        try:
            sentiment = report.cio_sentiment
            summary = getattr(report, "executive_summary", "") or ""
            recs = report.strategic_recommendations or []

            parts = [
                f"Good work team. My overall sentiment: **{sentiment.replace('_', ' ')}**.",
            ]
            if summary:
                parts.append(summary[:200])
            if recs:
                parts.append(f"Strategic priority: {recs[0][:120] if recs else 'maintain course'}.")

            await self.add_message(
                agent_role="cio",
                content=" ".join(parts),
                message_type="recommendation",
                mentions=["research_analyst", "portfolio_manager", "risk_manager", "execution_coordinator"],
                metadata={"sentiment": sentiment},
            )
        except Exception as e:
            logger.debug(f"Failed to log CIO report: {e}")

    async def log_agent_gate_block(self, agent_name: str, reason: str) -> None:
        """Log when an agent is blocked by a gate."""
        await self.add_message(
            agent_role="risk_manager",
            content=f"Blocked **{agent_name}** from running — {reason}.",
            message_type="warning",
            metadata={"blocked_agent": agent_name, "reason": reason},
        )

    async def log_strategy_review(self, review: Any) -> None:
        """Log the FM + TA strategy review results in team chat."""
        # Technical analyst speaks first about confluence
        if review.confluence_scores:
            symbols = list(review.confluence_scores.keys())[:3]
            confluence_lines = []
            for sym in symbols:
                data = review.confluence_scores[sym]
                confluence_lines.append(
                    f"{sym}: {data.get('signal', 'hold')} "
                    f"(confluence {data.get('score', 0):.0%}, "
                    f"{data.get('patterns', 0)} patterns, "
                    f"alignment: {data.get('alignment', '?')})"
                )
            await self.add_message(
                agent_role="technical_analyst",
                content=(
                    "Strategy review — technical confluence report:\n"
                    + "\n".join(confluence_lines)
                ),
                message_type="analysis",
                mentions=["@portfolio_manager"],
                metadata={"confluence_scores": review.confluence_scores},
            )

        # Portfolio manager speaks about evaluations and proposed actions
        if review.agent_evaluations:
            eval_lines = []
            for ev in sorted(review.agent_evaluations, key=lambda e: e.get('combined_score', 0) or 0, reverse=True):
                cs = ev.get('combined_score', 0) or 0
                emoji = "🟢" if cs >= 0.6 else "🟡" if cs >= 0.35 else "🔴"
                eval_lines.append(
                    f"{emoji} {ev['agent_name']}: score {cs:.2f} "
                    f"(perf {(ev.get('perf_score', 0) or 0):.2f}, fit {(ev.get('fit_score', 0) or 0):.2f}, "
                    f"confluence {(ev.get('confluence', 0) or 0):.2f})"
                )

            actions_text = ""
            if review.proposed_actions:
                action_lines = []
                for a in review.proposed_actions:
                    name = a.target_agent_name or "new agent"
                    action_lines.append(f"→ **{a.action}** {name}: {a.rationale[:100]}")
                actions_text = "\n\nProposed actions:\n" + "\n".join(action_lines)

            await self.add_message(
                agent_role="portfolio_manager",
                content=(
                    "Strategy review — agent effectiveness:\n"
                    + "\n".join(eval_lines[:6])
                    + actions_text
                ),
                message_type="decision",
                mentions=["@technical_analyst", "@cio"],
                metadata={
                    "evaluations": review.agent_evaluations,
                    "proposed_actions": [
                        {"action": a.action, "target": a.target_agent_name, "rationale": a.rationale}
                        for a in review.proposed_actions
                    ],
                },
            )


# Singleton
team_chat = TeamChatService()
