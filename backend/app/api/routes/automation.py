from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel
from app.services.agent_scheduler import agent_scheduler
from app.services.fund_manager import fund_manager

router = APIRouter(prefix="/automation", tags=["automation"])

_use_paper_trading = True


def get_use_paper() -> bool:
    return _use_paper_trading


class RunAgentRequest(BaseModel):
    agent_id: str
    name: str
    strategy_type: str
    trading_pairs: List[str]
    allocation_percentage: float
    max_position_size: float
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0


class RunAgentResponse(BaseModel):
    agent_id: str
    timestamp: str
    symbol: str
    signal: str
    confidence: float
    price: float
    executed: bool
    pnl: Optional[float] = None
    error: Optional[str] = None


class AgentMetricsResponse(BaseModel):
    agent_id: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    total_pnl: float
    buy_signals: int
    sell_signals: int
    hold_signals: int
    last_run: Optional[str]
    win_rate: float
    avg_pnl: float


class MarketConditionResponse(BaseModel):
    trend: str
    volatility: str
    rsi: float
    momentum: str
    recommendation: str


class AgentRecommendationResponse(BaseModel):
    agent_id: str
    agent_name: str
    action: str
    reason: str
    confidence: float


class FundAllocationResponse(BaseModel):
    agent_id: str
    allocation: float


@router.get("/status")
async def get_automation_status():
    return {
        "scheduler_running": agent_scheduler.is_running,
        "total_runs": len(agent_scheduler._agent_runs),
        "tracked_agents": len(agent_scheduler._agent_metrics),
    }


@router.post("/start")
async def start_automation():
    await agent_scheduler.start()
    return {"status": "started"}


@router.post("/stop")
async def stop_automation():
    await agent_scheduler.stop()
    return {"status": "stopped"}


@router.post("/run-agent", response_model=RunAgentResponse)
async def run_agent(request: RunAgentRequest, use_paper: Optional[bool] = None):
    use_paper_mode = use_paper if use_paper is not None else _use_paper_trading
    
    result = await agent_scheduler.run_agent(
        agent_id=request.agent_id,
        name=request.name,
        strategy_type=request.strategy_type,
        trading_pairs=request.trading_pairs,
        allocation_pct=request.allocation_percentage,
        max_position=request.max_position_size,
        stop_loss_pct=request.stop_loss_pct,
        take_profit_pct=request.take_profit_pct,
        use_paper=use_paper_mode
    )
    
    return RunAgentResponse(
        agent_id=result.agent_id,
        timestamp=result.timestamp.isoformat(),
        symbol=result.symbol,
        signal=result.signal,
        confidence=result.confidence,
        price=result.price,
        executed=result.executed,
        pnl=result.pnl,
        error=result.error
    )


@router.get("/metrics", response_model=List[AgentMetricsResponse])
async def get_all_metrics():
    metrics = await agent_scheduler.get_all_metrics()
    return [
        AgentMetricsResponse(
            agent_id=m.agent_id,
            total_runs=m.total_runs,
            successful_runs=m.successful_runs,
            failed_runs=m.failed_runs,
            total_pnl=m.total_pnl,
            buy_signals=m.buy_signals,
            sell_signals=m.sell_signals,
            hold_signals=m.hold_signals,
            last_run=m.last_run.isoformat() if m.last_run else None,
            win_rate=m.win_rate,
            avg_pnl=m.avg_pnl
        )
        for m in metrics
    ]


