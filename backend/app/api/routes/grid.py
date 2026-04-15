"""
Grid Trading API Routes
=======================
Endpoints for managing and inspecting grid trading state.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.database import get_async_session
from app.models import GridState, GridLevel, GridStatus, GridLevelStatus
from app.services.grid_engine import grid_engine

router = APIRouter(prefix="/grid", tags=["grid"])


def _level_to_dict(lv: GridLevel) -> dict:
    return {
        "id": lv.id,
        "grid_id": lv.grid_id,
        "level_index": lv.level_index,
        "price": lv.price,
        "side": lv.side.value if hasattr(lv.side, "value") else str(lv.side),
        "status": lv.status.value if hasattr(lv.status, "value") else str(lv.status),
        "quantity": lv.quantity,
        "position_id": lv.position_id,
        "entry_price": lv.entry_price,
        "exit_price": lv.exit_price,
        "pnl": lv.pnl,
        "created_at": lv.created_at.isoformat() if lv.created_at else None,
        "updated_at": lv.updated_at.isoformat() if lv.updated_at else None,
    }


def _grid_to_dict(grid: GridState, levels: list | None = None) -> dict:
    d = {
        "id": grid.id,
        "agent_id": grid.agent_id,
        "symbol": grid.symbol,
        "status": grid.status.value if hasattr(grid.status, "value") else str(grid.status),
        "grid_low": grid.grid_low,
        "grid_high": grid.grid_high,
        "grid_levels": grid.grid_levels,
        "grid_spacing_pct": grid.grid_spacing_pct,
        "current_price_at_creation": grid.current_price_at_creation,
        "regime_atr": grid.regime_atr,
        "total_invested": round(grid.total_invested or 0, 2),
        "realized_pnl": round(grid.realized_pnl or 0, 2),
        "cancel_reason": grid.cancel_reason,
        "created_at": grid.created_at.isoformat() if grid.created_at else None,
        "updated_at": grid.updated_at.isoformat() if grid.updated_at else None,
    }
    if levels is not None:
        d["levels"] = [_level_to_dict(lv) for lv in levels]
    return d


@router.get("")
async def list_grids(status: Optional[str] = None):
    """List all grid states, optionally filtered by status."""
    async with get_async_session() as db:
        q = select(GridState).order_by(GridState.created_at.desc())
        if status:
            try:
                q = q.where(GridState.status == GridStatus(status))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        grids = (await db.scalars(q)).all()
        return [_grid_to_dict(g) for g in grids]


@router.get("/{agent_id}")
async def get_agent_grid(agent_id: str, symbol: Optional[str] = None):
    """Get the active grid for a specific agent (optionally filtered by symbol)."""
    summary = await grid_engine.get_grid_summary(agent_id, symbol)
    return summary


@router.get("/{grid_id}/levels")
async def get_grid_levels(grid_id: str, status: Optional[str] = None):
    """List all price levels for a grid, optionally filtered by status."""
    async with get_async_session() as db:
        grid = await db.get(GridState, grid_id)
        if not grid:
            raise HTTPException(status_code=404, detail="Grid not found")

        q = select(GridLevel).where(GridLevel.grid_id == grid_id).order_by(GridLevel.level_index)
        if status:
            try:
                q = q.where(GridLevel.status == GridLevelStatus(status))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid level status: {status}")

        levels = (await db.scalars(q)).all()
        return {
            "grid_id": grid_id,
            "symbol": grid.symbol,
            "total_levels": len(levels),
            "levels": [_level_to_dict(lv) for lv in levels],
        }


@router.post("/{agent_id}/cancel")
async def cancel_agent_grid(agent_id: str, symbol: Optional[str] = None):
    """Manually cancel the active grid for an agent. Closes all open positions."""
    from app.services.paper_trading import paper_trading

    active = await grid_engine.get_active_grid(agent_id, symbol or "")
    if not active:
        # Try to find any active grid for this agent if no symbol given
        if not symbol:
            async with get_async_session() as db:
                active = await db.scalar(
                    select(GridState).where(
                        GridState.agent_id == agent_id,
                        GridState.status.in_([GridStatus.active, GridStatus.paused]),
                    )
                )
        if not active:
            raise HTTPException(status_code=404, detail="No active grid found for this agent")

    cancelled_grid, position_ids = await grid_engine.cancel_grid(active.id, "Manually cancelled by user")

    closed = 0
    for pos_id in position_ids:
        try:
            await paper_trading.close_position(pos_id)
            closed += 1
        except Exception:
            pass

    return {
        "message": f"Grid cancelled. Closed {closed}/{len(position_ids)} open position(s).",
        "grid_id": active.id,
        "symbol": active.symbol,
        "positions_closed": closed,
    }


@router.post("/{agent_id}/pause")
async def pause_agent_grid(agent_id: str, symbol: Optional[str] = None):
    """Pause a grid (stops placing new orders, keeps open positions)."""
    async with get_async_session() as db:
        q = select(GridState).where(
            GridState.agent_id == agent_id,
            GridState.status == GridStatus.active,
        )
        if symbol:
            q = q.where(GridState.symbol == symbol)
        grid = await db.scalar(q)
        if not grid:
            raise HTTPException(status_code=404, detail="No active grid found")

    updated = await grid_engine.pause_grid(grid.id)
    return {"message": "Grid paused", "grid_id": grid.id, "symbol": grid.symbol}


@router.post("/{agent_id}/resume")
async def resume_agent_grid(agent_id: str, symbol: Optional[str] = None):
    """Resume a paused grid."""
    async with get_async_session() as db:
        q = select(GridState).where(
            GridState.agent_id == agent_id,
            GridState.status == GridStatus.paused,
        )
        if symbol:
            q = q.where(GridState.symbol == symbol)
        grid = await db.scalar(q)
        if not grid:
            raise HTTPException(status_code=404, detail="No paused grid found")

    updated = await grid_engine.resume_grid(grid.id)
    return {"message": "Grid resumed", "grid_id": grid.id, "symbol": grid.symbol}


@router.get("/{agent_id}/summary")
async def get_grid_summary(agent_id: str, symbol: Optional[str] = None):
    """Return P&L, fill rate, and level breakdown for all grids of an agent."""
    return await grid_engine.get_grid_summary(agent_id, symbol)
