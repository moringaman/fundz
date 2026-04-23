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
    consistency_flags: Dict[str, str] = field(default_factory=dict)  # {trader_id: flag}
    sharpe_tiers: Dict[str, str] = field(default_factory=dict)       # {trader_id: tier}


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
            venue_stats = m.get("venue_stats") or {}
            _venue_breakdown = ""
            if venue_stats:
                _parts = []
                for _vn, _vs in venue_stats.items():
                    _parts.append(f"{_vn}: WR={_vs.get('win_rate', 0):.0%} P&L=${_vs.get('pnl', 0):.2f} ({_vs.get('trades', 0)}t)")
                _venue_breakdown = " [" + " | ".join(_parts) + "]"
            agent_summary_lines.append(
                f"  • {a['name']} ({a.get('strategy_type','?')}) — "
                f"pairs: {', '.join(pairs)}, "
                f"venue: {a.get('venue', 'phemex')}, "
                f"win_rate: {(m.get('win_rate', 0) * 100):.0f}%, "
                f"P&L: ${m.get('total_pnl', 0):.2f}, "
                f"runs: {m.get('total_runs', 0)}, "
                f"enabled: {a.get('is_enabled', False)}"
                f"{_venue_breakdown}"
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

        # Phase 9.2 — Pink Slip / succession context blocks
        pink_slip_block = ""
        if trader.get("drawdown_warning_level") == "warning":
            from app.services.drawdown_monitor import get_pink_slip_text
            _dd_pct = trader.get("lifetime_drawdown_pct") or 7.0
            pink_slip_block = f"\n{get_pink_slip_text(_dd_pct)}\n"
        elif trader.get("drawdown_warning_level") == "caution":
            _dd_pct = trader.get("lifetime_drawdown_pct") or 5.0
            pink_slip_block = (
                f"\n⚠️ CAUTION: Your portfolio is down {_dd_pct:.1f}% from its peak. "
                f"Exercise additional risk discipline — avoid new positions unless confidence ≥ 0.70.\n"
            )

        succession_block = ""
        if config.get("succession_context"):
            succession_block = f"\nEVOLUTION CONTEXT FROM PREDECESSOR:\n{config['succession_context']}\n"

        # Build venues block — Hyperliquid is always available for paper trading.
        # Live trading additionally requires HYPERLIQUID_WALLET_KEY to be set.
        _hl_live = bool(settings.hyperliquid_wallet_key)
        _hl_live_note = (
            " (live trading configured)"
            if _hl_live
            else " (paper trading only — wallet not configured for live)"
        )
        venues_block = (
            "\nAVAILABLE TRADING VENUES:\n"
            "  • phemex      — taker fee 0.06%, USDT-margined perps\n"
            f"  • hyperliquid — taker fee 0.035%, USDC-margined perps{_hl_live_note}\n"
            "\nVENUE SELECTION GUIDANCE:\n"
            "  - Prefer hyperliquid for: ema_crossover, momentum, breakout, grid — strategies that trade"
            " frequently and compound fee savings over many trades. At 0.035% vs 0.06% that is a 42%"
            " reduction in fee drag per trade.\n"
            "  - Use phemex for: mean_reversion or any strategy on a pair not listed on Hyperliquid"
            " (check agent names; common pairs like BTCUSDT, ETHUSDT, SOLUSDT are on both).\n"
            "  - If in doubt, choose hyperliquid — lower fees directly improve net P&L."
        )

        prompt = f"""You are Trader "{trader['name']}", a competing portfolio trader in a hedge fund.

YOUR TRADING STYLE: {config.get('style', 'Balanced approach')}
YOUR RISK TOLERANCE: {config.get('risk_tolerance', 'moderate')}
YOUR PREFERRED STRATEGIES: {config.get('preferred_strategies', ['momentum'])}
YOUR CAPITAL ALLOCATION: {trader.get('allocation_pct', 33.3):.1f}% of fund
{pink_slip_block}{succession_block}
MARKET CONDITIONS:
  Trend: {market_condition.get('trend', '?')}
  Volatility: {market_condition.get('volatility', '?')}
  Momentum: {market_condition.get('momentum', '?')}
{confluence_block}
{whale_block}
{marina_block}

STRATEGY REFERENCE:
{strategy_registry.ai_prompt_summary()}
{venues_block}

YOUR CURRENT AGENTS:
{agent_block}

Review your portfolio of agents. You may propose actions:
- "create_agent": Create a new agent (name, strategy_type, trading_pairs, stop_loss_pct, take_profit_pct, venue)
- "disable_agent": Disable an underperforming agent (agent_id, reason)
- "enable_agent": Re-enable a previously disabled agent (agent_id, reason)
- "adjust_params": Adjust parameters of an existing agent (agent_id, params: {stop_loss_pct, take_profit_pct, trailing_stop_pct, allocation_percentage, venue})

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
- For "venue": choose from the available venues listed above. Follow the venue selection guidance above — hyperliquid is preferred for most strategies when available due to its lower fee structure.

Return JSON:
{{
  "actions": [
    {{"action": "create_agent", "name": "SOL_Momentum", "strategy_type": "momentum", "trading_pairs": ["SOLUSDT"], "stop_loss_pct": 3.5, "take_profit_pct": 7.0, "venue": "hyperliquid", "reason": "..."}},
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
        is_paper: bool = True,
    ) -> TraderAllocation:
        """
        Fund Manager decides how to allocate capital across traders
        based on their aggregate performance.

        Phase 9.1 additions:
        - Consistency score (15% weight) replaces the flat 0.15 floor
        - 40% Rule: INCONSISTENT traders cannot receive a capital increase
        - Sharpe gating: multiplier applied post-allocation (0.7 / 1.0 / 1.3)
        """
        from app.services.consistency_scorer import compute_consistency

        trader_perfs = []
        for t in traders:
            if not t.get("is_enabled", True):
                continue
            t_agents = [a for a in all_agents if a.get("trader_id") == t["id"]]
            perf = self.get_trader_performance(t, t_agents, all_metrics)
            trader_perfs.append(perf)

        if not trader_perfs:
            return TraderAllocation(reasoning="No enabled traders")

        # ── Fetch consistency scores for all traders concurrently ─────────
        import asyncio
        consistency_map = {}
        tasks = [
            compute_consistency(
                p.trader_id,
                [a["id"] for a in all_agents if a.get("trader_id") == p.trader_id],
                is_paper=is_paper,
            )
            for p in trader_perfs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for p, res in zip(trader_perfs, results):
            if isinstance(res, Exception):
                logger.warning(f"Consistency scorer failed for {p.trader_id}: {res}")
                from app.services.consistency_scorer import ConsistencyResult
                consistency_map[p.trader_id] = ConsistencyResult(
                    trader_id=p.trader_id, consistency_score=0.5,
                    consistency_flag="INSUFFICIENT_DATA",
                    max_single_trade_pnl=0, total_pnl=0, offending_trade_pct=0,
                    sharpe=0, sharpe_tier="medium", sharpe_multiplier=1.0, trade_count=0,
                )
            else:
                consistency_map[p.trader_id] = res

        # ── Performance-weighted scoring (40% WR, 40% PnL contribution, 15% consistency, 5% floor) ──
        scores = {}
        
        # Calculate total P&L across all traders to find contribution ratio
        total_pnl_all = sum(max(p.total_pnl or 0, 0) for p in trader_perfs)
        
        for p in trader_perfs:
            wr_score = p.win_rate
            
            # P&L contribution: actual ratio of this trader's P&L to total
            # If Otto made $268 out of $352 total gross = 76% contribution
            trader_pnl = max(p.total_pnl or 0, 0)
            if total_pnl_all > 0:
                pnl_contribution = trader_pnl / total_pnl_all
            else:
                pnl_contribution = 0.5  # Neutral if no positive P&L yet
            
            cr = consistency_map[p.trader_id]
            consistency_score = cr.consistency_score
            
            # Score: 40% win_rate + 40% P&L contribution + 15% consistency + 5% floor
            # High-P&L traders like Otto get significant boost
            scores[p.trader_id] = max(
                wr_score * 0.40 + pnl_contribution * 0.40 + consistency_score * 0.15,
                0.05,
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

        # ── 9.1 Hard rules ────────────────────────────────────────────────
        prev_allocs = {t["id"]: t.get("allocation_pct", 33.3) for t in traders}
        for tid, cr in consistency_map.items():
            if tid not in allocations:
                continue
            proposed = allocations[tid]
            prev = prev_allocs.get(tid, 33.3)

            # 40% Rule: block capital increase for INCONSISTENT traders
            if cr.consistency_flag == "INCONSISTENT":
                allocations[tid] = min(proposed, prev)
                logger.info(
                    f"9.1 Consistency gate: {tid[:8]} flagged INCONSISTENT — "
                    f"capital increase blocked (capped at {prev:.1f}%)"
                )

            # Sharpe gate: apply multiplier then re-clamp
            allocations[tid] = max(
                self.MIN_ALLOCATION_PCT,
                min(self.MAX_ALLOCATION_PCT, allocations[tid] * cr.sharpe_multiplier),
            )

        # Re-normalize after hard-rule adjustments
        total_adj = sum(allocations.values())
        if total_adj > 0:
            allocations = {tid: pct / total_adj * 100 for tid, pct in allocations.items()}

        reasoning_parts = []
        for p in trader_perfs:
            cr = consistency_map[p.trader_id]
            reasoning_parts.append(
                f"{p.trader_name}: P&L=${p.total_pnl:+.2f}, "
                f"WR={p.win_rate:.0%}, trades={p.total_trades}, "
                f"consistency={cr.consistency_flag}, sharpe={cr.sharpe:.2f} ({cr.sharpe_tier}) → "
                f"{allocations.get(p.trader_id, 0):.1f}%"
            )

        return TraderAllocation(
            allocations=allocations,
            reasoning="Performance-weighted (9.1): " + "; ".join(reasoning_parts),
            consistency_flags={tid: cr.consistency_flag for tid, cr in consistency_map.items()},
            sharpe_tiers={tid: cr.sharpe_tier for tid, cr in consistency_map.items()},
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
                    "drawdown_warning_level": t.drawdown_warning_level,
                    "lifetime_drawdown_pct": t.lifetime_drawdown_pct,
                    "successor_of": t.successor_of,
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
                "drawdown_warning_level": t.drawdown_warning_level,
                "lifetime_drawdown_pct": t.lifetime_drawdown_pct,
                "successor_of": t.successor_of,
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
