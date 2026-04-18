from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
import logging
import json

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService
from app.services.llm import LLMService
from app.services.whale_intelligence import whale_intelligence

logger = logging.getLogger(__name__)


@dataclass
class MarketCondition:
    trend: str  # bullish, bearish, sideways
    volatility: str  # high, medium, low
    rsi: float
    momentum: str  # strong, moderate, weak
    recommendation: str


@dataclass
class AgentRecommendation:
    agent_id: str
    agent_name: str
    action: str  # enable, disable, maintain
    reason: str
    confidence: float
    allocation_change: float = 0


@dataclass
class AllocationDecision:
    """Portfolio allocation decision from portfolio manager"""
    timestamp: datetime
    allocation: Dict[str, float]  # {agent_id: capital_amount}
    allocation_pct: Dict[str, float]  # {agent_id: percentage}
    reasoning: str
    based_on_market_condition: Optional[MarketCondition] = None
    expected_return_pct: float = 0.0


@dataclass
class RebalancingPlan:
    """Plan for portfolio rebalancing"""
    timestamp: datetime
    rebalancing_needed: bool
    positions_to_reduce: List[Dict]  # [{agent_id, current_allocation, target_allocation}, ...]
    positions_to_increase: List[Dict]
    total_rebalancing_impact: float  # estimated % impact
    timing_recommendation: str  # "immediate", "gradual", "wait"
    reasoning: str


@dataclass
class PerformanceAttribution:
    """Attribution of fund returns by agent/strategy"""
    timestamp: datetime
    total_pnl: float
    agent_contributions: Dict[str, float]  # {agent_id: pnl_amount}
    strategy_contributions: Dict[str, float]  # {strategy_type: pnl_amount}
    top_performer: Optional[str]  # agent_id
    worst_performer: Optional[str]
    average_agent_return: float
    concentration_risk: float  # % of returns from top agent


