# AgentMetricRecord Modifications - Complete Search Results

## Overview
`AgentMetricRecord` is the ORM model that persists agent performance metrics to the database. It tracks:
- `actual_trades` - closed positions with PnL set
- `winning_trades` - closed positions with pnl > 0
- `total_pnl` - sum of all closed-trade PnL
- Plus signal counts, win_rate, and other performance metrics

---

## 1. Model Definition

### File: [backend/app/models/__init__.py](backend/app/models/__init__.py#L285-L306)

```python
class AgentMetricRecord(Base):
    __tablename__ = "agent_metric_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    is_paper = Column(Boolean, default=True)
    total_runs = Column(Integer, default=0)
    successful_runs = Column(Integer, default=0)
    failed_runs = Column(Integer, default=0)
    actual_trades = Column(Integer, default=0)   # closed positions only (pnl set)
    winning_trades = Column(Integer, default=0)  # closed positions with pnl > 0
    total_pnl = Column(Float, default=0.0)
    buy_signals = Column(Integer, default=0)
    sell_signals = Column(Integer, default=0)
    hold_signals = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_pnl = Column(Float, default=0.0)
    last_run = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "is_paper", name="uq_agent_metric_agent_mode"),
    )
```

---

## 2. In-Memory Metrics DataClass

### File: [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L67-L88)

```python
@dataclass
class AgentMetrics:
    agent_id: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    actual_trades: int = 0    # closed positions only (pnl was set)
    winning_trades: int = 0   # closed positions where pnl > 0
    total_pnl: float = 0.0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    last_run: Optional[datetime] = None
    win_rate: Optional[float] = None  # None until at least one trade closes
    avg_pnl: float = 0.0
```

This is the in-memory version that gets synced to the database.

---

## 3. Metrics Creation & Initial Seeding

### 3.1 Bootstrap from Backtest Results
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L745-L755)

When an agent is first initialized, metrics are seeded from backtest results:

```python
# Seed the pre-trade backtest cache so the first trade doesn't re-run it
self._backtest_cache[f"{agent_id}:{symbol}"] = (result, datetime.now())

metrics = self._agent_metrics[agent_id]
metrics.win_rate = result.win_rate
# Do NOT seed total_pnl from backtest — that would show fake historical P&L
# before any live trade has been placed. Only the win_rate prior is useful here.
```

**Important Note:** `total_pnl` is NOT seeded from backtest to avoid showing fake historical P&L before any live trade.

---

## 4. Metrics Accumulation During Execution

### 4.1 Per-Run Signal & Result Recording
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L4500-4517)

This is where metrics are **incremented in memory** after each agent run:

```python
def _record_agent_run(self, run: AgentRun, signal: str, pnl: Optional[float], use_paper: bool):
    agent_id = run.agent_id
    if agent_id not in self._agent_metrics:
        self._agent_metrics[agent_id] = AgentMetrics(agent_id=agent_id)

    metrics = self._agent_metrics[agent_id]
    metrics.total_runs += 1
    metrics.last_run = datetime.now(timezone.utc)

    if signal == 'error':
        metrics.failed_runs += 1
    else:
        metrics.successful_runs += 1
    if signal == 'buy':
        metrics.buy_signals += 1
    elif signal == 'sell':
        metrics.sell_signals += 1
    else:
        metrics.hold_signals += 1
    
    # ─── CRITICAL: actual_trades, winning_trades, total_pnl accumulation ───
    if pnl is not None:
        metrics.actual_trades += 1
        if pnl > 0:
            metrics.winning_trades += 1
        metrics.total_pnl += pnl
        metrics.avg_pnl = metrics.total_pnl / metrics.actual_trades if metrics.actual_trades > 0 else 0
    
    # Win rate = winning closed trades / all closed trades
    if metrics.actual_trades > 0:
        metrics.win_rate = metrics.winning_trades / metrics.actual_trades

    asyncio.create_task(self._persist_run(run, metrics, strategy_type, use_paper))
```

**Key Points:**
- `actual_trades` increments when PnL is recorded
- `winning_trades` increments when PnL > 0
- `total_pnl` is accumulated (not replaced)
- `win_rate` is calculated as `winning_trades / actual_trades`

