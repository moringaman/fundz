from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
import json
import re
import logging

from app.services.llm import LLMService

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM output that may contain markdown fences or preamble."""
    # Try raw parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fences
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Find first [ ... ] or { ... } block
    for start_char, end_char in [('[', ']'), ('{', '}')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise json.JSONDecodeError("No valid JSON found", text, 0)


@dataclass
class AgentLeaderboardEntry:
    """Agent performance leaderboard entry"""
    agent_id: str
    agent_name: str
    total_pnl: float
    win_rate: float
    total_runs: int
    contribution_pct: float  # % of total fund returns
    rank: int


@dataclass
class StrategicRecommendation:
    """Strategic recommendation from CIO"""
    recommendation: str  # "enable_agent", "disable_agent", "increase_allocation", etc.
    target: str  # agent_id or "portfolio"
    confidence: float  # 0.0-1.0
    rationale: str
    expected_impact: str  # qualitative description


@dataclass
class FundHealthReport:
    """Comprehensive fund health assessment"""
    timestamp: datetime
    period: str  # "daily", "weekly", "monthly"
    fund_performance: Dict[str, Any]  # {total_return, sharpe, max_drawdown, ...}
    agent_leaderboard: List[AgentLeaderboardEntry]
    strategy_performance: Dict[str, float]  # {strategy_type: contribution_pct}
    risk_metrics: Dict[str, Any]  # {current_risk_level, max_daily_loss, ...}
    strategic_recommendations: List[StrategicRecommendation]
    executive_summary: str  # High-level text summary
    cio_sentiment: str  # "very_bullish", "bullish", "neutral", "bearish", "very_bearish"
    cio_reasoning: str


class CIOAgent:
    """
    Chief Investment Officer Agent: Provides strategic oversight, fund reporting,
    performance attribution, and strategic recommendations for portfolio direction.
    """

    def __init__(self):
        self.llm_service = LLMService()

    async def generate_fund_report(
        self,
        analyst_report: Optional[Dict] = None,
        portfolio_decision: Optional[Dict] = None,
        risk_assessment: Optional[Dict] = None,
        agent_metrics: List[Dict] = None,
        current_positions: Optional[Dict] = None,
        fund_performance: Optional[Dict] = None,
        period: str = "daily",
        trade_insights: Optional[Dict] = None,
    ) -> FundHealthReport:
        """
        Generate comprehensive fund health report
        """
        timestamp = datetime.utcnow()
        agent_metrics = agent_metrics or []

        try:
            # Calculate fund performance metrics
            fund_perf = self._calculate_fund_performance(
                agent_metrics,
                fund_performance,
                current_positions
            )

            # Generate agent leaderboard
            leaderboard = self._generate_leaderboard(agent_metrics, fund_perf)

            # Analyze strategy performance
            strategy_perf = self._analyze_strategy_performance(agent_metrics)

            # Extract risk metrics
            risk_metrics = self._extract_risk_metrics(risk_assessment, current_positions)

            # Generate strategic recommendations using LLM
            strategic_recs = await self._generate_strategic_recommendations(
                fund_perf, leaderboard, strategy_perf, risk_metrics,
                analyst_report, portfolio_decision, agent_metrics,
                trade_insights=trade_insights
            )

            # Build executive summary
            executive_summary = self._build_executive_summary(
                fund_perf, leaderboard, strategic_recs, period
            )

            # Determine CIO sentiment
            cio_sentiment = self._assess_sentiment(fund_perf, strategic_recs)

            # Build detailed reasoning
            cio_reasoning = self._build_cio_reasoning(
                fund_perf, leaderboard, strategy_perf, strategic_recs
            )

            report = FundHealthReport(
                timestamp=timestamp,
                period=period,
                fund_performance=fund_perf,
                agent_leaderboard=leaderboard,
                strategy_performance=strategy_perf,
                risk_metrics=risk_metrics,
                strategic_recommendations=strategic_recs,
                executive_summary=executive_summary,
                cio_sentiment=cio_sentiment,
                cio_reasoning=cio_reasoning
            )

            return report

        except Exception as e:
            logger.error(f"Fund report generation failed: {e}")
            return self._default_report(timestamp, period)

    def _calculate_fund_performance(
        self,
        agent_metrics: List[Dict],
        fund_performance: Optional[Dict],
        current_positions: Optional[Dict]
    ) -> Dict[str, Any]:
        """Calculate fund-level performance metrics"""
        if fund_performance:
            return fund_performance

        # Fallback: calculate from agent metrics
        total_pnl = sum(m.get('total_pnl', 0) for m in agent_metrics)
        total_runs = sum(m.get('total_runs', 0) for m in agent_metrics)
        winning_runs = sum(
            int(m.get('win_rate', 0) * m.get('total_runs', 0))
            for m in agent_metrics
        )

        return {
            'total_return_pct': 0.0,  # Would need balance data
            'total_pnl': total_pnl,
            'win_rate': winning_runs / total_runs if total_runs > 0 else 0.0,
            'sharpe_ratio': 0.0,  # Would need time-series data
            'max_drawdown_pct': 0.0,  # Would need time-series data
            'total_runs': total_runs,
            'profitable_runs': winning_runs
        }

    def _generate_leaderboard(
        self,
        agent_metrics: List[Dict],
        fund_perf: Dict
    ) -> List[AgentLeaderboardEntry]:
        """Generate agent performance leaderboard"""
        leaderboard = []
        total_pnl = fund_perf.get('total_pnl', 1)  # Avoid division by zero

        for rank, metric in enumerate(sorted(
            agent_metrics,
            key=lambda m: m.get('total_pnl', 0),
            reverse=True
        ), 1):
            agent_id = metric.get('agent_id', f'agent_{rank}')
            agent_name = metric.get('agent_name', agent_id)

            contribution_pct = (
                (metric.get('total_pnl', 0) / total_pnl * 100)
                if total_pnl != 0 else 0
            )

            leaderboard.append(AgentLeaderboardEntry(
                agent_id=agent_id,
                agent_name=agent_name,
                total_pnl=metric.get('total_pnl', 0),
                win_rate=metric.get('win_rate', 0),
                total_runs=metric.get('total_runs', 0),
                contribution_pct=contribution_pct,
                rank=rank
            ))

        return leaderboard

    def _analyze_strategy_performance(
        self,
        agent_metrics: List[Dict]
    ) -> Dict[str, float]:
        """Analyze performance by strategy type"""
        strategy_pnl = {}
        strategy_runs = {}

        for metric in agent_metrics:
            strategy = metric.get('strategy_type', 'unknown')
            pnl = metric.get('total_pnl', 0)
            runs = metric.get('total_runs', 0)

            strategy_pnl[strategy] = strategy_pnl.get(strategy, 0) + pnl
            strategy_runs[strategy] = strategy_runs.get(strategy, 0) + runs

        total_pnl = sum(strategy_pnl.values()) or 1

        return {
            strategy: (pnl / total_pnl * 100)
            for strategy, pnl in strategy_pnl.items()
        }

    def _extract_risk_metrics(
        self,
        risk_assessment: Optional[Dict],
        current_positions: Optional[Dict]
    ) -> Dict[str, Any]:
        """Extract risk metrics from assessment"""
        if risk_assessment:
            return {
                'risk_level': risk_assessment.get('risk_level', 'unknown'),
                'daily_pnl': risk_assessment.get('daily_pnl', 0),
                'portfolio_exposure': risk_assessment.get('portfolio_exposure', 0),
                'max_daily_loss_limit': risk_assessment.get('max_daily_loss_limit', 0),
            }

        return {
            'risk_level': 'unknown',
            'daily_pnl': 0,
            'portfolio_exposure': 0,
            'max_daily_loss_limit': 0,
        }

    async def _generate_strategic_recommendations(
        self,
        fund_perf: Dict,
        leaderboard: List[AgentLeaderboardEntry],
        strategy_perf: Dict[str, float],
        risk_metrics: Dict,
        analyst_report: Optional[Dict],
        portfolio_decision: Optional[Dict],
        agent_metrics: List[Dict],
        trade_insights: Optional[Dict] = None,
    ) -> List[StrategicRecommendation]:
        """Generate LLM-based strategic recommendations"""
        recommendations = []

        try:
            # Build context for LLM
            context = self._build_strategic_context(
                fund_perf, leaderboard, strategy_perf, risk_metrics,
                analyst_report, portfolio_decision, agent_metrics,
                trade_insights=trade_insights
            )

            prompt = f"""You are the Chief Investment Officer of a crypto trading fund.
