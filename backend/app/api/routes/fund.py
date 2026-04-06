from fastapi import APIRouter, HTTPException
from typing import Any, List, Optional, Dict
from pydantic import BaseModel
from datetime import datetime

from app.services.research_analyst import research_analyst
from app.services.fund_manager import fund_manager
from app.services.risk_manager import risk_manager
from app.services.execution_coordinator import execution_coordinator
from app.services.cio_agent import cio_agent
from app.services.agent_scheduler import agent_scheduler
from app.services.llm import LLMRegistry

router = APIRouter(prefix="/fund", tags=["fund"])


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
        roles = ['research_analyst', 'portfolio_manager', 'risk_manager', 'execution_coordinator', 'cio_agent']
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
    Get research analyst's market analysis and opportunity identification
    """
    try:
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


# ==================== Portfolio Manager Endpoints ====================

@router.get("/allocation-decision", response_model=AllocationDecisionResponse)
async def get_allocation_decision(total_capital: float = 10000):
    """
    Get portfolio manager's capital allocation recommendation
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
                'total_runs': m.total_runs,
                'successful_runs': m.successful_runs,
                'total_pnl': m.total_pnl,
                'win_rate': m.win_rate,
                'last_run': m.last_run.isoformat() if m.last_run else None,
                'strategy_type': 'unknown'
            }
            for m in metrics_data
        ]

        market = await fund_manager.analyze_market()
        decision = await fund_manager.make_allocation_decision(agents, agent_metrics, market, total_capital)

        return AllocationDecisionResponse(
            timestamp=decision.timestamp,
            allocation=decision.allocation,
            allocation_pct=decision.allocation_pct,
            reasoning=decision.reasoning,
            expected_return_pct=decision.expected_return_pct
        )
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
async def get_risk_assessment(total_capital: float = 10000):
    """
    Get current portfolio risk assessment
    """
    try:
        daily_pnl = risk_manager.get_daily_pnl()

        assessment = await risk_manager.generate_risk_assessment(
            current_positions=[],
            daily_pnl=daily_pnl,
            total_capital=total_capital
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
            recommendations=assessment.recommendations,
            reasoning=assessment.reasoning
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
    Get CIO agent's comprehensive fund health report and strategic recommendations
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