### 4.2 Position Closure (Trade PnL Accumulation)
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L1745-1760)

When positions close, PnL is recorded:

```python
if pos.agent_id and pos.agent_id in self._agent_metrics:
    m = self._agent_metrics[pos.agent_id]
    m.total_pnl += pnl  # ← CRITICAL: increments total_pnl
    # Determine exit reason before recording
    _exit_type_pre = "trailing-stop" if "Trailing" in check.reason else (
        "take-profit" if "Take-profit" in check.reason else "stop-loss"
    )
    # Add this close to the agent_runs buffer so win rate is consistent
    self._agent_runs.append(AgentRun(
        # ... other fields ...
        pnl=pnl,
        exit_reason=_exit_type_pre,
    ))
```

---

## 5. Database Persistence (INSERT/UPDATE)

### 5.1 Upsert to AgentMetricRecord
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L4540-4575)

This persists in-memory metrics to the database using PostgreSQL upsert (INSERT ... ON CONFLICT ... DO UPDATE):

```python
async def _persist_run(
    self,
    run: AgentRun,
    metrics: AgentMetrics,
    strategy_type: Optional[str],
    use_paper: bool,
):
    from app.models import AgentRunRecord, AgentMetricRecord
    try:
        async with get_async_session() as db:
            db.add(AgentRunRecord(
                agent_id=run.agent_id,
                timestamp=run.timestamp,
                symbol=run.symbol,
                signal=run.signal,
                confidence=run.confidence,
                price=run.price,
                executed=run.executed,
                pnl=run.pnl,
                error=run.error,
                strategy_type=strategy_type,
                use_paper=use_paper,
            ))
            
            # ─── CRITICAL: Upsert metrics to database ───
            stmt = pg_insert(AgentMetricRecord).values(
                agent_id=metrics.agent_id,
                is_paper=_is_paper_mode(),
                total_runs=metrics.total_runs,
                successful_runs=metrics.successful_runs,
                failed_runs=metrics.failed_runs,
                actual_trades=metrics.actual_trades,      # ← Updated here
                winning_trades=metrics.winning_trades,    # ← Updated here
                total_pnl=metrics.total_pnl,              # ← Updated here
                buy_signals=metrics.buy_signals,
                sell_signals=metrics.sell_signals,
                hold_signals=metrics.hold_signals,
                win_rate=metrics.win_rate,
                avg_pnl=metrics.avg_pnl,
                last_run=metrics.last_run,
            ).on_conflict_do_update(
                index_elements=["agent_id", "is_paper"],
                set_=dict(
                    total_runs=metrics.total_runs,
                    successful_runs=metrics.successful_runs,
                    failed_runs=metrics.failed_runs,
                    actual_trades=metrics.actual_trades,
                    winning_trades=metrics.winning_trades,
                    total_pnl=metrics.total_pnl,
                    buy_signals=metrics.buy_signals,
                    sell_signals=metrics.sell_signals,
                    hold_signals=metrics.hold_signals,
                    win_rate=metrics.win_rate,
                    avg_pnl=metrics.avg_pnl,
                    last_run=metrics.last_run,
                ),
            )
            await db.execute(stmt)
            await db.commit()
```

**Operation Type:** PostgreSQL UPSERT (INSERT ... ON CONFLICT ... DO UPDATE)
**Conflict Key:** `(agent_id, is_paper)` unique constraint
**Sync Frequency:** After each agent run execution

---

## 6. Loading Metrics from Database

### 6.1 Restore on Scheduler Startup
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L575-610)

When the scheduler restarts, it reloads metrics from the database:

