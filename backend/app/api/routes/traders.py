"""CRUD + performance endpoints for the Trader layer."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import logging

from app.database import get_async_session
from app.services.trader_service import trader_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/traders", tags=["traders"])


# ── Pydantic models ─────────────────────────────────────────────────────────

class TraderResponse(BaseModel):
    id: str
    name: str
    llm_provider: str
    llm_model: str
    allocation_pct: float
    is_enabled: bool
    config: dict
    performance_metrics: dict
    agent_count: int = 0


class TraderCreate(BaseModel):
    name: str
    llm_provider: str = "openrouter"
    llm_model: str = "anthropic/claude-sonnet-4"
    allocation_pct: float = 33.3
    is_enabled: bool = True
    config: dict = {}


class TraderUpdate(BaseModel):
    name: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    allocation_pct: Optional[float] = None
    is_enabled: Optional[bool] = None
    config: Optional[dict] = None


class TraderPerformanceResponse(BaseModel):
    trader_id: str
    trader_name: str
    total_pnl: float
    win_rate: float
    total_trades: int
    winning_trades: int
    agent_count: int
    allocation_pct: float
    agents: list = []


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=List[TraderResponse])
async def list_traders():
    """List all traders with agent counts."""
    from sqlalchemy import select, func
    from app.models import Trader, Agent

    async with get_async_session() as db:
        # Get traders
        result = await db.execute(select(Trader).order_by(Trader.name))
        traders = result.scalars().all()

        # Get agent counts per trader
        count_result = await db.execute(
            select(Agent.trader_id, func.count(Agent.id))
            .where(Agent.trader_id.isnot(None))
            .group_by(Agent.trader_id)
        )
        counts = dict(count_result.all())

        return [
            TraderResponse(
                id=t.id,
                name=t.name,
                llm_provider=t.llm_provider,
                llm_model=t.llm_model,
                allocation_pct=t.allocation_pct,
                is_enabled=t.is_enabled,
                config=t.config or {},
                performance_metrics=t.performance_metrics or {},
                agent_count=counts.get(t.id, 0),
            )
            for t in traders
        ]


@router.post("", response_model=TraderResponse)
async def create_trader(body: TraderCreate):
    """Create a new trader."""
    from app.models import Trader

    async with get_async_session() as db:
        trader = Trader(
            name=body.name,
            llm_provider=body.llm_provider,
            llm_model=body.llm_model,
            allocation_pct=body.allocation_pct,
            is_enabled=body.is_enabled,
            config=body.config,
            performance_metrics={},
        )
        db.add(trader)
        await db.commit()
        await db.refresh(trader)

        return TraderResponse(
            id=trader.id,
            name=trader.name,
            llm_provider=trader.llm_provider,
            llm_model=trader.llm_model,
            allocation_pct=trader.allocation_pct,
            is_enabled=trader.is_enabled,
            config=trader.config or {},
            performance_metrics=trader.performance_metrics or {},
            agent_count=0,
        )


@router.get("/{trader_id}", response_model=TraderResponse)
async def get_trader(trader_id: str):
    """Get a single trader."""
    from sqlalchemy import select, func
    from app.models import Trader, Agent

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            raise HTTPException(status_code=404, detail="Trader not found")

        count_result = await db.execute(
            select(func.count(Agent.id)).where(Agent.trader_id == trader_id)
        )
        agent_count = count_result.scalar() or 0

        return TraderResponse(
            id=trader.id,
            name=trader.name,
            llm_provider=trader.llm_provider,
            llm_model=trader.llm_model,
            allocation_pct=trader.allocation_pct,
            is_enabled=trader.is_enabled,
            config=trader.config or {},
            performance_metrics=trader.performance_metrics or {},
            agent_count=agent_count,
        )


@router.put("/{trader_id}", response_model=TraderResponse)
async def update_trader(trader_id: str, body: TraderUpdate):
    """Update trader configuration."""
    from app.models import Trader

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            raise HTTPException(status_code=404, detail="Trader not found")

        if body.name is not None:
            trader.name = body.name
        if body.llm_provider is not None:
            trader.llm_provider = body.llm_provider
        if body.llm_model is not None:
            trader.llm_model = body.llm_model
        if body.allocation_pct is not None:
            trader.allocation_pct = body.allocation_pct
        if body.is_enabled is not None:
            trader.is_enabled = body.is_enabled
        if body.config is not None:
            trader.config = body.config

        await db.commit()
        await db.refresh(trader)

        # Invalidate LLM cache if model changed
        if body.llm_provider is not None or body.llm_model is not None:
            trader_service.invalidate_llm_cache(trader_id)

        return TraderResponse(
            id=trader.id,
            name=trader.name,
            llm_provider=trader.llm_provider,
            llm_model=trader.llm_model,
            allocation_pct=trader.allocation_pct,
            is_enabled=trader.is_enabled,
            config=trader.config or {},
            performance_metrics=trader.performance_metrics or {},
        )


@router.delete("/{trader_id}")
async def delete_trader(trader_id: str):
    """Delete a trader (agents become unassigned)."""
    from sqlalchemy import update
    from app.models import Trader, Agent

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            raise HTTPException(status_code=404, detail="Trader not found")

        # Unassign agents instead of deleting them
        await db.execute(
            update(Agent).where(Agent.trader_id == trader_id).values(trader_id=None)
        )
        await db.delete(trader)
        await db.commit()

    return {"status": "deleted", "trader_id": trader_id}


@router.post("/{trader_id}/toggle")
async def toggle_trader(trader_id: str):
    """Enable/disable a trader."""
    from app.models import Trader

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            raise HTTPException(status_code=404, detail="Trader not found")

        trader.is_enabled = not trader.is_enabled
        await db.commit()

    return {"trader_id": trader_id, "is_enabled": trader.is_enabled}


@router.get("/{trader_id}/performance", response_model=TraderPerformanceResponse)
async def get_trader_performance(trader_id: str):
    """Get aggregated performance for a trader."""
    from sqlalchemy import select
    from app.models import Trader, Agent, AgentMetricRecord

    async with get_async_session() as db:
        trader = await db.get(Trader, trader_id)
        if not trader:
            raise HTTPException(status_code=404, detail="Trader not found")

        # Fetch agents for this trader
        agents_result = await db.execute(
            select(Agent).where(Agent.trader_id == trader_id)
        )
        agents = agents_result.scalars().all()
        agent_ids = [a.id for a in agents]

        # Fetch metrics
        metrics_result = await db.execute(
            select(AgentMetricRecord).where(AgentMetricRecord.agent_id.in_(agent_ids))
        ) if agent_ids else None
        metrics = metrics_result.scalars().all() if metrics_result else []

        total_pnl = sum(m.total_pnl or 0 for m in metrics)
        total_trades = sum(m.total_runs or 0 for m in metrics)
        winning = sum(int((m.total_runs or 0) * (m.win_rate or 0)) for m in metrics)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

        agent_summaries = []
        for a in agents:
            m = next((m for m in metrics if m.agent_id == a.id), None)
            agent_summaries.append({
                "id": a.id,
                "name": a.name,
                "strategy_type": a.strategy_type,
                "is_enabled": a.is_enabled,
                "pnl": m.total_pnl if m else 0,
                "win_rate": m.win_rate if m else 0,
                "runs": m.total_runs if m else 0,
            })

        return TraderPerformanceResponse(
            trader_id=trader.id,
            trader_name=trader.name,
            total_pnl=total_pnl,
            win_rate=win_rate,
            total_trades=total_trades,
            winning_trades=winning,
            agent_count=len(agents),
            allocation_pct=trader.allocation_pct,
            agents=agent_summaries,
        )
