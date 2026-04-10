from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Agent as DBAgent, AgentSignal as DBAgentSignal, SignalType
from app.services.backtest import BacktestConfig, backtest_engine

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentConfig(BaseModel):
    name: str
    strategy_type: str
    trading_pairs: List[str]
    allocation_percentage: float = 10.0
    max_position_size: float = 0.1
    risk_limit: float = 2.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    trailing_stop_pct: Optional[float] = None
    run_interval_seconds: int = 3600
    indicators_config: dict = {}
    timeframe: str = "1h"


# Permitted timeframes per strategy — used for validation and UI hints
STRATEGY_TIMEFRAMES: dict = {
    "scalping":        {"default": "5m",  "allowed": ["1m", "5m", "15m"]},
    "momentum":        {"default": "15m", "allowed": ["15m", "30m", "1h"]},
    "mean_reversion":  {"default": "1h",  "allowed": ["15m", "30m", "1h", "4h"]},
    "breakout":        {"default": "1h",  "allowed": ["30m", "1h", "4h"]},
    "trend_following": {"default": "4h",  "allowed": ["1h", "4h", "1d"]},
    "grid":            {"default": "15m", "allowed": ["5m", "15m", "30m", "1h"]},
    "ai":              {"default": "1h",  "allowed": ["5m", "15m", "30m", "1h", "4h", "1d"]},
}

ALL_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def default_timeframe_for(strategy_type: str) -> str:
    return STRATEGY_TIMEFRAMES.get(strategy_type, {}).get("default", "1h")


class Agent(BaseModel):
    id: str
    name: str
    strategy_type: str
    trading_pairs: List[str]
    is_enabled: bool = False
    allocation_percentage: float = 10.0
    max_position_size: float = 0.1
    risk_limit: float = 2.0
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0
    trailing_stop_pct: Optional[float] = None
    run_interval_seconds: int = 3600
    indicators_config: dict = {}
    timeframe: str = "1h"
    trader_id: Optional[str] = None
    created_at: str


class AgentSignalResponse(BaseModel):
    id: str
    agent_id: str
    symbol: str
    signal: str
    confidence: float
    reasoning: str
    created_at: str


def agent_to_response(db_agent: DBAgent) -> Agent:
    return Agent(
        id=db_agent.id,
        name=db_agent.name,
        strategy_type=db_agent.strategy_type,
        trading_pairs=db_agent.config.get("trading_pairs", []),
        is_enabled=db_agent.is_enabled,
        allocation_percentage=db_agent.allocation_percentage,
        max_position_size=db_agent.max_position_size,
        risk_limit=db_agent.risk_limit,
        stop_loss_pct=db_agent.config.get("stop_loss_pct", 2.0),
        take_profit_pct=db_agent.config.get("take_profit_pct", 4.0),
        trailing_stop_pct=db_agent.config.get("trailing_stop_pct"),
        run_interval_seconds=db_agent.run_interval_seconds,
        indicators_config=db_agent.config.get("indicators_config", {}),
        timeframe=db_agent.config.get("timeframe", default_timeframe_for(db_agent.strategy_type)),
        trader_id=db_agent.trader_id,
        created_at=db_agent.created_at.isoformat() if db_agent.created_at else datetime.now().isoformat()
    )


@router.get("/strategy-timeframes")
async def get_strategy_timeframes():
    """Returns permitted timeframes and defaults per strategy type."""
    return STRATEGY_TIMEFRAMES


@router.get("", response_model=List[Agent])
async def get_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DBAgent))
    agents = result.scalars().all()
    return [agent_to_response(a) for a in agents]


