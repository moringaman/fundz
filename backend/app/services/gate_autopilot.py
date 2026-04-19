"""Gate Autopilot — dynamically adjusts TradingGates based on performance, market
session, and time of day.

When enabled the autopilot runs every 30 minutes, gathers rolling performance
metrics from AgentMetricRecord and RiskAssessmentRecord, classifies the current
market regime, and applies a bounded incremental adjustment to the live gate
thresholds.  All changes are persisted to the DB through the existing settings
pathway so they survive restarts.

Regime classification
─────────────────────
  AGGRESSIVE  win_rate > 62 % AND today's PnL ≥ 0
  BALANCED    win_rate 48–62 % (or no data yet)
  CAUTIOUS    win_rate 35–48 % OR today's PnL negative
  DEFENSIVE   win_rate < 35 % OR ≥ 3 consecutive losing days
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regime constants ──────────────────────────────────────────────────────────
REGIME_AGGRESSIVE        = "AGGRESSIVE"
REGIME_BALANCED          = "BALANCED"
REGIME_CAUTIOUS          = "CAUTIOUS"
REGIME_DEFENSIVE         = "DEFENSIVE"
# Fee-drag overlays: checked before win-rate regime.
# Severe:   gross_pnl / total_fees < 1.5  (fees eating >40% of gross profit)
# Moderate: gross_pnl / total_fees < 2.5  (fees eating >28% of gross profit)
REGIME_FEE_DRAG_SEVERE   = "FEE_DRAG_SEVERE"
REGIME_FEE_DRAG_MODERATE = "FEE_DRAG_MODERATE"
# High churn: > N trades/hour with poor fee coverage — enforce quality before quantity
REGIME_HIGH_CHURN        = "HIGH_CHURN"

REGIME_COLORS = {
    REGIME_AGGRESSIVE:        "green",
    REGIME_BALANCED:          "accent",
    REGIME_CAUTIOUS:          "amber",
    REGIME_DEFENSIVE:         "red",
    REGIME_FEE_DRAG_SEVERE:   "red",
    REGIME_FEE_DRAG_MODERATE: "amber",
    REGIME_HIGH_CHURN:        "amber",
}

# Fee drag detection thresholds
_FEE_DRAG_MIN_FEES_USD      = 50.0   # don't activate on cold-start noise
_FEE_DRAG_SEVERE_RATIO      = 1.5    # gross PnL / fees < 1.5 → severe
_FEE_DRAG_MODERATE_RATIO    = 2.5    # gross PnL / fees < 2.5 → moderate
_FEE_DRAG_MIN_CLOSED_TRADES = 20     # need meaningful sample before activating

_AUTOPILOT_SETTING_KEY = "gate_autopilot"
_RUN_INTERVAL_SECONDS  = 1800   # 30 minutes


class GateAutopilot:
    """Background service that auto-tunes TradingGates from live metrics."""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._last_regime: str = REGIME_BALANCED
        self._last_reason: str = "Autopilot not yet run"
        self._last_run: Optional[datetime] = None
        self._changes: dict = {}
        self._loaded: bool = False

    # ── Public accessors ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self) -> dict:
        return {
            "enabled":     self._enabled,
            "regime":      self._last_regime,
            "reason":      self._last_reason,
            "last_run":    self._last_run.isoformat() if self._last_run else None,
            "changes":     self._changes,
            "color":       REGIME_COLORS.get(self._last_regime, "accent"),
        }

    @staticmethod
    def _trade_sample_summary(metrics: dict) -> str:
        closed_trades = int(metrics.get("total_trades", 0) or 0)
        executed_runs = int(metrics.get("executed_runs", 0) or 0)
        filled_trades = int(metrics.get("filled_paper_trades", 0) or 0)
        
        if closed_trades > 0:
            return f"{closed_trades} closed trades analysed."
        
        if executed_runs > 0 or filled_trades > 0:
            summary_parts = []
            if closed_trades == 0:
                summary_parts.append("0 closed trades yet")
            if executed_runs > 0:
                summary_parts.append(f"{executed_runs} executed runs")
            if filled_trades > 0:
                summary_parts.append(f"{filled_trades} open positions")
            
            return f"({', '.join(summary_parts)})."
        
        return "Waiting for trading activity..."

    # ── Toggle on/off ─────────────────────────────────────────────────────────

    async def set_enabled(self, enabled: bool) -> dict:
        self._enabled = enabled
        await self._persist_state()
        if enabled:
            logger.info("Gate autopilot ENABLED — will run immediately then every 30 min")
            # Kick off an immediate evaluation without waiting for next cycle
            asyncio.create_task(self._safe_run())
        else:
            logger.info("Gate autopilot DISABLED — gates will no longer be auto-adjusted")
        return self.status()

    # ── Background loop ───────────────────────────────────────────────────────

    async def start_loop(self) -> None:
        """Long-running background task.  Wire into app lifespan."""
        await self._load_state()
        while True:
            await asyncio.sleep(_RUN_INTERVAL_SECONDS)
            if self._enabled:
                await self._safe_run()

    async def _safe_run(self) -> None:
        try:
            await self.run_once()
        except Exception as exc:
            logger.warning(f"Gate autopilot run failed (non-fatal): {exc}")

    # ── Core evaluation ───────────────────────────────────────────────────────

    async def run_once(self) -> dict:
        """Evaluate metrics → classify regime → apply gate adjustments."""
        from app.database import get_async_session
        from app.api.routes.settings import get_trading_gates, TradingGates

        metrics = await self._gather_metrics()
        regime  = self._classify_regime(metrics)
        gates   = get_trading_gates()
        new_gates, changes, reason = self._compute_adjustments(gates, regime, metrics)

        # Only write to DB if something actually changed
        if changes:
            from app.api.routes.settings import _runtime_trading_gates, _save_setting
            import app.api.routes.settings as _settings_mod
            _settings_mod._runtime_trading_gates = new_gates
            await _save_setting("trading_gates", new_gates.model_dump())
            logger.info(f"Gate autopilot [{regime}] applied {len(changes)} adjustment(s): {changes}")
        else:
            logger.debug(f"Gate autopilot [{regime}] — no changes needed")

        self._last_regime = regime
        self._last_reason = reason
        self._last_run    = datetime.now(timezone.utc)
        self._changes     = changes
        await self._persist_state()
        return self.status()

    # ── Metrics gathering ─────────────────────────────────────────────────────

    async def _gather_metrics(self) -> dict:
        """Pull rolling 7-day win rate, today's PnL, consecutive loss count, and daily fees."""
        from app.database import get_async_session
        from app.models import AgentMetricRecord, AgentRunRecord, RiskAssessmentRecord, Trade, OrderStatus
        from sqlalchemy import select, func as sqlfunc

        metrics = {
            "win_rate":               0.5,    # default to neutral
            "total_trades":           0,
            "executed_runs":          0,
            "filled_paper_trades":    0,
            "daily_pnl":              0.0,
            "consecutive_losing_days": 0,
            "daily_fees":             0.0,
            "daily_fees_pct":         0.0,
            "utc_hour":               datetime.now(timezone.utc).hour,
        }

        try:
            async with get_async_session() as db:
                # ── Count CLOSED trades (where PnL is set) from agent runs ────
                # This correctly reflects only completed trading cycles, not open positions
                closed_trades_result = await db.execute(
                    select(
                        sqlfunc.count(AgentRunRecord.id).label("closed_count"),
                        sqlfunc.sum(sqlfunc.cast(AgentRunRecord.pnl > 0, sqlfunc.Integer)).label("winners"),
                        sqlfunc.avg(AgentRunRecord.pnl).label("avg_pnl"),
                    ).where(AgentRunRecord.pnl.isnot(None))
                )
                closed_row = closed_trades_result.one_or_none()
                if closed_row and closed_row.closed_count and closed_row.closed_count > 0:
                    metrics["total_trades"] = int(closed_row.closed_count)
                    metrics["win_rate"] = (int(closed_row.winners or 0) / closed_row.closed_count) if closed_row.closed_count > 0 else 0.5

                executed_runs = await db.execute(
                    select(sqlfunc.count(AgentRunRecord.id)).where(AgentRunRecord.executed.is_(True))
                )
                metrics["executed_runs"] = int(executed_runs.scalar_one_or_none() or 0)

                filled_paper_trades = await db.execute(
                    select(sqlfunc.count(Trade.id)).where(
                        Trade.user_id == "default-user",
                        Trade.is_paper.is_(True),
                        Trade.status == OrderStatus.FILLED,
                    )
                )
                metrics["filled_paper_trades"] = int(filled_paper_trades.scalar_one_or_none() or 0)

                # ── Today's PnL from the most recent risk assessment ──────────
                now = datetime.now(timezone.utc)
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                result = await db.execute(
                    select(RiskAssessmentRecord.daily_pnl)
                    .where(RiskAssessmentRecord.timestamp >= day_start)
                    .order_by(RiskAssessmentRecord.timestamp.desc())
                    .limit(1)
                )
                pnl_row = result.scalar_one_or_none()
                if pnl_row is not None:
                    metrics["daily_pnl"] = float(pnl_row)

                # ── Consecutive losing days (last 5 daily risk assessments) ───
                results = await db.execute(
                    select(
                        sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp).label("day"),
                        sqlfunc.min(RiskAssessmentRecord.daily_pnl).label("worst_pnl"),
                    )
                    .group_by(sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp))
                    .order_by(sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp).desc())
                    .limit(5)
                )
                days = results.all()
                consecutive = 0
                for day_row in days:
                    if day_row.worst_pnl is not None and day_row.worst_pnl < 0:
                        consecutive += 1
                    else:
                        break
                metrics["consecutive_losing_days"] = consecutive

        except Exception as exc:
            logger.warning(f"Gate autopilot metrics gather failed: {exc}")

        # ── Fetch TRUE UTC-day fees (not lifetime cumulative) ──────────────────
        try:
            now_utc = datetime.now(timezone.utc)
            day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

            async with get_async_session() as db:
                fees_row = await db.execute(
                    select(sqlfunc.coalesce(sqlfunc.sum(Trade.fee), 0.0))
                    .where(
                        Trade.user_id == "default-user",
                        Trade.is_paper.is_(True),
                        Trade.status == OrderStatus.FILLED,
                        Trade.created_at >= day_start,
                    )
                )
                daily_fees = float(fees_row.scalar_one_or_none() or 0.0)

            metrics["daily_fees"] = daily_fees
            # Assume 50k starting capital for % calculation
            metrics["daily_fees_pct"] = (daily_fees / 50000.0) * 100.0
        except Exception as exc:
            logger.debug(f"Failed to gather UTC-day fees: {exc}")

        # ── Lifetime fee drag metrics (gross PnL vs total fees ever paid) ──────
        # Trade model has no pnl column; net PnL lives in AgentRunRecord.pnl.
        # gross_pnl = net_pnl + total_fees  (fees paid are part of gross returns).
        try:
            async with get_async_session() as db:
                # Total fees from all filled paper orders
                fees_row = await db.execute(
                    select(
                        sqlfunc.coalesce(sqlfunc.sum(Trade.fee), 0.0).label("total_fees"),
                    ).where(
                        Trade.user_id == "default-user",
                        Trade.is_paper.is_(True),
                        Trade.status == OrderStatus.FILLED,
                    )
                )
                _total_fees = float((fees_row.one_or_none() or (0.0,))[0] or 0.0)

                # Net PnL and closed trade count from AgentRunRecord
                pnl_row = await db.execute(
                    select(
                        sqlfunc.coalesce(sqlfunc.sum(AgentRunRecord.pnl), 0.0).label("net_pnl"),
                        sqlfunc.count(AgentRunRecord.id).label("closed_count"),
                    ).where(
                        AgentRunRecord.pnl.isnot(None),
                        AgentRunRecord.use_paper.is_(True),
                    )
                )
                pnl_data = pnl_row.one_or_none()
                _net_pnl     = float((pnl_data.net_pnl    if pnl_data else 0.0) or 0.0)
                _trade_count = int((pnl_data.closed_count  if pnl_data else 0)   or 0)

            # gross = price-movement earnings before fees were deducted
            _gross_pnl = _net_pnl + _total_fees
            metrics["total_fees_lifetime"]  = _total_fees
            metrics["gross_realized_pnl"]   = _gross_pnl
            metrics["fee_coverage_ratio"]   = (_gross_pnl / _total_fees) if _total_fees > 0 else None
            metrics["avg_trade_gross_pnl"]  = (_gross_pnl / _trade_count) if _trade_count > 0 else 0.0
            metrics["avg_fee_per_trade"]    = (_total_fees / _trade_count) if _trade_count > 0 else 0.0
            metrics["lifetime_trade_count"] = _trade_count
        except Exception as exc:
            logger.debug(f"Failed to gather fee drag metrics: {exc}")

        # ── Trades executed in the last 60 minutes (churn detection) ──────────────
        try:
            from app.models import AgentRunRecord
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            async with get_async_session() as db:
                recent_result = await db.execute(
                    select(sqlfunc.count(AgentRunRecord.id)).where(
                        AgentRunRecord.executed.is_(True),
                        AgentRunRecord.timestamp >= one_hour_ago,
                    )
                )
                metrics["trades_last_hour"] = int(recent_result.scalar_one_or_none() or 0)
        except Exception as exc:
            metrics["trades_last_hour"] = 0
            logger.debug(f"Failed to gather trades_last_hour: {exc}")

        return metrics


    # ── Regime classifier ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_regime(metrics: dict) -> str:
        wr  = metrics["win_rate"]
        pnl = metrics["daily_pnl"]
        cld = metrics["consecutive_losing_days"]
        n   = metrics["total_trades"]

        # Insufficient data → stay balanced
        if n < 5:
            return REGIME_BALANCED
        # ── High churn detection (checked before fee drag) ──────────────────────────
        # Many trades per hour combined with poor coverage = churn not edge.
        # Trigger BEFORE FEE_DRAG so the regime fires early in the day when
        # the fee budget is still intact but the hourly rate is already high.
        _trades_1h   = metrics.get("trades_last_hour", 0)
        _coverage_hc = metrics.get("fee_coverage_ratio")
        _lifetime_hc = metrics.get("lifetime_trade_count", 0)
        _HIGH_CHURN_TRADES_THRESHOLD = 5   # > 5 trades in last 60 min = churning
        if (
            _trades_1h > _HIGH_CHURN_TRADES_THRESHOLD
            and _coverage_hc is not None
            and _coverage_hc < _FEE_DRAG_MODERATE_RATIO
            and _lifetime_hc >= 5
        ):
            return REGIME_HIGH_CHURN
        # ── Fee drag detection (checked before win-rate regime) ───────────────
        # High-frequency low-profit trading can show an acceptable win rate while
        # fees silently consume the majority of gross returns.  Detect and
        # correct independently of the standard win-rate classification.
        _coverage   = metrics.get("fee_coverage_ratio")
        _lifetime_n = metrics.get("lifetime_trade_count", 0)
        _total_fees = metrics.get("total_fees_lifetime", 0.0)
        if (
            _coverage is not None
            and _lifetime_n >= _FEE_DRAG_MIN_CLOSED_TRADES
            and _total_fees >= _FEE_DRAG_MIN_FEES_USD
        ):
            if _coverage < _FEE_DRAG_SEVERE_RATIO:
                return REGIME_FEE_DRAG_SEVERE
            if _coverage < _FEE_DRAG_MODERATE_RATIO:
                return REGIME_FEE_DRAG_MODERATE

        if cld >= 3:
            return REGIME_DEFENSIVE
        if wr < 0.35:
            return REGIME_DEFENSIVE
        if wr < 0.48 or (pnl < 0 and cld >= 2):
            return REGIME_CAUTIOUS
        if wr > 0.62 and pnl >= 0:
            return REGIME_AGGRESSIVE
        return REGIME_BALANCED

    # ── Adjustment engine ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_adjustments(
        gates,
        regime: str,
        metrics: dict,
    ):
        """Return (new_gates, changes_dict, human_reason)."""
        from app.api.routes.settings import TradingGates

        defaults = TradingGates()   # factory-default values
        d = gates.model_dump()      # start from current live gates
        changes: dict = {}
        cld  = metrics["consecutive_losing_days"]
        wr   = metrics["win_rate"]
        n    = metrics["total_trades"]
        trade_sample_summary = GateAutopilot._trade_sample_summary(metrics)

        def _set(field: str, value: float) -> None:
            old = d.get(field)
            if old != value:
                changes[field] = {"from": old, "to": round(value, 4)}
                d[field] = value

        def _clamp(value: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, value))

        if regime == REGIME_AGGRESSIVE:
            _set("min_entry_confidence",   _clamp(defaults.min_entry_confidence - 0.04, 0.42, 0.65))
            _set("mtf_mixed_penalty",      _clamp(defaults.mtf_mixed_penalty    - 0.05, 0.08, 0.30))
            _set("mtf_opposed_penalty",    _clamp(defaults.mtf_opposed_penalty  - 0.05, 0.15, 0.35))
            _set("ta_penalty_multiplier",  _clamp(defaults.ta_penalty_multiplier - 0.05, 0.25, 0.50))
            _set("dead_zone_penalty",      _clamp(defaults.dead_zone_penalty    - 0.04, 0.04, 0.20))
            _set("sr_proximity_block_pct", _clamp(defaults.sr_proximity_block_pct - 0.0015, 0.0025, 0.015))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades + 5, 20, 60))
            reason = (f"Win rate {wr:.0%} with positive daily PnL → loosened gates to "
                      f"capture more of the current edge. {trade_sample_summary}")

        elif regime == REGIME_BALANCED:
            # Restore to defaults
            for field in (
                "min_entry_confidence", "mtf_mixed_penalty", "mtf_opposed_penalty",
                "ta_penalty_multiplier", "dead_zone_penalty", "sr_proximity_block_pct",
                "circuit_breaker_max_trades",
            ):
                _set(field, getattr(defaults, field))
            reason = (f"Win rate {wr:.0%} is within balanced range → "
                      f"restored gate thresholds to defaults. {trade_sample_summary}")

        elif regime == REGIME_CAUTIOUS:
            _set("min_entry_confidence",   _clamp(defaults.min_entry_confidence + 0.05, 0.50, 0.72))
            _set("mtf_mixed_penalty",      _clamp(defaults.mtf_mixed_penalty    + 0.05, 0.20, 0.40))
            _set("mtf_opposed_penalty",    _clamp(defaults.mtf_opposed_penalty  + 0.05, 0.30, 0.55))
            _set("ta_penalty_multiplier",  _clamp(defaults.ta_penalty_multiplier + 0.05, 0.40, 0.60))
            _set("dead_zone_penalty",      _clamp(defaults.dead_zone_penalty    + 0.04, 0.15, 0.30))
            _set("sr_proximity_block_pct", _clamp(defaults.sr_proximity_block_pct + 0.0015, 0.003, 0.02))
            reason = (f"Win rate {wr:.0%} below target or negative daily PnL → "
                      f"tightened gates to reduce low-quality entries. {trade_sample_summary}")

        elif regime == REGIME_HIGH_CHURN:
            _trades_1h = metrics.get("trades_last_hour", 0)
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.10, 0.65, 0.78))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 8, 8, 15))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 0.5, 3.0, 4.0))
            # Raise per-trade EV coverage requirement to force higher-quality entries only.
            # Reads the current gate value (default 3.0) and bumps it upward.
            _current_ev = d.get("min_trade_ev_coverage_ratio", 3.0)
            _set("min_trade_ev_coverage_ratio", _clamp(_current_ev + 2.0, 5.0, 12.0))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.05, 0.20, 0.35))
            reason = (
                f"High churn: {_trades_1h} trades in last hour with fee coverage "
                f"{_coverage:.2f}× — raising entry quality to enforce fewer, larger, "
                f"higher-quality trades. {trade_sample_summary}"
            )

        elif regime == REGIME_FEE_DRAG_MODERATE:
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _avg_gross = metrics.get("avg_trade_gross_pnl", 0.0) or 0.0
            _avg_fee   = metrics.get("avg_fee_per_trade", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.08, 0.62, 0.75))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 0.5, 3.0, 4.0))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 5, 12, 20))
            _set("confidence_size_floor",      _clamp(defaults.confidence_size_floor + 0.05, 0.30, 0.45))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.05, 0.20, 0.35))
            reason = (
                f"Fee drag MODERATE: coverage ratio {_coverage:.2f}x "
                f"(avg gross ${_avg_gross:.2f} vs avg fee ${_avg_fee:.2f}) — "
                f"raised entry confidence and fee-coverage guard, reduced daily trade cap "
                f"to favour fewer higher-quality setups. {trade_sample_summary}"
            )

        elif regime == REGIME_FEE_DRAG_SEVERE:
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _avg_gross = metrics.get("avg_trade_gross_pnl", 0.0) or 0.0
            _avg_fee   = metrics.get("avg_fee_per_trade", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.15, 0.72, 0.82))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 1.5, 4.0, 6.0))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 10, 8, 15))
            _set("confidence_size_floor",      _clamp(defaults.confidence_size_floor + 0.10, 0.35, 0.50))
            _set("confidence_size_reference",  _clamp(defaults.confidence_size_reference - 0.05, 0.72, 0.85))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.10, 0.25, 0.45))
            _set("mtf_opposed_penalty",        _clamp(defaults.mtf_opposed_penalty + 0.10, 0.35, 0.55))
            _set("dead_zone_noop_enabled",     True)
            reason = (
                f"Fee drag SEVERE: coverage ratio {_coverage:.2f}x "
                f"(avg gross ${_avg_gross:.2f} vs avg fee ${_avg_fee:.2f}) — "
                f"gates aggressively tightened for fewer, larger, higher-conviction trades only. "
                f"Fee coverage guard raised to {d.get('fee_coverage_min_ratio', 4.0):.1f}x. "
                f"Daily trade cap → {d.get('circuit_breaker_max_trades', 10)}. {trade_sample_summary}"
            )

        else:  # DEFENSIVE
            _set("min_entry_confidence",   _clamp(defaults.min_entry_confidence + 0.12, 0.62, 0.80))
            _set("mtf_mixed_penalty",      _clamp(defaults.mtf_mixed_penalty    + 0.12, 0.28, 0.50))
            _set("mtf_opposed_penalty",    _clamp(defaults.mtf_opposed_penalty  + 0.12, 0.35, 0.65))
            _set("ta_penalty_multiplier",  _clamp(defaults.ta_penalty_multiplier + 0.12, 0.48, 0.70))
            _set("dead_zone_penalty",      _clamp(defaults.dead_zone_penalty    + 0.08, 0.20, 0.40))
            _set("sr_proximity_block_pct", _clamp(defaults.sr_proximity_block_pct + 0.003, 0.004, 0.03))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 10, 10, 30))
            reason = (f"Win rate {wr:.0%} critically low or {cld} consecutive losing day(s) → "
                      f"gates significantly tightened. Capital preservation priority. {trade_sample_summary}")

        # ── Consecutive-loss override (additive on top of regime) ─────────────
        if cld >= 2:
            floor = 0.60 if cld == 2 else 0.65
            if d["min_entry_confidence"] < floor:
                _set("min_entry_confidence", floor)
        if cld >= 3:
            if d["circuit_breaker_max_trades"] > 20:
                _set("circuit_breaker_max_trades", 20)

        # ── Daily Fee Budget Circuit Breaker (hard override) ──────────────────
        # If daily fees exceed max_daily_fees_pct, hard-block all new entries
        # by setting min_entry_confidence to 1.0 (impossible threshold)
        daily_fees_pct = metrics.get("daily_fees_pct", 0.0)
        max_daily_fees = d.get("max_daily_fees_pct", 0.5)
        if daily_fees_pct > max_daily_fees:
            old_conf = d.get("min_entry_confidence", 0.5)
            if old_conf < 1.0:
                _set("min_entry_confidence", 1.0)
                reason = (f"DAILY FEE CIRCUIT BREAKER ACTIVATED. "
                         f"Daily fees {daily_fees_pct:.2f}% exceed budget {max_daily_fees:.2f}%. "
                         f"All new entries blocked until budget resets at midnight UTC.")

        new_gates = TradingGates(**d)
        return new_gates, changes, reason


    # ── Persistence ───────────────────────────────────────────────────────────

    async def _persist_state(self) -> None:
        try:
            from app.api.routes.settings import _save_setting
            await _save_setting(_AUTOPILOT_SETTING_KEY, {
                "enabled":    self._enabled,
                "regime":     self._last_regime,
                "reason":     self._last_reason,
                "last_run":   self._last_run.isoformat() if self._last_run else None,
                "changes":    self._changes,
            })
        except Exception as exc:
            logger.warning(f"Gate autopilot state persist failed: {exc}")

    async def _load_state(self) -> None:
        if self._loaded:
            return
        try:
            from app.api.routes.settings import _load_setting
            data = await _load_setting(_AUTOPILOT_SETTING_KEY)
            if data:
                self._enabled     = bool(data.get("enabled", False))
                self._last_regime = data.get("regime", REGIME_BALANCED)
                self._last_reason = data.get("reason", "Loaded from DB")
                self._changes     = data.get("changes", {})
                raw_ts = data.get("last_run")
                if raw_ts:
                    try:
                        self._last_run = datetime.fromisoformat(raw_ts)
                    except ValueError:
                        self._last_run = None
            logger.info(f"Gate autopilot loaded — enabled={self._enabled}, regime={self._last_regime}")
        except Exception as exc:
            logger.warning(f"Gate autopilot state load failed (using defaults): {exc}")
        self._loaded = True


# Singleton
gate_autopilot = GateAutopilot()