```python
async def _load_metrics_from_db(self):
    """Restore agent metrics from DB into memory so performance history
    survives scheduler restarts and doesn't reset to zero.
    Only loads records matching the current trading mode (paper or live)."""
    try:
        from app.database import get_async_session
        from app.models import AgentMetricRecord
        from sqlalchemy import select

        current_is_paper = _is_paper_mode()

        async with get_async_session() as session:
            result = await session.execute(
                select(AgentMetricRecord).where(
                    AgentMetricRecord.is_paper == current_is_paper
                )
            )
            records = result.scalars().all()

        loaded = 0
        for rec in records:
            self._agent_metrics[rec.agent_id] = AgentMetrics(
                agent_id=rec.agent_id,
                total_runs=rec.total_runs or 0,
                successful_runs=rec.successful_runs or 0,
                failed_runs=rec.failed_runs or 0,
                actual_trades=rec.actual_trades or 0,
                winning_trades=rec.winning_trades or 0,
                total_pnl=rec.total_pnl or 0.0,
                buy_signals=rec.buy_signals or 0,
                sell_signals=rec.sell_signals or 0,
                hold_signals=rec.hold_signals or 0,
                win_rate=rec.win_rate if rec.win_rate is not None else None,
                avg_pnl=rec.avg_pnl or 0.0,
                last_run=rec.last_run,
            )
```

### 6.2 Get Single Agent Metrics
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L4582-4610)

```python
async def get_agent_metrics(self, agent_id: str) -> Optional[AgentMetrics]:
    from app.models import AgentMetricRecord
    try:
        async with get_async_session() as db:
            row = await db.scalar(
                select(AgentMetricRecord).where(
                    AgentMetricRecord.agent_id == agent_id,
                    AgentMetricRecord.is_paper == _is_paper_mode(),
                )
            )
            if row:
                return AgentMetrics(
                    agent_id=row.agent_id,
                    total_runs=row.total_runs,
                    successful_runs=row.successful_runs,
                    failed_runs=row.failed_runs,
                    actual_trades=row.actual_trades or 0,
                    winning_trades=row.winning_trades or 0,
                    total_pnl=row.total_pnl,
                    buy_signals=row.buy_signals,
                    sell_signals=row.sell_signals,
                    hold_signals=row.hold_signals,
                    win_rate=row.win_rate,
                    avg_pnl=row.avg_pnl,
                    last_run=row.last_run,
                )
    except Exception as e:
        logger.error(f"Failed to fetch agent metrics from DB: {e}")
    return self._agent_metrics.get(agent_id)
```