Based on this comprehensive fund status, provide strategic recommendations:

FUND PERFORMANCE:
Total P&L: ${(fund_perf.get('total_pnl', 0) or 0):.2f}
Win Rate: {(fund_perf.get('win_rate', 0) or 0):.1%}
Sharpe Ratio: {(fund_perf.get('sharpe_ratio', 0) or 0):.2f}
Max Drawdown: {(fund_perf.get('max_drawdown_pct', 0) or 0):.2f}%

AGENT LEADERBOARD (Top 3):
{self._format_leaderboard(leaderboard[:3])}

STRATEGY PERFORMANCE:
{self._format_strategy_perf(strategy_perf)}

RISK STATUS:
{risk_metrics.get('risk_level', 'unknown')} (Daily P&L: ${risk_metrics.get('daily_pnl', 0):.2f})

MARKET CONTEXT:
{context}

Provide 2-3 strategic recommendations in JSON format.

IMPORTANT RULES:
- Only recommend disable_agent if the agent has ≥15 runs AND win rate < 30% AND P&L is negative.
- Recommend enable_agent if a disabled agent has ✓ regime fit (shown above) AND backtest results were positive.
- Momentum, breakout, and trend-following strategies are GOOD in trending_down markets — consider re-enabling them.
- Mean-reversion and grid strategies are GOOD in ranging markets — re-enable when regime shifts.
- Never disable an agent that was just re-enabled (give it at least 15 runs first).
- Use TRADE LEARNING data (shown above) when it indicates a strategy is systematically failing or excelling.

