"""
Whale Intelligence API — watchlist management and aggregated positioning data.
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_async_session
from app.services.whale_intelligence import whale_intelligence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whale", tags=["whale"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class WhaleAddressCreate(BaseModel):
    address: str
    label: Optional[str] = None
    notes: Optional[str] = None


class WhaleAddressUpdate(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None


class WhaleAddressResponse(BaseModel):
    id: str
    address: str
    label: Optional[str]
    notes: Optional[str]
    is_active: bool
    created_at: str


class WhaleBiasResponse(BaseModel):
    coin: str
    bias: str
    long_notional: float
    short_notional: float
    net_notional: float
    whale_count: int
    avg_leverage: float
    top_positions: List[dict] = []


class WhaleReportResponse(BaseModel):
    timestamp: str
    coin_biases: Dict[str, WhaleBiasResponse]
    total_whales_tracked: int
    total_whales_with_positions: int
    fetch_errors: int


# ── Watchlist endpoints ───────────────────────────────────────────────────────

@router.get("/watchlist", response_model=List[WhaleAddressResponse])
async def list_watchlist():
    """List all whale addresses in the watchlist."""
    from sqlalchemy import select
    from app.models import WhaleAddress

    async with get_async_session() as db:
        result = await db.execute(select(WhaleAddress).order_by(WhaleAddress.created_at.desc()))
        rows = result.scalars().all()
        return [
            WhaleAddressResponse(
                id=r.id,
                address=r.address,
                label=r.label,
                notes=r.notes,
                is_active=r.is_active,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]


@router.post("/watchlist", response_model=WhaleAddressResponse)
async def add_to_watchlist(body: WhaleAddressCreate):
    """Add a Hyperliquid address to the whale watchlist."""
    from sqlalchemy import select
    from app.models import WhaleAddress

    address = body.address.strip().lower()
    if not address.startswith("0x") or len(address) < 10:
        raise HTTPException(status_code=400, detail="Invalid Hyperliquid address format")

    async with get_async_session() as db:
        # Check for duplicate
        existing = await db.execute(
            select(WhaleAddress).where(WhaleAddress.address == address)
        )
        if existing.scalar():
            raise HTTPException(status_code=409, detail="Address already in watchlist")

        row = WhaleAddress(address=address, label=body.label, notes=body.notes, is_active=True)
        db.add(row)
        await db.commit()
        await db.refresh(row)

        # Invalidate watchlist cache so next fetch includes this address
        whale_intelligence.invalidate_watchlist_cache()

        return WhaleAddressResponse(
            id=row.id,
            address=row.address,
            label=row.label,
            notes=row.notes,
            is_active=row.is_active,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


@router.delete("/watchlist/{whale_id}")
async def remove_from_watchlist(whale_id: str):
    """Remove an address from the whale watchlist."""
    from sqlalchemy import select
    from app.models import WhaleAddress

    async with get_async_session() as db:
        result = await db.execute(select(WhaleAddress).where(WhaleAddress.id == whale_id))
        row = result.scalar()
        if not row:
            raise HTTPException(status_code=404, detail="Whale address not found")

        await db.delete(row)
        await db.commit()
        whale_intelligence.invalidate_watchlist_cache()
        return {"status": "deleted", "id": whale_id}


@router.patch("/watchlist/{whale_id}/toggle", response_model=WhaleAddressResponse)
async def toggle_watchlist_entry(whale_id: str):
    """Toggle active status of a whale address."""
    from sqlalchemy import select
    from app.models import WhaleAddress

    async with get_async_session() as db:
        result = await db.execute(select(WhaleAddress).where(WhaleAddress.id == whale_id))
        row = result.scalar()
        if not row:
            raise HTTPException(status_code=404, detail="Whale address not found")

        row.is_active = not row.is_active
        await db.commit()
        await db.refresh(row)
        whale_intelligence.invalidate_watchlist_cache()

        return WhaleAddressResponse(
            id=row.id,
            address=row.address,
            label=row.label,
            notes=row.notes,
            is_active=row.is_active,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


# ── Intelligence endpoints ────────────────────────────────────────────────────

@router.get("/intelligence", response_model=WhaleReportResponse)
async def get_whale_intelligence():
    """Get the current aggregated whale intelligence report (uses 60s cache)."""
    async with get_async_session() as db:
        report = await whale_intelligence.fetch_whale_report(db)

    if report is None:
        raise HTTPException(status_code=503, detail="Whale intelligence unavailable — Hyperliquid API unreachable")

    return WhaleReportResponse(
        timestamp=report.timestamp.isoformat(),
        coin_biases={
            coin: WhaleBiasResponse(
                coin=bias.coin,
                bias=bias.bias,
                long_notional=bias.long_notional,
                short_notional=bias.short_notional,
                net_notional=bias.net_notional,
                whale_count=bias.whale_count,
                avg_leverage=bias.avg_leverage,
                top_positions=[p.to_dict() for p in bias.top_positions],
            )
            for coin, bias in report.coin_biases.items()
        },
        total_whales_tracked=report.total_whales_tracked,
        total_whales_with_positions=report.total_whales_with_positions,
        fetch_errors=report.fetch_errors,
    )


@router.get("/intelligence/{symbol}")
async def get_whale_bias_for_symbol(symbol: str):
    """Get whale bias for a specific trading symbol (e.g. BTCUSDT)."""
    async with get_async_session() as db:
        report = await whale_intelligence.fetch_whale_report(db)

    if report is None:
        return {"symbol": symbol, "bias": None, "message": "No data available"}

    coin = whale_intelligence.symbol_to_coin(symbol.upper())
    bias = report.coin_biases.get(coin)

    if bias is None:
        return {"symbol": symbol, "coin": coin, "bias": None, "message": "No whale positions for this coin"}

    return {
        "symbol": symbol,
        "coin": coin,
        **bias.to_dict(),
    }


@router.post("/refresh")
async def refresh_whale_intelligence():
    """Force cache invalidation and re-fetch whale intelligence."""
    whale_intelligence._cache = None
    whale_intelligence._cache_ts = None

    async with get_async_session() as db:
        report = await whale_intelligence.fetch_whale_report(db)

    if report is None:
        raise HTTPException(status_code=503, detail="Refresh failed — Hyperliquid API unreachable")

    return {
        "status": "refreshed",
        "timestamp": report.timestamp.isoformat(),
        "whales_tracked": report.total_whales_tracked,
        "whales_with_positions": report.total_whales_with_positions,
        "coins_active": list(report.coin_biases.keys()),
    }


@router.post("/watchlist/reseed")
async def reseed_watchlist_from_leaderboard():
    """
    Clear the auto-seeded entries and re-fetch the top traders from the
    Hyperliquid leaderboard.  User-added addresses (those without the
    'Auto-seeded' note prefix) are preserved.
    """
    from sqlalchemy import select
    from app.models import WhaleAddress

    async with get_async_session() as db:
        # Remove only auto-seeded rows so user additions are preserved
        result = await db.execute(select(WhaleAddress))
        for row in result.scalars().all():
            if row.notes and row.notes.startswith("Auto-seeded"):
                await db.delete(row)
        await db.commit()
        whale_intelligence.invalidate_watchlist_cache()

        # Re-seed from leaderboard
        await whale_intelligence.seed_default_watchlist(db)
        whale_intelligence.invalidate_watchlist_cache()

        # Return updated list
        result2 = await db.execute(select(WhaleAddress).order_by(WhaleAddress.created_at.desc()))
        rows = result2.scalars().all()

    return {
        "status": "reseeded",
        "total": len(rows),
        "addresses": [{"address": r.address, "label": r.label, "is_active": r.is_active} for r in rows],
    }