### 6.3 Get All Agent Metrics
**File:** [backend/app/services/agent_scheduler.py](backend/app/services/agent_scheduler.py#L4612-4630)

```python
async def get_all_metrics(self) -> List[AgentMetrics]:
    from app.models import AgentMetricRecord
    try:
        async with get_async_session() as db:
            rows = (await db.execute(
                select(AgentMetricRecord).where(
                    AgentMetricRecord.is_paper == _is_paper_mode()
                )
            )).scalars().all()
            if rows:
                return [
                    AgentMetrics(
                        agent_id=r.agent_id,
                        total_runs=r.total_runs,
                        successful_runs=r.successful_runs,
                        failed_runs=r.failed_runs,
                        actual_trades=r.actual_trades or 0,
                        winning_trades=r.winning_trades or 0,
                        total_pnl=r.total_pnl,
                        buy_signals=r.buy_signals,
                        sell_signals=r.sell_signals,
                        hold_signals=r.hold_signals,
                        win_rate=r.win_rate,
                        avg_pnl=r.avg_pnl,
                        last_run=r.last_run,
                    )
                    for r in rows
                ]
    except Exception as e:
        logger.error(f"Failed to fetch all agent metrics from DB: {e}")
    return list(self._agent_metrics.values())
```

---

## 7. Metrics Aggregation & Queries

### 7.1 Gate Autopilot - Sum Winning & Actual Trades
**File:** [backend/app/services/gate_autopilot.py](backend/app/services/gate_autopilot.py#L150-171)

Queries aggregate metrics across all agents:

```python
async def _gather_metrics(self) -> dict:
    """Pull rolling 7-day win rate, today's PnL, consecutive loss count, and daily fees."""
    from app.database import get_async_session
    from app.models import AgentMetricRecord, AgentRunRecord, RiskAssessmentRecord, Trade, OrderStatus
    from sqlalchemy import select, func as sqlfunc

    metrics = {
        "win_rate":               0.5,    # default to neutral
        "total_trades":           0,
        # ... other fields ...
    }

    try:
        async with get_async_session() as db:
            # ── Aggregate across all agent metric records ─────────────────
            row = await db.execute(
                select(
                    sqlfunc.sum(AgentMetricRecord.winning_trades).label("wins"),  # ← Queries winning_trades
                    sqlfunc.sum(AgentMetricRecord.actual_trades).label("total"),   # ← Queries actual_trades
                )
            )
            agg = row.one_or_none()
            if agg and agg.total and agg.total > 0:
                metrics["win_rate"]     = (agg.wins or 0) / agg.total
                metrics["total_trades"] = int(agg.total)
```

**Query Type:** Aggregate SUM across all agent metrics
**Metrics Queried:** `winning_trades`, `actual_trades`

### 7.2 Trader Performance - Per-Agent Metrics
**File:** [backend/app/api/routes/traders.py](backend/app/api/routes/traders.py#L251-283)

```python
@router.get("/{trader_id}/performance", response_model=TraderPerformanceResponse)
async def get_trader_performance(trader_id: str):
    """Get aggregated performance for a trader, filtered by current trading mode."""
    from sqlalchemy import select
    from app.models import Trader, Agent, AgentMetricRecord
    from app.services.paper_trading import paper_trading
    from app.services.trading_service import _is_paper_mode

    is_paper = _is_paper_mode()

    async with get_async_session() as db:
        # ... trader and agent queries ...

        # Fetch metrics for the CURRENT mode only
        metrics_result = await db.execute(
            select(AgentMetricRecord).where(
                AgentMetricRecord.agent_id.in_(agent_ids),
                AgentMetricRecord.is_paper == is_paper,
            )
        ) if agent_ids else None
        metrics = metrics_result.scalars().all() if metrics_result else []

    # Use closed-trade DB records as authoritative source for trade counts and win rate
    if is_paper:
        db_perf = await paper_trading.get_agent_performance_from_db()
    else:
        # For live, AgentMetricRecord is the authoritative source (updated by monitor loop)
        db_perf = {
            m.agent_id: {
                "net_pnl": m.total_pnl or 0.0,    # ← Uses total_pnl
                "win_rate": m.win_rate,
                "total_trades": m.actual_trades or 0,  # ← Uses actual_trades
            }
            for m in metrics
        }
```

**Query Type:** SELECT by agent_id + is_paper mode filter
**Metrics Retrieved:** `total_pnl`, `actual_trades`, `win_rate`

---

## 8. Daily Report Service

**File:** [backend/app/services/daily_report.py](backend/app/services/daily_report.py#L18-20)

AgentMetricRecord is imported but primarily used for querying metrics for reporting purposes (no direct updates):

```python
from app.models import (
    DailyReport,
    AgentRunRecord,
    AgentMetricRecord,  # ← Imported for metric queries
    TeamChatMessageRecord,
)
```

---

## Summary Table: Modification Locations

| Location | Operation | Fields Modified | Frequency |
|----------|-----------|-----------------|-----------|
| `agent_scheduler.py:4500-4517` | In-memory accumulation | `actual_trades`, `winning_trades`, `total_pnl` | Per agent run |
| `agent_scheduler.py:1745-1760` | Position close PnL | `total_pnl` | Per position close |
| `agent_scheduler.py:4540-4575` | Database UPSERT | All metric fields | After each run (persisted) |
| `agent_scheduler.py:575-610` | Database SELECT (Load) | N/A (read-only) | On scheduler startup |
| `agent_scheduler.py:4582-4610` | Database SELECT (Get single) | N/A (read-only) | On-demand query |
| `agent_scheduler.py:4612-4630` | Database SELECT (Get all) | N/A (read-only) | On-demand query |
| `gate_autopilot.py:150-171` | Database SUM aggregate | N/A (read-only) | Per gate check cycle |
| `traders.py:251-283` | Database SELECT (by trader) | N/A (read-only) | Per API request |

---

## Key Insights

1. **Dual-layer tracking:** Metrics exist in memory (`AgentMetrics` dataclass) and are synced to database after each run
2. **Upsert strategy:** Uses PostgreSQL `INSERT ... ON CONFLICT` for atomic updates
3. **No backtest seeding:** `total_pnl` is NOT initialized from backtest (only `win_rate` is)
4. **PnL accumulation:** Happens both at trade execution AND position closure
5. **Filtering by mode:** All queries filter by `is_paper` to separate paper trading from live
6. **Read-only after persistence:** Once persisted, metrics are mostly read for queries and aggregation