IMPORTANT: Respond with ONLY the JSON array, no markdown fences, no preamble text.
[
  {{
    "recommendation": "enable_agent|disable_agent|increase_allocation|reduce_risk|add_new_strategy|diversify|pause_strategy",
    "target": "agent_name or strategy_type or portfolio",
    "confidence": 0.0-1.0,
    "rationale": "brief explanation",
    "expected_impact": "qualitative description of impact"
  }},
  ...
]
"""

            response = await self.llm_service._call_llm(prompt)

            try:
                recs_data = _extract_json(response.content)
                # Handle LLM wrapping the list in a dict like {"recommendations": [...]}
                if isinstance(recs_data, dict):
                    recs_data = recs_data.get('recommendations', list(recs_data.values())[0] if recs_data else [])
                if not isinstance(recs_data, list):
                    recs_data = [recs_data]
                for rec_dict in recs_data:
                    if not isinstance(rec_dict, dict):
                        continue
                    recommendations.append(StrategicRecommendation(
                        recommendation=rec_dict.get('recommendation', ''),
                        target=rec_dict.get('target', ''),
                        confidence=float(rec_dict.get('confidence', 0.5)),
                        rationale=rec_dict.get('rationale', ''),
                        expected_impact=rec_dict.get('expected_impact', '')
                    ))
            except (json.JSONDecodeError, ValueError, KeyError, AttributeError, TypeError) as e:
                logger.warning(f"Failed to parse LLM recommendations ({e}), raw: {response.content[:200]}")
                recommendations = self._default_recommendations(fund_perf, leaderboard)

        except Exception as e:
            logger.error(f"Strategic recommendation generation failed: {e}")
            recommendations = self._default_recommendations(fund_perf, leaderboard)

        return recommendations

    def _build_strategic_context(
        self,
        fund_perf: Dict,
        leaderboard: List[AgentLeaderboardEntry],
        strategy_perf: Dict[str, float],
        risk_metrics: Dict,
        analyst_report: Optional[Dict],
        portfolio_decision: Optional[Dict],
        agent_metrics: List[Dict],
        trade_insights: Optional[Dict] = None,
    ) -> str:
        """Build context string for LLM"""
        context_lines = []

        current_regime = "unknown"
        if analyst_report:
            current_regime = analyst_report.get('regime', 'unknown')
            context_lines.append(f"Analyst Market Sentiment: {analyst_report.get('sentiment', 'neutral')}")
            context_lines.append(f"Market Regime: {current_regime}")

        if portfolio_decision:
            context_lines.append(f"Portfolio Manager Recommendation: {portfolio_decision.get('recommendation', 'hold')}")

        # Best performing agent
        if leaderboard:
            top = leaderboard[0]
            context_lines.append(f"Top performer: {top.agent_name} (+${top.total_pnl:.2f}, {top.win_rate:.1%} win rate)")

        # Best performing strategy
        if strategy_perf:
            best_strategy = max(strategy_perf.items(), key=lambda x: x[1])
            context_lines.append(f"Best strategy: {best_strategy[0]} ({best_strategy[1]:.1f}% of returns)")

        # Disabled agents that may be suitable for current regime
        disabled = [m for m in agent_metrics if not m.get("is_enabled", True)]
        if disabled:
            try:
                import app.strategies as strategy_registry
                regime_suitable = []
                for m in disabled:
                    stype = m.get("strategy_type", "")
                    profile = strategy_registry.get(stype) or {}
                    avoid = profile.get("avoid_conditions", [])
                    good_for = profile.get("market_conditions", [])
                    regime_ok = current_regime not in avoid
                    regime_match = not good_for or current_regime in good_for
                    runs = m.get("total_runs", 0)
                    win_rate = m.get("win_rate", 0.0)
                    regime_suitable.append(
                        f"  {m.get('name', stype)} ({stype}): "
                        f"{runs} runs, {win_rate:.0%} WR — "
                        f"regime fit: {'✓' if (regime_ok and regime_match) else '✗'} "
                        f"({'good for ' + current_regime if regime_ok and regime_match else 'avoid in ' + current_regime})"
                    )
                context_lines.append(f"\nDISABLED AGENTS ({len(disabled)} total — consider re-enabling if regime fits):")
                context_lines.extend(regime_suitable)
            except Exception:
                context_lines.append(f"\nDisabled agents: {len(disabled)} (regime fitness unavailable)")

        # ── Trade learning: strategy-level retrospective insights ──────────────
        if trade_insights:
            strategy_insights = trade_insights.get("strategy_insights", {})
            agent_insights = trade_insights.get("agent_insights", {})
            trade_count = trade_insights.get("trade_count", 0)
            summary = trade_insights.get("summary", "")

            if strategy_insights or agent_insights:
                context_lines.append(f"\nTRADE LEARNING ({trade_count} trades analysed in last 48h):")
                if summary:
                    context_lines.append(f"  Summary: {summary}")

                # Strategy-level patterns
                if strategy_insights:
                    context_lines.append("  Strategy performance across all agents:")
                    for stype, si in strategy_insights.items():
                        wr = si.get("win_rate", 0)
                        total = si.get("total_trades", 0)
                        n_agents = si.get("agent_count", 1)
                        weaknesses = si.get("weaknesses", [])
                        strengths = si.get("strengths", [])
                        adj = si.get("confidence_adj", 0.0)
                        adj_str = f" [confidence {'+' if adj >= 0 else ''}{adj:.0%}]" if adj != 0 else ""
                        line = f"    {stype}: {wr:.0%} WR over {total} trades ({n_agents} agents){adj_str}"
                        if weaknesses:
                            line += f" — ⚠ {weaknesses[0]}"
                        elif strengths:
                            line += f" — ✓ {strengths[0]}"
                        context_lines.append(line)

                # Agent-level highlights (top issues only)
                struggling_agents = [
                    (aid, ins) for aid, ins in agent_insights.items()
                    if ins.get("win_rate", 1.0) < 0.35 and ins.get("total_trades", 0) >= 3
                ]
                if struggling_agents:
                    context_lines.append("  Underperforming agents (WR < 35%, ≥3 trades):")
                    for aid, ins in struggling_agents[:5]:
                        name = ins.get("agent_name", aid[:12])
                        wr = ins.get("win_rate", 0)
                        weakness = ins.get("weaknesses", ["no pattern identified"])[0]
                        context_lines.append(f"    {name}: {wr:.0%} WR — {weakness}")

        return "\n".join(context_lines) if context_lines else "Standard market conditions"

    def _format_leaderboard(self, leaderboard: List[AgentLeaderboardEntry]) -> str:
        """Format leaderboard for LLM"""
        lines = []
        for entry in leaderboard:
            lines.append(
                f"{entry.rank}. {entry.agent_name}: ${entry.total_pnl:.2f} "
                f"({entry.win_rate:.1%} WR, {entry.total_runs} runs)"
            )
        return "\n".join(lines) if lines else "No leaderboard data"

    def _format_strategy_perf(self, strategy_perf: Dict[str, float]) -> str:
        """Format strategy performance for LLM"""
        lines = []
        for strategy, contrib in sorted(strategy_perf.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  {strategy}: {contrib:.1f}% of returns")
        return "\n".join(lines) if lines else "No strategy data"

    def _build_executive_summary(
        self,
        fund_perf: Dict,
        leaderboard: List[AgentLeaderboardEntry],
        recommendations: List[StrategicRecommendation],
        period: str
    ) -> str:
        """Build high-level executive summary"""
        lines = []

        # Performance summary
        pnl = fund_perf.get('total_pnl', 0) or 0
        wr = fund_perf.get('win_rate', 0) or 0
        lines.append(f"**{period.capitalize()} Summary**")
        lines.append(f"P&L: ${pnl:+.2f} | Win Rate: {wr:.1%} | Total Runs: {fund_perf.get('total_runs', 0)}")

        # Top agent
        if leaderboard:
            top = leaderboard[0]
            lines.append(f"\nTop Agent: {top.agent_name} ({top.contribution_pct:+.1f}% of returns)")

        # Key recommendations
        if recommendations:
            lines.append(f"\nKey Actions:")
            for rec in recommendations[:3]:
                lines.append(f"  • {rec.recommendation.replace('_', ' ').title()}: {rec.rationale}")

        return "\n".join(lines)

    def _assess_sentiment(
        self,
        fund_perf: Dict,
        recommendations: List[StrategicRecommendation]
    ) -> str:
        """Assess CIO sentiment based on performance and recommendations"""
        pnl = fund_perf.get('total_pnl', 0) or 0
        wr = fund_perf.get('win_rate', 0) or 0

        if pnl > 1000 and wr > 0.65:
            return "very_bullish"
        elif pnl > 0 and wr > 0.55:
            return "bullish"
        elif pnl > -500 and wr > 0.45:
            return "neutral"
        elif pnl < -500 and wr < 0.45:
            return "bearish"
        else:
            return "very_bearish"

    def _build_cio_reasoning(
        self,
        fund_perf: Dict,
        leaderboard: List[AgentLeaderboardEntry],
        strategy_perf: Dict[str, float],
        recommendations: List[StrategicRecommendation]
    ) -> str:
        """Build detailed CIO reasoning"""
        lines = [
            f"Fund Performance: P&L ${(fund_perf.get('total_pnl', 0) or 0):+.2f}, "
            f"WR {(fund_perf.get('win_rate', 0) or 0):.1%}, "
            f"Runs {fund_perf.get('total_runs', 0)}"
        ]

        if leaderboard:
            lines.append(f"\nAgent Performance: {leaderboard[0].agent_name} leading with "
                        f"${leaderboard[0].total_pnl:.2f}")

        if strategy_perf:
            best = max(strategy_perf.items(), key=lambda x: x[1])
            lines.append(f"Best Strategy: {best[0]} contributing {best[1]:.1f}% of returns")

        lines.append(f"\nStrategic Priorities:")
        for rec in recommendations[:3]:
            lines.append(f"  {rec.recommendation}: {rec.rationale}")

        return "\n".join(lines)

    def _default_recommendations(
        self,
        fund_perf: Dict,
        leaderboard: List[AgentLeaderboardEntry]
    ) -> List[StrategicRecommendation]:
        """Return safe default recommendations"""
        recommendations = []

        if fund_perf.get('total_pnl', 0) < 0:
            recommendations.append(StrategicRecommendation(
                recommendation="reduce_risk",
                target="portfolio",
                confidence=0.7,
                rationale="Fund in drawdown, recommend reducing portfolio exposure",
                expected_impact="Lower volatility and daily losses"
            ))

        if leaderboard and leaderboard[0].total_runs > 10:
            recommendations.append(StrategicRecommendation(
                recommendation="increase_allocation",
                target=leaderboard[0].agent_id,
                confidence=0.6,
                rationale=f"Top performer {leaderboard[0].agent_name} should receive more capital",
                expected_impact="Increase fund returns"
            ))

        return recommendations

    def _default_report(
        self,
        timestamp: datetime,
        period: str
    ) -> FundHealthReport:
        """Return safe default report"""
        return FundHealthReport(
            timestamp=timestamp,
            period=period,
            fund_performance={'total_pnl': 0, 'win_rate': 0, 'total_runs': 0},
            agent_leaderboard=[],
            strategy_performance={},
            risk_metrics={'risk_level': 'unknown'},
            strategic_recommendations=[],
            executive_summary="Report generation failed. Manual review required.",
            cio_sentiment="neutral",
            cio_reasoning="Unable to assess fund status. Check data sources."
        )


# Global singleton
cio_agent = CIOAgent()
