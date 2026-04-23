"""
Strategy Review Service — Fund Manager ↔ Technical Analyst Cooperation

This service runs as part of the team analysis tier and enables the fund manager
and technical analyst to jointly evaluate agent effectiveness and propose actions:
  - create_agent: Propose a new agent for an underserved strategy/market condition
  - disable_agent: Disable a consistently underperforming agent
  - enable_agent: Re-enable a previously disabled agent with improving conditions
  - adjust_params: Modify agent allocation or risk parameters

Safeguards:
  - Max 1 create + 1 disable per review cycle
  - Actions logged to team chat with full rationale
  - All backtest results persisted for audit trail
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass
import logging

from app.services.technical_analyst import technical_analyst, TechnicalAnalystReport
from app.services.fund_manager import fund_manager, MarketCondition
from app.services.backtest import backtest_engine, BacktestConfig

logger = logging.getLogger(__name__)


@dataclass
class StrategyActionProposal:
    action: str  # create_agent, disable_agent, enable_agent, adjust_params
    target_agent_id: Optional[str]
    target_agent_name: Optional[str]
    strategy_type: Optional[str]
    params: Dict[str, Any]
    rationale: str
    initiated_by: str  # fund_manager, technical_analyst, joint
    confluence_score: float
    backtest_net_pnl: Optional[float] = None
    fit_score: Optional[float] = None


@dataclass
class StrategyReviewResult:
    timestamp: datetime
    confluence_scores: Dict[str, Dict[str, Any]]
    agent_evaluations: List[Dict[str, Any]]
    proposed_actions: List[StrategyActionProposal]
    summary: str


class StrategyReviewService:
    """Coordinates fund manager and technical analyst for strategy lifecycle decisions."""

    # Safeguard limits per cycle
    MAX_CREATES_PER_CYCLE = 1
    MAX_DISABLES_PER_CYCLE = 1
    # Thresholds
    DISABLE_THRESHOLD_SCORE = 0.25   # below this → propose disable
    ENABLE_THRESHOLD_SCORE = 0.6     # above this → propose enable
    CREATE_THRESHOLD_SCORE = 0.7     # confluence must be this high to justify new agent

    async def run_strategy_review(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict],
        market_condition: MarketCondition,
        analyst_report=None,  # Optional[ResearchReport] — Marina's latest regime analysis
    ) -> StrategyReviewResult:
        """
        Joint FM + TA strategy review. Called from agent_scheduler._run_team_analysis().
        
        Flow:
        1. TA: Get confluence scores for all symbols agents trade
        2. TA: Evaluate strategy fit for each agent against current conditions
        3. FM: Score each agent on performance + TA fit
        4. Joint: Propose actions (create/disable/enable/adjust), boosted by Marina's regime recs
        5. Return structured result for team chat + execution
        """
        timestamp = datetime.utcnow()
        metrics_by_id = {m['agent_id']: m for m in agent_metrics}

        # 1. Gather all unique symbols
        symbols = set()
        for agent in agents:
            pairs = agent.get('trading_pairs', ['BTCUSDT'])
            symbols.update(pairs)
        symbols = list(symbols) or ['BTCUSDT']

        # 2. Technical analyst: confluence scores
        try:
            confluence_scores = await technical_analyst.get_confluence_scores(symbols)
        except Exception as e:
            logger.error(f"Strategy review: confluence scoring failed: {e}")
            confluence_scores = {}

        # 3. Evaluate each agent
        agent_evaluations = []
        for agent in agents:
            agent_id = agent['id']
            agent_name = agent.get('name', agent_id)
            strategy_type = agent.get('strategy_type', 'momentum')
            is_enabled = agent.get('is_enabled', True)
            pairs = agent.get('trading_pairs', ['BTCUSDT'])
            primary_symbol = pairs[0] if pairs else 'BTCUSDT'
            metrics = metrics_by_id.get(agent_id, {})

            # TA evaluation
            symbol_confluence = confluence_scores.get(primary_symbol, {})
            confluence = symbol_confluence.get('score', 0.3) or 0

            try:
                ta_report = await technical_analyst.analyze(primary_symbol)
                strategy_fit = technical_analyst.evaluate_strategy_fit(strategy_type, ta_report)
            except Exception:
                strategy_fit = {"fit_score": 0.5, "reasoning": "Analysis unavailable", "recommended_action": "maintain"}

            # FM evaluation: combine performance + technical fit.
            # win_rate is None until _WIN_RATE_MIN_SAMPLE trades — use 0.5 (neutral)
            # so that a missing sample doesn't register as 0% WR and tank the score.
            win_rate = metrics.get('win_rate', 0.5) or 0.5
            total_pnl = metrics.get('total_pnl', 0) or 0
            total_runs = metrics.get('total_runs', 0) or 0
            actual_trades = metrics.get('actual_trades', 0) or 0

            perf_score = win_rate * 0.6 + min(max(total_pnl / 500, -0.4), 0.4)
            if total_runs < 3 or actual_trades < 10:
                perf_score = 0.5  # neutral for new/untested agents — not enough signal to judge

            # Combined score: 40% performance, 30% strategy fit, 30% confluence
            combined = perf_score * 0.4 + strategy_fit['fit_score'] * 0.3 + confluence * 0.3
            combined = round(combined, 3)

            evaluation = {
                'agent_id': agent_id,
                'agent_name': agent_name,
                'strategy_type': strategy_type,
                'is_enabled': is_enabled,
                'symbol': primary_symbol,
                'perf_score': round(perf_score, 3),
                'fit_score': strategy_fit['fit_score'],
                'confluence': confluence,
                'combined_score': combined,
                'fit_reasoning': strategy_fit['reasoning'],
                'fit_action': strategy_fit['recommended_action'],
                'total_runs': total_runs,
                'actual_trades': actual_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
            }
            agent_evaluations.append(evaluation)

        # 4. Propose actions — pass Marina's recommendations for regime-boosted scoring
        proposed_actions = await self._propose_actions(
            agent_evaluations, confluence_scores, market_condition, analyst_report
        )

        # 5. Build summary
        summary = self._build_summary(agent_evaluations, proposed_actions, market_condition)

        return StrategyReviewResult(
            timestamp=timestamp,
            confluence_scores=confluence_scores,
            agent_evaluations=agent_evaluations,
            proposed_actions=proposed_actions,
            summary=summary,
        )

    async def _propose_actions(
        self,
        evaluations: List[Dict],
        confluence_scores: Dict,
        market_condition: MarketCondition,
        analyst_report=None,  # Optional[ResearchReport]
    ) -> List[StrategyActionProposal]:
        proposals: List[StrategyActionProposal] = []
        creates = 0
        disables = 0

        # Build a quick lookup: strategy_type → priority from Marina's recommendations
        marina_priority: Dict[str, float] = {}
        marina_symbols: Dict[str, List[str]] = {}
        if analyst_report and getattr(analyst_report, 'strategy_recommendations', None):
            for rec in analyst_report.strategy_recommendations:
                marina_priority[rec.strategy_type] = rec.priority
                marina_symbols[rec.strategy_type] = rec.recommended_symbols

        # Sort by combined score to process worst first
        sorted_evals = sorted(evaluations, key=lambda e: e['combined_score'])

        for ev in sorted_evals:
            # DISABLE: Very poor combined score + enough data
            if (ev['combined_score'] < self.DISABLE_THRESHOLD_SCORE
                    and ev['is_enabled']
                    and ev['total_runs'] >= 5
                    and disables < self.MAX_DISABLES_PER_CYCLE):
                proposals.append(StrategyActionProposal(
                    action="disable_agent",
                    target_agent_id=ev['agent_id'],
                    target_agent_name=ev['agent_name'],
                    strategy_type=ev['strategy_type'],
                    params={},
                    rationale=(
                        f"Combined score {ev['combined_score']:.2f} below threshold "
                        f"({self.DISABLE_THRESHOLD_SCORE}). "
                        f"Performance: {ev['win_rate']:.0%} win rate, "
                        f"${ev['total_pnl']:.2f} PnL over {ev['total_runs']} runs. "
                        f"Technical fit: {ev['fit_reasoning']}"
                    ),
                    initiated_by="joint",
                    confluence_score=ev['confluence'],
                    fit_score=ev['fit_score'],
                ))
                disables += 1

            # ENABLE: Good conditions for a disabled agent
            elif (ev['combined_score'] >= self.ENABLE_THRESHOLD_SCORE
                    and not ev['is_enabled']):
                proposals.append(StrategyActionProposal(
                    action="enable_agent",
                    target_agent_id=ev['agent_id'],
                    target_agent_name=ev['agent_name'],
                    strategy_type=ev['strategy_type'],
                    params={},
                    rationale=(
                        f"Market conditions now favor this agent. "
                        f"Combined score {ev['combined_score']:.2f} above enable threshold. "
                        f"Technical fit: {ev['fit_reasoning']}"
                    ),
                    initiated_by="joint",
                    confluence_score=ev['confluence'],
                    fit_score=ev['fit_score'],
                ))

            # ADJUST: Allocation increase/decrease based on fit
            elif ev['is_enabled'] and ev['fit_action'] != 'maintain':
                adjust_pct = 5.0 if ev['fit_action'] == 'increase_allocation' else -5.0
                proposals.append(StrategyActionProposal(
                    action="adjust_params",
                    target_agent_id=ev['agent_id'],
                    target_agent_name=ev['agent_name'],
                    strategy_type=ev['strategy_type'],
                    params={"allocation_change_pct": adjust_pct},
                    rationale=(
                        f"Technical conditions suggest {ev['fit_action'].replace('_', ' ')}. "
                        f"{ev['fit_reasoning']} "
                        f"(fit: {ev['fit_score']:.2f}, confluence: {ev['confluence']:.2f})"
                    ),
                    initiated_by="technical_analyst",
                    confluence_score=ev['confluence'],
                    fit_score=ev['fit_score'],
                ))

        # CREATE: Check if there's an opportunity for an underserved strategy
        if creates < self.MAX_CREATES_PER_CYCLE:
            proposal = await self._evaluate_new_agent_opportunity(
                evaluations, confluence_scores, market_condition,
                marina_priority=marina_priority, marina_symbols=marina_symbols,
            )
            if proposal:
                proposals.append(proposal)

        return proposals

    async def _evaluate_new_agent_opportunity(
        self,
        evaluations: List[Dict],
        confluence_scores: Dict,
        market_condition: MarketCondition,
        marina_priority: Dict[str, float] = None,
        marina_symbols: Dict[str, List[str]] = None,
    ) -> Optional[StrategyActionProposal]:
        """Check if market conditions warrant creating a new agent."""
        import app.strategies as strategy_registry
        existing_strategies = {ev['strategy_type'] for ev in evaluations}
        # Load enabled candidates from DB-merged registry; grid requires Marina's explicit recommendation
        try:
            from app.database import get_async_session
            from app.models import StrategyOverride
            from sqlalchemy import select as sa_select
            async with get_async_session() as _db:
                _result = await _db.execute(sa_select(StrategyOverride))
                _enabled = {r.strategy_type for r in _result.scalars().all() if r.enabled}
        except Exception:
            _enabled = set(strategy_registry.all_types())  # fallback: treat all as enabled
        all_strategies = [
            s for s in strategy_registry.ai_proposable()
            if s != 'ai' and s in _enabled
        ]
        if not marina_priority.get('grid', 0):
            all_strategies = [s for s in all_strategies if s != 'grid']
        missing = [s for s in all_strategies if s not in existing_strategies]

        if not missing:
            return None

        marina_priority = marina_priority or {}
        marina_symbols = marina_symbols or {}

        # Sort missing strategies by Marina's priority (higher → try first)
        def _marina_rank(s):
            return marina_priority.get(s, 0.0)

        missing.sort(key=_marina_rank, reverse=True)

        # Pick the best missing strategy for current conditions
        best_strategy = None
        best_bt_result = None
        best_confluence = 0.0
        best_symbol = 'BTCUSDT'

        for strategy in missing:
            # Quick backtest to validate on the best confluence symbol
            try:
                symbol = 'BTCUSDT'
                # Marina's recommended symbols take precedence over pure confluence ranking
                if strategy in marina_symbols and marina_symbols[strategy]:
                    symbol = marina_symbols[strategy][0]
                    best_confluence = marina_priority.get(strategy, self.CREATE_THRESHOLD_SCORE)
                else:
                    for sym, data in confluence_scores.items():
                        if data.get('score', 0) > best_confluence:
                            best_confluence = data['score']
                            symbol = sym

                if best_confluence < self.CREATE_THRESHOLD_SCORE:
                    continue

                config = BacktestConfig(
                    symbol=symbol,
                    strategy=strategy,
                    candle_limit=500,
                )
                result = await backtest_engine.run_backtest(config)

                if result.net_pnl > 0 and result.win_rate > 0.4 and result.sharpe_ratio > 0.5:
                    if best_bt_result is None or result.net_pnl > best_bt_result.net_pnl:
                        best_strategy = strategy
                        best_bt_result = result
                        best_symbol = symbol
            except Exception as e:
                logger.debug(f"New agent backtest for {strategy} failed: {e}")
                continue

        if best_strategy and best_bt_result:
            STRATEGY_NAMES = {
                "momentum": "Momentum Rider",
                "mean_reversion": "Mean Reverter",
                "breakout": "Breakout Hunter",
                "scalping": "Scalp Sniper",
                "trend_following": "Trend Follower",
                "grid": "Grid Trader",
            }
            marina_note = ""
            if best_strategy in marina_priority:
                marina_note = f" Marina research priority: {marina_priority[best_strategy]:.0%}."
            return StrategyActionProposal(
                action="create_agent",
                target_agent_id=None,
                target_agent_name=STRATEGY_NAMES.get(best_strategy, f"Auto-{best_strategy.title()}"),
                strategy_type=best_strategy,
                params={
                    "symbol": best_symbol,
                    "backtest_win_rate": best_bt_result.win_rate,
                    "backtest_net_pnl": best_bt_result.net_pnl,
                    "backtest_sharpe": best_bt_result.sharpe_ratio,
                    "backtest_trades": best_bt_result.total_trades,
                },
                rationale=(
                    f"No active {best_strategy} agent. Backtest shows "
                    f"{(best_bt_result.win_rate or 0):.0%} win rate, "
                    f"${(best_bt_result.net_pnl or 0):.2f} net PnL, "
                    f"Sharpe {(best_bt_result.sharpe_ratio or 0):.2f} "
                    f"over {best_bt_result.total_trades} trades. "
                    f"Market confluence: {(best_confluence or 0):.2f}.{marina_note}"
                ),
                initiated_by="joint",
                confluence_score=best_confluence,
                backtest_net_pnl=best_bt_result.net_pnl,
            )

        return None

    def _build_summary(
        self,
        evaluations: List[Dict],
        actions: List[StrategyActionProposal],
        market: MarketCondition,
    ) -> str:
        lines = [f"Strategy Review — Market: {market.trend}, Vol: {market.volatility}"]
        
        if evaluations:
            best = max(evaluations, key=lambda e: e['combined_score'])
            worst = min(evaluations, key=lambda e: e['combined_score'])
            lines.append(
                f"Best fit: {best['agent_name']} ({(best['combined_score'] or 0):.2f}), "
                f"Weakest: {worst['agent_name']} ({(worst['combined_score'] or 0):.2f})"
            )

        if actions:
            for a in actions:
                name = a.target_agent_name or "new agent"
                lines.append(f"→ {a.action}: {name} ({a.strategy_type}) — {a.rationale[:80]}")
        else:
            lines.append("No strategy changes recommended this cycle.")

        return "\n".join(lines)


strategy_review_service = StrategyReviewService()
