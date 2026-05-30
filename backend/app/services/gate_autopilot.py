"""Gate Autopilot — dynamically adjusts TradingGates based on performance, market
session, and time of day.

When enabled the autopilot runs every 30 minutes, gathers rolling performance
metrics from AgentMetricRecord and RiskAssessmentRecord, classifies the current
market regime, and applies a bounded incremental adjustment to the live gate
thresholds.  All changes are persisted to the DB through the existing settings
pathway so they survive restarts.

Regime classification
─────────────────────
  The base regime is computed per strategy type from each strategy's own win
  rate and PnL. The overall regime uses the WORST strategy's classification
  (weakest link determines the gate tightness).

  Two preemptive overlays adjust the result BEFORE the win-rate check:
    1. GMM regime (risk_on / range / risk_off) — if the statistical model
       says the market turned dangerous, shift one notch defensive before
       looking at win rates.
    2. Per-strategy fees and churn — checked before win-rate regime.

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
_FEE_DRAG_SEVERE_RATIO      = 1.2    # gross PnL / fees < 1.2 → severe
_FEE_DRAG_MODERATE_RATIO    = 2.0    # gross PnL / fees < 2.0 → moderate
_FEE_DRAG_MIN_CLOSED_TRADES = 10     # need meaningful sample before activating

# Hysteresis: require this many consecutive evaluations before switching regimes
# (runs every 30 min, so 2 = 1 hour of sustained evidence)
_REGIME_SWITCH_THRESHOLD = 2

_AUTOPILOT_SETTING_KEY = "gate_autopilot"
_RUN_INTERVAL_SECONDS  = 1800   # 30 minutes


class GateAutopilot:
    """Background service that auto-tunes TradingGates from live metrics."""

    def __init__(self) -> None:
        self._enabled: bool = False
        self._last_regime: str = REGIME_BALANCED
        self._pending_regime: Optional[str] = None
        self._pending_count: int = 0
        self._last_reason: str = "Autopilot not yet run"
        self._last_run: Optional[datetime] = None
        self._changes: dict = {}
        self._loaded: bool = False
        # Fields the autopilot has modified (so BALANCED only resets what we touched)
        self._autopilot_fields: set = set()

    # ── Public accessors ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self) -> dict:
        return {
            "enabled":     self._enabled,
            "regime":      self._last_regime,
            "pending_regime": self._pending_regime,
            "pending_count": self._pending_count,
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
        from app.api.routes.settings import get_trading_gates, TradingGates, _load_setting

        metrics = await self._gather_metrics()
        raw_regime  = self._classify_regime(metrics)
        gates   = get_trading_gates()

        # ── Hysteresis: don't switch regimes on a single data point ───────────────
        # Require _REGIME_SWITCH_THRESHOLD consecutive evaluations to agree
        # before leaving the current regime. This stops thrashing when WR
        # oscillates right on a boundary (e.g. 47→49→47→49).
        if raw_regime == self._last_regime:
            self._pending_regime = None
            self._pending_count = 0
            regime = raw_regime
        else:
            if raw_regime == self._pending_regime:
                self._pending_count += 1
            else:
                self._pending_regime = raw_regime
                self._pending_count = 1

            if self._pending_count >= _REGIME_SWITCH_THRESHOLD:
                logger.info(
                    f"Gate autopilot switching regime {self._last_regime} → {raw_regime} "
                    f"({self._pending_count} consecutive evaluations)"
                )
                regime = raw_regime
                self._pending_regime = None
                self._pending_count = 0
            else:
                logger.info(
                    f"Gate autopilot holding {self._last_regime} (raw={raw_regime}, "
                    f"pending {self._pending_count}/{_REGIME_SWITCH_THRESHOLD})"
                )
                regime = self._last_regime

        # Arrr, respect the captain's manual orders.
        # Use the operator's saved baseline so adjustments are relative to THEIR
        # preferred values, not the factory defaults — otherwise every 30-min BALANCED
        # cycle silently overwrites the manual saves and they think the UI is broken.
        user_baseline: TradingGates
        try:
            baseline_data = await _load_setting("trading_gates_user_baseline")
            user_baseline = TradingGates(**(baseline_data or {}))
        except Exception:
            user_baseline = TradingGates()

        new_gates, changes, reason = self._compute_adjustments(
            gates, regime, metrics, user_baseline,
            autopilot_fields=self._autopilot_fields,
        )

        # Only write to DB if something actually changed
        if changes:
            from app.api.routes.settings import _runtime_trading_gates, _save_setting
            import app.api.routes.settings as _settings_mod
            _settings_mod._runtime_trading_gates = new_gates
            await _save_setting("trading_gates", new_gates.model_dump())
            logger.info(f"Gate autopilot [{regime}] applied {len(changes)} adjustment(s): {changes}")
        else:
            logger.debug(f"Gate autopilot [{regime}] — no changes needed")

        # Track which fields the autopilot has touched so BALANCED only resets those
        self._autopilot_fields.update(changes.keys())
        # If we just returned to BALANCED, clear the tracked set so future regimes
        # start fresh.
        if regime == REGIME_BALANCED:
            self._autopilot_fields.clear()

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
        from app.models import AgentMetricRecord, AgentRunRecord, RiskAssessmentRecord, Trade, OrderStatus, RegimeStateRecord
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
                        Trade.user_id == get_current_user_id(),
                        Trade.is_paper.is_(True),
                        Trade.status == OrderStatus.FILLED,
                    )
                )
                metrics["filled_paper_trades"] = int(filled_paper_trades.scalar_one_or_none() or 0)

                # ── Active agent count (for per-agent churn scaling) ──────────────
                from app.models import Agent as _AgentModel
                _agent_count_result = await db.execute(
                    select(sqlfunc.count(_AgentModel.id)).where(_AgentModel.enabled.is_(True))
                )
                metrics["active_agents"] = int(_agent_count_result.scalar_one_or_none() or 1)

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

                # ── Consecutive losing days (last 5 daily risk assessments) ──────────
                # Use the LAST assessment of each UTC day (ordered desc), not the
                # minimum, so a flash dip during a green day doesn't count as a loss.
                results = await db.execute(
                    select(
                        sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp).label("day"),
                        RiskAssessmentRecord.daily_pnl.label("last_pnl"),
                    ).distinct(
                        sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp)
                    ).order_by(
                        sqlfunc.date_trunc("day", RiskAssessmentRecord.timestamp).desc(),
                        RiskAssessmentRecord.timestamp.desc(),
                    ).limit(5)
                )
                days = results.all()
                consecutive = 0
                for day_row in days:
                    if day_row.last_pnl is not None and day_row.last_pnl < 0:
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
                        Trade.user_id == get_current_user_id(),
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

        # ── Rolling 14-day fee drag metrics ─────────────────────────────────
        # Replace lifetime cumulative with a 14-day window so stale history
        # from a different market regime doesn't permanently poison the overlay.
        try:
            _window_start = datetime.now(timezone.utc) - timedelta(days=14)
            async with get_async_session() as db:
                fees_row = await db.execute(
                    select(
                        sqlfunc.coalesce(sqlfunc.sum(Trade.fee), 0.0).label("total_fees"),
                    ).where(
                        Trade.user_id == get_current_user_id(),
                        Trade.is_paper.is_(True),
                        Trade.status == OrderStatus.FILLED,
                        Trade.created_at >= _window_start,
                    )
                )
                _total_fees = float((fees_row.one_or_none() or (0.0,))[0] or 0.0)

                pnl_row = await db.execute(
                    select(
                        sqlfunc.coalesce(sqlfunc.sum(AgentRunRecord.pnl), 0.0).label("net_pnl"),
                        sqlfunc.count(AgentRunRecord.id).label("closed_count"),
                    ).where(
                        AgentRunRecord.pnl.isnot(None),
                        AgentRunRecord.use_paper.is_(True),
                        AgentRunRecord.timestamp >= _window_start,
                    )
                )
                pnl_data = pnl_row.one_or_none()
                _net_pnl     = float((pnl_data.net_pnl    if pnl_data else 0.0) or 0.0)
                _trade_count = int((pnl_data.closed_count  if pnl_data else 0)   or 0)

            _gross_pnl = _net_pnl + _total_fees
            metrics["total_fees_lifetime"]  = _total_fees
            metrics["gross_realized_pnl"]   = _gross_pnl
            metrics["fee_coverage_ratio"]   = (_gross_pnl / _total_fees) if _total_fees > 0 else None
            metrics["avg_trade_gross_pnl"]  = (_gross_pnl / _trade_count) if _trade_count > 0 else 0.0
            metrics["avg_fee_per_trade"]    = (_total_fees / _trade_count) if _trade_count > 0 else 0.0
            metrics["lifetime_trade_count"] = _trade_count
        except Exception as exc:
            logger.debug(f"Failed to gather fee drag metrics: {exc}")

        # ── GMM market regime from RegimeStateRecord (preemptive overlay) ─────────
        # The statistical model classifies each symbol independently. We take the
        # most common label across all tracked symbols. If the most conservative
        # label is risk_off, that's the one that matters.
        try:
            from app.models import RegimeStateRecord
            async with get_async_session() as db:
                regime_rows = await db.execute(
                    select(
                        RegimeStateRecord.regime_label,
                        sqlfunc.count(RegimeStateRecord.id).label("cnt"),
                    )
                    .group_by(RegimeStateRecord.regime_label)
                    .order_by(sqlfunc.count(RegimeStateRecord.id).desc())
                )
                _gmm_labels = {row.regime_label: row.cnt for row in regime_rows.all()}
                # Prefer the most conservative label present
                if _gmm_labels.get("risk_off"):
                    metrics["gmm_regime"] = "risk_off"
                elif _gmm_labels.get("range"):
                    metrics["gmm_regime"] = "range"
                elif _gmm_labels.get("risk_on"):
                    metrics["gmm_regime"] = "risk_on"
                else:
                    metrics["gmm_regime"] = "unknown"
        except Exception as exc:
            metrics["gmm_regime"] = "unknown"
            logger.debug(f"Failed to gather GMM regime: {exc}")

        # ── Per-strategy metrics ──────────────────────────────────────────────────
        # Group by strategy_type to identify which strategies are dragging
        # performance down. The worst strategy determines the gate tightness.
        try:
            async with get_async_session() as db:
                strat_rows = await db.execute(
                    select(
                        AgentRunRecord.strategy_type,
                        sqlfunc.count(AgentRunRecord.id).label("closed_count"),
                        sqlfunc.sum(sqlfunc.cast(AgentRunRecord.pnl > 0, sqlfunc.Integer)).label("winners"),
                        sqlfunc.sum(AgentRunRecord.pnl).label("total_pnl"),
                    ).where(
                        AgentRunRecord.pnl.isnot(None),
                        AgentRunRecord.strategy_type.isnot(None),
                    ).group_by(AgentRunRecord.strategy_type)
                )
                per_strategy = {}
                for row in strat_rows.all():
                    st = row.strategy_type
                    cc = int(row.closed_count or 0)
                    w  = int(row.winners or 0)
                    tp = float(row.total_pnl or 0.0)
                    per_strategy[st] = {
                        "trades": cc,
                        "wins": w,
                        "win_rate": (w / cc) if cc > 0 else 0.5,
                        "total_pnl": tp,
                        "avg_pnl": tp / cc if cc > 0 else 0.0,
                    }
                metrics["per_strategy"] = per_strategy
        except Exception as exc:
            metrics["per_strategy"] = {}
            logger.debug(f"Failed to gather per-strategy metrics: {exc}")

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

        # ── Preemptive overlay: GMM market regime ────────────────────────────────
        # If the statistical model classifies the market as risk_off, shift the
        # base classification one notch defensive before looking at win rates.
        # This catches the May 4→5 scenario: the GMM would have detected the
        # trend reversal hours before the losses piled up.
        gmm_regime = metrics.get("gmm_regime", "unknown")
        gmm_penalty = 0
        if gmm_regime == "risk_off":
            gmm_penalty = 1  # shift one notch defensive
            logger.info(f"GMM preemptive overlay: risk_off detected — will shift regime one notch defensive")
        elif gmm_regime == "range":
            gmm_penalty = 0  # ranging is neutral, no shift
        # risk_on → no penalty (allow aggressive)

        # ── Per-strategy win rates (weakest-link) ──────────────────────────────
        # The worst-performing strategy determines the gate tightness, since
        # one bleeding strategy can drag the whole portfolio down (agent 03316f19
        # lost 6 of the 11 losses while other strategies were fine).
        per_strategy = metrics.get("per_strategy", {})
        _worst_strategy = None
        _strat_wr = wr  # default to global
        _strat_n  = n
        _strat_pnl = pnl
        for st_name, st_data in per_strategy.items():
            st_wr = st_data.get("win_rate", 0.5)
            st_n  = st_data.get("trades", 0)
            st_pnl = st_data.get("total_pnl", 0.0)
            if st_n >= 3 and st_wr < _strat_wr:
                _worst_strategy = st_name
                _strat_wr = st_wr
                _strat_n  = st_n
                _strat_pnl = st_pnl

        # Use worst strategy metrics for classification
        wr = _strat_wr
        n  = _strat_n

        # Apply GMM penalty to win rate (shift classification notch)
        if gmm_penalty == 1 and n >= 3:
            # risk_off: penalize the effective win rate by 10 points
            # so a strategy at 45% WR gets classified as defensive instead of cautious
            wr = wr - 0.10

        # Insufficient data → stay balanced
        if n < 5:
            # Still check GMM: risk_off with little data → cautious anyway
            if gmm_regime == "risk_off":
                return REGIME_CAUTIOUS
            return REGIME_BALANCED

        # ── High churn detection (checked before fee drag) ────────────────────
        _trades_1h   = metrics.get("trades_last_hour", 0)
        _coverage_hc = metrics.get("fee_coverage_ratio")
        _lifetime_hc = metrics.get("lifetime_trade_count", 0)
        _active_agents = max(1, metrics.get("active_agents", 1))
        # Scale threshold with fleet size: 0.8 trades/hour per agent, min 5
        _HIGH_CHURN_TRADES_THRESHOLD = max(5, int(_active_agents * 0.8))
        if (
            _trades_1h > _HIGH_CHURN_TRADES_THRESHOLD
            and _coverage_hc is not None
            and _coverage_hc < _FEE_DRAG_MODERATE_RATIO
            and _lifetime_hc >= 5
        ):
            return REGIME_HIGH_CHURN

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
        user_baseline=None,
        autopilot_fields: Optional[set] = None,
    ):
        """Return (new_gates, changes_dict, human_reason)."""
        from app.api.routes.settings import TradingGates

        # Arrr, the baseline is the captain's last saved preferences.
        # If the crew hasn't set one yet we fall back to factory defaults.
        # This is why adjustments feel relative to YOUR settings, not some
        # hardcoded reference that ignores your manual saves.
        defaults = user_baseline if user_baseline is not None else TradingGates()
        d = gates.model_dump()      # start from current live gates
        changes: dict = {}
        cld  = metrics["consecutive_losing_days"]
        wr   = metrics["win_rate"]
        n    = metrics["total_trades"]
        trade_sample_summary = GateAutopilot._trade_sample_summary(metrics)
        _ap_fields = autopilot_fields or set()

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
            _set("fast_entry_enabled", True)
            reason = (f"Win rate {wr:.0%} with positive daily PnL → loosened gates to "
                      f"capture more of the current edge. Fast entry enabled. {trade_sample_summary}")

        elif regime == REGIME_BALANCED:
            # Restore to defaults ONLY for fields the autopilot previously modified.
            # This preserves manual operator tweaks on fields the autopilot never touched.
            for field in (
                "min_entry_confidence", "mtf_mixed_penalty", "mtf_opposed_penalty",
                "ta_penalty_multiplier", "dead_zone_penalty", "sr_proximity_block_pct",
                "circuit_breaker_max_trades", "fast_entry_enabled",
            ):
                if field in _ap_fields:
                    _set(field, getattr(defaults, field))
            _gmm_r = metrics.get("gmm_regime", "unknown")
            _gmm_info = f" GMM={_gmm_r}," if _gmm_r not in ("unknown",) else ""
            _ps = metrics.get("per_strategy", {})
            _worst_st = None
            if _ps:
                _worst_st = min(_ps, key=lambda s: _ps[s].get("win_rate", 1))
            _worst_info = f" worst={_worst_st}({_ps[_worst_st]['win_rate']:.0%})" if _worst_st else ""
            reason = (f"Win rate {wr:.0%} within balanced range → "
                      f"restored autopilot-modified gate thresholds to defaults.{_gmm_info}{_worst_info} {trade_sample_summary}")

        elif regime == REGIME_CAUTIOUS:
            _set("min_entry_confidence",   _clamp(defaults.min_entry_confidence + 0.05, 0.50, 0.72))
            _set("mtf_mixed_penalty",      _clamp(defaults.mtf_mixed_penalty    + 0.05, 0.20, 0.40))
            _set("mtf_opposed_penalty",    _clamp(defaults.mtf_opposed_penalty  + 0.05, 0.30, 0.55))
            _set("ta_penalty_multiplier",  _clamp(defaults.ta_penalty_multiplier + 0.05, 0.40, 0.60))
            _set("dead_zone_penalty",      _clamp(defaults.dead_zone_penalty    + 0.04, 0.15, 0.30))
            _set("sr_proximity_block_pct", _clamp(defaults.sr_proximity_block_pct + 0.0015, 0.003, 0.02))
            _set("max_position_size_pct",  _clamp(defaults.max_position_size_pct - 1.0, 2.0, 5.0))
            _set("fast_entry_enabled", False)
            reason = (f"Win rate {wr:.0%} below target or negative daily PnL → "
                      f"tightened gates to reduce low-quality entries. Fast entry disabled. {trade_sample_summary}")

        elif regime == REGIME_HIGH_CHURN:
            _trades_1h = metrics.get("trades_last_hour", 0)
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.10, 0.65, 0.78))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 8, 8, 15))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 0.5, 3.0, 4.0))
            _set("min_net_tp_pct",             _clamp(defaults.min_net_tp_pct + 0.25, 0.60, 1.50))
            _current_ev = d.get("min_trade_ev_coverage_ratio", 3.0)
            _set("min_trade_ev_coverage_ratio", _clamp(_current_ev + 2.0, 5.0, 12.0))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.05, 0.20, 0.35))
            _set("fast_entry_enabled", False)
            reason = (
                f"High churn: {_trades_1h} trades in last hour with fee coverage "
                f"{_coverage:.2f}× — raising entry quality to enforce fewer, larger, "
                f"higher-quality trades. Fast entry disabled. {trade_sample_summary}"
            )

        elif regime == REGIME_FEE_DRAG_MODERATE:
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _avg_gross = metrics.get("avg_trade_gross_pnl", 0.0) or 0.0
            _avg_fee   = metrics.get("avg_fee_per_trade", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.08, 0.62, 0.75))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 0.5, 3.0, 4.0))
            _set("min_net_tp_pct",             _clamp(defaults.min_net_tp_pct + 0.15, 0.55, 1.00))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 5, 12, 20))
            _set("confidence_size_floor",      _clamp(defaults.confidence_size_floor + 0.05, 0.30, 0.45))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.05, 0.20, 0.35))
            _set("min_notional",               _clamp(defaults.min_notional + 5.0, 10.0, 50.0))
            _set("max_position_size_pct",      _clamp(defaults.max_position_size_pct - 1.0, 2.0, 5.0))
            _set("max_daily_loss_pct",         _clamp(defaults.max_daily_loss_pct - 1.0, 2.0, 5.0))
            _set("fast_entry_enabled", False)
            reason = (
                f"Fee drag MODERATE: coverage ratio {_coverage:.2f}x "
                f"(avg gross ${_avg_gross:.2f} vs avg fee ${_avg_fee:.2f}) — "
                f"raised entry confidence and fee-coverage guard, reduced daily trade cap "
                f"to favour fewer higher-quality setups. Fast entry disabled. {trade_sample_summary}"
            )

        elif regime == REGIME_FEE_DRAG_SEVERE:
            _coverage  = metrics.get("fee_coverage_ratio", 0.0) or 0.0
            _avg_gross = metrics.get("avg_trade_gross_pnl", 0.0) or 0.0
            _avg_fee   = metrics.get("avg_fee_per_trade", 0.0) or 0.0
            _set("min_entry_confidence",       _clamp(defaults.min_entry_confidence + 0.15, 0.72, 0.82))
            _set("fee_coverage_min_ratio",     _clamp(defaults.fee_coverage_min_ratio + 1.5, 4.0, 6.0))
            _set("min_net_tp_pct",             _clamp(defaults.min_net_tp_pct + 0.50, 0.75, 2.00))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 10, 8, 15))
            _set("confidence_size_floor",      _clamp(defaults.confidence_size_floor + 0.10, 0.35, 0.50))
            _set("confidence_size_reference",  _clamp(defaults.confidence_size_reference - 0.05, 0.72, 0.85))
            _set("mtf_mixed_penalty",          _clamp(defaults.mtf_mixed_penalty + 0.10, 0.25, 0.45))
            _set("mtf_opposed_penalty",        _clamp(defaults.mtf_opposed_penalty + 0.10, 0.35, 0.55))
            _set("dead_zone_noop_enabled",     True)
            _set("min_notional",               _clamp(defaults.min_notional + 10.0, 15.0, 75.0))
            _set("max_position_size_pct",      _clamp(defaults.max_position_size_pct - 2.0, 1.0, 4.0))
            _set("max_daily_loss_pct",         _clamp(defaults.max_daily_loss_pct - 2.0, 1.0, 3.0))
            _set("fast_entry_enabled", False)
            reason = (
                f"Fee drag SEVERE: coverage ratio {_coverage:.2f}x "
                f"(avg gross ${_avg_gross:.2f} vs avg fee ${_avg_fee:.2f}) — "
                f"gates aggressively tightened for fewer, larger, higher-conviction trades only. "
                f"Fee coverage guard raised to {d.get('fee_coverage_min_ratio', 4.0):.1f}x. "
                f"Daily trade cap → {d.get('circuit_breaker_max_trades', 10)}. Fast entry disabled. {trade_sample_summary}"
            )

        else:  # DEFENSIVE
            _set("min_entry_confidence",   _clamp(defaults.min_entry_confidence + 0.12, 0.62, 0.80))
            _set("mtf_mixed_penalty",      _clamp(defaults.mtf_mixed_penalty    + 0.12, 0.28, 0.50))
            _set("mtf_opposed_penalty",    _clamp(defaults.mtf_opposed_penalty  + 0.12, 0.35, 0.65))
            _set("ta_penalty_multiplier",  _clamp(defaults.ta_penalty_multiplier + 0.12, 0.48, 0.70))
            _set("dead_zone_penalty",      _clamp(defaults.dead_zone_penalty    + 0.08, 0.20, 0.40))
            _set("sr_proximity_block_pct", _clamp(defaults.sr_proximity_block_pct + 0.003, 0.004, 0.03))
            _set("min_net_tp_pct",         _clamp(defaults.min_net_tp_pct + 0.35, 0.65, 1.75))
            _set("circuit_breaker_max_trades", _clamp(defaults.circuit_breaker_max_trades - 10, 10, 30))
            _set("max_position_size_pct",  _clamp(defaults.max_position_size_pct - 2.0, 1.0, 3.0))
            _set("max_daily_loss_pct",     _clamp(defaults.max_daily_loss_pct - 2.0, 1.0, 3.0))
            _set("fast_entry_enabled", False)
            reason = (f"Win rate {wr:.0%} critically low or {cld} consecutive losing day(s) → "
                      f"gates significantly tightened. Capital preservation priority. Fast entry disabled. {trade_sample_summary}")

        # ── Consecutive-loss override (additive on top of regime) ─────────────
        if cld >= 2:
            floor = 0.60 if cld == 2 else 0.65
            if d["min_entry_confidence"] < floor:
                _set("min_entry_confidence", floor)
        if cld >= 3:
            if d["circuit_breaker_max_trades"] > 20:
                _set("circuit_breaker_max_trades", 20)

        # ── Per-strategy confidence floors ──────────────────────────────────────
        # Even in a BALANCED overall regime, an individual strategy with a poor
        # win rate should require higher confidence to enter. This prevents a
        # single bleeding strategy from diluting the portfolio while allowing
        # healthy strategies to keep trading normally.
        # The gates are global (not per-strategy), so we raise the global floor
        # high enough to protect the weakest strategy.
        per_strategy = metrics.get("per_strategy", {})
        _strat_floors = []
        for st_name, st_data in per_strategy.items():
            st_wr = st_data.get("win_rate", 0.5)
            st_n  = st_data.get("trades", 0)
            if st_n < 3:
                continue
            if st_wr < 0.35:
                _strat_floors.append(0.72)
            elif st_wr < 0.48:
                _strat_floors.append(0.65)
        if _strat_floors:
            _per_strat_floor = max(_strat_floors)
            if d["min_entry_confidence"] < _per_strat_floor:
                _set("min_entry_confidence", _per_strat_floor)

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
                "pending_regime": self._pending_regime,
                "pending_count": self._pending_count,
                "reason":     self._last_reason,
                "last_run":   self._last_run.isoformat() if self._last_run else None,
                "changes":    self._changes,
                "autopilot_fields": list(self._autopilot_fields),
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
                self._pending_regime = data.get("pending_regime")
                self._pending_count = int(data.get("pending_count", 0))
                self._last_reason = data.get("reason", "Loaded from DB")
                self._changes     = data.get("changes", {})
                self._autopilot_fields = set(data.get("autopilot_fields", []))
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
