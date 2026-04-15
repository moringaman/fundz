"""
Trader Service — each Trader is a competing capital manager backed by its own LLM.

The Fund Manager allocates capital to Traders (not agents directly).
Each Trader manages its own agents, strategies, and capital pool.
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
import json
import logging

from app.config import settings
from app.services.llm import LLMService, LLMRegistry
import app.strategies as strategy_registry

logger = logging.getLogger(__name__)


# ── Default trader definitions (seeded on first boot) ────────────────────────
DEFAULT_TRADERS = [
    {
        "name": "Kai Nakamura",
        "llm_provider": "openrouter",
        "llm_model": "anthropic/claude-sonnet-4",
        "config": {
            "style": "Calm and methodical. Waits for high-conviction setups with strong multi-timeframe confluence before committing. Never chases.",
            "risk_tolerance": "moderate",
            "preferred_strategies": ["momentum", "breakout"],
            "avatar": "🧠",
            "bio": "Former quant researcher turned discretionary trader. 8 years in derivatives. Known for patience and precision — only enters when the risk/reward is unambiguous.",
        },
    },
    {
        "name": "Zara Hassan",
        "llm_provider": "openrouter",
        "llm_model": "openai/gpt-4o",
        "config": {
            "style": "Aggressive and conviction-driven. Moves fast on breakouts, cuts losers without hesitation, and lets winners run with tight trailing stops.",
            "risk_tolerance": "high",
            "preferred_strategies": ["breakout", "ai"],
            "avatar": "⚡",
            "bio": "Ex-prop desk at a crypto-native fund. Built her edge on volatility plays and momentum surges. High energy, high output — tracks 15+ pairs simultaneously.",
        },
    },
    {
        "name": "Otto Brenner",
        "llm_provider": "openrouter",
        "llm_model": "google/gemini-2.0-flash-001",
        "config": {
            "style": "Conservative contrarian. Fades extremes, targets mean reversion, and keeps position sizes small for consistent compounding over big swings.",
            "risk_tolerance": "low",
            "preferred_strategies": ["mean_reversion", "momentum"],
            "avatar": "🎯",
            "bio": "Veteran trader with a background in statistical arbitrage. Values capital preservation above all else. Rarely wrong, rarely in a hurry.",
        },
    },
]


@dataclass
class TraderPerformance:
    trader_id: str
    trader_name: str
    total_pnl: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    agent_count: int = 0
    allocation_pct: float = 33.3
    sharpe_estimate: float = 0.0


@dataclass
class TraderAllocation:
    """Fund Manager's capital allocation across traders."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    allocations: Dict[str, float] = field(default_factory=dict)  # {trader_id: pct}
    reasoning: str = ""


