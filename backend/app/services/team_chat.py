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

from app.utils import fmt_price

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
        meta = metadata or {}
        # Support trader roles: override name/avatar via metadata if role isn't a standard agent
        if agent_role not in AGENT_PROFILES and not agent_role.startswith("trader_"):
            profile = _get_agent_profile(agent_role)
        else:
            profile = AGENT_PROFILES.get(agent_role) or {"name": agent_role, "role": agent_role, "title": agent_role, "avatar": "🤖"}

        display_name = meta.get("_override_name") or profile.get("name", agent_role)
        display_avatar = meta.get("_override_avatar") or profile.get("avatar", "🤖")

        msg = ChatMessage(
            id=self._next_id(),
            agent_id=agent_role,
            agent_name=display_name,
            agent_role=profile.get("role", agent_role),
            avatar=display_avatar,
            content=content,
            message_type=message_type,
            mentions=mentions or [],
            metadata=meta,
        )
        self._messages.append(msg)
        logger.info(f"TeamChat [{display_name}]: {content[:80]}…" if len(content) > 80 else f"TeamChat [{display_name}]: {content}")

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

            # Append strategy recommendations if present
            recs = getattr(report, 'strategy_recommendations', None) or []
            if recs:
                rec_lines = [f"📊 **Strategy Research** — regime-aligned proposals:"]
                for rec in recs[:3]:
                    syms = ", ".join(rec.recommended_symbols[:2])
                    rec_lines.append(
                        f"  • **{rec.strategy_type}** on {syms} ({rec.timeframe}) — "
                        f"priority {(rec.priority or 0):.0%}. {rec.rationale[:80]}"
                    )
                parts.append("\n".join(rec_lines))

            await self.add_message(
                agent_role="research_analyst",
                content=" ".join(parts),
                message_type="analysis",
                metadata={
                    "regime": regime.regime,
                    "sentiment": regime.sentiment,
                    "strategy_recommendations": [
                        {"strategy_type": r.strategy_type, "priority": r.priority,
                         "symbols": r.recommended_symbols}
                        for r in recs
                    ],
                },
            )
        except Exception as e:
            logger.debug(f"Failed to log analyst report: {e}")

    async def log_portfolio_decision(self, decision: Any, agents_list: list) -> None:
        """Generate a chat message from Portfolio Manager output (no-trader mode only)."""
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

    async def log_trader_allocation(
        self,
        trader_alloc_pct: dict,
        traders: list,
        reasoning: str = "",
    ) -> None:
        """
        Log James's trader-level allocation decision to team chat.
        Called when traders are present — James allocates to traders, not directly to agents.
        """
        try:
            trader_map = {t["id"]: t for t in traders}
            sorted_allocs = sorted(trader_alloc_pct.items(), key=lambda x: x[1], reverse=True)
            alloc_strs = [
                f"{trader_map.get(tid, {}).get('name', tid)}: **{pct:.0f}%**"
                for tid, pct in sorted_allocs
            ]
            content = (
                f"Capital allocation updated across trader portfolios. "
                f"{', '.join(alloc_strs)}."
            )
            if reasoning:
                content += f" {reasoning[:150]}"

            await self.add_message(
                agent_role="portfolio_manager",
                content=content,
                message_type="decision",
                metadata={"trader_allocations": trader_alloc_pct},
            )
        except Exception as e:
            logger.debug(f"Failed to log trader allocation: {e}")

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

            # Only post when there are actually orders to discuss
            if pending == 0:
                return

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

    async def log_trade_intent(
        self,
        trader_name: str,
        trader_avatar: str,
        agent_name: str,
        symbol: str,
        side: str,
        strategy: str,
        confidence: float,
        reasoning: str,
    ) -> None:
        """Trader announces an intended trade with rationale before execution gates."""
        direction = "LONG 📈" if side == "buy" else "SHORT 📉"
        content = (
            f"Intending to go **{direction}** on **{symbol}** via *{agent_name}* "
            f"({strategy} strategy, {confidence:.0%} confidence).\n\n"
            f"Rationale: {reasoning[:250]}{'…' if len(reasoning) > 250 else ''}\n\n"
            f"Requesting TA confluence from @technical_analyst and risk clearance from @risk_manager."
        )
        await self.add_message(
            agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
            content=content,
            message_type="decision",
            mentions=["@technical_analyst", "@risk_manager"],
            metadata={
                "trader_name": trader_name,
                "agent_name": agent_name,
                "symbol": symbol,
                "side": side,
                "strategy": strategy,
                "confidence": confidence,
                "_override_name": trader_name,
                "_override_avatar": trader_avatar,
            },
        )

    async def log_ta_confluence(
        self,
        symbol: str,
        ta_signal: str,
        ta_confidence: float,
        patterns: list,
        support_levels: list,
        resistance_levels: list,
        trade_signal: str,
    ) -> None:
        """Marcus (Technical Analyst) responds with chart confluence for the requested symbol."""
        # Normalise TA vocabulary for alignment check:
        # TA emits 'bullish'/'bearish'; agent signals are 'buy'/'sell'.
        _ta_norm = "buy" if ta_signal == "bullish" else ("sell" if ta_signal == "bearish" else ta_signal)
        _is_aligned  = _ta_norm == trade_signal
        _is_opposing = (_ta_norm == "buy" and trade_signal == "sell") or (_ta_norm == "sell" and trade_signal == "buy")
        alignment = "✅ Aligned" if _is_aligned else ("❌ Opposing" if _is_opposing else "⚠️ Neutral")

        # Friendly display names for the new leading-indicator pattern types
        _PATTERN_LABELS = {
            "ema8_21_bull_cross":     "EMA 8/21 Bull Cross ⚡",
            "ema8_21_bear_cross":     "EMA 8/21 Bear Cross ⚡",
            "rsi_bullish_divergence": "RSI Bullish Divergence ⚡",
            "rsi_bearish_divergence": "RSI Bearish Divergence ⚡",
        }

        pattern_text = ""
        leading_flag = ""
        if patterns:
            top = patterns[:4]
            pattern_parts = []
            has_leading = False
            for p in top:
                if not hasattr(p, 'pattern_type'):
                    continue
                pt = p.pattern_type or ""
                label = _PATTERN_LABELS.get(pt)
                if label is None:
                    # Candlestick patterns start with "candle_" — pretty-print the suffix
                    if pt.startswith("candle_"):
                        label = "🕯 " + pt.replace("candle_", "").replace("_", " ").title()
                    else:
                        label = pt.replace("_", " ").title()
                else:
                    has_leading = True
                pattern_parts.append(f"**{label}** ({p.confidence:.0%})")
            if pattern_parts:
                pattern_text = "\n\nPatterns detected: " + ", ".join(pattern_parts)
            if has_leading:
                leading_flag = "\n\n⚡ *Leading indicators active — signal fired before lagging confirmation.*"

        level_text = ""
        if support_levels or resistance_levels:
            s_str = ", ".join(fmt_price(s) for s in (support_levels or [])[:2]) or "—"
            r_str = ", ".join(fmt_price(r) for r in (resistance_levels or [])[:2]) or "—"
            level_text = f"\n\nKey levels — Support: {s_str} | Resistance: {r_str}"

        # Veto notice: TA can now actually veto (vocab bug fixed in ccc35a1)
        veto_note = ""
        if _is_opposing and ta_confidence >= 0.75:
            veto_note = f"\n\n🚫 **VETO ACTIVE** — {ta_confidence:.0%} opposing confidence exceeds the 75% veto threshold. This trade will be blocked."
        elif _is_opposing and ta_confidence >= 0.55:
            veto_note = f"\n\n⚠️ *Caution: opposing signal at {ta_confidence:.0%} — below veto threshold, trade may proceed.*"

        content = (
            f"TA confluence on **{symbol}**: signal **{ta_signal.upper()}** at {ta_confidence:.0%} confidence. "
            f"Trade intent {alignment}.{pattern_text}{leading_flag}{level_text}{veto_note}"
        )
        await self.add_message(
            agent_role="technical_analyst",
            content=content,
            message_type="analysis",
            mentions=["@risk_manager"],
            metadata={
                "symbol": symbol,
                "ta_signal": ta_signal,
                "ta_confidence": ta_confidence,
                "alignment": alignment,
            },
        )

    async def log_risk_decision(
        self,
        agent_name: str,
        symbol: str,
        side: str,
        allowed: bool,
        reason: str,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
    ) -> None:
        """Elena (Risk Manager) posts approval or denial with sizing details."""
        if allowed:
            sl_str = f"${sl_price:,.2f}" if sl_price else "N/A"
            tp_str = f"${tp_price:,.2f}" if tp_price else "N/A"
            content = (
                f"✅ **APPROVED** — {agent_name} may proceed with **{side.upper()} {symbol}**.\n\n"
                f"Position sizing cleared. SL: {sl_str} | TP: {tp_str}. "
                f"Risk parameters within fund limits."
            )
            msg_type = "analysis"
        else:
            content = (
                f"🚫 **DENIED** — {agent_name} trade on **{symbol}** blocked.\n\n"
                f"Reason: {reason}"
            )
            msg_type = "warning"
        await self.add_message(
            agent_role="risk_manager",
            content=content,
            message_type=msg_type,
            mentions=["@portfolio_manager"],
            metadata={
                "agent_name": agent_name,
                "symbol": symbol,
                "side": side,
                "allowed": allowed,
                "reason": reason,
                "sl_price": sl_price,
                "tp_price": tp_price,
            },
        )

    async def log_trade_executed(
        self,
        trader_name: str,
        trader_avatar: str,
        agent_name: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        sl_price: Optional[float],
        tp_price: Optional[float],
    ) -> None:
        """Trader confirms trade placement and thanks the team."""
        direction = "LONG" if side == "buy" else "SHORT"
        sl_str = f"${sl_price:,.2f}" if sl_price else "N/A"
        tp_str = f"${tp_price:,.2f}" if tp_price else "N/A"
        value_str = f"${quantity * price:,.2f}"
        content = (
            f"Trade executed — **{direction} {symbol}** @ ${price:,.4f} ({value_str} notional).\n\n"
            f"SL: {sl_str} | TP: {tp_str}\n\n"
            f"Thanks @technical_analyst for the confluence and @risk_manager for the clearance. "
            f"Position is live — monitoring closely. 🎯"
        )
        await self.add_message(
            agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
            content=content,
            message_type="decision",
            mentions=["@technical_analyst", "@risk_manager"],
            metadata={
                "trader_name": trader_name,
                "agent_name": agent_name,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "_override_name": trader_name,
                "_override_avatar": trader_avatar,
            },
        )

    async def log_trade_blocked(
        self,
        trader_name: str,
        trader_avatar: str,
        agent_name: str,
        symbol: str,
        side: str,
        reason: str,
    ) -> None:
        """Trader acknowledges a rejected trade and stands down."""
        direction = "LONG" if side == "buy" else "SHORT"
        content = (
            f"Standing down on **{direction} {symbol}** for *{agent_name}*. "
            f"Reason: {reason}. Will wait for better conditions. 🤝"
        )
        await self.add_message(
            agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
            content=content,
            message_type="warning",
            metadata={
                "trader_name": trader_name,
                "agent_name": agent_name,
                "symbol": symbol,
                "side": side,
                "reason": reason,
                "_override_name": trader_name,
                "_override_avatar": trader_avatar,
            },
        )

    async def log_scale_out(
        self,
        trader_name: str,
        trader_avatar: str,
        agent_name: str,
        symbol: str,
        side: str,
        close_pct: float,
        close_quantity: float,
        close_price: float,
        realised_pnl: float,
        remaining_quantity: float,
        sl_moved_to_breakeven: bool,
        tranche_label: str,
    ) -> None:
        """Trader announces a scale-out (partial profit-take) in team chat."""
        direction = "LONG" if side == "buy" else "SHORT"
        pnl_emoji = "💰" if realised_pnl >= 0 else "📉"
        be_note = " SL moved to breakeven — runner is risk-free. 🔒" if sl_moved_to_breakeven else ""
        content = (
            f"📤 **Scale-out {tranche_label}** — {direction} {symbol} | *{agent_name}*\n\n"
            f"Closed **{close_pct:.0%}** of position ({close_quantity:.4f} units) @ ${close_price:,.4f}\n"
            f"{pnl_emoji} Locked P&L: **${realised_pnl:+,.2f}** | Remaining: {remaining_quantity:.4f} units\n"
            f"{be_note}"
        )
        await self.add_message(
            agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
            content=content,
            message_type="decision",
            mentions=["@risk_manager"],
            metadata={
                "trader_name": trader_name,
                "agent_name": agent_name,
                "symbol": symbol,
                "side": side,
                "close_pct": close_pct,
                "close_quantity": close_quantity,
                "close_price": close_price,
                "realised_pnl": realised_pnl,
                "remaining_quantity": remaining_quantity,
                "sl_moved_to_breakeven": sl_moved_to_breakeven,
                "tranche_label": tranche_label,
                "_override_name": trader_name,
                "_override_avatar": trader_avatar,
            },
        )

    async def log_whale_alert(
        self,
        symbol: str,
        whale_label: str,
        side: str,
        notional_usd: float,
        leverage: float,
        change_type: str,  # "opened", "closed", "increased", "decreased"
    ) -> None:
        """Log a significant Hyperliquid whale position change to team chat."""
        direction = "LONG" if side == "long" else "SHORT"

        def fmt_usd(v: float) -> str:
            if v >= 1_000_000:
                return f"${v/1_000_000:.1f}M"
            if v >= 1_000:
                return f"${v/1_000:.0f}K"
            return f"${v:.0f}"

        verb = {
            "opened": "has opened a new",
            "closed": "has closed their",
            "increased": "has increased their",
            "decreased": "has reduced their",
        }.get(change_type, f"has {change_type}")

        content = (
            f"🐋 **Whale Alert — {symbol}**: _{whale_label}_ {verb} "
            f"**{direction}** position ({fmt_usd(notional_usd)} notional, "
            f"{leverage:.0f}x leverage). "
            f"Monitor Hyperliquid intelligence panel for updated positioning."
        )
        await self.add_message(
            agent_role="technical_analyst",
            content=content,
            message_type="warning",
            mentions=["@risk_manager", "@portfolio_manager"],
            metadata={
                "symbol": symbol,
                "whale_label": whale_label,
                "side": side,
                "notional_usd": notional_usd,
                "leverage": leverage,
                "change_type": change_type,
            },
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
