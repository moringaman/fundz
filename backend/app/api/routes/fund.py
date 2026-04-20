from fastapi import APIRouter, HTTPException
from typing import Any, List, Optional, Dict
from pydantic import BaseModel
from datetime import datetime, date

from app.services.research_analyst import research_analyst
from app.services.fund_manager import fund_manager
from app.services.risk_manager import risk_manager
from app.services.execution_coordinator import execution_coordinator
from app.services.cio_agent import cio_agent
from app.services.agent_scheduler import agent_scheduler
from app.services.llm import LLMRegistry
from app.services.team_chat import team_chat
from app.services.daily_report import daily_report_service
from app.services.firm_advisor import firm_advisor

router = APIRouter(prefix="/fund", tags=["fund"])


# ── Performance gate helper ────────────────────────────────────────────────
# Mirrors the _perf_mult / conf-floor logic in agent_scheduler.run_agent so
# the UI can display the current tier and multiplier without a separate query.
def _compute_perf_gate(tp: dict) -> dict:
    """Return perf_mult, perf_tier, and conf_floor_boost from a trader perf dict."""
    try:
        from app.api.routes.settings import get_trading_gates as _gtg
        _g = _gtg()
        _min_trades  = _g.perf_gate_min_trades
        _start_mult  = _g.new_trader_starting_mult
    except Exception:
        _min_trades  = 5
        _start_mult  = 0.75
    if not tp:
        return {"perf_mult": _start_mult, "perf_tier": "new", "conf_floor_boost": 0.0}
    wr       = float(tp.get("win_rate", 0.5) or 0.5)
    pnl      = float(tp.get("gross_pnl") if tp.get("gross_pnl") is not None else tp.get("total_pnl", 0) or 0)
    n_trades = int(tp.get("total_trades", 0) or 0)
    if n_trades < _min_trades:
        return {"perf_mult": _start_mult, "perf_tier": "new", "conf_floor_boost": 0.0}
    pnl_score = min(max(pnl / 500.0 + 0.5, 0.0), 1.0)
    raw_score = wr * 0.60 + pnl_score * 0.40
    perf_mult = round(0.60 + raw_score * 0.70, 3)
    if wr >= 0.60 and pnl >= 0:
        tier, boost = "strong",   0.00
    elif wr >= 0.50:
        tier, boost = "moderate", 0.07
    elif wr >= 0.40:
        tier, boost = "weak",     0.12
    else:
        tier, boost = "poor",     0.20
    return {"perf_mult": perf_mult, "perf_tier": tier, "conf_floor_boost": boost}


# ==================== Response Models ====================

class MarketOpportunityResponse(BaseModel):
    symbol: str
    opportunity_type: str
    confidence: float
    recommended_action: str
    entry_level: Optional[float] = None
    target_level: Optional[float] = None
    stop_level: Optional[float] = None
    reasoning: str


class MarketRegimeResponse(BaseModel):
    regime: str
    regime_confidence: float
    sentiment: str
    correlation_status: str
    volatility_regime: str
    macro_context: str


class AnalystReportResponse(BaseModel):
    timestamp: datetime
    market_regime: MarketRegimeResponse
    opportunities: List[MarketOpportunityResponse]
    symbols_analyzed: List[str]
    sector_leadership: Dict[str, str]
    top_opportunity: Optional[MarketOpportunityResponse] = None
    top_risk: str
    analyst_recommendation: str
    reasoning: str


class AllocationDecisionResponse(BaseModel):
    timestamp: datetime
    allocation: Dict[str, float]
    allocation_pct: Dict[str, float]
    reasoning: str
    expected_return_pct: float


class RiskAssessmentResponse(BaseModel):
    timestamp: datetime
    risk_level: str
    daily_pnl: float
    portfolio_exposure: float
    exposure_pct_of_capital: float
    largest_position_symbol: Optional[str] = None
    largest_position_size: float
    concentration_risk: str
    recommendations: List[str]
    reasoning: str


class StrategicRecommendationResponse(BaseModel):
    recommendation: str
    target: str
    confidence: float
    rationale: str
    expected_impact: str


class AgentLeaderboardEntryResponse(BaseModel):
    agent_id: str
    agent_name: str
    total_pnl: float
    win_rate: float
    total_runs: int
    contribution_pct: float
    rank: int