class FundManagerAgent:
    def __init__(self):
        self.indicator_service = IndicatorService()
        self.phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet
        )
        self.llm_service = LLMService()
        self._last_rebalance = None
    
    async def analyze_market(self, symbol: str = None) -> MarketCondition:
        if not symbol:
            try:
                from app.api.routes.settings import get_trading_prefs
                pairs = get_trading_prefs().trading_pairs
                symbol = pairs[0] if pairs else "BTCUSDT"
            except Exception:
                symbol = "BTCUSDT"
        try:
            klines = await self.phemex.get_klines(symbol, "1h", 100)

            # get_klines returns a list of rows directly
            data = klines if isinstance(klines, list) else klines.get('data', [])

            if not data or len(data) < 50:
                return MarketCondition(
                    trend="sideways",
                    volatility="medium",
                    rsi=50,
                    momentum="moderate",
                    recommendation="hold"
                )
            df_data = []
            for k in data:
                df_data.append({
                    'time': k[0] / 1000,
                    'open': float(k[2]),
                    'high': float(k[3]),
                    'low': float(k[4]),
                    'close': float(k[5]),
                    'volume': float(k[7]),
                })
            
            df = pd.DataFrame(df_data)
            df = df.sort_values('time')
            
            indicators = self.indicator_service.calculate_all(df)
            
            rsi = indicators.get('rsi', 50) or 50
            sma_20 = indicators.get('sma_20')
            sma_50 = indicators.get('sma_50')
            sma_200 = indicators.get('sma_200')
            
            current_price = df['close'].iloc[-1]
            
            if sma_20 and sma_50:
                if current_price > sma_20 > sma_50:
                    trend = "bullish"
                elif current_price < sma_20 < sma_50:
                    trend = "bearish"
                else:
                    trend = "sideways"
            else:
                trend = "sideways"
            
            if sma_20 and sma_200:
                if current_price > sma_200:
                    trend = "bullish" if trend == "sideways" else trend
                else:
                    trend = "bearish" if trend == "sideways" else trend
            
            volatility = "medium"
            if sma_20 and sma_50:
                volatility_pct = abs(sma_20 - sma_50) / sma_20 * 100
                if volatility_pct > 5:
                    volatility = "high"
                elif volatility_pct < 2:
                    volatility = "low"
            
            if rsi < 30:
                momentum = "strong" if trend == "bullish" else "weak"
            elif rsi > 70:
                momentum = "strong" if trend == "bearish" else "weak"
            else:
                momentum = "moderate"
            
            if trend == "bullish" and rsi < 70 and momentum != "weak":
                recommendation = "buy"
            elif trend == "bearish" and rsi > 30 and momentum != "weak":
                recommendation = "sell"
            else:
                recommendation = "hold"
            
            return MarketCondition(
                trend=trend,
                volatility=volatility,
                rsi=rsi,
                momentum=momentum,
                recommendation=recommendation
            )
            
        except Exception as e:
            logger.error(f"Market analysis failed: {e}")
            return MarketCondition(
                trend="sideways",
                volatility="medium",
                rsi=50,
                momentum="moderate",
                recommendation="hold"
            )
    
    async def evaluate_agents(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict],
        market_condition: MarketCondition
    ) -> List[AgentRecommendation]:
        recommendations = []
        
        metrics_by_id = {m['agent_id']: m for m in agent_metrics}
        
        for agent in agents:
            agent_id = agent['id']
            agent_name = agent['name']
            strategy_type = agent.get('strategy_type', 'momentum')
            metrics = metrics_by_id.get(agent_id, {})
            
            win_rate = metrics.get('win_rate', 0) or 0
            total_pnl = metrics.get('total_pnl', 0) or 0
            total_runs = metrics.get('total_runs', 0) or 0
            last_run = metrics.get('last_run')
            
            score = 0
            reasons = []
            
            if total_runs > 0:
                if win_rate >= 0.6:
                    score += 30
                    reasons.append(f"High win rate: {win_rate:.1%}")
                elif win_rate >= 0.5:
                    score += 15
                    reasons.append(f"Positive win rate: {win_rate:.1%}")
                elif win_rate >= 0.4:
                    score += 0
                    reasons.append(f"Low win rate: {win_rate:.1%}")
                else:
                    score -= 20
                    reasons.append(f"Poor win rate: {win_rate:.1%}")
                
                if total_pnl > 0:
                    score += 20
                    reasons.append(f"Profitable: ${total_pnl:.2f}")
                elif total_pnl < -100:
                    score -= 30
                    reasons.append(f"Losses: ${total_pnl:.2f}")
                
                if last_run:
                    parsed = datetime.fromisoformat(last_run.replace('Z', '+00:00'))
                    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
                    time_since_run = now - parsed
                    if time_since_run.total_seconds() < 3600:
                        score += 10
                        reasons.append("Recently active")
            else:
                score += 5
                reasons.append("New agent - no performance data")
            
            strategy_match = self._strategy_matches_market(strategy_type, market_condition)
            if strategy_match['match']:
                score += 15
                reasons.append(strategy_match['reason'])
            else:
                score -= 10
                reasons.append(strategy_match['reason'])
            
            if market_condition.volatility == "high":
                if strategy_type in ["breakout", "momentum"]:
                    score += 10
                    reasons.append("Strategy suited for high volatility")
                elif strategy_type == "mean_reversion":
                    score -= 15
                    reasons.append("Mean reversion risky in high volatility")
            
            action = "maintain"
            if score >= 40:
                action = "enable"
            elif score <= -20:
                action = "disable"
            
            confidence = min(abs(score) / 60, 1.0)
            
            recommendations.append(AgentRecommendation(
                agent_id=agent_id,
                agent_name=agent_name,
                action=action,
                reason="; ".join(reasons) if reasons else "No issues found",
                confidence=confidence
            ))
        
        return recommendations

    async def make_allocation_decision(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict],
        market_condition: MarketCondition,
        total_capital: float = 10000,
        confluence_scores: Optional[Dict[str, Dict]] = None,
    ) -> AllocationDecision:
        """
        Use LLM to make intelligent capital allocation decisions
        based on agent performance, market conditions, and technical confluence.
        """
        try:
            metrics_by_id = {m['agent_id']: m for m in agent_metrics}

            # Build context for LLM
            context = self._build_allocation_context(
                agents, metrics_by_id, market_condition, total_capital,
                confluence_scores=confluence_scores,
            )

            confluence_section = ""
            if confluence_scores:
                conf_lines = []
                for sym, data in confluence_scores.items():
                    conf_lines.append(
                        f"  {sym}: signal={data.get('signal','?')}, "
                        f"confluence={data.get('score',0):.0%}, "
                        f"alignment={data.get('alignment','?')}, "
                        f"patterns={data.get('patterns',0)}"
                    )
                confluence_section = "\nTECHNICAL CONFLUENCE (from Technical Analyst):\n" + "\n".join(conf_lines) + "\n"

            # Whale intelligence context
            try:
                whale_report = await whale_intelligence.fetch_whale_report()
                whale_section = whale_intelligence.build_llm_context_block(whale_report)
                whale_section += whale_intelligence.build_exit_pressure_insights(whale_report)
            except Exception:
                whale_section = ""

            prompt = f"""You are a professional portfolio manager allocating capital across trading agents.

AVAILABLE CAPITAL: ${total_capital:,.0f}

MARKET CONDITIONS:
Trend: {market_condition.trend}
Volatility: {market_condition.volatility}
RSI: {(market_condition.rsi or 50):.1f}
Momentum: {market_condition.momentum}
Recommendation: {market_condition.recommendation}
{confluence_section}{whale_section}

AGENT PERFORMANCE DATA:
{context}

ALLOCATION RULES:
1. Allocate more capital to agents that perform well in current market
2. Reduce allocation to underperforming agents, but never below 5%
3. Consider market regime (bullish/bearish/sideways)
4. Diversify across strategies
5. Minimum 5% for every enabled agent (new agents need trades to build history)
6. Maximum 40% per agent
7. Prefer agents whose strategy aligns with technical confluence (high confluence = favorable)

Determine optimal capital allocation. Return JSON:
{{
  "allocation": {{"agent_id": amount_in_dollars, ...}},
  "allocation_pct": {{"agent_id": percentage, ...}},
  "reasoning": "explanation of allocation logic",
  "expected_return_pct": 0.0-10.0
}}
"""

            response = await self.llm_service._call_llm(prompt)

            try:
                data = json.loads(response.content)
                alloc_pct = data.get('allocation_pct', {})

                # Enforce minimum 5% floor — LLMs sometimes ignore the instruction
                min_pct = 5.0
                agent_ids = [a['id'] for a in agents]
                for aid in agent_ids:
                    if alloc_pct.get(aid, 0) < min_pct:
                        alloc_pct[aid] = min_pct
                # Re-normalise so they sum to ~100
                total_pct = sum(alloc_pct.get(aid, 0) for aid in agent_ids)
                if total_pct > 0:
                    scale = 100 / total_pct
                    alloc_pct = {aid: alloc_pct.get(aid, 0) * scale for aid in agent_ids}

                allocation = {aid: total_capital * alloc_pct.get(aid, 0) / 100 for aid in agent_ids}

                return AllocationDecision(
                    timestamp=datetime.utcnow(),
                    allocation=allocation,
                    allocation_pct=alloc_pct,
                    reasoning=data.get('reasoning', ''),
                    based_on_market_condition=market_condition,
                    expected_return_pct=float(data.get('expected_return_pct', 0))
                )
            except (json.JSONDecodeError, ValueError):
                # Fallback: performance-weighted allocation without LLM
                logger.warning("LLM allocation parse failed, using performance-weighted fallback")
                scores = {}
                for a in agents:
                    aid = a['id']
                    m = metrics_by_id.get(aid, {})
                    wr = m.get('win_rate', 0.5) or 0.5
                    pnl = m.get('total_pnl', 0) or 0
                    pnl_factor = min(max(pnl / 1000 + 0.5, 0), 1.0)
                    scores[aid] = max(wr * 0.6 + pnl_factor * 0.4, 0.1)

                total_score = sum(scores.values()) or 1
                alloc_pct = {aid: max((s / total_score) * 100, 5.0) for aid, s in scores.items()}
                norm = sum(alloc_pct.values())
                if norm > 0:
                    alloc_pct = {aid: p / norm * 100 for aid, p in alloc_pct.items()}
                allocation = {aid: total_capital * alloc_pct.get(aid, 0) / 100 for aid in alloc_pct}

                return AllocationDecision(
                    timestamp=datetime.utcnow(),
                    allocation=allocation,
                    allocation_pct=alloc_pct,
                    reasoning="Performance-weighted allocation (LLM response parsing failed)",
                    based_on_market_condition=market_condition
                )

        except Exception as e:
            logger.error(f"Allocation decision failed: {e}")
            return AllocationDecision(
                timestamp=datetime.utcnow(),
                allocation={},
                allocation_pct={},
                reasoning=f"Error: {str(e)}",
                based_on_market_condition=market_condition
            )

    async def make_trader_allocation_decision(
        self,
        traders: List[Dict],
        trader_performance: List[Dict],
        market_condition: MarketCondition,
        total_capital: float = 10000,
        confluence_scores: Optional[Dict[str, Dict]] = None,
    ) -> Dict[str, float]:
        """
        James (Portfolio Manager) allocates capital proportionately across traders
        using LLM judgment based on trader performance and market conditions.
        Returns {trader_id: allocation_pct}.
        """
        try:
            if not traders:
                return {}

            # Build trader context for LLM
            context_lines = []
            for t in traders:
                if not t.get("is_enabled", True):
                    continue
                perf = next((p for p in trader_performance if p.get("trader_id") == t["id"]), {})
                agent_count = perf.get("agent_count", len([
                    a for a in t.get("agents", []) if a
                ]))
                context_lines.append(f"\n{t.get('name', t['id'])} ({t.get('llm_provider','?')} / {t.get('llm_model','?')}):")
                context_lines.append(f"  Current Allocation: {t.get('allocation_pct', 33.3):.1f}%")
                context_lines.append(f"  Strategies Managed: {perf.get('agent_count', 0)}")
                context_lines.append(f"  Total P&L: ${perf.get('total_pnl', 0):+.2f}")
                context_lines.append(f"  Win Rate: {perf.get('win_rate', 0):.1%}")
                context_lines.append(f"  Total Trades: {perf.get('total_trades', 0)}")
                sharpe = perf.get("sharpe_ratio", None)
                if sharpe is not None:
                    context_lines.append(f"  Sharpe Ratio: {sharpe:.2f}")
                if perf.get("total_trades", 0) == 0:
                    context_lines.append(f"  Status: NEW TRADER (no live trades yet — give time to establish)")

            trader_context = "\n".join(context_lines)

            confluence_section = ""
            if confluence_scores:
                conf_lines = []
                for sym, data in confluence_scores.items():
                    conf_lines.append(
                        f"  {sym}: signal={data.get('signal','?')}, "
                        f"confluence={data.get('score',0):.0%}, "
                        f"alignment={data.get('alignment','?')}"
                    )
                confluence_section = "\nTECHNICAL CONFLUENCE:\n" + "\n".join(conf_lines) + "\n"

            # Whale intelligence context
            try:
                whale_report = await whale_intelligence.fetch_whale_report()
                trader_whale_section = whale_intelligence.build_llm_context_block(whale_report)
                trader_whale_section += whale_intelligence.build_exit_pressure_insights(whale_report)
            except Exception:
                trader_whale_section = ""

            trader_ids = [t["id"] for t in traders if t.get("is_enabled", True)]
            trader_names = {t["id"]: t.get("name", t["id"]) for t in traders}

            prompt = f"""You are James, the Portfolio Manager of an AI trading fund. You must allocate the fund's capital across competing trader AIs. Each trader manages their own pool of trading strategies.

TOTAL FUND CAPITAL: ${total_capital:,.0f}

MARKET CONDITIONS:
Trend: {market_condition.trend}
Volatility: {market_condition.volatility}
RSI: {(market_condition.rsi or 50):.1f}
Momentum: {market_condition.momentum}
Recommendation: {market_condition.recommendation}
{confluence_section}{trader_whale_section}

TRADER PERFORMANCE DATA:
{trader_context}

ALLOCATION RULES:
1. Allocate capital in strict proportion to risk-adjusted performance (win rate × P&L quality)
2. A trader with 2× the win rate of another should receive roughly 2× the capital
3. Minimum allocation: 10% per enabled trader (floor so all traders can operate)
4. Maximum allocation: 60% to any single trader (diversification cap)
5. New traders with zero trades and zero P&L may receive equal share, but if P&L already differs significantly between traders, reflect that in allocations even with few trades
6. In high-volatility markets, shift weight toward traders with highest win rates over P&L
7. In trending markets, shift weight toward traders with the highest P&L growth
8. Underperforming traders (win rate < 40% AND negative P&L) should be at the floor (10%)
9. Allocations must sum to exactly 100%
10. Do NOT equalise allocations — performance differences MUST result in allocation differences
11. If one trader has significantly more P&L than others, they MUST receive more capital — even a ${100:,.0f} advantage warrants a 5%+ reallocation

Return JSON only:
{{
  "allocation_pct": {{"{trader_ids[0] if trader_ids else 'trader_id'}": percentage, ...}},
  "reasoning": "brief explanation of your allocation logic"
}}"""

            response = await self.llm_service._call_llm(prompt)

            try:
                data = json.loads(response.content)
                alloc_pct: Dict[str, float] = data.get("allocation_pct", {})

                # Enforce min 10%, max 60%, only for enabled traders
                for tid in trader_ids:
                    pct = alloc_pct.get(tid, 100.0 / len(trader_ids))
                    alloc_pct[tid] = max(10.0, min(60.0, pct))

                # Normalise to 100
                total = sum(alloc_pct.get(tid, 0) for tid in trader_ids)
                if total > 0:
                    alloc_pct = {tid: alloc_pct.get(tid, 0) / total * 100 for tid in trader_ids}

                reasoning = data.get("reasoning", "LLM trader allocation")
                logger.info(f"PM trader allocation: {reasoning[:120]}")

                # Sanity-check: if LLM returned near-equal allocation despite meaningful
                # performance differences, override with deterministic scoring.
                alloc_values = list(alloc_pct.values())
                allocation_spread = max(alloc_values) - min(alloc_values)
                max_pnl_diff = max(
                    abs(a.get("total_pnl", 0) - b.get("total_pnl", 0))
                    for a in trader_performance
                    for b in trader_performance
                ) if len(trader_performance) > 1 else 0
                min_trades = min(
                    (p.get("total_trades", 0) for p in trader_performance), default=0
                )
                if allocation_spread < 5.0 and max_pnl_diff > 150 and min_trades >= 3:
                    logger.warning(
                        f"LLM returned near-equal allocation (spread {allocation_spread:.1f}%) "
                        f"despite P&L diff of ${max_pnl_diff:.0f} — using deterministic scoring"
                    )
                    return self._deterministic_trader_allocation(traders, trader_performance)

                return alloc_pct

            except (json.JSONDecodeError, ValueError):
                logger.warning("PM trader allocation parse failed — using deterministic scoring")
                return self._deterministic_trader_allocation(traders, trader_performance)

        except Exception as e:
            logger.error(f"PM trader allocation failed: {e}")
            return self._deterministic_trader_allocation(traders, trader_performance)

    @staticmethod
    def _deterministic_trader_allocation(
        traders: List[Dict],
        trader_performance: List[Dict],
    ) -> Dict[str, float]:
        """
        Performance-weighted allocation that always differentiates based on P&L and win rate.
        Used as the authoritative fallback when LLM allocation fails or returns unusable output.

        Scoring (per trader):
          base   = win_rate × 0.50
                 + pnl_score × 0.40   (pnl_score = clamp(pnl/500 + 0.5, 0→1))
                 + trade_bonus × 0.10  (trade_bonus = min(total_trades/50, 1))
        Bounds: 10% floor, 60% ceiling, normalised to 100%.
        """
        MIN_PCT = 10.0
        MAX_PCT = 60.0

        perf_map = {p.get("trader_id"): p for p in trader_performance}
        enabled = [t for t in traders if t.get("is_enabled", True)]
        if not enabled:
            return {}
        if len(enabled) == 1:
            return {enabled[0]["id"]: 100.0}

        scores: Dict[str, float] = {}
        for t in enabled:
            tid = t["id"]
            p = perf_map.get(tid, {})
            wr          = float(p.get("win_rate", 0.5) or 0.5)
            pnl         = float(p.get("total_pnl", 0) or 0)
            n_trades    = int(p.get("total_trades", 0) or 0)
            pnl_score   = min(max(pnl / 500 + 0.5, 0.0), 1.0)
            trade_bonus = min(n_trades / 50, 1.0)
            scores[tid] = max(wr * 0.50 + pnl_score * 0.40 + trade_bonus * 0.10, 0.05)

        total = sum(scores.values())
        raw   = {tid: (s / total) * 100 for tid, s in scores.items()}

        clamped = {tid: max(MIN_PCT, min(MAX_PCT, pct)) for tid, pct in raw.items()}
        total_c = sum(clamped.values())
        result  = {tid: pct / total_c * 100 for tid, pct in clamped.items()}

        parts = [f"{perf_map.get(tid,{}).get('trader_name', tid)}: "
                 f"P&L=${perf_map.get(tid,{}).get('total_pnl',0):+.0f} → {result[tid]:.1f}%"
                 for tid in result]
        logger.info("Deterministic trader allocation: " + ", ".join(parts))
        return result


    async def recommend_rebalancing(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict],
        current_allocations: Dict[str, float],
        target_allocations: Dict[str, float]
    ) -> RebalancingPlan:
        """
        Recommend portfolio rebalancing based on drift from targets
        """
        try:
            timestamp = datetime.utcnow()

            # Check if rebalancing is needed
            max_drift = max(
                abs(current_allocations.get(a['id'], 0) - target_allocations.get(a['id'], 0))
                for a in agents
            ) if agents else 0

            rebalancing_needed = max_drift > 5  # More than 5% drift

            positions_to_reduce = []
            positions_to_increase = []
            total_impact = 0.0

            for agent in agents:
                agent_id = agent['id']
                current = current_allocations.get(agent_id, 0)
                target = target_allocations.get(agent_id, 0)
                drift = target - current

                if drift < -5:  # More than 5% over-allocated
                    positions_to_reduce.append({
                        'agent_id': agent_id,
                        'current_allocation': current,
                        'target_allocation': target,
                        'reduction_needed': abs(drift)
                    })
                    total_impact += abs(drift)
                elif drift > 5:  # More than 5% under-allocated
                    positions_to_increase.append({
                        'agent_id': agent_id,
                        'current_allocation': current,
                        'target_allocation': target,
                        'increase_needed': drift
                    })
                    total_impact += drift

            # Determine timing
            if total_impact > 20:
                timing = "immediate"
            elif total_impact > 10:
                timing = "gradual"
            else:
                timing = "wait"

            reasoning = f"Drift: {max_drift:.1f}% | Need to rebalance {len(positions_to_reduce)} reduces + {len(positions_to_increase)} increases"

            return RebalancingPlan(
                timestamp=timestamp,
                rebalancing_needed=rebalancing_needed,
                positions_to_reduce=positions_to_reduce,
                positions_to_increase=positions_to_increase,
                total_rebalancing_impact=total_impact,
                timing_recommendation=timing,
                reasoning=reasoning
            )

        except Exception as e:
            logger.error(f"Rebalancing recommendation failed: {e}")
            return RebalancingPlan(
                timestamp=datetime.utcnow(),
                rebalancing_needed=False,
                positions_to_reduce=[],
                positions_to_increase=[],
                total_rebalancing_impact=0.0,
                timing_recommendation="wait",
                reasoning=f"Error: {str(e)}"
            )

    async def analyze_performance_attribution(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict]
    ) -> PerformanceAttribution:
        """
        Attribute fund returns to individual agents and strategies
        """
        try:
            timestamp = datetime.utcnow()
            metrics_by_id = {m['agent_id']: m for m in agent_metrics}

            agent_contributions = {}
            strategy_contributions = {}
            total_pnl = 0

            # Calculate agent contributions
            for agent in agents:
                agent_id = agent['id']
                metrics = metrics_by_id.get(agent_id, {})
                pnl = metrics.get('total_pnl', 0)
                agent_contributions[agent_id] = pnl
                total_pnl += pnl

                # Accumulate by strategy
                strategy = agent.get('strategy_type', 'unknown')
                strategy_contributions[strategy] = strategy_contributions.get(strategy, 0) + pnl

            # Find top and worst performers
            if agent_contributions:
                top_performer = max(agent_contributions, key=agent_contributions.get)
                worst_performer = min(agent_contributions, key=agent_contributions.get)
            else:
                top_performer = None
                worst_performer = None

            # Calculate concentration risk (% from top agent)
            if total_pnl != 0 and top_performer:
                concentration = (agent_contributions.get(top_performer, 0) / total_pnl) * 100
            else:
                concentration = 0.0

            # Average agent return
            num_agents = len(agents)
            avg_return = total_pnl / num_agents if num_agents > 0 else 0

            return PerformanceAttribution(
                timestamp=timestamp,
                total_pnl=total_pnl,
                agent_contributions=agent_contributions,
                strategy_contributions=strategy_contributions,
                top_performer=top_performer,
                worst_performer=worst_performer,
                average_agent_return=avg_return,
                concentration_risk=concentration
            )

        except Exception as e:
            logger.error(f"Performance attribution failed: {e}")
            return PerformanceAttribution(
                timestamp=datetime.utcnow(),
                total_pnl=0,
                agent_contributions={},
                strategy_contributions={},
                top_performer=None,
                worst_performer=None,
                average_agent_return=0,
                concentration_risk=0
            )

    def _build_allocation_context(
        self,
        agents: List[Dict],
        metrics_by_id: Dict,
        market_condition: MarketCondition,
        total_capital: float,
        confluence_scores: Optional[Dict[str, Dict]] = None,
    ) -> str:
        """Build context string for LLM allocation decision"""
        lines = []

        for agent in agents:
            agent_id = agent['id']
            metrics = metrics_by_id.get(agent_id, {})
            total_runs = metrics.get('total_runs', 0)
            pairs = agent.get('trading_pairs', agent.get('config', {}).get('trading_pairs', []))
            pairs_str = ", ".join(pairs) if pairs else "none configured"

            lines.append(f"\n{agent.get('name', agent_id)}:")
            lines.append(f"  Strategy: {agent.get('strategy_type', 'unknown')}")
            lines.append(f"  Symbols: {pairs_str}")
            if total_runs == 0:
                lines.append(f"  Status: NEW AGENT (no live trades yet — metrics from backtest)")
            lines.append(f"  Win Rate: {(metrics.get('win_rate', 0.5) or 0):.1%}")
            lines.append(f"  Total P&L: ${(metrics.get('total_pnl', 0) or 0):.2f}")
            lines.append(f"  Runs: {total_runs}")
            lines.append(f"  Current Allocation: {(agent.get('allocation_percentage', 10) or 0):.1f}%")

            # Add confluence info for all pairs
            if confluence_scores:
                for pair in pairs:
                    if pair in confluence_scores:
                        conf = confluence_scores[pair]
                        lines.append(
                            f"  {pair} Confluence: {conf.get('score', 0):.0%} "
                            f"({conf.get('alignment', '?')} alignment, "
                            f"{conf.get('patterns', 0)} patterns)"
                        )

        return "\n".join(lines)

    def _strategy_matches_market(self, strategy_type: str, market: MarketCondition) -> Dict:
        if strategy_type == "momentum":
            if market.trend in ["bullish", "bearish"]:
                return {"match": True, "reason": "Momentum suits trending market"}
            return {"match": False, "reason": "Momentum less effective in sideways market"}
        
        elif strategy_type == "mean_reversion":
            if market.rsi < 30 or market.rsi > 70:
                return {"match": True, "reason": "RSI indicates overbought/oversold"}
            elif market.volatility == "low":
                return {"match": True, "reason": "Low volatility suits mean reversion"}
            return {"match": False, "reason": "Market not ideal for mean reversion"}
        
        elif strategy_type == "breakout":
            if market.volatility == "high":
                return {"match": True, "reason": "High volatility good for breakouts"}
            return {"match": False, "reason": "Low volatility limits breakout opportunities"}

        elif strategy_type == "grid":
            if market.trend == "sideways":
                return {"match": True, "reason": "Grid strategy thrives in sideways/ranging market"}
            elif market.volatility == "low":
                return {"match": True, "reason": "Low volatility and tight range suits grid trading"}
            return {"match": False, "reason": "Grid strategy less effective in trending market"}
        
        return {"match": True, "reason": "Neutral strategy fit"}
    
    async def get_fund_allocation(
        self,
        agents: List[Dict],
        agent_metrics: List[Dict],
        total_capital: float = 10000
    ) -> Dict[str, float]:
        allocations = {}
        
        metrics_by_id = {m['agent_id']: m for m in agent_metrics}
        
        scores = []
        for agent in agents:
            agent_id = agent['id']
            metrics = metrics_by_id.get(agent_id, {})
            
            win_rate = metrics.get('win_rate', 0.5)
            total_runs = metrics.get('total_runs', 0)
            allocation_pct = agent.get('allocation_percentage', 10)
            
            # New agents default to 50 (neutral). Never multiply by 0.
            score = max(win_rate, 0.3) * 100
            if total_runs > 10:
                score *= 1.2
            elif total_runs < 3:
                score = max(score, 30)  # floor at 30 so new agents get allocation
            
            scores.append((agent_id, score, allocation_pct))
        
        total_score = sum(s[1] for s in scores) or 1
        
        for agent_id, score, allocation_pct in scores:
            weight = score / total_score
            allocations[agent_id] = total_capital * weight
        
        return allocations


fund_manager = FundManagerAgent()