@router.post("", response_model=Agent)
async def create_agent(config: AgentConfig, db: AsyncSession = Depends(get_db)):
    import uuid
    agent_id = str(uuid.uuid4())
    agent = DBAgent(
        id=agent_id,
        user_id="default-user",  # TODO: Add proper user auth
        name=config.name,
        strategy_type=config.strategy_type,
        config={
            "trading_pairs": config.trading_pairs,
            "indicators_config": config.indicators_config,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "trailing_stop_pct": config.trailing_stop_pct,
            "timeframe": config.timeframe or default_timeframe_for(config.strategy_type),
        },
        is_enabled=False,
        allocation_percentage=config.allocation_percentage,
        max_position_size=config.max_position_size,
        risk_limit=config.risk_limit,
        run_interval_seconds=config.run_interval_seconds,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent_to_response(agent)


@router.get("/{agent_id}", response_model=Agent)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DBAgent).where(DBAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_to_response(agent)


@router.put("/{agent_id}", response_model=Agent)
async def update_agent(agent_id: str, config: AgentConfig, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DBAgent).where(DBAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    agent.name = config.name
    agent.strategy_type = config.strategy_type
    agent.config = {
        "trading_pairs": config.trading_pairs,
        "indicators_config": config.indicators_config,
        "stop_loss_pct": config.stop_loss_pct,
        "take_profit_pct": config.take_profit_pct,
        "trailing_stop_pct": config.trailing_stop_pct,
        "timeframe": config.timeframe or default_timeframe_for(config.strategy_type),
    }
    agent.allocation_percentage = config.allocation_percentage
    agent.max_position_size = config.max_position_size
    agent.risk_limit = config.risk_limit
    agent.run_interval_seconds = config.run_interval_seconds
    
    await db.commit()
    await db.refresh(agent)
    return agent_to_response(agent)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DBAgent).where(DBAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    await db.delete(agent)
    await db.commit()
    return {"status": "deleted"}


@router.post("/{agent_id}/toggle")
async def toggle_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    from app.services.agent_scheduler import agent_scheduler

    result = await db.execute(select(DBAgent).where(DBAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    agent.is_enabled = not agent.is_enabled
    await db.commit()

    # Sync with scheduler
    if agent.is_enabled:
        agent_scheduler.register_agent({
            "id": agent.id,
            "name": agent.name,
            "strategy_type": agent.strategy_type,
            "trading_pairs": agent.config.get("trading_pairs", []) if isinstance(agent.config, dict) else [],
            "is_enabled": True,
            "allocation_percentage": agent.allocation_percentage,
            "max_position_size": agent.max_position_size,
            "trader_id": agent.trader_id,
        })
    else:
        agent_scheduler.unregister_agent(agent.id)

    return {"is_enabled": agent.is_enabled}


@router.get("/signals", response_model=List[AgentSignalResponse])
async def get_signals(agent_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(DBAgentSignal).order_by(DBAgentSignal.created_at.desc()).limit(50)
    if agent_id:
        query = query.where(DBAgentSignal.agent_id == agent_id)
    
    result = await db.execute(query)
    signals = result.scalars().all()
    
    return [
        AgentSignalResponse(
            id=s.id,
            agent_id=s.agent_id,
            symbol=s.trading_pair_id,
            signal=s.signal_type.value,
            confidence=s.confidence,
            reasoning=s.reasoning or "",
            created_at=s.created_at.isoformat() if s.created_at else ""
        )
        for s in signals
    ]


@router.post("/{agent_id}/backtest")
async def run_agent_backtest(
    agent_id: str,
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DBAgent).where(DBAgent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    config = BacktestConfig(
        symbol=symbol,
        interval=interval,
        initial_balance=10000,
        position_size_pct=agent.allocation_percentage / 100,
        stop_loss_pct=agent.risk_limit / 100,
        take_profit_pct=agent.risk_limit * 2 / 100,
        strategy=agent.strategy_type,
    )
    
    backtest_result = await backtest_engine.run_backtest(config)

    # Persist to DB
    try:
        from app.models import BacktestRecord
        record = BacktestRecord(
            agent_id=agent_id,
            symbol=symbol,
            strategy=agent.strategy_type,
            interval=interval,
            config_params={
                "initial_balance": config.initial_balance,
                "position_size_pct": config.position_size_pct,
                "stop_loss_pct": config.stop_loss_pct,
                "take_profit_pct": config.take_profit_pct,
            },
            total_trades=backtest_result.total_trades,
            winning_trades=backtest_result.winning_trades,
            losing_trades=backtest_result.losing_trades,
            win_rate=backtest_result.win_rate,
            total_pnl=backtest_result.total_pnl,
            net_pnl=backtest_result.net_pnl,
            total_fees=backtest_result.total_fees,
            max_drawdown=backtest_result.max_drawdown,
            sharpe_ratio=backtest_result.sharpe_ratio,
            avg_trade_pnl=backtest_result.avg_trade_pnl,
            profit_factor=backtest_result.profit_factor,
            equity_curve=backtest_result.equity_curve[-200:],
            trades_data=backtest_result.trades[-50:],
            source="manual",
            candle_count=len(backtest_result.equity_curve),
        )
        db.add(record)
        await db.commit()
    except Exception:
        pass  # non-critical

    return {
        "agent_id": agent_id,
        "config": {
            "symbol": symbol,
            "interval": interval,
            "strategy": agent.strategy_type,
        },
        "metrics": {
            "total_trades": backtest_result.total_trades,
            "winning_trades": backtest_result.winning_trades,
            "losing_trades": backtest_result.losing_trades,
            "win_rate": backtest_result.win_rate,
            "total_pnl": backtest_result.total_pnl,
            "net_pnl": backtest_result.net_pnl,
            "total_fees": backtest_result.total_fees,
            "max_drawdown": backtest_result.max_drawdown,
            "sharpe_ratio": backtest_result.sharpe_ratio,
            "avg_trade_pnl": backtest_result.avg_trade_pnl,
            "profit_factor": backtest_result.profit_factor,
            "avg_win": backtest_result.avg_win,
            "avg_loss": backtest_result.avg_loss,
        },
        "trades": backtest_result.trades[-10:],
        "equity_curve": backtest_result.equity_curve,
    }


async def get_agents_from_db():
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(DBAgent))
        agents = result.scalars().all()
        return [
            {
                "id": a.id,
                "name": a.name,
                "strategy_type": a.strategy_type,
                "trading_pairs": a.config.get("trading_pairs", []),
                "is_enabled": a.is_enabled,
                "allocation_percentage": a.allocation_percentage,
                "max_position_size": a.max_position_size,
            }
            for a in agents
        ]