class FundHealthReportResponse(BaseModel):
    timestamp: datetime
    period: str
    fund_performance: Dict[str, Any]
    agent_leaderboard: List[AgentLeaderboardEntryResponse]
    strategy_performance: Dict[str, Any]
    risk_metrics: Dict[str, Any]
    strategic_recommendations: List[StrategicRecommendationResponse]
    executive_summary: str
    cio_sentiment: str
    cio_reasoning: str


class ExecutionPlanResponse(BaseModel):
    timestamp: datetime
    pending_orders_count: int
    execution_sequence: List[str]
    aggregate_slippage_estimate: float
    recommended_action: str
    reasoning: str


class AgentDecisionResponse(BaseModel):
    agent_id: str
    decision_type: str
    decision_data: Dict
    reasoning: str
    timestamp: datetime


class TeamMemberResponse(BaseModel):
    role: str
    name: str
    title: str
    avatar: str
    bio: str
    model: str


# ==================== Team Roster Endpoints ====================

@router.get("/team-roster", response_model=List[TeamMemberResponse])
async def get_team_roster():
    """
    Get the fund management team roster with member info
    """
    try:
        roles = ['research_analyst', 'technical_analyst', 'portfolio_manager', 'risk_manager', 'execution_coordinator', 'cio_agent']
        roster = []

        for role in roles:
            agent_info = LLMRegistry.get_agent_info(role)
            roster.append(TeamMemberResponse(
                role=role,
                name=agent_info.get('name'),
                title=agent_info.get('title'),
                avatar=agent_info.get('avatar'),
                bio=agent_info.get('bio'),
                model=agent_info.get('model')
            ))

        return roster
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Team roster fetch failed: {str(e)}")


# ==================== Research Analyst Endpoints ====================

