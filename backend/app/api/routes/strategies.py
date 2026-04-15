"""
Strategy Registry API
=====================
CRUD access to strategy overrides — the DB layer that sits on top of
registry.yaml. The YAML provides immutable base definitions; this table
stores per-environment runtime overrides (enable/disable, default params).

Routes
------
GET  /api/strategies              — merged list (YAML + DB overrides)
GET  /api/strategies/:id          — single merged strategy
PUT  /api/strategies/:id          — upsert override fields
POST /api/strategies/:id/reset    — delete override, revert to YAML defaults
"""

from __future__ import annotations

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import get_db
from app.models import StrategyOverride
import app.strategies as strategy_registry

router = APIRouter(prefix="/strategies", tags=["strategies"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class StrategyOverrideIn(BaseModel):
    enabled: Optional[bool] = None
    display_order: Optional[int] = None
    default_stop_loss_pct: Optional[float] = None
    default_take_profit_pct: Optional[float] = None
    default_trailing_stop_pct: Optional[float] = None
    default_timeframe: Optional[str] = None
    notes: Optional[str] = None


class StrategyOverrideOut(BaseModel):
    strategy_type: str
    enabled: bool
    display_order: Optional[int]
    default_stop_loss_pct: Optional[float]
    default_take_profit_pct: Optional[float]
    default_trailing_stop_pct: Optional[float]
    default_timeframe: Optional[str]
    notes: Optional[str]
    updated_at: Optional[datetime]


# ── Helper ────────────────────────────────────────────────────────────────────

async def _fetch_overrides(db: AsyncSession) -> dict[str, dict]:
    """Return all DB overrides keyed by strategy_type."""
    result = await db.execute(select(StrategyOverride))
    rows = result.scalars().all()
    return {
        r.strategy_type: {
            "enabled": r.enabled,
            "display_order": r.display_order,
            "default_stop_loss_pct": r.default_stop_loss_pct,
            "default_take_profit_pct": r.default_take_profit_pct,
            "default_trailing_stop_pct": r.default_trailing_stop_pct,
            "default_timeframe": r.default_timeframe,
            "notes": r.notes,
            "updated_at": r.updated_at,
        }
        for r in rows
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_strategies(db: AsyncSession = Depends(get_db)):
    """
    Return all strategies with YAML base definitions merged with DB overrides.
    This is the canonical source for the UI and agent creation.
    """
    overrides = await _fetch_overrides(db)
    return strategy_registry.for_ui(overrides)


@router.get("/{strategy_type}")
async def get_strategy(strategy_type: str, db: AsyncSession = Depends(get_db)):
    """Return a single merged strategy definition."""
    if strategy_type not in strategy_registry.all_types():
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_type}' not found in registry")
    overrides = await _fetch_overrides(db)
    strategies = {s["value"]: s for s in strategy_registry.for_ui(overrides)}
    return strategies[strategy_type]


@router.put("/{strategy_type}", response_model=StrategyOverrideOut)
async def upsert_strategy_override(
    strategy_type: str,
    body: StrategyOverrideIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Create or update a DB override for a strategy type.
    Only provided (non-None) fields are written — others are left unchanged.
    """
    if strategy_type not in strategy_registry.all_types():
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_type}' not found in registry")

    # Fetch existing row or create fresh values dict
    result = await db.execute(
        select(StrategyOverride).where(StrategyOverride.strategy_type == strategy_type)
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = StrategyOverride(strategy_type=strategy_type, enabled=True)
        db.add(row)

    # Apply only the fields the caller provided
    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(row, field, value)

    await db.commit()
    await db.refresh(row)
    return StrategyOverrideOut(
        strategy_type=row.strategy_type,
        enabled=row.enabled,
        display_order=row.display_order,
        default_stop_loss_pct=row.default_stop_loss_pct,
        default_take_profit_pct=row.default_take_profit_pct,
        default_trailing_stop_pct=row.default_trailing_stop_pct,
        default_timeframe=row.default_timeframe,
        notes=row.notes,
        updated_at=row.updated_at,
    )


@router.post("/{strategy_type}/reset")
async def reset_strategy_override(
    strategy_type: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete the DB override for a strategy, reverting it to YAML defaults.
    Re-inserts a clean enabled=True row.
    """
    if strategy_type not in strategy_registry.all_types():
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_type}' not found in registry")

    result = await db.execute(
        select(StrategyOverride).where(StrategyOverride.strategy_type == strategy_type)
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()

    # Re-seed with clean defaults
    new_row = StrategyOverride(strategy_type=strategy_type, enabled=True)
    db.add(new_row)
    await db.commit()

    return {"status": "reset", "strategy_type": strategy_type}
