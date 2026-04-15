"""
Grid Trading Engine
===================
Manages the full lifecycle of a price-grid trading strategy:
  - Initialise a grid over a price range derived from ATR + Bollinger Bands
  - Track which levels are pending / open / filled
  - Place counter-orders when a level fills
  - Detect exit conditions (range break, ATR spike, SMA crossover)
  - Cancel and rebalance grids

Each agent can have one active GridState per symbol.  Positions created for
individual levels are linked back via Position.grid_id / grid_level_id.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select

from app.database import get_async_session
from app.models import GridState, GridLevel, GridStatus, GridLevelStatus, OrderSide

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LEVELS = 10          # number of price levels
DEFAULT_LEVEL_BUFFER = 0.01  # 1% outside BB to set grid bounds
ATR_MULT_RANGE = 1.5         # grid_high/low = mid ± ATR_MULT_RANGE × ATR
ATR_SPIKE_MULT = 2.0         # cancel if current ATR > ATR_SPIKE_MULT × regime_ATR
PRICE_BREAK_BUFFER = 0.01    # 1% beyond grid edge triggers cancel


class GridEngine:
    """
    Stateless service — all state lives in DB (GridState + GridLevel rows).
    Every method that modifies state opens its own DB session and commits.
    """

    # ── Initialisation ───────────────────────────────────────────────────────

    async def initialise_grid(
        self,
        agent_id: str,
        symbol: str,
        current_price: float,
        atr: float,
        bb_upper: float,
        bb_lower: float,
        capital: float,
        n_levels: int = DEFAULT_LEVELS,
    ) -> Optional[GridState]:
        """
        Create a new GridState + GridLevel rows for the given (agent, symbol).
        Returns None if a grid already exists or if market conditions are invalid.
        """
        async with get_async_session() as db:
            # Guard: only one active grid per agent per symbol
            existing = await db.scalar(
                select(GridState).where(
                    GridState.agent_id == agent_id,
                    GridState.symbol == symbol,
                    GridState.status.in_([GridStatus.active, GridStatus.paused]),
                )
            )
            if existing:
                logger.debug(f"Grid already active for agent {agent_id} on {symbol}")
                return existing

            # Compute grid bounds — tighter of ATR-based or BB-based range
            atr_pct = atr / current_price if current_price else 0
            atr_low = current_price * (1 - ATR_MULT_RANGE * atr_pct)
            atr_high = current_price * (1 + ATR_MULT_RANGE * atr_pct)
            grid_low = max(bb_lower * (1 - DEFAULT_LEVEL_BUFFER), atr_low)
            grid_high = min(bb_upper * (1 + DEFAULT_LEVEL_BUFFER), atr_high)

            if grid_high <= grid_low or n_levels < 2:
                logger.warning(f"Grid init failed: invalid range [{grid_low:.4f}, {grid_high:.4f}]")
                return None

            spacing = (grid_high - grid_low) / (n_levels - 1)
            spacing_pct = spacing / current_price * 100
            capital_per_level = capital / n_levels

            grid = GridState(
                agent_id=agent_id,
                symbol=symbol,
                status=GridStatus.active,
                grid_low=round(grid_low, 8),
                grid_high=round(grid_high, 8),
                grid_levels=n_levels,
                grid_spacing_pct=round(spacing_pct, 4),
                current_price_at_creation=current_price,
                regime_atr=atr,
                total_invested=0.0,
                realized_pnl=0.0,
            )
            db.add(grid)
            await db.flush()  # get grid.id

            for i in range(n_levels):
                level_price = grid_low + i * spacing
                # Levels below current price → buy; above → sell
                side = OrderSide.BUY if level_price < current_price else OrderSide.SELL
                qty = capital_per_level / level_price if level_price > 0 else 0
                level = GridLevel(
                    grid_id=grid.id,
                    level_index=i,
                    price=round(level_price, 8),
                    side=side,
                    status=GridLevelStatus.pending,
                    quantity=round(qty, 8),
                )
                db.add(level)

            await db.commit()
            await db.refresh(grid)
            logger.info(
                f"Grid initialised: {symbol} agent={agent_id[:8]} "
                f"range=[{grid_low:.4f}, {grid_high:.4f}] "
                f"levels={n_levels} spacing={spacing_pct:.2f}% "
                f"capital=${capital:.2f}"
            )
            return grid

    # ── Level queries ────────────────────────────────────────────────────────

    async def get_active_grid(self, agent_id: str, symbol: str) -> Optional[GridState]:
        """Return the active (or paused) grid for this agent/symbol, or None."""
        async with get_async_session() as db:
            return await db.scalar(
                select(GridState).where(
                    GridState.agent_id == agent_id,
                    GridState.symbol == symbol,
                    GridState.status.in_([GridStatus.active, GridStatus.paused]),
                )
            )

    async def get_pending_levels(
        self, grid: GridState, current_price: float, proximity_pct: float = 1.5
    ) -> List[GridLevel]:
        """
        Return pending levels within proximity_pct % of the current price.
        Limits to buy levels below price and sell levels above price.
        """
        async with get_async_session() as db:
            levels = (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid.id,
                    GridLevel.status == GridLevelStatus.pending,
                )
            )).all()

        threshold = current_price * proximity_pct / 100
        result = []
        for lv in levels:
            dist = abs(lv.price - current_price)
            if dist <= threshold:
                # Only place if direction is correct
                if lv.side == OrderSide.BUY and lv.price < current_price:
                    result.append(lv)
                elif lv.side == OrderSide.SELL and lv.price > current_price:
                    result.append(lv)
        return result

    async def get_open_levels(self, grid: GridState) -> List[GridLevel]:
        """Return levels with open positions (filled, awaiting counter-exit)."""
        async with get_async_session() as db:
            return (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid.id,
                    GridLevel.status.in_([GridLevelStatus.open, GridLevelStatus.filled]),
                )
            )).all()

    async def count_open_levels(self, grid: GridState) -> int:
        levels = await self.get_open_levels(grid)
        return len(levels)

    # ── State mutations ──────────────────────────────────────────────────────

    async def mark_level_open(self, level_id: str, position_id: str) -> None:
        """Called after an order is successfully placed for this level."""
        async with get_async_session() as db:
            lv = await db.get(GridLevel, level_id)
            if lv:
                lv.status = GridLevelStatus.open
                lv.position_id = position_id
                # Increment total_invested on the parent grid
                grid = await db.get(GridState, lv.grid_id)
                if grid:
                    grid.total_invested = (grid.total_invested or 0) + (lv.quantity * lv.price)
                await db.commit()

    async def on_fill(
        self,
        level_id: str,
        fill_price: float,
        position_id: str,
    ) -> Optional[GridLevel]:
        """
        Mark a level as filled and create the counter-level order.
        Returns the new counter GridLevel (or None if at grid edge).
        """
        async with get_async_session() as db:
            lv = await db.get(GridLevel, level_id)
            if not lv:
                return None

            lv.status = GridLevelStatus.filled
            lv.entry_price = fill_price
            lv.position_id = position_id
            await db.flush()

            grid = await db.get(GridState, lv.grid_id)
            if not grid:
                await db.commit()
                return None

            # Counter level = one spacing step in the opposite direction
            spacing = (grid.grid_high - grid.grid_low) / max(grid.grid_levels - 1, 1)
            if lv.side == OrderSide.BUY:
                counter_price = lv.price + spacing
                counter_side = OrderSide.SELL
            else:
                counter_price = lv.price - spacing
                counter_side = OrderSide.BUY

            # Don't place counter outside grid bounds
            if counter_price < grid.grid_low * 0.995 or counter_price > grid.grid_high * 1.005:
                logger.debug(f"Grid {grid.id[:8]}: counter level {counter_price:.4f} outside bounds, skipping")
                await db.commit()
                return None

            counter = GridLevel(
                grid_id=grid.id,
                level_index=lv.level_index,  # re-use same index slot
                price=round(counter_price, 8),
                side=counter_side,
                status=GridLevelStatus.pending,
                quantity=lv.quantity,
            )
            db.add(counter)
            lv.status = GridLevelStatus.counter_placed
            await db.commit()
            await db.refresh(counter)
            logger.info(
                f"Grid fill: {grid.symbol} level {lv.level_index} filled @ {fill_price:.4f} "
                f"→ counter {counter_side.value} @ {counter_price:.4f}"
            )
            return counter

    async def close_level(
        self,
        level_id: str,
        exit_price: float,
    ) -> Optional[float]:
        """Mark a filled level as closed and record its P&L. Returns the P&L."""
        async with get_async_session() as db:
            lv = await db.get(GridLevel, level_id)
            if not lv or not lv.entry_price:
                return None

            if lv.side == OrderSide.BUY:
                pnl = (exit_price - lv.entry_price) * lv.quantity
            else:
                pnl = (lv.entry_price - exit_price) * lv.quantity

            lv.exit_price = exit_price
            lv.pnl = round(pnl, 6)
            lv.status = GridLevelStatus.closed

            grid = await db.get(GridState, lv.grid_id)
            if grid:
                grid.realized_pnl = round((grid.realized_pnl or 0) + pnl, 6)
                grid.total_invested = max(0.0, (grid.total_invested or 0) - (lv.quantity * (lv.entry_price or lv.price)))

            await db.commit()
            return pnl

    # ── Exit conditions ──────────────────────────────────────────────────────

    def check_exit_conditions(
        self,
        grid: GridState,
        current_price: float,
        current_atr: float,
        sma_20: Optional[float] = None,
        sma_50: Optional[float] = None,
        prev_sma_20: Optional[float] = None,
        prev_sma_50: Optional[float] = None,
    ) -> Optional[str]:
        """
        Return a cancel reason string if the grid should be torn down, else None.

        Exit triggers:
        1. Price breaks outside grid bounds by PRICE_BREAK_BUFFER
        2. ATR spikes to > ATR_SPIKE_MULT × regime_ATR (volatility expansion)
        3. SMA20/SMA50 crossover detected (trend emerging)
        """
        low_break = grid.grid_low * (1 - PRICE_BREAK_BUFFER)
        high_break = grid.grid_high * (1 + PRICE_BREAK_BUFFER)

        if current_price < low_break:
            return f"Price {current_price:.4f} broke below grid low {grid.grid_low:.4f} (buffer {PRICE_BREAK_BUFFER:.0%})"

        if current_price > high_break:
            return f"Price {current_price:.4f} broke above grid high {grid.grid_high:.4f} (buffer {PRICE_BREAK_BUFFER:.0%})"

        if grid.regime_atr and current_atr > ATR_SPIKE_MULT * grid.regime_atr:
            return (
                f"ATR spike: current {current_atr:.4f} > "
                f"{ATR_SPIKE_MULT}× regime ATR {grid.regime_atr:.4f} — volatility expansion"
            )

        if (sma_20 and sma_50 and prev_sma_20 and prev_sma_50):
            bullish_cross = sma_20 > sma_50 and prev_sma_20 <= prev_sma_50
            bearish_cross = sma_20 < sma_50 and prev_sma_20 >= prev_sma_50
            if bullish_cross:
                return "SMA20 crossed above SMA50 — bullish trend detected, grid cancelled"
            if bearish_cross:
                return "SMA20 crossed below SMA50 — bearish trend detected, grid cancelled"

        return None

    # ── Cancel / pause / resume ──────────────────────────────────────────────

    async def cancel_grid(
        self, grid_id: str, reason: str
    ) -> Tuple[GridState, List[str]]:
        """
        Mark grid cancelled. Returns (updated GridState, list of open position_ids to close).
        """
        async with get_async_session() as db:
            grid = await db.get(GridState, grid_id)
            if not grid:
                return None, []

            grid.status = GridStatus.cancelled
            grid.cancel_reason = reason

            # Collect position IDs for open levels to allow caller to close them
            levels = (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid_id,
                    GridLevel.status.in_([
                        GridLevelStatus.open,
                        GridLevelStatus.filled,
                        GridLevelStatus.counter_placed,
                    ]),
                )
            )).all()

            position_ids = [lv.position_id for lv in levels if lv.position_id]

            # Mark all pending levels cancelled
            for lv in (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid_id,
                    GridLevel.status == GridLevelStatus.pending,
                )
            )).all():
                lv.status = GridLevelStatus.closed

            await db.commit()
            await db.refresh(grid)
            logger.info(f"Grid {grid_id[:8]} ({grid.symbol}) cancelled: {reason}")
            return grid, position_ids

    async def pause_grid(self, grid_id: str) -> Optional[GridState]:
        async with get_async_session() as db:
            grid = await db.get(GridState, grid_id)
            if grid and grid.status == GridStatus.active:
                grid.status = GridStatus.paused
                await db.commit()
                await db.refresh(grid)
            return grid

    async def resume_grid(self, grid_id: str) -> Optional[GridState]:
        async with get_async_session() as db:
            grid = await db.get(GridState, grid_id)
            if grid and grid.status == GridStatus.paused:
                grid.status = GridStatus.active
                await db.commit()
                await db.refresh(grid)
            return grid

    # ── Rebalance ────────────────────────────────────────────────────────────

    async def rebalance_grid(
        self,
        grid_id: str,
        current_price: float,
        atr: float,
        bb_upper: float,
        bb_lower: float,
    ) -> Optional[GridState]:
        """
        Shift the grid centre if price has drifted > 50% of range from centre.
        Cancels pending levels outside the new range and creates new pending levels.
        Does NOT touch filled/open levels.
        """
        async with get_async_session() as db:
            grid = await db.get(GridState, grid_id)
            if not grid or grid.status != GridStatus.active:
                return grid

            grid_centre = (grid.grid_low + grid.grid_high) / 2
            half_range = (grid.grid_high - grid.grid_low) / 2
            drift = abs(current_price - grid_centre)

            if drift < 0.5 * half_range:
                return grid  # within acceptable range, no rebalance needed

            # Compute new bounds centred on current price
            atr_pct = atr / current_price if current_price else 0
            new_low = max(bb_lower * (1 - DEFAULT_LEVEL_BUFFER), current_price * (1 - ATR_MULT_RANGE * atr_pct))
            new_high = min(bb_upper * (1 + DEFAULT_LEVEL_BUFFER), current_price * (1 + ATR_MULT_RANGE * atr_pct))

            if new_high <= new_low:
                return grid

            spacing = (new_high - new_low) / max(grid.grid_levels - 1, 1)

            # Cancel pending levels outside new range
            pending = (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid_id,
                    GridLevel.status == GridLevelStatus.pending,
                )
            )).all()

            cancelled = 0
            for lv in pending:
                if lv.price < new_low or lv.price > new_high:
                    lv.status = GridLevelStatus.closed
                    cancelled += 1

            # Add new levels in the new range (only where no pending/open level exists)
            existing_prices = {lv.price for lv in (await db.scalars(
                select(GridLevel).where(
                    GridLevel.grid_id == grid_id,
                    GridLevel.status.in_([
                        GridLevelStatus.pending, GridLevelStatus.open, GridLevelStatus.filled
                    ]),
                )
            )).all()}

            added = 0
            capital_per_level = (grid.grid_high - grid.grid_low) * grid.grid_levels * 0.01  # rough
            for i in range(grid.grid_levels):
                lp = new_low + i * spacing
                if any(abs(lp - ep) < spacing * 0.3 for ep in existing_prices):
                    continue  # close enough to existing level, skip
                side = OrderSide.BUY if lp < current_price else OrderSide.SELL
                qty = capital_per_level / lp if lp > 0 else 0
                db.add(GridLevel(
                    grid_id=grid_id,
                    level_index=i,
                    price=round(lp, 8),
                    side=side,
                    status=GridLevelStatus.pending,
                    quantity=round(qty, 8),
                ))
                added += 1

            grid.grid_low = round(new_low, 8)
            grid.grid_high = round(new_high, 8)
            grid.grid_spacing_pct = round(spacing / current_price * 100, 4)
            await db.commit()
            await db.refresh(grid)
            logger.info(
                f"Grid {grid_id[:8]} rebalanced: range=[{new_low:.4f}, {new_high:.4f}] "
                f"cancelled={cancelled} added={added}"
            )
            return grid

    # ── Summary ──────────────────────────────────────────────────────────────

    async def get_grid_summary(self, agent_id: str, symbol: Optional[str] = None) -> dict:
        """Return a status dict for active grids owned by this agent."""
        async with get_async_session() as db:
            q = select(GridState).where(GridState.agent_id == agent_id)
            if symbol:
                q = q.where(GridState.symbol == symbol)
            grids = (await db.scalars(q.order_by(GridState.created_at.desc()))).all()

            active_grid = None
            historical_grids = []

            for grid in grids:
                levels = (await db.scalars(
                    select(GridLevel).where(GridLevel.grid_id == grid.id)
                )).all()

                by_status: dict[str, int] = {}
                for lv in levels:
                    by_status[lv.status.value] = by_status.get(lv.status.value, 0) + 1

                entry = {
                    "grid_id": grid.id,
                    "symbol": grid.symbol,
                    "status": grid.status.value,
                    "grid_low": grid.grid_low,
                    "grid_high": grid.grid_high,
                    "grid_levels": grid.grid_levels,
                    "grid_spacing_pct": grid.grid_spacing_pct,
                    "total_invested": round(grid.total_invested or 0, 2),
                    "realized_pnl": round(grid.realized_pnl or 0, 2),
                    "level_counts": by_status,
                    "open_levels": by_status.get("open", 0),
                    "fill_rate": round(
                        by_status.get("filled", 0) / max(len(levels), 1) * 100, 1
                    ),
                    "cancel_reason": grid.cancel_reason,
                    "created_at": grid.created_at.isoformat() if grid.created_at else None,
                    "levels": [
                        {
                            "level_index": lv.level_index,
                            "price": lv.price,
                            "side": lv.side.value,
                            "status": lv.status.value,
                            "quantity": lv.quantity,
                            "entry_price": lv.entry_price,
                            "exit_price": lv.exit_price,
                            "pnl": lv.pnl,
                        }
                        for lv in sorted(levels, key=lambda x: x.level_index)
                    ],
                }

                if grid.status in (GridStatus.active, GridStatus.paused):
                    active_grid = entry
                else:
                    historical_grids.append(entry)

            return {
                "agent_id": agent_id,
                "active_grid": active_grid,
                "historical_grids": historical_grids,
            }


# Global singleton
grid_engine = GridEngine()