@router.get("/market-analysis", response_model=AnalystReportResponse)
async def get_market_analysis(symbols: Optional[List[str]] = None):
    """
    Get research analyst's market analysis. Serves the scheduler's cached report
    (updated every 5 min). Falls back to a fresh analysis only on first load.
    """
    try:
        cached = agent_scheduler.get_current_analyst_report()
        if cached:
            report = cached
        else:
            report = await research_analyst.analyze_markets(symbols=symbols)

        return AnalystReportResponse(
            timestamp=report.timestamp,
            market_regime=MarketRegimeResponse(
                regime=report.market_regime.regime,
                regime_confidence=report.market_regime.regime_confidence,
                sentiment=report.market_regime.sentiment,
                correlation_status=report.market_regime.correlation_status,
                volatility_regime=report.market_regime.volatility_regime,
                macro_context=report.market_regime.macro_context
            ),
            opportunities=[
                MarketOpportunityResponse(
                    symbol=opp.symbol,
                    opportunity_type=opp.opportunity_type,
                    confidence=opp.confidence,
                    recommended_action=opp.recommended_action,
                    entry_level=opp.entry_level,
                    target_level=opp.target_level,
                    stop_level=opp.stop_level,
                    reasoning=opp.reasoning
                )
                for opp in report.opportunities
            ],
            symbols_analyzed=report.symbols_analyzed,
            sector_leadership=report.sector_leadership,
            top_opportunity=MarketOpportunityResponse(
                symbol=report.top_opportunity.symbol,
                opportunity_type=report.top_opportunity.opportunity_type,
                confidence=report.top_opportunity.confidence,
                recommended_action=report.top_opportunity.recommended_action,
                entry_level=report.top_opportunity.entry_level,
                target_level=report.top_opportunity.target_level,
                stop_level=report.top_opportunity.stop_level,
                reasoning=report.top_opportunity.reasoning
            ) if report.top_opportunity else None,
            top_risk=report.top_risk,
            analyst_recommendation=report.analyst_recommendation,
            reasoning=report.reasoning
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Market analysis failed: {str(e)}")


# ==================== Technical Analyst Endpoints ====================

class PriceLevelResponse(BaseModel):
    support: List[float]
    resistance: List[float]
    pivot_points: Dict[str, float]
    fibonacci_retracements: Dict[str, float]
    fibonacci_extensions: Dict[str, float]


class PatternSignalResponse(BaseModel):
    pattern_type: str
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    reasoning: str


class MultiTimeframeResponse(BaseModel):
    timeframe_1h: Dict[str, str]
    timeframe_4h: Dict[str, str]
    timeframe_1d: Dict[str, str]
    alignment: str
    trend_confirmation: bool
    confluence_score: float
    tf_primary: str = "1h"
    tf_mid: str = "4h"
    tf_high: str = "1d"


class TechnicalAnalysisResponse(BaseModel):
    timestamp: datetime
    symbol: str
    current_price: float
    price_levels: PriceLevelResponse
    patterns: List[PatternSignalResponse]
    multi_timeframe: Optional[MultiTimeframeResponse]
    overall_signal: str
    confidence: float
    key_observations: List[str]


@router.get("/technical-analysis", response_model=TechnicalAnalysisResponse)
async def get_technical_analysis(symbol: str = "BTCUSDT", timeframe: str = "1h"):
    """
    Get technical analyst's chart analysis with price levels, patterns, and multi-timeframe.
    Pass ?timeframe=4h to get analysis scaled to the strategy's timeframe.
    """
    try:
        from app.services.technical_analyst import technical_analyst

        report = await technical_analyst.analyze(symbol, timeframe=timeframe)

        return _report_to_response(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Technical analysis failed: {str(e)}")


def _report_to_response(report) -> TechnicalAnalysisResponse:
    """Convert a TechnicalAnalystReport to API response."""
    return TechnicalAnalysisResponse(
        timestamp=report.timestamp,
        symbol=report.symbol,
        current_price=report.current_price,
        price_levels=PriceLevelResponse(
            support=report.price_levels.support,
            resistance=report.price_levels.resistance,
            pivot_points=report.price_levels.pivot_points,
            fibonacci_retracements=report.price_levels.fibonacci_retracements,
            fibonacci_extensions=report.price_levels.fibonacci_extensions
        ),
        patterns=[
            PatternSignalResponse(
                pattern_type=p.pattern_type,
                direction=p.direction,
                confidence=p.confidence,
                entry_price=p.entry_price,
                stop_loss=p.stop_loss,
                take_profit_1=p.take_profit_1,
                take_profit_2=p.take_profit_2,
                risk_reward=p.risk_reward,
                reasoning=p.reasoning
            )
            for p in report.patterns
        ],
        multi_timeframe=MultiTimeframeResponse(
            timeframe_1h=report.multi_timeframe.timeframe_1h,
            timeframe_4h=report.multi_timeframe.timeframe_4h,
            timeframe_1d=report.multi_timeframe.timeframe_1d,
            alignment=report.multi_timeframe.alignment,
            trend_confirmation=report.multi_timeframe.trend_confirmation,
            confluence_score=report.multi_timeframe.confluence_score,
            tf_primary=report.multi_timeframe.tf_primary,
            tf_mid=report.multi_timeframe.tf_mid,
            tf_high=report.multi_timeframe.tf_high,
        ) if report.multi_timeframe else None,
        overall_signal=report.overall_signal,
        confidence=report.confidence,
        key_observations=report.key_observations
    )


@router.get("/technical-analysis/batch", response_model=List[TechnicalAnalysisResponse])
async def get_technical_analysis_batch():
    """
    Get technical analysis for all unique trading pairs across active agents.
    Falls back to BTCUSDT if no agents have trading pairs configured.
    """
    import asyncio
    from app.services.technical_analyst import technical_analyst
    from app.api.routes.agents import get_agents_from_db

    try:
        agents = await get_agents_from_db()

        # Group symbols by the most common agent timeframe trading that pair
        from collections import Counter, defaultdict
        symbol_tfs: dict = defaultdict(list)
        for agent in agents:
            pairs = agent.get("trading_pairs") or ["BTCUSDT"]
            if isinstance(pairs, str):
                pairs = [pairs]
            tf = agent.get("timeframe", "1h")
            for pair in pairs:
                symbol_tfs[pair].append(tf)

        if not symbol_tfs:
            symbol_tfs["BTCUSDT"] = ["1h"]

        async def analyze_safe(sym: str, tf: str):
            try:
                return await technical_analyst.analyze(sym, timeframe=tf)
            except Exception:
                return None

        tasks = [
            analyze_safe(sym, Counter(tfs).most_common(1)[0][0])
            for sym, tfs in symbol_tfs.items()
        ]
        reports = await asyncio.gather(*tasks)
        results = [_report_to_response(r) for r in reports if r is not None]
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch technical analysis failed: {str(e)}")


# ==================== Portfolio Manager Endpoints ====================

@router.get("/allocation-decision", response_model=AllocationDecisionResponse)
async def get_allocation_decision(total_capital: float = 10000):
    """
    Get portfolio manager's capital allocation recommendation.
    Returns the scheduler's live allocation if available (computed every 5 min),
    otherwise computes a performance-weighted allocation without LLM dependency.
    """
    try:
        from app.api.routes.agents import get_agents_from_db

        agents = await get_agents_from_db()
        if not agents:
            raise HTTPException(status_code=404, detail="No agents found")

        # Prefer the live scheduler allocation (already computed by FM with LLM)
        live_alloc = agent_scheduler.get_current_allocation()
        if live_alloc and len(live_alloc) > 0:
            allocation_dollars = {
                aid: total_capital * pct / 100
                for aid, pct in live_alloc.items()
            }
            return AllocationDecisionResponse(
                timestamp=datetime.utcnow(),
                allocation=allocation_dollars,
                allocation_pct=live_alloc,
                reasoning=agent_scheduler._current_allocation_reasoning or "Live allocation from Portfolio Manager",
                expected_return_pct=0.0
            )

        # Fallback: performance-weighted allocation (no LLM needed)
        metrics_data = await agent_scheduler.get_all_metrics()
        metrics_by_id = {m.agent_id: m for m in metrics_data}

        scores = {}
        for agent in agents:
            aid = agent['id']
            m = metrics_by_id.get(aid)
            if m and m.total_runs > 0:
                # Score = win_rate * 0.6 + pnl_factor * 0.4, floored at 0.1
                win_rate = m.win_rate or 0
                pnl_factor = min(max((m.total_pnl or 0) / 1000 + 0.5, 0), 1)
                scores[aid] = max(win_rate * 0.6 + pnl_factor * 0.4, 0.1)
            else:
                scores[aid] = 0.5  # New agents get neutral score

        total_score = sum(scores.values()) or 1
        alloc_pct = {}
        for aid, score in scores.items():
            raw_pct = (score / total_score) * 100
            alloc_pct[aid] = max(raw_pct, 5.0)  # Floor: 5%

        # Re-normalize
        total_pct = sum(alloc_pct.values())
        if total_pct > 0:
            alloc_pct = {aid: pct / total_pct * 100 for aid, pct in alloc_pct.items()}

        allocation_dollars = {aid: total_capital * pct / 100 for aid, pct in alloc_pct.items()}

        return AllocationDecisionResponse(
            timestamp=datetime.utcnow(),
            allocation=allocation_dollars,
            allocation_pct=alloc_pct,
            reasoning="Performance-weighted allocation (Portfolio Manager not yet initialized)",
            expected_return_pct=0.0
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Allocation decision failed: {str(e)}")


@router.get("/performance-attribution")
async def get_performance_attribution():
    """
    Get attribution of fund returns by agent and strategy
    """
    try:
        from app.api.routes.agents import get_agents_from_db

        agents = await get_agents_from_db()
        metrics_data = await agent_scheduler.get_all_metrics()

        # Create agent_id to name mapping
        agent_name_map = {agent['id']: agent['name'] for agent in agents}

        agent_metrics = [
            {
                'agent_id': m.agent_id,
                'agent_name': agent_name_map.get(m.agent_id, m.agent_id),
                'total_pnl': m.total_pnl,
                'win_rate': m.win_rate,
                'total_runs': m.total_runs,
                'strategy_type': 'unknown'
            }
            for m in metrics_data
        ]

        attribution = await fund_manager.analyze_performance_attribution(agents, agent_metrics)

        return {
            "timestamp": attribution.timestamp,
            "total_pnl": attribution.total_pnl,
            "agent_contributions": attribution.agent_contributions,
            "strategy_contributions": attribution.strategy_contributions,
            "top_performer": attribution.top_performer,
            "worst_performer": attribution.worst_performer,
            "average_agent_return": attribution.average_agent_return,
            "concentration_risk": attribution.concentration_risk
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Performance attribution failed: {str(e)}")


# ==================== Risk Manager Endpoints ====================

@router.get("/risk-assessment", response_model=RiskAssessmentResponse)
async def get_risk_assessment():
    """
    Get current portfolio risk assessment.
    Serves the scheduler's cached assessment (computed every 5 min with real positions).
    Falls back to a live computation if no cached data exists yet.
    """
    try:
        # Prefer the scheduler's cached assessment (has real position data)
        cached = agent_scheduler.get_current_risk_assessment()
        if cached:
            return RiskAssessmentResponse(
                timestamp=cached.timestamp,
                risk_level=cached.risk_level,
                daily_pnl=cached.daily_pnl,
                portfolio_exposure=cached.portfolio_exposure,
                exposure_pct_of_capital=cached.exposure_pct_of_capital,
                largest_position_symbol=cached.largest_position_symbol,
                largest_position_size=cached.largest_position_size,
                concentration_risk=cached.concentration_risk,
                recommendations=cached.recommendations or [],
                reasoning=cached.reasoning or "",
            )

        # Fallback: compute live with real positions and real capital
        from app.services.paper_trading import paper_trading
        positions_live = await paper_trading.get_positions_live()
        current_positions = [
            {
                "symbol": p["symbol"],
                "quantity": p["quantity"],
                "entry_price": p["entry_price"],
                "current_price": p.get("current_price", p["entry_price"]),
                "unrealized_pnl": p.get("unrealized_pnl", 0.0),
            }
            for p in positions_live
        ]

        # Compute real total capital using the scheduler helper (live or paper)
        try:
            positions_value = sum(
                p.get("quantity", 0) * p.get("current_price", p.get("entry_price", 0))
                for p in positions_live
            )
            total_capital = await agent_scheduler._get_total_capital(positions_value=positions_value)
        except Exception:
            total_capital = agent_scheduler._total_capital or 50_000.0

        # Compute daily P&L from DB (FIFO-matched sells today)
        try:
            daily_pnl = await agent_scheduler._compute_daily_pnl()
        except Exception:
            daily_pnl = risk_manager.get_daily_pnl()

        assessment = await risk_manager.generate_risk_assessment(
            current_positions=current_positions,
            daily_pnl=daily_pnl,
            total_capital=total_capital,
        )

        return RiskAssessmentResponse(
            timestamp=assessment.timestamp,
            risk_level=assessment.risk_level,
            daily_pnl=assessment.daily_pnl,
            portfolio_exposure=assessment.portfolio_exposure,
            exposure_pct_of_capital=assessment.exposure_pct_of_capital,
            largest_position_symbol=assessment.largest_position_symbol,
            largest_position_size=assessment.largest_position_size,
            concentration_risk=assessment.concentration_risk,
            recommendations=assessment.recommendations or [],
            reasoning=assessment.reasoning or "",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk assessment failed: {str(e)}")


# ==================== Execution Coordinator Endpoints ====================

@router.get("/execution-plan", response_model=ExecutionPlanResponse)
async def get_execution_plan():
    """
    Get coordinator's optimized execution plan for pending orders
    """
    try:
        plan = await execution_coordinator.optimize_execution_plan([])

        return ExecutionPlanResponse(
            timestamp=plan.timestamp,
            pending_orders_count=plan.pending_orders_count,
            execution_sequence=plan.execution_sequence,
            aggregate_slippage_estimate=plan.aggregate_slippage_estimate,
            recommended_action=plan.recommended_action,
            reasoning=plan.reasoning
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution plan failed: {str(e)}")


# ==================== CIO Agent Endpoints ====================

@router.get("/cio-report", response_model=FundHealthReportResponse)
async def get_cio_report(period: str = "daily"):
    """
    Get CIO agent's comprehensive fund health report. Serves the scheduler's cached
    report (updated every 5 min). Falls back to a fresh report only on first load.
    """
    try:
        cached = agent_scheduler.get_current_cio_report()
        if cached:
            report = cached
        else:
            from app.api.routes.agents import get_agents_from_db

            agents = await get_agents_from_db()
            metrics_data = await agent_scheduler.get_all_metrics()
            agent_name_map = {agent['id']: agent['name'] for agent in agents}
            agent_metrics = [
                {
                    'agent_id': m.agent_id,
                    'agent_name': agent_name_map.get(m.agent_id, m.agent_id),
                    'total_runs': m.total_runs,
                    'successful_runs': m.successful_runs,
                    'total_pnl': m.total_pnl,
                    'win_rate': m.win_rate,
                    'strategy_type': 'unknown'
                }
                for m in metrics_data
            ]
            report = await cio_agent.generate_fund_report(
                agent_metrics=agent_metrics,
                period=period
            )

        return FundHealthReportResponse(
            timestamp=report.timestamp,
            period=report.period,
            fund_performance=report.fund_performance,
            agent_leaderboard=[
                AgentLeaderboardEntryResponse(
                    agent_id=entry.agent_id,
                    agent_name=entry.agent_name,
                    total_pnl=entry.total_pnl,
                    win_rate=entry.win_rate,
                    total_runs=entry.total_runs,
                    contribution_pct=entry.contribution_pct,
                    rank=entry.rank
                )
                for entry in report.agent_leaderboard
            ],
            strategy_performance=report.strategy_performance,
            risk_metrics=report.risk_metrics,
            strategic_recommendations=[
                StrategicRecommendationResponse(
                    recommendation=rec.recommendation,
                    target=rec.target,
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    expected_impact=rec.expected_impact
                )
                for rec in report.strategic_recommendations
            ],
            executive_summary=report.executive_summary,
            cio_sentiment=report.cio_sentiment,
            cio_reasoning=report.cio_reasoning
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CIO report failed: {str(e)}")


@router.post("/strategy-review")
async def post_strategy_review(question: str, context: Optional[Dict] = None):
    """
    Ask CIO agent a strategic question and get recommendation
    """
    try:
        from app.api.routes.agents import get_agents_from_db

        agents = await get_agents_from_db()
        metrics_data = await agent_scheduler.get_all_metrics()

        # Create agent_id to name mapping
        agent_name_map = {agent['id']: agent['name'] for agent in agents}

        agent_metrics = [
            {
                'agent_id': m.agent_id,
                'agent_name': agent_name_map.get(m.agent_id, m.agent_id),
                'total_pnl': m.total_pnl,
                'win_rate': m.win_rate
            }
            for m in metrics_data
        ]

        # Generate report as context for strategic question
        report = await cio_agent.generate_fund_report(agent_metrics=agent_metrics)

        return {
            "question": question,
            "timestamp": datetime.utcnow(),
            "cio_sentiment": report.cio_sentiment,
            "recommendations": [
                {
                    "recommendation": rec.recommendation,
                    "confidence": rec.confidence,
                    "rationale": rec.rationale
                }
                for rec in report.strategic_recommendations[:3]
            ],
            "reasoning": report.cio_reasoning
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Strategy review failed: {str(e)}")


# ==================== Team Decision Visibility ====================

@router.get("/conversations")
async def get_conversations(limit: int = 50, since: Optional[str] = None):
    """
    Get recent team chat messages (agent-to-agent conversations).
    Optionally filter to messages after a given ISO timestamp.
    """
    try:
        return team_chat.get_messages(limit=limit, since=since)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch conversations: {str(e)}")


# ==================== Daily Reports ====================

@router.get("/daily-report")
async def get_daily_report(report_date: Optional[str] = None):
    """
    Get the daily report for a specific date (YYYY-MM-DD).
    Defaults to today. Returns null if no report exists yet.
    """
    try:
        target = date.fromisoformat(report_date) if report_date else date.today()
        report = await daily_report_service.get_report(target)
        return report or {"message": f"No report available for {target.isoformat()}"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch daily report: {str(e)}")


@router.get("/daily-reports")
async def get_daily_reports(limit: int = 30):
    """Get the most recent daily reports."""
    try:
        return await daily_report_service.get_reports(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch daily reports: {str(e)}")


@router.post("/daily-report/generate")
async def generate_daily_report(report_date: Optional[str] = None, force: bool = False):
    """Manually trigger daily report generation."""
    try:
        target = date.fromisoformat(report_date) if report_date else date.today()
        report = await daily_report_service.generate_daily_report(report_date=target, force=force)
        return report
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate daily report: {str(e)}")


@router.get("/team-decisions", response_model=List[AgentDecisionResponse])
async def get_team_decisions(limit: int = 50):
    """
    Get recent decisions made by all team agents
    """
    try:
        # In a production system, this would query the database
        # For now, return empty list as placeholder
        decisions = []
        return decisions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch team decisions: {str(e)}")


@router.get("/team-status")
async def get_team_status():
    """
    Get overall team and fund status summary
    """
    try:
        risk_assessment = await risk_manager.generate_risk_assessment()
        analyst_report = await research_analyst.analyze_markets()
        cio_report = await cio_agent.generate_fund_report()

        return {
            "timestamp": datetime.utcnow(),
            "risk_level": risk_assessment.risk_level,
            "market_sentiment": analyst_report.market_regime.sentiment,
            "cio_sentiment": cio_report.cio_sentiment,
            "fund_pnl": cio_report.fund_performance.get("total_pnl", 0),
            "agents_active": len(agent_scheduler._enabled_agents),
            "top_agent": (
                cio_report.agent_leaderboard[0].agent_name
                if cio_report.agent_leaderboard else "None"
            )
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


@router.get("/strategy-actions")
async def get_strategy_actions(limit: int = 20):
    """Retrieve history of automated strategy actions (create/disable/enable/adjust)."""
    try:
        from sqlalchemy import select, desc
        from app.database import get_async_session
        from app.models import StrategyAction
        async with get_async_session() as session:
            query = select(StrategyAction).order_by(desc(StrategyAction.created_at)).limit(limit)
            result = await session.execute(query)
            records = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "action": r.action,
                    "target_agent_id": r.target_agent_id,
                    "target_agent_name": r.target_agent_name,
                    "strategy_type": r.strategy_type,
                    "params": r.params,
                    "rationale": r.rationale,
                    "initiated_by": r.initiated_by,
                    "confluence_score": r.confluence_score,
                    "backtest_net_pnl": r.backtest_net_pnl,
                    "executed": r.executed,
                    "execution_result": r.execution_result,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in records
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch strategy actions: {str(e)}")


# ==================== Firm Advisor Chatbot ====================

class AdvisorQuestionRequest(BaseModel):
    message: str

class AdvisorResponse(BaseModel):
    response: str
    timestamp: str
    context_summary: Dict[str, Any]

class AdvisorMessage(BaseModel):
    role: str
    content: str
    timestamp: str


@router.post("/advisor/ask", response_model=AdvisorResponse)
async def ask_firm_advisor(req: AdvisorQuestionRequest):
    """Ask the fund management team a question about strategy or market conditions."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        result = await firm_advisor.ask(req.message.strip())
        return AdvisorResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Advisor failed: {str(e)}")


@router.get("/advisor/history", response_model=List[AdvisorMessage])
async def get_advisor_history(limit: int = 50):
    """Get conversation history with the firm advisor."""
    history = firm_advisor.get_history(limit)
    return [AdvisorMessage(**msg) for msg in history]


@router.post("/advisor/clear")
async def clear_advisor_history():
    """Clear the advisor conversation history."""
    firm_advisor.clear_history()
    return {"status": "cleared"}


# ==================== Trade Retrospective ====================

@router.get("/trade-retrospective")
async def get_trade_retrospective():
    """Get the latest trade retrospective analysis — patterns, insights, adjustments."""
    from app.services.trade_retrospective import trade_retrospective

    result = trade_retrospective._cached_result
    if result:
        return result

    # If no cached result, run a fresh analysis
    from app.services.agent_scheduler import agent_scheduler
    agents_list = []
    try:
        from app.database import get_async_session
        from app.models import Agent as DBAgent
        from sqlalchemy import select
        async with get_async_session() as db:
            rows = await db.execute(select(DBAgent))
            for a in rows.scalars().all():
                agents_list.append({
                    "id": str(a.id),
                    "name": a.name,
                    "strategy_type": a.strategy_type,
                    "is_enabled": a.is_enabled,
                })
    except Exception:
        pass

    fresh = await trade_retrospective.analyze_recent_trades(agents_list)
    return fresh or {"trade_analyses": [], "agent_insights": {}, "parameter_adjustments": [], "summary": "No trades to analyse yet."}


# ==================== Trader Endpoints ====================

@router.get("/traders/leaderboard")
async def get_trader_leaderboard():
    """Get trader leaderboard with allocation and performance data.

    P&L and win rate are sourced from actual closed DB trades (net of fees),
    not the in-memory agent metrics buffer which is pruned and mixes bootstrap
    backtest data with live trade results.
    """
    from sqlalchemy import select as sa_select
    from app.models import Agent as DBAgent
    from app.database import get_async_session
    from app.services.trader_service import trader_service
    from app.services.paper_trading import paper_trading

    traders = agent_scheduler.get_traders()
    allocations = agent_scheduler.get_trader_allocations()
    total_capital = agent_scheduler._total_capital or 0.0

    # Pull per-agent performance from DB (authoritative, net-of-fees, all agents)
    db_perf = await paper_trading.get_agent_performance_from_db()

    # Load ALL DB agents for each trader (not just enabled ones in memory)
    async with get_async_session() as db:
        all_agents_result = await db.execute(sa_select(DBAgent))
        all_db_agents = all_agents_result.scalars().all()

    # Build trader_id → list of agent dicts
    trader_agents_map: dict = {}
    for a in all_db_agents:
        tid = a.trader_id
        if tid not in trader_agents_map:
            trader_agents_map[tid] = []
        trader_agents_map[tid].append({"id": a.id, "name": a.name, "is_enabled": a.is_enabled})

    leaderboard = []
    for t in traders:
        t_agents = trader_agents_map.get(t["id"], [])

        # Aggregate DB-sourced metrics across all agents for this trader
        total_pnl = 0.0
        total_trades = 0
        winning_trades = 0
        for a in t_agents:
            ap = db_perf.get(a["id"])
            if ap:
                total_pnl += ap["net_pnl"]
                total_trades += ap["total_trades"]
                winning_trades += int(ap["total_trades"] * (ap["win_rate"] or 0))

        win_rate = winning_trades / total_trades if total_trades > 0 else None

        # Fall back to in-memory metrics only when there are no DB trades yet
        # (e.g., brand-new agents that haven't closed a trade)
        if total_trades == 0:
            agent_metrics_list = []
            for a in t_agents:
                m = agent_scheduler._agent_metrics.get(a["id"])
                if m:
                    agent_metrics_list.append({
                        "agent_id": a["id"],
                        "total_pnl": m.total_pnl,
                        "win_rate": m.win_rate,
                        "total_runs": m.actual_trades,  # use actual trades, not runs
                    })
            perf = trader_service.get_trader_performance(t, t_agents, agent_metrics_list)
            total_pnl = perf.total_pnl
            win_rate = perf.win_rate
            total_trades = perf.total_trades

        alloc_pct = allocations.get(t["id"], t.get("allocation_pct", 33.3))

        # Phase 9.1 — attach live consistency & Sharpe data
        consistency_flag = agent_scheduler._consistency_flags.get(t["id"], "INSUFFICIENT_DATA")
        from app.services.consistency_scorer import compute_consistency
        agent_ids = [a["id"] for a in t_agents]
        is_paper = not bool(t.get("live_mode"))
        try:
            cr = await compute_consistency(t["id"], agent_ids, is_paper=is_paper)
            consistency_flag = cr.consistency_flag
            consistency_score = cr.consistency_score
            sharpe = cr.sharpe
            sharpe_tier = cr.sharpe_tier
        except Exception:
            consistency_score = 0.5
            sharpe = 0.0
            sharpe_tier = "medium"

        leaderboard.append({
            "id": t["id"],
            "name": t["name"],
            "llm_model": t.get("llm_model", ""),
            "allocation_pct": alloc_pct,
            "allocation_dollars": total_capital * alloc_pct / 100,
            "total_capital": total_capital,
            "is_enabled": t.get("is_enabled", True),
            "config": t.get("config", {}),
            "total_pnl": round(total_pnl, 2),
            "win_rate": win_rate,
            "total_trades": total_trades,
            "agent_count": len(t_agents),
            "consistency_flag": consistency_flag,
            "consistency_score": consistency_score,
            "sharpe": sharpe,
            "sharpe_tier": sharpe_tier,
            # Phase 9.2 — drawdown fields
            "drawdown_warning_level": t.get("drawdown_warning_level"),
            "lifetime_drawdown_pct": t.get("lifetime_drawdown_pct"),
            "successor_of": t.get("successor_of"),
            # Performance gate fields (full-pool sizing model)
            # Computed from live _current_trader_perf; neutral defaults until ≥5 trades.
            **_compute_perf_gate(agent_scheduler._current_trader_perf.get(t["id"], {})),
        })

    leaderboard.sort(key=lambda x: x["total_pnl"], reverse=True)
    return leaderboard


@router.get("/trader-allocation")
async def get_trader_allocation():
    """
    James's (Portfolio Manager) current capital allocation across traders.
    Returns trader_id → pct, plus trader name and reasoning for display.
    """
    traders = agent_scheduler.get_traders()
    allocations = agent_scheduler.get_trader_allocations()
    reasoning = agent_scheduler._current_allocation_reasoning or ""

    result = []
    for t in traders:
        if not t.get("is_enabled", True):
            continue
        result.append({
            "id": t["id"],
            "name": t.get("name", t["id"]),
            "avatar": t.get("config", {}).get("avatar", "🤖"),
            "llm_model": t.get("llm_model", ""),
            "allocation_pct": allocations.get(t["id"], t.get("allocation_pct", 33.3)),
        })

    # Sort by allocation descending
    result.sort(key=lambda x: x["allocation_pct"], reverse=True)
    return {"traders": result, "reasoning": reasoning}