class TraderService:
    """Manages the lifecycle and execution of competing traders."""

    MIN_ALLOCATION_PCT = 15.0
    MAX_ALLOCATION_PCT = 50.0

    def __init__(self):
        self._trader_llm_cache: Dict[str, LLMService] = {}

    # ── LLM instance per trader ──────────────────────────────────────────

    async def get_trader_llm(self, trader: dict) -> LLMService:
        """Get or create an LLM service instance for a specific trader."""
        trader_id = trader["id"]
        if trader_id not in self._trader_llm_cache:
            llm = LLMService()
            llm.provider = trader.get("llm_provider", settings.llm_provider)
            llm.model = trader.get("llm_model", settings.llm_model)
            llm.temperature = trader.get("config", {}).get("temperature", 0.5)
            llm.max_tokens = trader.get("config", {}).get("max_tokens", 1200)
            await llm.initialize()
            self._trader_llm_cache[trader_id] = llm
            logger.info(
                f"Trader LLM initialized: {trader.get('name')} → "
                f"{llm.provider}/{llm.model}"
            )
        return self._trader_llm_cache[trader_id]

    def invalidate_llm_cache(self, trader_id: str):
        """Clear cached LLM instance when trader config changes."""
        self._trader_llm_cache.pop(trader_id, None)

    # ── Strategy review (trader-level) ───────────────────────────────────

    async def manage_agents(
        self,
        trader: dict,
        trader_agents: List[dict],
        agent_metrics: List[dict],
        market_condition: dict,
        confluence_scores: Optional[Dict[str, dict]] = None,
        analyst_report=None,  # Optional[ResearchReport]
    ) -> List[dict]:
        """
        Trader reviews its own agents and proposes actions (create/disable/adjust).
        Returns a list of proposed action dicts.
        """
        llm = await self.get_trader_llm(trader)
        config = trader.get("config", {})
        metrics_by_id = {m.get("agent_id"): m for m in agent_metrics}

        agent_summary_lines = []
        for a in trader_agents:
            m = metrics_by_id.get(a["id"], {})
            pairs = a.get("trading_pairs", [])
            agent_summary_lines.append(
                f"  • {a['name']} ({a.get('strategy_type','?')}) — "
                f"pairs: {', '.join(pairs)}, "
                f"win_rate: {(m.get('win_rate', 0) * 100):.0f}%, "
                f"P&L: ${m.get('total_pnl', 0):.2f}, "
                f"runs: {m.get('total_runs', 0)}, "
                f"enabled: {a.get('is_enabled', False)}"
            )
        agent_block = "\n".join(agent_summary_lines) if agent_summary_lines else "  (no agents yet)"

        confluence_block = ""
        if confluence_scores:
            lines = []
            for sym, data in confluence_scores.items():
                lines.append(
                    f"  {sym}: signal={data.get('signal','?')}, "
                    f"confluence={data.get('score',0):.0%}"
                )
            confluence_block = "\nTECHNICAL CONFLUENCE:\n" + "\n".join(lines)

        # Additive: inject Hyperliquid whale intelligence context (graceful degradation)
        whale_block = ""
        try:
            from app.services.whale_intelligence import whale_intelligence
            whale_report = await whale_intelligence.fetch_whale_report()
            whale_block = whale_intelligence.build_llm_context_block(whale_report)
        except Exception:
            pass  # Trader prompt continues without whale data

        # Additive: inject Marina's regime-derived strategy recommendations
        marina_block = ""
        if analyst_report and getattr(analyst_report, 'strategy_recommendations', None):
            recs = analyst_report.strategy_recommendations
            regime = getattr(analyst_report, 'market_regime', None)
            regime_str = f"{regime.regime} / {regime.sentiment}" if regime else "unknown"
            lines = [f"\nMARINA'S RESEARCH (regime: {regime_str}):"]
            for rec in recs[:4]:
                syms = ", ".join(rec.recommended_symbols[:3])
                lines.append(
                    f"  • {rec.strategy_type.upper()} on {syms} ({rec.timeframe}) "
                    f"— priority {(rec.priority or 0):.0%}. {rec.rationale[:100]}"
                )
            marina_block = "\n".join(lines)

        prompt = f"""You are Trader "{trader['name']}", a competing portfolio trader in a hedge fund.

YOUR TRADING STYLE: {config.get('style', 'Balanced approach')}
YOUR RISK TOLERANCE: {config.get('risk_tolerance', 'moderate')}
YOUR PREFERRED STRATEGIES: {config.get('preferred_strategies', ['momentum'])}
YOUR CAPITAL ALLOCATION: {trader.get('allocation_pct', 33.3):.1f}% of fund

MARKET CONDITIONS:
  Trend: {market_condition.get('trend', '?')}
  Volatility: {market_condition.get('volatility', '?')}
  Momentum: {market_condition.get('momentum', '?')}
{confluence_block}
{whale_block}
{marina_block}

STRATEGY REFERENCE:
{strategy_registry.ai_prompt_summary()}

YOUR CURRENT AGENTS:
{agent_block}

Review your portfolio of agents. You may propose actions:
- "create_agent": Create a new agent (name, strategy_type, trading_pairs, stop_loss_pct, take_profit_pct)
- "disable_agent": Disable an underperforming agent (agent_id, reason)
- "enable_agent": Re-enable a previously disabled agent (agent_id, reason)
- "adjust_params": Adjust parameters of an existing agent (agent_id, params)

Rules:
- Maximum 4 agents per trader
- Each agent should have a **single primary trading pair** — do NOT pass a list of pairs. Pick one symbol that best fits the strategy and current conditions (e.g., "BTCUSDT" or "SOLUSDT"). Multiple-pair agents reduce signal clarity.
- The agent name must reflect the primary pair. Format: "SYMBOL_StrategyType", e.g., "BTC_Momentum", "SOL_Breakout", "ETH_MeanRev". Never name an agent after a symbol it doesn't trade.
- Each agent should have a clear purpose and differentiated strategy
- Don't create duplicates of existing agents
- Disable agents with consistently negative P&L after 10+ runs
- If market is sideways / ranging with low volatility, prefer mean_reversion or grid; if trending, prefer momentum/breakout/ema_crossover
- Grid strategy (`strategy_type: "grid"`) requires Marina's explicit recommendation or confirmed ranging/low-volatility regime before proposing
- Marina's research recommendations above are research-grade signals — strongly consider them when proposing new agents or re-enabling existing ones
- Refer to the strategy reference below for guidance on which strategy to use in current conditions

Return JSON:
{{
  "actions": [
    {{"action": "create_agent", "name": "SOL_Momentum", "strategy_type": "momentum", "trading_pairs": ["SOLUSDT"], "stop_loss_pct": 3.5, "take_profit_pct": 7.0, "reason": "..."}},
    ...
  ],
  "reasoning": "explanation of your decisions"
}}

If no changes needed, return {{"actions": [], "reasoning": "All agents performing adequately"}}
"""

        try:
            response = await llm._call_llm(prompt)
            data = json.loads(response.content)
            actions = data.get("actions", [])
            reasoning = data.get("reasoning", "")
            logger.info(
                f"Trader {trader['name']} strategy review: "
                f"{len(actions)} actions proposed — {reasoning[:100]}"
            )
            return actions
        except Exception as e:
            logger.error(f"Trader {trader['name']} strategy review failed: {e}")
            return []

    # ── Sub-allocation (trader → agents) ─────────────────────────────────

    async def allocate_to_agents(
        self,
        trader: dict,
        trader_agents: List[dict],
        agent_metrics: List[dict],
        trader_capital: float,
    ) -> Dict[str, float]:
        """
        Trader sub-allocates its capital budget across its own agents.
        Returns {agent_id: allocation_pct} relative to the trader's capital.
        """
        if not trader_agents:
            return {}

        metrics_by_id = {m.get("agent_id"): m for m in agent_metrics}
        n = len(trader_agents)

        # Performance-weighted allocation (no LLM needed for sub-allocation)
        scores = {}
        for a in trader_agents:
            if not a.get("is_enabled", True):
                continue
            m = metrics_by_id.get(a["id"], {})
            wr = m.get("win_rate", 0.5) or 0.5
            pnl = m.get("total_pnl", 0) or 0
            pnl_factor = min(max(pnl / 500 + 0.5, 0), 1.0)
            runs = m.get("total_runs", 0) or 0
            # New agents get a boost
            newness_bonus = 0.2 if runs < 5 else 0.0
            scores[a["id"]] = max(wr * 0.5 + pnl_factor * 0.35 + newness_bonus + 0.15, 0.1)

        if not scores:
            return {}

        total_score = sum(scores.values())
        alloc = {}
        for aid, s in scores.items():
            pct = (s / total_score) * 100
            pct = max(pct, 100.0 / n * 0.5)  # floor at half-equal
            alloc[aid] = pct

        # Normalize to 100%
        total = sum(alloc.values())
        if total > 0:
            alloc = {aid: p / total * 100 for aid, p in alloc.items()}

        return alloc

    # ── Performance aggregation ──────────────────────────────────────────

    def get_trader_performance(
        self, trader: dict, trader_agents: List[dict], agent_metrics: List[dict]
    ) -> TraderPerformance:
        """Aggregate performance metrics across all agents for a trader."""
        metrics_by_id = {m.get("agent_id"): m for m in agent_metrics}

        total_pnl = 0.0
        total_trades = 0
        winning_trades = 0

        for a in trader_agents:
            m = metrics_by_id.get(a["id"], {})
            total_pnl += m.get("total_pnl", 0) or 0
            # Use actual_trades (filled orders), not total_runs (scheduler cycles incl. holds)
            actual = m.get("actual_trades", 0) or 0
            wins = m.get("winning_trades", 0) or 0
            if actual == 0:
                # Fallback: derive from win_rate × total_runs only if actual_trades missing
                runs = m.get("total_runs", 0) or 0
                wr = m.get("win_rate", 0) or 0
                actual = runs
                wins = int(runs * wr)
            total_trades += actual
            winning_trades += wins

        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        return TraderPerformance(
            trader_id=trader["id"],
            trader_name=trader["name"],
            total_pnl=total_pnl,
            win_rate=win_rate,
            total_trades=total_trades,
            winning_trades=winning_trades,
            agent_count=len(trader_agents),
            allocation_pct=trader.get("allocation_pct", 33.3),
        )

    # ── Fund Manager allocation to traders ───────────────────────────────

    async def compute_trader_allocations(
        self,
        traders: List[dict],
        all_agents: List[dict],
        all_metrics: List[dict],
        total_capital: float,
    ) -> TraderAllocation:
        """
        Fund Manager decides how to allocate capital across traders
        based on their aggregate performance.
        """
        trader_perfs = []
        for t in traders:
            if not t.get("is_enabled", True):
                continue
            t_agents = [a for a in all_agents if a.get("trader_id") == t["id"]]
            perf = self.get_trader_performance(t, t_agents, all_metrics)
            trader_perfs.append(perf)

        if not trader_perfs:
            return TraderAllocation(reasoning="No enabled traders")

        # Performance-weighted allocation with min/max bounds
        scores = {}
        for p in trader_perfs:
            pnl_score = min(max(p.total_pnl / 500 + 0.5, 0), 1.0)
            wr_score = p.win_rate
            trade_count_bonus = min(p.total_trades / 50, 0.2)
            scores[p.trader_id] = max(
                wr_score * 0.5 + pnl_score * 0.35 + trade_count_bonus + 0.15,
                0.1,
            )

        total_score = sum(scores.values())
        raw_pcts = {tid: (s / total_score) * 100 for tid, s in scores.items()}

        # Clamp to [MIN, MAX] and re-normalize
        clamped = {
            tid: max(self.MIN_ALLOCATION_PCT, min(self.MAX_ALLOCATION_PCT, pct))
            for tid, pct in raw_pcts.items()
        }
        total_clamped = sum(clamped.values())
        allocations = {tid: pct / total_clamped * 100 for tid, pct in clamped.items()}

        reasoning_parts = []
        for p in trader_perfs:
            reasoning_parts.append(
                f"{p.trader_name}: P&L=${p.total_pnl:+.2f}, "
                f"WR={p.win_rate:.0%}, trades={p.total_trades} → "
                f"{allocations.get(p.trader_id, 0):.1f}%"
            )

        return TraderAllocation(
            allocations=allocations,
            reasoning="Performance-weighted: " + "; ".join(reasoning_parts),
        )

    # ── Seed default traders ─────────────────────────────────────────────

    async def seed_default_traders(self, db_session) -> List[dict]:
        """Create the 3 default traders if they don't exist. Returns list of trader dicts."""
        from sqlalchemy import select
        from app.models import Trader

        result = await db_session.execute(select(Trader))
        existing = result.scalars().all()

        if existing:
            # Update names/config if traders still have old Greek names
            old_to_new = {"Alpha": 0, "Beta": 1, "Gamma": 2}
            updated = False
            for t in existing:
                if t.name in old_to_new:
                    defn = DEFAULT_TRADERS[old_to_new[t.name]]
                    t.name = defn["name"]
                    t.llm_provider = defn["llm_provider"]
                    t.llm_model = defn["llm_model"]
                    t.config = defn["config"]
                    updated = True
            if updated:
                await db_session.commit()
                for t in existing:
                    await db_session.refresh(t)
                logger.info(f"Trader layer: renamed traders to {[t.name for t in existing]}")
            else:
                logger.info(f"Trader layer: loaded {len(existing)} existing traders from DB")
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "llm_provider": t.llm_provider,
                    "llm_model": t.llm_model,
                    "allocation_pct": t.allocation_pct,
                    "is_enabled": t.is_enabled,
                    "config": t.config or {},
                    "performance_metrics": t.performance_metrics or {},
                }
                for t in existing
            ]

        created = []
        for defn in DEFAULT_TRADERS:
            trader = Trader(
                name=defn["name"],
                llm_provider=defn["llm_provider"],
                llm_model=defn["llm_model"],
                allocation_pct=33.3,
                is_enabled=True,
                config=defn["config"],
                performance_metrics={},
            )
            db_session.add(trader)
            created.append(trader)

        await db_session.commit()
        for t in created:
            await db_session.refresh(t)

        logger.info(f"Seeded {len(created)} default traders: {[t.name for t in created]}")
        return [
            {
                "id": t.id,
                "name": t.name,
                "llm_provider": t.llm_provider,
                "llm_model": t.llm_model,
                "allocation_pct": t.allocation_pct,
                "is_enabled": t.is_enabled,
                "config": t.config or {},
                "performance_metrics": t.performance_metrics or {},
            }
            for t in created
        ]

    # ── DB helpers ───────────────────────────────────────────────────────

    async def fetch_all_traders(self, db_session) -> List[dict]:
        """Fetch all traders from DB as dicts."""
        from sqlalchemy import select
        from app.models import Trader

        result = await db_session.execute(select(Trader))
        traders = result.scalars().all()
        return [
            {
                "id": t.id,
                "name": t.name,
                "llm_provider": t.llm_provider,
                "llm_model": t.llm_model,
                "allocation_pct": t.allocation_pct,
                "is_enabled": t.is_enabled,
                "config": t.config or {},
                "performance_metrics": t.performance_metrics or {},
            }
            for t in traders
        ]

    async def assign_existing_agents_to_trader(self, db_session, trader_id: str):
        """Assign all unassigned agents to a specific trader (migration helper)."""
        from sqlalchemy import update
        from app.models import Agent

        await db_session.execute(
            update(Agent).where(Agent.trader_id.is_(None)).values(trader_id=trader_id)
        )
        await db_session.commit()
        logger.info(f"Assigned unassigned agents to trader {trader_id}")

    async def update_trader_allocation(self, db_session, trader_id: str, allocation_pct: float):
        """Update a trader's allocation percentage in DB."""
        from app.models import Trader

        trader = await db_session.get(Trader, trader_id)
        if trader:
            trader.allocation_pct = allocation_pct
            await db_session.commit()

    async def update_trader_performance(self, db_session, trader_id: str, metrics: dict):
        """Persist aggregated performance metrics for a trader."""
        from app.models import Trader

        trader = await db_session.get(Trader, trader_id)
        if trader:
            trader.performance_metrics = metrics
            await db_session.commit()


# Module-level singleton
trader_service = TraderService()