@router.get("/metrics/{agent_id}", response_model=AgentMetricsResponse)
async def get_agent_metrics(agent_id: str):
    metrics = await agent_scheduler.get_agent_metrics(agent_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Agent metrics not found")
    
    return AgentMetricsResponse(
        agent_id=metrics.agent_id,
        total_runs=metrics.total_runs,
        successful_runs=metrics.successful_runs,
        failed_runs=metrics.failed_runs,
        total_pnl=metrics.total_pnl,
        buy_signals=metrics.buy_signals,
        sell_signals=metrics.sell_signals,
        hold_signals=metrics.hold_signals,
        last_run=metrics.last_run.isoformat() if metrics.last_run else None,
        win_rate=metrics.win_rate,
        avg_pnl=metrics.avg_pnl
    )


@router.get("/runs", response_model=List[RunAgentResponse])
async def get_recent_runs(agent_id: Optional[str] = None, limit: int = 50):
    runs = await agent_scheduler.get_recent_runs(agent_id, limit)
    return [
        RunAgentResponse(
            agent_id=r.agent_id,
            timestamp=r.timestamp.isoformat(),
            symbol=r.symbol,
            signal=r.signal,
            confidence=r.confidence,
            price=r.price,
            executed=r.executed,
            pnl=r.pnl,
            error=r.error
        )
        for r in runs
    ]


@router.get("/market-analysis", response_model=MarketConditionResponse)
async def analyze_market(symbol: str = "BTCUSDT"):
    condition = await fund_manager.analyze_market(symbol)
    return MarketConditionResponse(
        trend=condition.trend,
        volatility=condition.volatility,
        rsi=condition.rsi,
        momentum=condition.momentum,
        recommendation=condition.recommendation
    )


@router.get("/agent-recommendations", response_model=List[AgentRecommendationResponse])
async def get_agent_recommendations():
    from app.api.routes.agents import get_agents_from_db

    agents = await get_agents_from_db()
    metrics_data = await agent_scheduler.get_all_metrics()
    
    agent_metrics = [
        {
            'agent_id': m.agent_id,
            'total_runs': m.total_runs,
            'successful_runs': m.successful_runs,
            'total_pnl': m.total_pnl,
            'win_rate': m.win_rate,
            'last_run': m.last_run.isoformat() if m.last_run else None
        }
        for m in metrics_data
    ]
    
    market = await fund_manager.analyze_market()
    recommendations = await fund_manager.evaluate_agents(agents, agent_metrics, market)
    
    return [
        AgentRecommendationResponse(
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            action=r.action,
            reason=r.reason,
            confidence=r.confidence
        )
        for r in recommendations
    ]


@router.get("/fund-allocation", response_model=List[FundAllocationResponse])
async def get_fund_allocation(total_capital: float = 10000):
    from app.api.routes.agents import get_agents_from_db

    agents = await get_agents_from_db()
    metrics_data = await agent_scheduler.get_all_metrics()
    
    agent_metrics = [
        {
            'agent_id': m.agent_id,
            'win_rate': m.win_rate,
            'total_runs': m.total_runs
        }
        for m in metrics_data
    ]
    
    allocations = await fund_manager.get_fund_allocation(agents, agent_metrics, total_capital)
    
    return [
        FundAllocationResponse(agent_id=aid, allocation=alloc)
        for aid, alloc in allocations.items()
    ]


@router.post("/register-agent/{agent_id}")
async def register_agent_for_automation(agent_id: str):
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models import Agent as DBAgent
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(DBAgent).where(DBAgent.id == agent_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        agent_config = {
            'id': agent.id,
            'name': agent.name,
            'strategy_type': agent.strategy_type,
            'trading_pairs': agent.config.get('trading_pairs', []),
            'allocation_percentage': agent.allocation_percentage,
            'max_position_size': agent.max_position_size,
            'run_interval_seconds': agent.run_interval_seconds,
        }
        
        agent_scheduler.register_agent(agent_config)
        
        return {"status": "registered", "agent_id": agent_id}


@router.post("/unregister-agent/{agent_id}")
async def unregister_agent_from_automation(agent_id: str):
    agent_scheduler.unregister_agent(agent_id)
    return {"status": "unregistered", "agent_id": agent_id}


@router.get("/registered-agents")
async def get_registered_agents():
    return {"agents": list(agent_scheduler._enabled_agents.values())}
