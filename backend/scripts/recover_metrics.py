"""
Recover Agent Metrics from Trade History
=========================================
Use this when the agent_metric_records table is empty (e.g. after migrating
to a new database that has trades but no pre-computed metrics).

Rebuilds AgentMetricRecord rows from closed trades and agent_run_records
so the scheduler can warm its in-memory metrics without starting from zero.

Usage:
    docker compose exec backend python scripts/recover_metrics.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func as sa_func, case as _case
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import get_async_session
from app.models import Trade, AgentRunRecord, AgentMetricRecord, OrderStatus, ArchivedTrade


async def recover_metrics():
    print("=" * 60)
    print("Agent Metrics Recovery")
    print("=" * 60)

    # ── Step 1: Recover metrics from closed trades (live + archived) ─────
    async with get_async_session() as db:
        # Closed trades grouped by agent_id + is_paper
        rows = await db.execute(
            select(
                Trade.agent_id,
                Trade.is_paper,
                sa_func.count(Trade.id).label("trade_count"),
                sa_func.sum(Trade.total).label("total_volume"),
                sa_func.sum(Trade.fee).label("total_fees"),
            )
            .where(
                Trade.status == OrderStatus.FILLED,
                Trade.agent_id.isnot(None),
            )
            .group_by(Trade.agent_id, Trade.is_paper)
        )
        trade_stats = {f"{r.agent_id}:{r.is_paper}": r for r in rows}

        # Also include archived trades (preserved across paper trading resets)
        try:
            arch_rows = await db.execute(
                select(
                    ArchivedTrade.agent_id,
                    sa_func.count(ArchivedTrade.id).label("trade_count"),
                    sa_func.sum(ArchivedTrade.total).label("total_volume"),
                    sa_func.sum(ArchivedTrade.fee).label("total_fees"),
                )
                .where(ArchivedTrade.agent_id.isnot(None))
                .group_by(ArchivedTrade.agent_id)
            )
            for r in arch_rows:
                key = f"{r.agent_id}:true"  # archived trades are always paper
                if key in trade_stats:
                    ts = trade_stats[key]
                    trade_stats[key] = type('_', (), {
                        'agent_id': r.agent_id, 'is_paper': True,
                        'trade_count': (ts.trade_count or 0) + (r.trade_count or 0),
                        'total_volume': (ts.total_volume or 0) + (r.total_volume or 0),
                        'total_fees': (ts.total_fees or 0) + (r.total_fees or 0),
                    })()
                else:
                    trade_stats[key] = r
        except Exception:
            pass

        print(f"Found {len(trade_stats)} agent/mode groups with closed trades")

        # Winning trades (net_pnl > 0)
        win_rows = await db.execute(
            select(
                Trade.agent_id,
                Trade.is_paper,
                sa_func.count(Trade.id).label("win_count"),
                sa_func.sum(Trade.total - (Trade.quantity * Trade.price)).label("pnl"),
            )
            .where(
                Trade.status == OrderStatus.FILLED,
                Trade.agent_id.isnot(None),
                Trade.total > Trade.quantity * Trade.price,
            )
            .group_by(Trade.agent_id, Trade.is_paper)
        )
        win_stats = {f"{r.agent_id}:{r.is_paper}": r for r in win_rows}

        # Include archived winning trades
        try:
            arch_win_rows = await db.execute(
                select(
                    ArchivedTrade.agent_id,
                    sa_func.count(ArchivedTrade.id).label("win_count"),
                    sa_func.sum(ArchivedTrade.total - (ArchivedTrade.quantity * ArchivedTrade.price)).label("pnl"),
                )
                .where(
                    ArchivedTrade.agent_id.isnot(None),
                    ArchivedTrade.total > ArchivedTrade.quantity * ArchivedTrade.price,
                )
                .group_by(ArchivedTrade.agent_id)
            )
            for r in arch_win_rows:
                key = f"{r.agent_id}:true"
                if key in win_stats:
                    ws = win_stats[key]
                    win_stats[key] = type('_', (), {
                        'agent_id': r.agent_id, 'is_paper': True,
                        'win_count': (ws.win_count or 0) + (r.win_count or 0),
                        'pnl': (ws.pnl or 0) + (r.pnl or 0),
                    })()
                else:
                    win_stats[key] = r
        except Exception:
            pass

        # ── Step 2: Recover run counts and PnL from agent_run_records ─────
        run_rows = await db.execute(
            select(
                AgentRunRecord.agent_id,
                AgentRunRecord.use_paper,
                sa_func.count(AgentRunRecord.id).label("total_runs"),
                sa_func.sum(_case((AgentRunRecord.executed == True, 1), else_=0)).label("successful_runs"),
                sa_func.sum(AgentRunRecord.pnl).label("total_pnl_from_runs"),
                sa_func.count(AgentRunRecord.pnl).label("closed_trades_from_runs"),
            )
            .where(AgentRunRecord.agent_id.isnot(None))
            .group_by(AgentRunRecord.agent_id, AgentRunRecord.use_paper)
        )
        run_stats = {f"{r.agent_id}:{r.use_paper}": r for r in run_rows}
        print(f"Found {len(run_stats)} agent/mode groups with run records")

        # ── Step 3: Merge and upsert ─────────────────────────────────────
        all_keys = set(trade_stats.keys()) | set(run_stats.keys())
        upserted = 0
        skipped = 0

        for key in sorted(all_keys):
            agent_id, is_paper_str = key.split(":")
            is_paper = is_paper_str.lower() == "true"
            ts = trade_stats.get(key)
            rs = run_stats.get(key)
            ws = win_stats.get(key)

            total_trades = int(ts.trade_count) if ts else 0
            winning = int(ws.win_count) if ws else 0
            # PnL from run records is authoritative (trades store individual fills, not round-trip PnL)
            total_pnl = float(rs.total_pnl_from_runs) if rs and rs.total_pnl_from_runs else 0.0
            total_runs = int(rs.total_runs) if rs else total_trades
            successful_runs = int(rs.successful_runs) if rs else total_trades
            win_rate = round(winning / total_trades, 3) if total_trades > 0 else None
            avg_pnl = round(total_pnl / total_trades, 2) if total_trades > 0 else 0.0

            # Skip if this would write all zeros (no data to recover)
            if total_trades == 0 and total_runs == 0:
                skipped += 1
                continue

            stmt = pg_insert(AgentMetricRecord).values(
                agent_id=agent_id,
                is_paper=is_paper,
                total_runs=total_runs,
                successful_runs=successful_runs,
                failed_runs=max(0, total_runs - successful_runs),
                actual_trades=total_trades,
                winning_trades=winning,
                total_pnl=round(total_pnl, 2),
                win_rate=win_rate,
                avg_pnl=avg_pnl,
                buy_signals=0,
                sell_signals=0,
                hold_signals=0,
            ).on_conflict_do_update(
                index_elements=["agent_id", "is_paper"],
                set_=dict(
                    total_runs=total_runs,
                    successful_runs=successful_runs,
                    failed_runs=max(0, total_runs - successful_runs),
                    actual_trades=total_trades,
                    winning_trades=winning,
                    total_pnl=round(total_pnl, 2),
                    win_rate=win_rate,
                    avg_pnl=avg_pnl,
                ),
            )
            await db.execute(stmt)
            upserted += 1

        await db.commit()

    print(f"\n✓ Recovered metrics for {upserted} agent/mode combinations")
    if skipped:
        print(f"  Skipped {skipped} empty groups")
    print("=" * 60)
    print("Done. Restart the backend for the scheduler to pick up the recovered metrics:")
    print("  docker restart phemex-ai-trader-backend")
    print("=" * 60)


async def main():
    try:
        await recover_metrics()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
