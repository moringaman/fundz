from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import asyncio
import json
import logging

from sqlalchemy import select, desc, func as sqlfunc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.clients.phemex import PhemexClient
from app.config import settings
from app.database import get_async_session
from app.services.indicators import IndicatorService
from app.services.paper_trading import paper_trading
from app.services.trading_service import trading_service, _is_paper_mode
from app.services.position_sync import position_sync_service
from app.services.backtest import BacktestEngine
from app.services.risk_manager import risk_manager, RiskCheckResult, RiskConfig
from app.models import OrderSide, OrderStatus, Trade
from app.services.research_analyst import research_analyst
from app.services.fund_manager import fund_manager
from app.services.cio_agent import cio_agent
from app.services.execution_coordinator import execution_coordinator, CycleTradeRecord
from app.services.technical_analyst import technical_analyst
from app.services.team_chat import team_chat
from app.services.daily_report import daily_report_service
from app.services.strategy_review import strategy_review_service
from app.utils import fmt_price
from app.services.trader_service import trader_service
from app.services.telegram_service import telegram_service

logger = logging.getLogger(__name__)


async def _send_telegram(coro) -> None:
    """Fire-and-forget Telegram alert with error logging (not silent discard)."""
    try:
        await coro
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


async def _run_drawdown_check(trader_id: str, agent_ids: list, is_paper: bool) -> None:
    """Phase 9.2: fire-and-forget drawdown check after a position closes."""
    try:
        from app.services.drawdown_monitor import update_trader_drawdown
        await update_trader_drawdown(trader_id, agent_ids, is_paper)
    except Exception as e:
        logger.warning(f"Drawdown check failed for trader {trader_id}: {e}")



@dataclass
class AgentRun:
    agent_id: str
    timestamp: datetime
    symbol: str
    signal: str
    confidence: float
    price: float
    executed: bool
    pnl: Optional[float] = None
    error: Optional[str] = None
    exit_reason: Optional[str] = None  # "stop-loss" | "take-profit" | "trailing-stop" | None


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


class AgentScheduler:
    def __init__(self):
        self.indicator_service = IndicatorService()
        self.phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet
        )
        self.backtest_engine = BacktestEngine(self.phemex)
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        self._agent_runs: List[AgentRun] = []
        self._agent_metrics: Dict[str, AgentMetrics] = {}
        self._enabled_agents: Dict[str, dict] = {}
        self._scheduler_task: Optional[asyncio.Task] = None

        # Pre-trade backtest cache: key = "agent_id:symbol", value = (BacktestResult, cached_at datetime)
        self._backtest_cache: Dict[str, tuple] = {}

        # Per-cycle trade buffer for Alex Liu's conflict detection.
        # Cleared at the start of each _run_enabled_agents pass.
        self._cycle_trades: List[CycleTradeRecord] = []

        # Post-close cooldown: key = "agent_id:symbol", value = datetime of last close.
        # Prevents whipsaw re-entries on the same symbol within 15 minutes of a close.
        self._recent_closes: Dict[str, datetime] = {}
        self._POST_CLOSE_COOLDOWN_SECONDS = 900  # 15 minutes (base; scales with fee pressure)

        # Fee-pressure state: cached from team analysis cycle (15 min refresh)
        # so per-trade paths don't need their own DB query.
        self._cached_budget_ratio: float = 0.0

        # Hourly trade counter: reset on UTC hour boundary, used by soft hourly limit gate.
        self._trades_this_hour: int = 0
        self._trades_hour_bucket: str = ""  # UTC hour string e.g. "2026-04-19T09"

        # Per-agent TA cache: key = "symbol:timeframe", value = (TechnicalAnalystReport, cached_at datetime)
        # TTL: 5 minutes — keeps context fresh without hammering Phemex on every agent run
        self._ta_cache: Dict[str, tuple] = {}
        _TA_CACHE_TTL_MINUTES = 5

        # Team Decision Tier state
        self._last_team_analysis: Optional[datetime] = None
        self._last_daily_report: Optional[datetime] = None
        self._last_daily_email_date: Optional[str] = None  # ISO date of last email sent
        self._current_allocation: Dict[str, float] = {}
        self._current_allocation_reasoning: str = ""
        self._current_risk_assessment = None
        self._current_analyst_report = None
        self._current_execution_plan = None
        self._current_cio_report = None
        self._current_confluence_scores: Dict[str, Dict] = {}
        self._current_trade_insights: Optional[Dict] = None

        # Trader layer state
        self._traders: List[dict] = []
        self._trader_allocations: Dict[str, float] = {}  # {trader_id: pct}
        self._trader_agent_allocations: Dict[str, Dict[str, float]] = {}  # {trader_id: {agent_id: pct}}
        self._total_capital: float = 0.0  # Total fund capital — set from live or paper holdings

        # US Open session management
        self._us_open_sweep_date: Optional[str] = None      # ISO date of last pre-open sweep
        self._us_open_blackout_notified: bool = False        # team chat posted for current blackout window
        self._dead_zone_noop_notified: bool = False          # team chat posted for overnight no-op window
        self._last_trader_checkin: Optional[datetime] = None

        # Phase 9.1 — Consistency gating
        self._consistency_flags: Dict[str, str] = {}  # {trader_id: last_known_flag}

        # ── Setup Maturing Watchlist ───────────────────────────────────────
        # When a scan produces a near-miss signal (confidence 0.55–0.65) the
        # symbol is added here with its last confidence and timestamp. On the
        # next cycle the agent checks watchlisted symbols FIRST so a maturing
        # setup doesn't wait a full interval before being reconsidered.
        # key = "agent_id:symbol", value = {"confidence": float, "signal": str, "at": datetime}
        self._setup_watchlist: Dict[str, dict] = {}
        self._WATCHLIST_CONF_LOW  = 0.55   # enter watchlist above this
        self._WATCHLIST_CONF_HIGH = 0.65   # remove from watchlist above this (trade or expired)
        self._WATCHLIST_TTL_SECS  = 3600   # expire after 1 hour

    async def _get_total_capital(self, positions_value: float = 0.0) -> float:
        """Return total fund capital.

        Live mode: USDT balance from Phemex + open positions value.
        Paper mode: paper DB USDT balance + open positions value.
        Falls back to the paper DB default seed value (50 000) on any error.
        """
        _PAPER_DEFAULT = 50_000.0
        try:
            if not paper_trading._enabled:
                # Live mode — pull real balance from Phemex
                raw = await self.phemex.get_account_balance()
                wallets = raw.get("data", [])
                usdt_ev = next(
                    (w.get("balanceEv", 0) for w in wallets if w.get("currency") == "USDT"),
                    None,
                )
                if usdt_ev is None:
                    logger.warning("Phemex balance response missing USDT wallet; falling back to paper balance")
                    balances = await paper_trading.get_all_balances()
                    usdt_balance = next((b.available for b in balances if b.asset == "USDT"), _PAPER_DEFAULT)
                else:
                    usdt_balance = float(usdt_ev) / 100_000_000
            else:
                # Paper mode — use simulated DB balance
                balances = await paper_trading.get_all_balances()
                usdt_balance = next((b.available for b in balances if b.asset == "USDT"), _PAPER_DEFAULT)
        except Exception as exc:
            logger.warning(f"Failed to fetch capital balance ({exc}); using paper default ${_PAPER_DEFAULT:,.0f}")
            usdt_balance = _PAPER_DEFAULT

        return usdt_balance + positions_value

    @property
    def is_running(self) -> bool:
        return self._running

    def get_current_allocation(self) -> Dict[str, float]:
        """Return the live allocation percentages computed by the Portfolio Manager."""
        return dict(self._current_allocation)

    def get_current_risk_assessment(self):
        """Return the latest risk assessment computed during team analysis."""
        return self._current_risk_assessment

    def get_current_analyst_report(self):
        """Return the latest market analysis report from the Research Analyst."""
        return self._current_analyst_report

    def get_current_cio_report(self):
        """Return the latest CIO fund health report."""
        return self._current_cio_report

    def get_current_confluence_scores(self) -> Dict[str, Dict]:
        """Return the latest technical confluence scores keyed by symbol."""
        return dict(self._current_confluence_scores)

    async def _compute_daily_pnl(self) -> float:
        """Compute today's realized P&L from FIFO-matched buy→sell orders."""
        from datetime import date
        try:
            orders = await paper_trading.get_orders(limit=500)
            today = date.today()
            # Filter sells executed today
            today_sells = [
                o for o in orders
                if o.side == OrderSide.SELL
                and o.status == OrderStatus.FILLED
                and o.created_at
                and o.created_at.date() == today
            ]
            if not today_sells:
                return 0.0

            # Get ALL filled orders for FIFO matching
            all_orders = sorted(orders, key=lambda o: o.created_at or datetime.min)
            # Group buys by (symbol, agent_id)
            buy_queues: dict = {}
            daily_pnl = 0.0

            for o in all_orders:
                key = (o.symbol, o.agent_id or "__none__")
                if o.side == OrderSide.BUY and o.status == OrderStatus.FILLED:
                    buy_queues.setdefault(key, []).append({"qty": o.quantity, "price": o.price})
                elif o.side == OrderSide.SELL and o.status == OrderStatus.FILLED:
                    buys = buy_queues.get(key, [])
                    remaining = o.quantity
                    sell_pnl = 0.0
                    while remaining > 1e-12 and buys:
                        fill = min(remaining, buys[0]["qty"])
                        sell_pnl += fill * (o.price - buys[0]["price"])
                        remaining -= fill
                        buys[0]["qty"] -= fill
                        if buys[0]["qty"] <= 1e-12:
                            buys.pop(0)
                    # Only count P&L for today's sells
                    if o.created_at and o.created_at.date() == today:
                        daily_pnl += sell_pnl

            return round(daily_pnl, 4)
        except Exception as e:
            logger.error(f"Failed to compute daily P&L: {e}")
            return risk_manager.get_daily_pnl()

    async def _build_team_context(self, agent_id: str, symbol: str, timeframe: str = "1h") -> Optional[Dict]:
        """Build team intelligence dict for LLM-based agents.

        TA data is fetched/cached per (symbol, timeframe) so each agent sees
        confluence scores aligned to its own trading timeframe, not the team-level
        dominant timeframe.
        """
        ctx = {}

        # Technical Analyst data — per-agent timeframe, cached 5 min
        ta_report = None
        _ta_key = f"{symbol}:{timeframe}"
        _ta_cached = self._ta_cache.get(_ta_key)
        if _ta_cached:
            _ta_report_cached, _ta_cached_at = _ta_cached
            if (datetime.now() - _ta_cached_at).total_seconds() < 300:
                ta_report = _ta_report_cached
        if ta_report is None:
            try:
                ta_report = await technical_analyst.analyze(symbol, timeframe=timeframe)
                self._ta_cache[_ta_key] = (ta_report, datetime.now())
            except Exception as _ta_err:
                logger.debug(f"TA context fetch failed for {symbol}/{timeframe}: {_ta_err}")

        if ta_report:
            mtf = ta_report.multi_timeframe
            # Build per-tier breakdown so the LLM knows which timeframes agree/disagree
            tier_labels = []
            if mtf:
                tiers = [
                    (mtf.tf_primary, mtf.timeframe_1h.get("trend", "neutral")),
                    (mtf.tf_mid,     mtf.timeframe_4h.get("trend", "neutral")),
                    (mtf.tf_high,    mtf.timeframe_1d.get("trend", "neutral")),
                ]
                tier_labels = [f"{tf}:{trend}" for tf, trend in tiers]
            ctx["ta"] = {
                "signal": ta_report.overall_signal,
                "confidence": ta_report.confidence,
                "alignment": mtf.alignment if mtf else "unknown",
                "confluence_score": mtf.confluence_score if mtf else 0,
                "trend_confirmed": mtf.trend_confirmation if mtf else False,
                "timeframe_breakdown": ", ".join(tier_labels),  # e.g. "15m:bullish, 1h:bullish, 4h:neutral"
                "patterns_count": len(ta_report.patterns),
                "patterns_summary": "; ".join(
                    f"{p.pattern_type}({p.direction},{p.confidence:.0%})" for p in ta_report.patterns[:3]
                ),
                "observations": "; ".join(ta_report.key_observations[:3]),
                "support": ta_report.price_levels.support[0] if ta_report.price_levels.support else 0,
                "resistance": ta_report.price_levels.resistance[0] if ta_report.price_levels.resistance else 0,
            }
        elif self._current_confluence_scores:
            # Fall back to team-level scores if per-agent TA unavailable
            ta_data = self._current_confluence_scores.get(symbol)
            if ta_data:
                ctx["ta"] = {
                    "signal": ta_data.get("signal", "hold"),
                    "confidence": ta_data.get("confidence", 0),
                    "alignment": ta_data.get("alignment", "unknown"),
                    "confluence_score": ta_data.get("score", 0),
                    "trend_confirmed": False,
                    "timeframe_breakdown": "team-level (fallback)",
                    "patterns_count": ta_data.get("patterns", 0),
                    "patterns_summary": ta_data.get("details", ""),
                    "observations": ta_data.get("details", ""),
                    "support": 0,
                    "resistance": 0,
                }

        # Research Analyst data
        if self._current_analyst_report:
            report = self._current_analyst_report
            regime = getattr(report, 'market_regime', None)
            top_opp = getattr(report, 'top_opportunity', None)
            if regime:
                ctx["research"] = {
                    "regime": getattr(regime, 'regime', 'unknown'),
                    "sentiment": getattr(regime, 'sentiment', 'neutral'),
                    "volatility": getattr(regime, 'volatility_regime', 'medium'),
                    "correlation": getattr(regime, 'correlation_status', 'mixed'),
                    "top_opportunity": (
                        f"{top_opp.symbol} {top_opp.recommended_action} ({top_opp.confidence:.0%})"
                        if top_opp else "None"
                    ),
                }

        # Risk Manager data
        if self._current_risk_assessment:
            ra = self._current_risk_assessment
            ctx["risk"] = {
                "risk_level": ra.risk_level,
                "exposure_pct": ra.exposure_pct_of_capital,
                "daily_pnl": ra.daily_pnl,
                "concentration": ra.concentration_risk,
                "recommendations": "; ".join(ra.recommendations[:3]) if ra.recommendations else "None",
            }

        # Agent's own performance
        metrics = self._agent_metrics.get(agent_id)
        if metrics:
            ctx["agent_performance"] = {
                "win_rate": metrics.win_rate,
                "total_runs": metrics.total_runs,
                "total_pnl": metrics.total_pnl,
                "streak": (
                    f"{metrics.successful_runs}W/{metrics.failed_runs}L"
                    if metrics.total_runs > 0 else "No trades yet"
                ),
            }

        # Trade retrospective insights for this agent
        if self._current_trade_insights and self._current_trade_insights.get("agent_insights"):
            agent_insight = self._current_trade_insights["agent_insights"].get(agent_id)
            if agent_insight:
                ctx["trade_patterns"] = agent_insight

        # Strategy-level retrospective insights (cross-agent learning)
        if self._current_trade_insights and self._current_trade_insights.get("strategy_insights"):
            _stype = self._enabled_agents.get(agent_id, {}).get("strategy_type", "")
            _strat_insight = self._current_trade_insights["strategy_insights"].get(_stype)
            if _strat_insight:
                ctx["strategy_learning"] = _strat_insight

        # Detect recent stop-out on this symbol — tells the LLM this is a re-entry
        # opportunity, not a fresh position.  Backtest gate also uses this key.
        _four_hours_ago = datetime.now().timestamp() - 4 * 3600
        _recent_runs_for_symbol = [
            r for r in self._agent_runs
            if r.agent_id == agent_id
            and r.symbol == symbol
            and r.pnl is not None
            and r.timestamp.timestamp() > _four_hours_ago
        ]
        if _recent_runs_for_symbol:
            _last_closed = max(_recent_runs_for_symbol, key=lambda r: r.timestamp)
            if getattr(_last_closed, "exit_reason", None) == "stop-loss":
                ctx["recent_stopout"] = {
                    "symbol": symbol,
                    "pnl": _last_closed.pnl,
                    "minutes_ago": int(
                        (datetime.now().timestamp() - _last_closed.timestamp.timestamp()) / 60
                    ),
                }

        # Whale intelligence — Hyperliquid on-chain positioning (graceful degradation)
        # Uses the in-memory cache warmed by the 60s broadcast loop; no DB call needed.
        try:
            from app.services.whale_intelligence import whale_intelligence as _whale_svc
            _whale_report = await _whale_svc.fetch_whale_report()
            if _whale_report is not None:
                _coin = _whale_svc.symbol_to_coin(symbol)
                _bias = _whale_report.coin_biases.get(_coin)
                if _bias is not None:
                    ctx["whale"] = {
                        "coin": _coin,
                        "bias": _bias.bias,
                        "long_notional": _bias.long_notional,
                        "short_notional": _bias.short_notional,
                        "net_notional": _bias.net_notional,
                        "whale_count": _bias.whale_count,
                        "avg_leverage": _bias.avg_leverage,
                    }
        except Exception:
            pass  # Whale intelligence is additive; TA / signal generation continues

        # Market session context — lets agents self-regulate around high-volatility
        # windows (e.g. the US open at 13:00 UTC) with full situational awareness.
        try:
            _ms = self._get_market_session_info()
            _ms_note = (
                f"⚠️ US MARKET OPEN IN ~{_ms['minutes_to_us_open']} MINUTES — expect sharp volatility "
                f"and trend fades as US institutions establish positions. "
                f"Consider tightening your position management."
                if _ms["in_preopen"] else
                "⚠️ US OPEN CHAOS WINDOW (12:45–13:30 UTC) — do NOT open new positions. "
                "Maximum volatility. Trend reversals expected."
                if _ms["in_blackout"] else
                f"📊 US OPEN CONFIRMATION WINDOW — require strong conviction ({_ms['confirmation_confidence']:.0%}+) "
                f"before entering. US session direction is establishing itself."
                if _ms["in_confirmation"] else
                ""
            )
            ctx["market_session"] = {
                "session": _ms["session"],
                "utc_hhmm": _ms["utc_hhmm"],
                "us_open_in_mins": _ms["minutes_to_us_open"],
                "in_us_open_blackout": _ms["in_blackout"],
                "in_us_open_confirmation": _ms["in_confirmation"],
                "note": _ms_note,
            }
        except Exception:
            pass

        # Phase 9.2 — inject trader drawdown / Pink Slip pressure into per-trade context
        try:
            _agent_cfg_tc = self._enabled_agents.get(agent_id, {})
            _trader_id_tc = _agent_cfg_tc.get("trader_id") or _agent_cfg_tc.get("_trader_id_pre")
            if _trader_id_tc:
                _trader_tc = next((t for t in self._traders if t["id"] == _trader_id_tc), None)
                if _trader_tc:
                    _dd_level = _trader_tc.get("drawdown_warning_level")
                    _dd_pct = _trader_tc.get("lifetime_drawdown_pct") or 0.0
                    if _dd_level:
                        from app.services.drawdown_monitor import get_pink_slip_text
                        _dd_note = (
                            get_pink_slip_text(_dd_pct)
                            if _dd_level == "warning"
                            else (
                                f"⚠️ CAUTION: Your trader is in drawdown ({_dd_pct:.1f}% from peak). "
                                f"Only enter with conviction ≥ 0.70. Preserve capital."
                            )
                        )
                        ctx["trader_risk_status"] = {
                            "warning_level": _dd_level,
                            "drawdown_pct": round(_dd_pct, 2),
                            "note": _dd_note,
                        }
        except Exception:
            pass

        return ctx if ctx else None

    _HTF_MAP = {
        "1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h",
        "1h": "4h",  "4h": "1d", "1d": "1d",
    }

    async def _get_htf_trend(self, symbol: str, timeframe: str) -> Optional[str]:
        """
        Fetch one higher timeframe and return 'bullish', 'bearish', or 'neutral'.
        Used to gate/boost lower-TF signals. Returns None on any failure (graceful).
        """
        htf = self._HTF_MAP.get(timeframe, "4h")
        if htf == timeframe:
            return None  # already on highest TF
        try:
            klines = await self.phemex.get_klines(symbol, htf, 60)
            data = klines.get('data', klines) if isinstance(klines, dict) else klines
            if not data or len(data) < 30:
                return None
            import pandas as _pd
            close = _pd.Series([float(k[5]) for k in data])
            sma20 = close.rolling(20).mean().iloc[-1]
            sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
            price = close.iloc[-1]
            if sma50 and not _pd.isna(sma50):
                if price > sma20 > sma50:
                    return "bullish"
                elif price < sma20 < sma50:
                    return "bearish"
            else:
                if not _pd.isna(sma20):
                    return "bullish" if price > sma20 else "bearish"
            return "neutral"
        except Exception:
            return None

    def _build_market_context(self, agent_id: str, symbol: str) -> Optional[Dict]:
        """Build market context dict for non-AI (indicator-based) strategies."""
        ctx = {}

        # Market regime from Research Analyst
        if self._current_analyst_report:
            regime = getattr(self._current_analyst_report, 'market_regime', None)
            if regime:
                ctx["regime"] = getattr(regime, 'regime', 'unknown')

        # TA signal for this symbol
        if hasattr(self, '_current_confluence_scores') and self._current_confluence_scores:
            ta_data = self._current_confluence_scores.get(symbol)
            if ta_data:
                ctx["ta_signal"] = ta_data.get("signal", "hold")
                ctx["ta_confidence"] = ta_data.get("confidence", 0)
                ctx["ta_alignment"] = ta_data.get("alignment", "unknown")
                ctx["ta_confluence_score"] = ta_data.get("score", 0.0)

        # Risk level
        if self._current_risk_assessment:
            ctx["risk_level"] = self._current_risk_assessment.risk_level

        # Agent win rate — only inject when there are actual closed trades to avoid
        # penalising agents that simply haven't had a position close yet.
        metrics = self._agent_metrics.get(agent_id)
        closed_count = sum(1 for r in self._agent_runs if r.agent_id == agent_id and r.pnl is not None)
        if metrics and metrics.win_rate is not None and closed_count >= 5:
            ctx["win_rate"] = metrics.win_rate

        # Detect recent stop-out on this specific symbol — tells the LLM this is a
        # re-entry opportunity, not a fresh position.  The thesis may still be valid;
        # the SL was just too tight relative to the current volatility.
        _four_hours_ago = datetime.now().timestamp() - 4 * 3600
        _recent_runs_for_symbol = [
            r for r in self._agent_runs
            if r.agent_id == agent_id
            and r.symbol == symbol
            and r.pnl is not None
            and r.timestamp.timestamp() > _four_hours_ago
        ]
        if _recent_runs_for_symbol:
            _last_closed = max(_recent_runs_for_symbol, key=lambda r: r.timestamp)
            if _last_closed.exit_reason == "stop-loss":
                ctx["recent_stopout"] = {
                    "symbol": symbol,
                    "pnl": _last_closed.pnl,
                    "minutes_ago": int((datetime.now().timestamp() - _last_closed.timestamp.timestamp()) / 60),
                }

        return ctx if ctx else None
    
    async def start(self):
        if self._running:
            return
        self._running = True

        # Load persisted settings from DB before any logic that depends on them
        from app.api.routes.settings import _load_all_settings
        await _load_all_settings()

        # Restore persisted metrics from DB so allocation decisions survive restarts
        await self._load_metrics_from_db()

        # Auto-register all enabled agents from the database
        await self._auto_register_agents()

        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Agent scheduler started")
        asyncio.create_task(_send_telegram(telegram_service.alert_automation_started()))
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
                loaded += 1

            mode_label = "paper" if current_is_paper else "LIVE"
            if loaded:
                logger.info(f"Scheduler: restored {mode_label} metrics for {loaded} strategies from DB")
        except Exception as e:
            logger.warning(f"Scheduler: could not restore metrics from DB (will rebuild): {e}")

    async def _auto_register_agents(self):
        """Load enabled agents from DB and register them for automated trading.
        
        Also seeds default traders and assigns unassigned agents to Trader Alpha.
        For agents with no trade history, run a quick backtest to seed their
        metrics so the portfolio manager gives them a fair allocation.
        """
        try:
            # Load traders from DB (seed defaults only if none exist yet)
            from app.database import get_async_session
            from app.models import Agent as DBAgent
            from sqlalchemy import select
            async with get_async_session() as session:
                self._traders = await trader_service.seed_default_traders(session)
                if self._traders:
                    alpha = self._traders[0]
                    # Only assign unassigned agents if any exist (one-time migration guard)
                    unassigned_result = await session.execute(
                        select(DBAgent).where(DBAgent.trader_id.is_(None))
                    )
                    unassigned = unassigned_result.scalars().all()
                    if unassigned:
                        await trader_service.assign_existing_agents_to_trader(session, alpha["id"])
                        logger.info(f"Trader layer: assigned {len(unassigned)} unassigned strategies → {alpha['name']}")
                    else:
                        logger.info(f"Trader layer: {len(self._traders)} traders loaded")

            agents = await self._fetch_agents_from_db()

            registered = 0
            for agent in agents:
                if agent.get("is_enabled"):
                    self.register_agent(agent)
                    registered += 1

                    # Bootstrap metrics from backtest if agent has never traded
                    metrics = self._agent_metrics.get(agent["id"])
                    if metrics and metrics.total_runs == 0:
                        await self._bootstrap_from_backtest(agent)

            logger.info(f"Auto-registered {registered}/{len(agents)} enabled agents for trading")
        except Exception as e:
            logger.error(f"Failed to auto-register agents: {e}")

    async def _bootstrap_from_backtest(self, agent: dict):
        """Run a quick backtest and seed agent metrics so new agents get fair allocation."""
        agent_id = agent["id"]
        pairs = agent.get("trading_pairs", [])
        symbol = pairs[0] if pairs else None
        if not symbol:
            logger.warning(f"Agent {agent_id} has no trading pairs — skipping bootstrap")
            return
        strategy = agent.get("strategy_type", "momentum")
        timeframe = agent.get("timeframe", "1h")

        # Use the same strategy-aware SL/TP defaults as the live trading path
        import app.strategies as strategy_registry
        _BOOTSTRAP_RR = strategy_registry.bootstrap_rr()
        _sl_pct, _tp_pct = _BOOTSTRAP_RR.get(strategy, (2.5, 6.0))
        _sl_cfg = agent.get("stop_loss_pct", _sl_pct) or _sl_pct
        _tp_cfg = agent.get("take_profit_pct", _tp_pct) or _tp_pct
        if _tp_cfg < _sl_cfg * 2.0:
            _tp_cfg = round(_sl_cfg * 2.0, 1)

        try:
            from app.services.backtest import BacktestConfig
            config = BacktestConfig(
                symbol=symbol,
                interval=timeframe,
                initial_balance=10000.0,
                position_size_pct=0.1,
                stop_loss_pct=_sl_cfg / 100,
                take_profit_pct=_tp_cfg / 100,
                strategy=strategy,
                candle_limit=2000,
            )
            result = await self.backtest_engine.run_backtest(config)

            # Seed the pre-trade backtest cache so the first trade doesn't re-run it
            self._backtest_cache[f"{agent_id}:{symbol}"] = (result, datetime.now())

            metrics = self._agent_metrics[agent_id]
            metrics.win_rate = result.win_rate
            # Do NOT seed total_pnl from backtest — that would show fake historical P&L
            # before any live trade has been placed. Only the win_rate prior is useful here.

            # Persist the bootstrap backtest to DB for historical tracking
            try:
                from app.database import get_async_session
                from app.models import BacktestRecord
                async with get_async_session() as session:
                    record = BacktestRecord(
                        agent_id=agent_id,
                        symbol=symbol,
                        strategy=strategy,
                        interval=timeframe,
                        config_params={
                            "initial_balance": 10000.0,
                            "position_size_pct": 0.1,
                            "stop_loss_pct": _sl_cfg / 100,
                            "take_profit_pct": _tp_cfg / 100,
                        },
                        total_trades=result.total_trades,
                        winning_trades=result.winning_trades,
                        losing_trades=result.losing_trades,
                        win_rate=result.win_rate,
                        total_pnl=result.total_pnl,
                        net_pnl=result.net_pnl,
                        total_fees=result.total_fees,
                        max_drawdown=result.max_drawdown,
                        sharpe_ratio=result.sharpe_ratio,
                        avg_trade_pnl=result.avg_trade_pnl,
                        profit_factor=result.profit_factor,
                        equity_curve=result.equity_curve[-200:],
                        trades_data=result.trades[-50:],
                        source="bootstrap",
                        candle_count=len(result.equity_curve),
                    )
                    session.add(record)
                    await session.commit()
            except Exception as persist_err:
                logger.debug(f"Failed to persist bootstrap backtest: {persist_err}")

            logger.info(
                f"Bootstrapped {agent.get('name', agent_id)} from backtest: "
                f"win_rate={(metrics.win_rate or 0):.1%}, net_pnl=${(metrics.total_pnl or 0):.2f}, "
                f"fees=${(result.total_fees or 0):.2f}, sharpe={(result.sharpe_ratio or 0):.2f}"
            )
        except Exception as e:
            logger.warning(f"Backtest bootstrap failed for {agent_id}: {e}")
    
    async def stop(self):
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        logger.info("Agent scheduler stopped")
        asyncio.create_task(_send_telegram(telegram_service.alert_automation_stopped()))
    def register_agent(self, agent_config: dict):
        agent_id = agent_config['id']
        was_absent = agent_id not in self._enabled_agents
        self._enabled_agents[agent_id] = agent_config

        # Clear stale backtest cache so the agent gets a fresh evaluation
        # instead of being immediately re-blocked by old results.
        stale_keys = [k for k in self._backtest_cache if k.startswith(f"{agent_id}:")]
        for k in stale_keys:
            del self._backtest_cache[k]
        if stale_keys:
            logger.info(f"Cleared {len(stale_keys)} stale backtest cache entries for re-enabled agent {agent_id}")

        # Ensure agent has a metrics entry so it's visible to allocation decisions
        if agent_id not in self._agent_metrics:
            self._agent_metrics[agent_id] = AgentMetrics(
                agent_id=agent_id,
                # Neutral prior: new agents start at 50% win rate so the
                # portfolio manager doesn't starve them of allocation.
                win_rate=0.5,
            )
        logger.info(f"Registered agent {agent_id} for automated execution")
    
    def unregister_agent(self, agent_id: str):
        if agent_id in self._enabled_agents:
            del self._enabled_agents[agent_id]
            logger.info(f"Unregistered agent {agent_id}")
    
    async def _scheduler_loop(self):
        _last_sync: Optional[datetime] = None
        while self._running:
            try:
                # LIVE POSITION SYNC: Every 30 seconds when in live mode
                if _last_sync is None or (datetime.now() - _last_sync).total_seconds() >= 30:
                    asyncio.create_task(position_sync_service.sync_once())
                    _last_sync = datetime.now()

                # CIRCUIT BREAKER: Check daily loss limit before running agents
                if self._check_circuit_breaker():
                    await asyncio.sleep(60)
                    continue

                # US OPEN SESSION MANAGEMENT
                # Pre-open sweep: tighten profitable SLs to breakeven at 12:30 UTC
                await self._maybe_us_open_preopen_sweep()
                _us_sess = self._get_market_session_info()

                # Trader heartbeat: emit a concise check-in every 30 minutes so
                # operators can see that trader processes are active even when
                # market conditions produce few/no entries.
                await self._maybe_emit_trader_checkins(_us_sess)

                if _us_sess["in_blackout"]:
                    # Blackout window (default 12:45–13:30 UTC): block all new entries.
                    # Position monitoring (SL/TP exits) continues normally.
                    if not self._us_open_blackout_notified:
                        asyncio.create_task(team_chat.add_message(
                            agent_role="risk_manager",
                            content=(
                                "🔴 **US Open Blackout active — no new entries (12:45–13:30 UTC).** "
                                "The US equity open is the most volatile window of the day. "
                                "Institutional order flow routinely fades the Asian/European trend, "
                                "triggering stop cascades and sharp reversals. "
                                "All open positions are monitored. Sitting on hands until 13:30 UTC. ⏸️"
                            ),
                            message_type="alert",
                        ))
                        self._us_open_blackout_notified = True
                    # Still monitor exits, but skip new entry decisions
                    await self._monitor_open_positions()
                    await asyncio.sleep(60)
                    continue
                else:
                    if self._us_open_blackout_notified:
                        # Blackout just ended — announce confirmation window
                        asyncio.create_task(team_chat.add_message(
                            agent_role="risk_manager",
                            content=(
                                "📊 **US Open Blackout lifted — confirmation window now active (13:30–14:15 UTC).** "
                                "The opening volatility is settling. We can re-enter but require "
                                f"**{_us_sess['confirmation_confidence']:.0%} confidence** minimum — "
                                "direction must be proven, not guessed. Trade only high-conviction "
                                "setups aligned with the US session bias. ✅"
                            ),
                            message_type="alert",
                        ))
                    self._us_open_blackout_notified = False

                # OVERNIGHT DEAD ZONE NO-OP
                # Skip team/trader/agent decision loops to conserve LLM/API tokens.
                # Position monitoring remains active so exits still trigger.
                if _us_sess.get("in_dead_zone") and _us_sess.get("dead_zone_noop_enabled", True):
                    if not self._dead_zone_noop_notified:
                        asyncio.create_task(team_chat.add_message(
                            agent_role="risk_manager",
                            content=(
                                "🌙 **Overnight dead zone no-op active — decision engines paused.** "
                                "Skipping trader/team/agent analysis to conserve LLM tokens during thin liquidity "
                                "window. Open positions are still monitored for SL/TP exits."
                            ),
                            message_type="alert",
                        ))
                        self._dead_zone_noop_notified = True

                    await self._monitor_open_positions()
                    await asyncio.sleep(60)
                    continue
                else:
                    if self._dead_zone_noop_notified:
                        asyncio.create_task(team_chat.add_message(
                            agent_role="risk_manager",
                            content=(
                                "🌅 **Overnight dead zone ended — decision engines resumed.** "
                                "Trader/team/agent cycles are back online for active-session execution."
                            ),
                            message_type="alert",
                        ))
                    self._dead_zone_noop_notified = False

                # TEAM DECISION TIER: Run every 15 minutes (900 seconds)
                # Market regime and allocation decisions don't change meaningfully in 5 min.
                # Cutting from 5→15 min saves ~24 LLM calls/hour from this tier alone.
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 900:
                    logger.info("Running team analysis tier")
                    await self._run_team_analysis()
                    self._last_team_analysis = datetime.now()

                # INDIVIDUAL AGENT TIER (EXISTING): Run per-agent on their schedule
                await self._run_enabled_agents()

                # DAILY REPORT TIER: Generate once per hour (catches end-of-day)
                await self._maybe_generate_daily_report()

                # DAILY EMAIL: Send once at 5pm
                await self._maybe_send_daily_email()

                # POSITION MONITORING: Check SL/TP on every loop iteration
                await self._monitor_open_positions()

            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")

            await asyncio.sleep(60)

    async def _maybe_emit_trader_checkins(self, session_info: Optional[dict] = None) -> None:
        """Post a heartbeat check-in from each enabled trader every 30 minutes."""
        if not self._traders:
            return

        now = datetime.now()
        if self._last_trader_checkin and (now - self._last_trader_checkin).total_seconds() < 1800:
            return

        _session_label = (session_info or {}).get("session", "unknown").replace("_", " ")

        # Build latest execution lookup once for this heartbeat.
        latest_exec_by_agent: Dict[str, datetime] = {}
        for run in reversed(self._agent_runs):
            if not run.executed:
                continue
            if run.agent_id not in latest_exec_by_agent:
                latest_exec_by_agent[run.agent_id] = run.timestamp

        for trader in [t for t in self._traders if t.get("is_enabled", True)]:
            try:
                trader_id = trader.get("id")
                trader_name = trader.get("name", "Trader")
                trader_cfg = trader.get("config") or {}
                trader_avatar = trader_cfg.get("avatar", "🤖") if isinstance(trader_cfg, dict) else "🤖"
                alloc = float(self._trader_allocations.get(trader_id, trader.get("allocation_pct", 0.0) or 0.0))

                trader_agents = [
                    a for a in self._enabled_agents.values()
                    if a.get("trader_id") == trader_id and a.get("is_enabled", True)
                ]
                trader_agent_ids = {a.get("id") for a in trader_agents}

                latest_exec = None
                for aid in trader_agent_ids:
                    ts = latest_exec_by_agent.get(aid)
                    if ts and (latest_exec is None or ts > latest_exec):
                        latest_exec = ts

                if latest_exec is None:
                    last_exec_text = "No recent executions yet"
                else:
                    mins_ago = max(0, int((now - latest_exec).total_seconds() // 60))
                    if mins_ago < 1:
                        last_exec_text = "Last execution: just now"
                    else:
                        last_exec_text = f"Last execution: {mins_ago}m ago"

                content = (
                    f"30-minute check-in: actively managing **{len(trader_agents)}** agent(s) "
                    f"at **{alloc:.1f}%** fund allocation. "
                    f"Session: **{_session_label}**. {last_exec_text}."
                )

                await team_chat.add_message(
                    agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
                    content=content,
                    message_type="analysis",
                    metadata={
                        "_override_name": trader_name,
                        "_override_avatar": trader_avatar,
                        "trader_checkin": True,
                        "allocation_pct": alloc,
                        "active_agents": len(trader_agents),
                        "session": _session_label,
                    },
                )
            except Exception as e:
                logger.debug(f"Trader check-in emit failed for {trader.get('name', 'trader')}: {e}")

        self._last_trader_checkin = now

    # ------------------------------------------------------------------
    # Circuit Breaker (11.6)
    # ------------------------------------------------------------------
    _DAILY_LOSS_PCT = 5.0          # fallback if trading gates not loaded
    _DAILY_TRADE_MAX = 20          # fallback if trading gates not loaded
    _cb_halted: bool = False
    _cb_halt_reason: str = ""

    def _check_circuit_breaker(self) -> bool:
        """Return True (and log) if trading should be halted this cycle."""
        try:
            from app.api.routes.settings import get_trading_prefs, get_trading_gates, get_risk_limits
            prefs = get_trading_prefs()
            gates = get_trading_gates()
            risk = get_risk_limits()
            fund_size = getattr(prefs, "total_fund_usd", 10000) or 10000
            _daily_loss_pct = risk.max_daily_loss_pct
            _daily_trade_max = gates.circuit_breaker_max_trades
        except Exception:
            fund_size = 10000
            _daily_loss_pct = self._DAILY_LOSS_PCT
            _daily_trade_max = self._DAILY_TRADE_MAX

        today_pnl = risk_manager.get_daily_pnl() if hasattr(risk_manager, "get_daily_pnl") else 0.0
        today_trades = getattr(risk_manager, "_daily_trade_count", 0)
        max_loss = fund_size * (_daily_loss_pct / 100)

        if today_pnl < -max_loss:
            reason = f"Daily loss limit breached: {today_pnl:+.2f} USD (limit -{max_loss:.0f} USD)"
            if not self._cb_halted or self._cb_halt_reason != reason:
                self._cb_halted = True
                self._cb_halt_reason = reason
                logger.warning(f"🔴 CIRCUIT BREAKER: {reason}")
                asyncio.create_task(team_chat.add_message(
                    agent_role="risk_manager",
                    content=f"🔴 **Circuit breaker activated** — {reason}. All new positions halted.",
                    message_type="alert",
                ))
            return True

        if today_trades >= _daily_trade_max:
            reason = f"Daily trade limit reached: {today_trades} trades (max {_daily_trade_max})"
            if not self._cb_halted or self._cb_halt_reason != reason:
                self._cb_halted = True
                self._cb_halt_reason = reason
                logger.warning(f"🔴 CIRCUIT BREAKER: {reason}")
                asyncio.create_task(team_chat.add_message(
                    agent_role="risk_manager",
                    content=f"🔴 **Circuit breaker activated** — {reason}. All new positions halted.",
                    message_type="alert",
                ))
            return True

        if self._cb_halted:
            logger.info("✅ Circuit breaker cleared — resuming normal operation")
            self._cb_halted = False
            self._cb_halt_reason = ""
        return False

    async def _get_daily_fee_pressure(self) -> dict:
        """Return UTC-day fee-budget usage so entry quality can tighten immediately."""
        try:
            from app.api.routes.settings import get_trading_gates

            gates = get_trading_gates()
            max_daily_fees_pct = float(getattr(gates, "max_daily_fees_pct", 0.5) or 0.5)
            fee_coverage_guard_enabled = bool(getattr(gates, "fee_coverage_guard_enabled", True))
            fee_coverage_min_ratio = float(getattr(gates, "fee_coverage_min_ratio", 2.5) or 2.5)
            fee_coverage_min_fees_usd = float(getattr(gates, "fee_coverage_min_fees_usd", 25.0) or 25.0)
            fee_coverage_window_trades = int(getattr(gates, "fee_coverage_window_trades", 60) or 60)
            fee_coverage_min_closed_trades = int(getattr(gates, "fee_coverage_min_closed_trades", 8) or 8)
            fee_coverage_include_slippage = bool(getattr(gates, "fee_coverage_include_slippage", True))
            fee_coverage_slippage_bps = float(getattr(gates, "fee_coverage_slippage_bps", 2.0) or 2.0)
            fee_coverage_include_funding = bool(getattr(gates, "fee_coverage_include_funding", True))
        except Exception:
            max_daily_fees_pct = 0.5
            fee_coverage_guard_enabled = True
            fee_coverage_min_ratio = 2.5
            fee_coverage_min_fees_usd = 25.0
            fee_coverage_window_trades = 60
            fee_coverage_min_closed_trades = 8
            fee_coverage_include_slippage = True
            fee_coverage_slippage_bps = 2.0
            fee_coverage_include_funding = True

        metrics = {
            "daily_fees_paid": 0.0,
            "daily_fees_pct": 0.0,
            "max_daily_fees_pct": max_daily_fees_pct,
            "budget_used_ratio": 0.0,
            "realized_pnl": 0.0,
            "total_fees": 0.0,
            "fee_coverage_ratio": None,
            "fee_coverage_guard_enabled": fee_coverage_guard_enabled,
            "fee_coverage_min_ratio": fee_coverage_min_ratio,
            "fee_coverage_min_fees_usd": fee_coverage_min_fees_usd,
            "fee_coverage_window_trades": fee_coverage_window_trades,
            "fee_coverage_min_closed_trades": fee_coverage_min_closed_trades,
            "fee_coverage_include_slippage": fee_coverage_include_slippage,
            "fee_coverage_slippage_bps": fee_coverage_slippage_bps,
            "fee_coverage_include_funding": fee_coverage_include_funding,
            "closed_trades_count": 0,
            "gross_realized_pnl": 0.0,
            "net_realized_edge": 0.0,
            "slippage_costs": 0.0,
            "funding_costs": 0.0,
            "fee_coverage_guard_active": False,
        }

        try:
            day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            async with get_async_session() as session:
                result = await session.execute(
                    select(sqlfunc.coalesce(sqlfunc.sum(Trade.fee), 0.0)).where(
                        Trade.user_id == "default-user",
                        Trade.is_paper.is_(_is_paper_mode()),
                        Trade.status == OrderStatus.FILLED,
                        Trade.created_at >= day_start,
                    )
                )
                daily_fees_paid = float(result.scalar_one_or_none() or 0.0)

            capital_base = self._total_capital or 50_000.0
            daily_fees_pct = (daily_fees_paid / capital_base) * 100 if capital_base > 0 else 0.0
            budget_used_ratio = (daily_fees_pct / max_daily_fees_pct) if max_daily_fees_pct > 0 else 0.0

            metrics.update({
                "daily_fees_paid": daily_fees_paid,
                "daily_fees_pct": daily_fees_pct,
                "budget_used_ratio": budget_used_ratio,
            })

            # Fee coverage diagnostics (paper mode): compute a rolling net-edge
            # ratio using recent closed trades. This avoids leverage-based boosts
            # and evaluates edge quality after real execution costs.
            if _is_paper_mode():
                closed_trades = await paper_trading.get_closed_trades(limit=fee_coverage_window_trades)
                closed_count = len(closed_trades)
                total_fees = float(sum(float(t.get("fee", 0.0) or 0.0) for t in closed_trades))
                gross_realized_pnl = float(
                    sum(
                        float(t.get("gross_pnl", (float(t.get("net_pnl", 0.0) or 0.0) + float(t.get("fee", 0.0) or 0.0))))
                        for t in closed_trades
                    )
                )
                realized_pnl = float(sum(float(t.get("net_pnl", 0.0) or 0.0) for t in closed_trades))

                slippage_costs = 0.0
                if fee_coverage_include_slippage and fee_coverage_slippage_bps > 0:
                    slippage_rate = fee_coverage_slippage_bps / 10_000.0
                    for t in closed_trades:
                        qty = abs(float(t.get("quantity", 0.0) or 0.0))
                        entry = abs(float(t.get("entry_price", 0.0) or 0.0))
                        exit_ = abs(float(t.get("exit_price", 0.0) or 0.0))
                        # Two-leg slippage estimate for round-trip fills.
                        slippage_costs += ((qty * entry) + (qty * exit_)) * slippage_rate

                # Funding is included as an explicit placeholder so the metric
                # contract remains stable while exchange funding data is wired in.
                funding_costs = 0.0
                if fee_coverage_include_funding:
                    funding_costs = 0.0

                net_realized_edge = realized_pnl - slippage_costs - funding_costs
                fee_coverage_ratio = (net_realized_edge / total_fees) if total_fees > 0 else None

                metrics.update({
                    "realized_pnl": realized_pnl,
                    "total_fees": total_fees,
                    "closed_trades_count": closed_count,
                    "gross_realized_pnl": gross_realized_pnl,
                    "net_realized_edge": net_realized_edge,
                    "slippage_costs": slippage_costs,
                    "funding_costs": funding_costs,
                    "fee_coverage_ratio": fee_coverage_ratio,
                })

                if (
                    fee_coverage_guard_enabled
                    and closed_count >= fee_coverage_min_closed_trades
                    and total_fees >= fee_coverage_min_fees_usd
                    and fee_coverage_ratio is not None
                    and fee_coverage_ratio < fee_coverage_min_ratio
                ):
                    metrics["fee_coverage_guard_active"] = True
        except Exception as exc:
            logger.debug(f"Daily fee pressure lookup failed: {exc}")

        return metrics

    async def _determine_leverage(
        self,
        side: str,
        confidence: float,
        entry_price: float,
        base_position_value: float,
        total_capital: float,
        current_positions: List[dict],
    ) -> dict:
        """Resolve leverage tier, cap it against current portfolio usage, and compute liquidation."""
        try:
            from app.api.routes.settings import get_trading_gates, get_risk_limits

            gates = get_trading_gates()
            limits = get_risk_limits()
        except Exception:
            return {
                "leverage": 1.0,
                "margin_used": base_position_value,
                "leveraged_notional": base_position_value,
                "liquidation_price": None,
            }

        leverage = 1.0
        max_leverage = float(getattr(limits, "max_leverage", 1.0) or 1.0)
        if bool(getattr(gates, "leverage_enabled", True)) and confidence >= float(getattr(gates, "leverage_confidence_threshold", 0.75) or 0.75):
            tier_1 = min(float(getattr(gates, "leverage_tier_1_multiplier", 2.0) or 2.0), max_leverage)
            tier_2 = min(float(getattr(gates, "leverage_tier_2_multiplier", 3.0) or 3.0), max_leverage)
            tier_3 = min(float(getattr(gates, "leverage_tier_3_multiplier", 5.0) or 5.0), max_leverage)

            if confidence >= float(getattr(gates, "leverage_tier_3_min_confidence", 0.95) or 0.95):
                leverage = max(1.0, tier_3)
            elif confidence >= float(getattr(gates, "leverage_tier_2_min_confidence", 0.85) or 0.85):
                leverage = max(1.0, tier_2)
            elif confidence >= float(getattr(gates, "leverage_tier_1_min_confidence", 0.75) or 0.75):
                leverage = max(1.0, tier_1)

        current_notional = sum(
            p.get("notional", (p.get("quantity", 0) or 0) * (p.get("current_price") or p.get("entry_price") or 0))
            for p in current_positions
        )
        max_leveraged_notional = 0.0
        if total_capital > 0:
            max_leveraged_notional = total_capital * (float(getattr(limits, "max_leveraged_notional_pct", 200.0) or 200.0) / 100)

        tier_1_floor = min(float(getattr(gates, "leverage_tier_1_multiplier", 2.0) or 2.0), max_leverage)
        tier_2_floor = min(float(getattr(gates, "leverage_tier_2_multiplier", 3.0) or 3.0), max_leverage)
        while leverage > 1.0 and max_leveraged_notional > 0:
            leveraged_notional = base_position_value * leverage
            if current_notional + leveraged_notional <= max_leveraged_notional:
                break
            if leverage > tier_2_floor:
                leverage = tier_2_floor
            elif leverage > tier_1_floor:
                leverage = tier_1_floor
            else:
                leverage = 1.0

        leveraged_notional = base_position_value * leverage
        liquidation_price = risk_manager.calculate_liquidation_price(entry_price, side, leverage)
        return {
            "leverage": leverage,
            "margin_used": base_position_value,
            "leveraged_notional": leveraged_notional,
            "liquidation_price": liquidation_price,
        }

    # ------------------------------------------------------------------
    # US Market Open Session Management
    # ------------------------------------------------------------------

    def _get_market_session_info(self) -> dict:
        """Return current market session context based on UTC time.

        Session windows (all UTC):
            asia              00:00–08:00 — Asian markets, low vol
            london_open       08:00–08:30 — London open accumulation phase
            london_fakeout    08:30–09:00 — Fake-out spike before real direction; confidence dampened
            europe            09:00–12:00 — European session, moderate trends, cleaner signals
            europe_late       12:00 → blackout_start — end of European liquidity
            us_open_chaos     blackout_start → blackout_end (default 12:45–13:30)
            us_open_confirm   blackout_end → confirm_end (default 13:30–14:15)
            us_session        confirm_end → 20:00 — cleanest trending window
            dead_zone         20:00–00:00 — thin volume, noise-heavy; confidence dampened

        The 12:30 UTC pre-open sweep moves profitable SLs to breakeven so that
        the typical 13:00 reversal exits us at scratch rather than a loss.
        After 13:30, direction is established — the confirmation window requires
        high conviction (default 80%) before entering any new position.
        London open (08:30–09:00): sharp spike often reverses; confidence penalty applied.
        Overnight dead zone (20:00–00:00): thin liquidity; confidence penalty for trending strategies.
        """
        from datetime import datetime, timezone as _tz
        now_utc = datetime.now(_tz.utc)
        hhmm = now_utc.hour * 100 + now_utc.minute
        mins_now = now_utc.hour * 60 + now_utc.minute

        try:
            from app.api.routes.settings import get_trading_gates as _gtg_sess
            _g = _gtg_sess()
            blackout_enabled  = _g.us_open_blackout_enabled
            blackout_start    = _g.us_open_blackout_start_utc
            blackout_end      = _g.us_open_blackout_end_utc
            confirm_end       = _g.us_open_confirmation_end_utc
            confirm_thresh    = _g.us_open_confirmation_confidence
            preopen_tighten   = _g.us_open_preopen_sl_tighten
            preopen_tighten_t = _g.us_open_preopen_tighten_utc
            london_fakeout_enabled   = getattr(_g, 'london_open_fakeout_enabled', True)
            london_fakeout_start     = getattr(_g, 'london_open_fakeout_start_utc', 830)
            london_fakeout_end       = getattr(_g, 'london_open_fakeout_end_utc', 900)
            london_fakeout_penalty   = getattr(_g, 'london_open_fakeout_penalty', 0.15)
            london_fakeout_min_conf  = getattr(_g, 'london_open_fakeout_min_confidence', 0.75)
            dead_zone_enabled        = getattr(_g, 'dead_zone_enabled', True)
            dead_zone_start          = getattr(_g, 'dead_zone_start_utc', 2000)
            dead_zone_end            = getattr(_g, 'dead_zone_end_utc', 2359)
            dead_zone_penalty        = getattr(_g, 'dead_zone_penalty', 0.15)
            dead_zone_min_conf       = getattr(_g, 'dead_zone_min_confidence', 0.70)
            dead_zone_noop_enabled   = getattr(_g, 'dead_zone_noop_enabled', True)
        except Exception:
            blackout_enabled  = True
            blackout_start    = 1245
            blackout_end      = 1330
            confirm_end       = 1415
            confirm_thresh    = 0.80
            preopen_tighten   = True
            preopen_tighten_t = 1230
            london_fakeout_enabled  = True
            london_fakeout_start    = 830
            london_fakeout_end      = 900
            london_fakeout_penalty  = 0.15
            london_fakeout_min_conf = 0.75
            dead_zone_enabled       = True
            dead_zone_start         = 2000
            dead_zone_end           = 2359
            dead_zone_penalty       = 0.15
            dead_zone_min_conf      = 0.70
            dead_zone_noop_enabled  = True

        def _hhmm_to_mins(h: int) -> int:
            return (h // 100) * 60 + (h % 100)

        in_blackout          = blackout_enabled and blackout_start <= hhmm < blackout_end
        in_confirmation      = blackout_end <= hhmm < confirm_end
        in_preopen           = preopen_tighten and preopen_tighten_t <= hhmm < blackout_start
        in_london_fakeout    = london_fakeout_enabled and london_fakeout_start <= hhmm < london_fakeout_end
        if dead_zone_start <= dead_zone_end:
            in_dead_zone = dead_zone_enabled and dead_zone_start <= hhmm < dead_zone_end
        else:
            # Wrap-around window support (e.g. 20:00 -> 02:00)
            in_dead_zone = dead_zone_enabled and (hhmm >= dead_zone_start or hhmm < dead_zone_end)
        mins_to_open         = max(0, _hhmm_to_mins(blackout_start) - mins_now) if hhmm < blackout_start else 0

        if hhmm < 800:
            session = "asia"
        elif hhmm < london_fakeout_start:
            session = "london_open"
        elif in_london_fakeout:
            session = "london_fakeout"
        elif hhmm < 1200:
            session = "europe"
        elif hhmm < blackout_start:
            session = "europe_late"
        elif in_blackout:
            session = "us_open_chaos"
        elif in_confirmation:
            session = "us_open_confirmation"
        elif hhmm < dead_zone_start:
            session = "us_session"
        else:
            session = "dead_zone"

        return {
            "session": session,
            "utc_hhmm": hhmm,
            "in_blackout": in_blackout,
            "in_confirmation": in_confirmation,
            "in_preopen": in_preopen,
            "in_london_fakeout": in_london_fakeout,
            "london_fakeout_penalty": london_fakeout_penalty,
            "london_fakeout_min_conf": london_fakeout_min_conf,
            "in_dead_zone": in_dead_zone,
            "dead_zone_penalty": dead_zone_penalty,
            "dead_zone_min_conf": dead_zone_min_conf,
            "dead_zone_noop_enabled": dead_zone_noop_enabled,
            "minutes_to_us_open": mins_to_open,
            "confirmation_confidence": confirm_thresh,
        }

    async def _maybe_us_open_preopen_sweep(self):
        """At the pre-open tighten time (default 12:30 UTC), move all profitable
        open position SLs to breakeven so that a US-open reversal cannot turn
        winners into losses.  Runs at most once per UTC calendar day.
        """
        from datetime import datetime, timezone as _tz
        today_str = datetime.now(_tz.utc).date().isoformat()
        if self._us_open_sweep_date == today_str:
            return  # already ran today

        sess = self._get_market_session_info()
        if not sess["in_preopen"]:
            return  # not yet time (or window passed for today)

        self._us_open_sweep_date = today_str
        logger.info("US Open pre-open sweep: tightening profitable position SLs to breakeven")

        try:
            positions = list(await paper_trading.get_positions() or [])
            try:
                from app.api.routes.settings import get_trading_prefs as _gtp_sweep
                if not _gtp_sweep().paper_trading_default:
                    from app.services.live_trading import live_trading as _lt_sw
                    positions += list(await _lt_sw.get_positions() or [])
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"US Open sweep: could not fetch positions: {e}")
            return

        if not positions:
            return

        tightened = 0
        for _sw_pos in positions:
            try:
                _sw_is_paper = getattr(_sw_pos, "is_paper", True)
                if _sw_is_paper:
                    _sw_svc = paper_trading
                else:
                    from app.services.live_trading import live_trading as _lt_sw2
                    _sw_svc = _lt_sw2

                _sw_entry = _sw_pos.entry_price or 0
                if _sw_entry <= 0:
                    continue

                _sw_price = await _sw_svc.fetch_current_price(_sw_pos.symbol)
                if _sw_price <= 0:
                    continue

                _sw_fee_rt = _sw_svc.fee_rate_for(_sw_pos.symbol) * 2
                _sw_side = _sw_pos.side.value if hasattr(_sw_pos.side, "value") else str(_sw_pos.side)
                _sw_is_short = _sw_side.lower() == "sell"
                _sw_existing_sl = getattr(_sw_pos, "stop_loss_price", None)

                if _sw_is_short:
                    if (_sw_entry - _sw_price) / _sw_entry <= _sw_fee_rt:
                        continue  # not yet profitable after fees
                    _sw_be_sl = _sw_entry * (1 - _sw_fee_rt)
                    if _sw_existing_sl is None or _sw_be_sl < _sw_existing_sl:
                        await _sw_svc.update_position_sl_tp(_sw_pos.id, stop_loss_price=_sw_be_sl)
                        tightened += 1
                        logger.info(f"🔐 Pre-open sweep: {_sw_pos.symbol} SHORT SL → ${_sw_be_sl:.4f} (BE)")
                else:
                    if (_sw_price - _sw_entry) / _sw_entry <= _sw_fee_rt:
                        continue
                    _sw_be_sl = _sw_entry * (1 + _sw_fee_rt)
                    if _sw_existing_sl is None or _sw_be_sl > _sw_existing_sl:
                        await _sw_svc.update_position_sl_tp(_sw_pos.id, stop_loss_price=_sw_be_sl)
                        tightened += 1
                        logger.info(f"🔐 Pre-open sweep: {_sw_pos.symbol} LONG SL → ${_sw_be_sl:.4f} (BE)")
            except Exception as e:
                logger.warning(f"US Open sweep error on {getattr(_sw_pos, 'symbol', '?')}: {e}")

        _sweep_msg = (
            f"🕐 **Pre-open sweep — {tightened} position{'' if tightened == 1 else 's'} protected.** "
            f"Moved SL to breakeven {sess['minutes_to_us_open']} minutes before the US open. "
            f"If the 13:00 UTC reversal hits, we exit at scratch. "
            f"If the trend continues, positions stay open to ride it. 🔐"
            if tightened else
            f"🕐 **Pre-open sweep complete** — no profitable positions to tighten "
            f"(all positions either flat, in loss, or already at breakeven)."
        )
        asyncio.create_task(team_chat.add_message(
            agent_role="risk_manager",
            content=_sweep_msg,
            message_type="alert",
        ))

    def _get_cached_price_levels(self, symbol: str, agent_config: dict):
        """Return PriceLevels from the TA cache, or None if stale/absent.

        The TA cache is populated every agent cycle (5-minute TTL).  The
        position monitor reads from it without triggering new API calls,
        so structural-level snapping is essentially free.
        """
        _tf = agent_config.get("timeframe", "1h")
        _ta_key = f"{symbol}:{_tf}"
        cached = self._ta_cache.get(_ta_key)
        if cached:
            _report, _cached_at = cached
            # Accept levels up to 10 minutes old — twice the normal TTL
            if (datetime.now() - _cached_at).total_seconds() < 600:
                return getattr(_report, "price_levels", None)
        return None

    async def _monitor_open_positions(self):
        """Check open positions against live prices and trigger SL/TP exits."""
        try:
            paper_positions = await paper_trading.get_positions()
            # Also include live positions when in live mode
            live_positions: list = []
            try:
                from app.api.routes.settings import get_trading_prefs
                if not get_trading_prefs().paper_trading_default:
                    from app.services.live_trading import live_trading
                    live_positions = list(await live_trading.get_positions() or [])
            except Exception:
                pass
            positions = list(paper_positions or []) + live_positions
            if not positions:
                return

            for pos in positions:
                try:
                    # Route all trading calls to the correct backend for this position
                    _pos_is_paper = getattr(pos, "is_paper", True)
                    if _pos_is_paper:
                        _svc = paper_trading
                    else:
                        from app.services.live_trading import live_trading as _live_svc
                        _svc = _live_svc

                    current_price = await paper_trading.fetch_current_price(pos.symbol)
                    if current_price <= 0:
                        logger.warning(f"SL/TP monitor: skipping {pos.symbol} — bad price {current_price}")
                        continue

                    entry = pos.entry_price or 0
                    if entry <= 0:
                        logger.warning(f"SL/TP monitor: skipping {pos.symbol} — bad entry {entry}")
                        continue

                    pos_side = pos.side.value if hasattr(pos.side, 'value') else str(pos.side)
                    is_short = pos_side.lower() == 'sell'

                    # Use stored TA-informed SL/TP from position, fall back to agent config %
                    agent_config = self._enabled_agents.get(pos.agent_id, {})
                    # Structural levels for S/R-aware SL adjustments (from TA cache)
                    _pos_levels = self._get_cached_price_levels(pos.symbol, agent_config)
                    sl_pct = agent_config.get('stop_loss_pct', 3.5) or 3.5
                    tp_pct = agent_config.get('take_profit_pct', 7.0) or 7.0
                    try:
                        from app.api.routes.settings import get_risk_limits as _get_risk_limits
                        _liq_buffer_pct = float(_get_risk_limits().liquidation_buffer_pct or 12.5)
                    except Exception:
                        _liq_buffer_pct = 12.5
                    trailing_pct = (
                        getattr(pos, 'trailing_stop_pct', None)
                        or agent_config.get('trailing_stop_pct')
                        or 4.0  # default 4% trailing stop — wide enough not to snap on noise
                    )

                    stored_sl = getattr(pos, 'stop_loss_price', None)
                    stored_tp = getattr(pos, 'take_profit_price', None)

                    if is_short:
                        sl_price = stored_sl if stored_sl is not None else entry * (1 + sl_pct / 100)
                        tp_price = stored_tp if stored_tp is not None else entry * (1 - tp_pct / 100)
                    else:
                        sl_price = stored_sl if stored_sl is not None else entry * (1 - sl_pct / 100)
                        tp_price = stored_tp if stored_tp is not None else entry * (1 + tp_pct / 100)

                    # ── Minimum TP distance: TP must clear round-trip fees + profit floor ──
                    # Prevents a stored TP tightened by LLM review from triggering at a
                    # net loss.  Minimum = round-trip taker fees (e.g. 0.2% USDT) + 0.3%
                    # net profit floor, so every TP hit books a genuine gain.
                    _fee_rt_min = _svc.fee_rate_for(pos.symbol) * 2
                    _min_tp_move = _fee_rt_min + 0.003  # fees + 0.3% net profit floor
                    if entry and tp_price is not None:
                        if is_short:
                            _min_tp_price = entry * (1 - _min_tp_move)
                            if tp_price > _min_tp_price:
                                logger.info(
                                    f"⚠️  TP too close: {pos.symbol} SHORT TP ${tp_price:.4f} "
                                    f"< {_min_tp_move:.2%} from entry — bumped to ${_min_tp_price:.4f}"
                                )
                                await _svc.update_position_sl_tp(pos.id, take_profit_price=_min_tp_price)
                                tp_price = _min_tp_price
                        else:
                            _min_tp_price = entry * (1 + _min_tp_move)
                            if tp_price < _min_tp_price:
                                logger.info(
                                    f"⚠️  TP too close: {pos.symbol} LONG TP ${tp_price:.4f} "
                                    f"< {_min_tp_move:.2%} from entry — bumped to ${_min_tp_price:.4f}"
                                )
                                await _svc.update_position_sl_tp(pos.id, take_profit_price=_min_tp_price)
                                tp_price = _min_tp_price

                    # Update watermark: highest_price for longs, lowest_price for shorts
                    highest = getattr(pos, 'highest_price', None) or entry
                    if is_short:
                        if current_price < highest:
                            highest = current_price
                            await _svc.update_highest_price(pos.id, current_price, is_short=True)
                    else:
                        if current_price > highest:
                            highest = current_price
                            await _svc.update_highest_price(pos.id, current_price, is_short=False)

                    risk_config = RiskConfig(
                        stop_loss_pct=sl_pct,
                        take_profit_pct=tp_pct,
                        trailing_stop_pct=trailing_pct,
                        liquidation_buffer_pct=_liq_buffer_pct,
                    )

                    # ── Stage 1 & 2: Breakeven + profit-lock SL progression ──────
                    # Runs independently of trailing stop, using TP distance milestones.
                    #
                    # Stage 1 — BREAKEVEN (50% of way to TP reached):
                    #   Move SL to entry + round-trip fees (0.12%) so the trade
                    #   can never close at a loss from this point forward.
                    #   Requires at least 0.5% absolute profit to avoid snapping SL
                    #   on noise immediately after entry.
                    #
                    # Stage 2 — PROFIT LOCK (80% of way to TP reached):
                    #   Move SL to entry + 33% of current profit so a meaningful
                    #   portion of the gain is protected, while leaving room for
                    #   the trade to breathe through normal volatility.
                    #
                    # Both stages only ever TIGHTEN the SL (never widen it).
                    # A minimum 0.2% price-move filter prevents DB thrashing.
                    # ─────────────────────────────────────────────────────────
                    _FEE_RT = _svc.fee_rate_for(pos.symbol) * 2  # round-trip taker fee
                    _MIN_PROFIT_TO_BREAKEVEN = 0.005  # require at least 0.5% absolute gain first
                    if entry and tp_price and sl_price is not None:
                        if is_short:
                            _full_move = entry - tp_price          # total move to TP (positive)
                            _current_move = entry - current_price  # how far price has moved (positive = good)
                        else:
                            _full_move = tp_price - entry
                            _current_move = current_price - entry

                        if _full_move > 0 and _current_move > 0:
                            _progress = _current_move / _full_move  # 0.0 → 1.0
                            _abs_profit_pct = _current_move / entry  # actual % gain

                            if is_short:
                                _breakeven_sl = entry * (1 - _FEE_RT)   # just below entry (covers fees)
                                _lock_sl      = entry - (_current_move * 0.50)  # lock HALF of profit
                                # Snap profit-lock to nearest structural level
                                if _pos_levels:
                                    from app.services.technical_analyst import snap_sl_to_structure
                                    _snapped = snap_sl_to_structure(_lock_sl, _pos_levels, current_price, is_short=True, max_widen_pct=0.10)
                                    # Only accept if the snap TIGHTENS (moves SL closer to entry for shorts = higher)
                                    if _snapped < _lock_sl:
                                        _lock_sl = _snapped

                                if _progress >= 0.50 and _lock_sl < sl_price:
                                    # Stage 2: price ≥ 50% to TP — lock half of profit
                                    if (sl_price - _lock_sl) >= current_price * 0.002:
                                        await _svc.update_position_sl_tp(pos.id, stop_loss_price=_lock_sl)
                                        logger.info(
                                            f"🔒 Profit lock: {pos.symbol} SHORT SL "
                                            f"${sl_price:.4f}→${_lock_sl:.4f} "
                                            f"(50% to TP, locking {_current_move * 0.50 / entry:.2%} profit)"
                                        )
                                        sl_price = _lock_sl
                                elif _progress >= 0.30 and _abs_profit_pct >= _MIN_PROFIT_TO_BREAKEVEN and _breakeven_sl < sl_price:
                                    # Stage 1: price ≥ 30% to TP and at least 0.3% profit — breakeven
                                    if (sl_price - _breakeven_sl) >= current_price * 0.002:
                                        await _svc.update_position_sl_tp(pos.id, stop_loss_price=_breakeven_sl)
                                        logger.info(
                                            f"⚖️  Breakeven SL: {pos.symbol} SHORT "
                                            f"${sl_price:.4f}→${_breakeven_sl:.4f} "
                                            f"({_progress:.0%} to TP, fees covered)"
                                        )
                                        sl_price = _breakeven_sl
                            else:
                                _breakeven_sl = entry * (1 + _FEE_RT)   # just above entry (covers fees)
                                _lock_sl      = entry + (_current_move * 0.50)  # lock HALF of profit
                                # Snap profit-lock to nearest structural level
                                if _pos_levels:
                                    from app.services.technical_analyst import snap_sl_to_structure
                                    _snapped = snap_sl_to_structure(_lock_sl, _pos_levels, current_price, is_short=False, max_widen_pct=0.10)
                                    # Only accept if the snap TIGHTENS (moves SL closer to price for longs = higher)
                                    if _snapped > _lock_sl:
                                        _lock_sl = _snapped

                                if _progress >= 0.50 and _lock_sl > sl_price:
                                    # Stage 2: price ≥ 50% to TP — lock half of profit
                                    if (_lock_sl - sl_price) >= current_price * 0.002:
                                        await _svc.update_position_sl_tp(pos.id, stop_loss_price=_lock_sl)
                                        logger.info(
                                            f"🔒 Profit lock: {pos.symbol} LONG SL "
                                            f"${sl_price:.4f}→${_lock_sl:.4f} "
                                            f"(50% to TP, locking {_current_move * 0.50 / entry:.2%} profit)"
                                        )
                                        sl_price = _lock_sl
                                elif _progress >= 0.30 and _abs_profit_pct >= _MIN_PROFIT_TO_BREAKEVEN and _breakeven_sl > sl_price:
                                    # Stage 1: price ≥ 30% to TP and at least 0.3% profit — breakeven
                                    if (_breakeven_sl - sl_price) >= current_price * 0.002:
                                        await _svc.update_position_sl_tp(pos.id, stop_loss_price=_breakeven_sl)
                                        logger.info(
                                            f"⚖️  Breakeven SL: {pos.symbol} LONG "
                                            f"${sl_price:.4f}→${_breakeven_sl:.4f} "
                                            f"({_progress:.0%} to TP, fees covered)"
                                        )
                                        sl_price = _breakeven_sl

                    # ── Stage 3: Trailing stop watermark ──────────────────────────
                    # When price has moved favourably, physically move the SL
                    # so profits are locked in even between LLM review cycles.
                    # Runs after breakeven/lock stages so it can tighten further.
                    #
                    # Activation guard: trailing only kicks in once price has
                    # moved past fee-covered breakeven.  We take the LARGER of
                    #   (a) 1.0× the configured trail distance, and
                    #   (b) the round-trip fee distance (entry × _FEE_RT)
                    # so the trail never activates on a position that hasn't
                    # even paid for itself yet.
                    if trailing_pct and highest:
                        _fee_breakeven_dist = entry * _FEE_RT
                        _trail_activation_distance = max(
                            entry * (trailing_pct / 100) * 1.0,
                            _fee_breakeven_dist,
                        )
                        if is_short:
                            # For shorts: trailing SL moves DOWN as price falls
                            # Only activate once price has moved past fee-covered breakeven
                            _trail_activated = (entry - highest) >= _trail_activation_distance
                            ideal_sl = highest * (1 + trailing_pct / 100)
                            # Trail SL must stay below fee-adjusted breakeven
                            _be_short = entry * (1 - _FEE_RT)
                            if ideal_sl > _be_short:
                                ideal_sl = _be_short
                            # Snap to nearest structural level (resistance above current price)
                            if _pos_levels:
                                from app.services.technical_analyst import snap_sl_to_structure
                                _snapped_trail = snap_sl_to_structure(ideal_sl, _pos_levels, current_price, is_short=True, max_widen_pct=0.10)
                                # Accept if snapped is tighter (lower for shorts) but still above TP
                                if _snapped_trail < ideal_sl and (tp_price is None or _snapped_trail > tp_price):
                                    ideal_sl = _snapped_trail
                            if _trail_activated and (sl_price is None or ideal_sl < sl_price):
                                # Only tighten (lower SL for shorts)
                                if sl_price is None or (sl_price - ideal_sl) >= current_price * 0.002:
                                    await _svc.update_position_sl_tp(
                                        pos.id, stop_loss_price=ideal_sl
                                    )
                                    logger.info(
                                        f"Trailing SL tightened: {pos.symbol} SHORT "
                                        f"SL ${sl_price or 0:.2f}→${ideal_sl:.2f} "
                                        f"(low watermark ${highest:.2f}, trail {trailing_pct}%)"
                                    )
                                    sl_price = ideal_sl
                        else:
                            # For longs: trailing SL moves UP as price rises
                            # Only activate once price has moved past fee-covered breakeven
                            _trail_activated = (highest - entry) >= _trail_activation_distance
                            ideal_sl = highest * (1 - trailing_pct / 100)
                            # Trail SL must stay above fee-adjusted breakeven
                            _be_long = entry * (1 + _FEE_RT)
                            if ideal_sl < _be_long:
                                ideal_sl = _be_long
                            # Snap to nearest structural level (support below current price)
                            if _pos_levels:
                                from app.services.technical_analyst import snap_sl_to_structure
                                _snapped_trail = snap_sl_to_structure(ideal_sl, _pos_levels, current_price, is_short=False, max_widen_pct=0.10)
                                # Accept if snapped is tighter (higher for longs) but still below TP
                                if _snapped_trail > ideal_sl and (tp_price is None or _snapped_trail < tp_price):
                                    ideal_sl = _snapped_trail
                            if _trail_activated and ideal_sl > _be_long and (sl_price is None or ideal_sl > sl_price):
                                # Only tighten (raise SL for longs), and only above entry
                                if sl_price is None or (ideal_sl - sl_price) >= current_price * 0.002:
                                    await _svc.update_position_sl_tp(
                                        pos.id, stop_loss_price=ideal_sl
                                    )
                                    logger.info(
                                        f"Trailing SL tightened: {pos.symbol} LONG "
                                        f"SL ${sl_price or 0:.2f}→${ideal_sl:.2f} "
                                        f"(high watermark ${highest:.2f}, trail {trailing_pct}%)"
                                    )
                                    sl_price = ideal_sl

                    # ── Scale-out: partial profit-taking on the way to TP ──────────
                    # Process each untriggered level. When price crosses a level's
                    # threshold, close that slice, mark the level triggered, and
                    # move SL to breakeven so the remainder can never become a loss.
                    _scale_raw = getattr(pos, 'scale_out_levels', None)
                    if _scale_raw and tp_price and entry:
                        try:
                            _scale_levels = json.loads(_scale_raw)
                            _levels_dirty = False

                            if is_short:
                                _full_move = entry - tp_price
                                _current_move = entry - current_price
                            else:
                                _full_move = tp_price - entry
                                _current_move = current_price - entry

                            if _full_move > 0 and _current_move > 0:
                                _progress = _current_move / _full_move

                                for _lvl in _scale_levels:
                                    if _lvl.get("triggered"):
                                        continue
                                    if _progress >= _lvl["pct_of_tp"]:
                                        _close_pct = _lvl["close_pct"]
                                        _close_qty = max((pos.quantity or 0) * _close_pct, 0.0)
                                        _fee_rate = _svc.fee_rate_for(pos.symbol)

                                        # Only scale out when the tranche itself is net-profitable
                                        # after estimated round-trip fees.
                                        if is_short:
                                            _gross_pnl = (entry - current_price) * _close_qty
                                        else:
                                            _gross_pnl = (current_price - entry) * _close_qty
                                        _est_fees = (entry * _close_qty + current_price * _close_qty) * _fee_rate
                                        _est_net_pnl = _gross_pnl - _est_fees
                                        if _est_net_pnl <= 0:
                                            logger.info(
                                                f"⏭️ Skip scale-out {_close_pct:.0%} {pos.symbol} @ ${current_price:.4g}: "
                                                f"estimated net tranche PnL ${_est_net_pnl:+.2f} <= 0"
                                            )
                                            continue

                                        _result = await _svc.partial_close(
                                            position_id=pos.id,
                                            close_pct=_close_pct,
                                            price=current_price,
                                            agent_id=pos.agent_id,
                                            label=f"scale-out-{_lvl['pct_of_tp']:.0%}",
                                        )
                                        if _result:
                                            _lvl["triggered"] = True
                                            _levels_dirty = True
                                            logger.info(
                                                f"📤 Scale-out {_close_pct:.0%} of {pos.symbol} "
                                                f"@ ${current_price:.4g} "
                                                f"({_lvl['pct_of_tp']:.0%} to TP, "
                                                f"net PnL ${_result['net_pnl']:+.2f}, "
                                                f"qty remaining {_result['remaining_quantity']:.4g})"
                                            )
                                            # Move SL to breakeven after first scale
                                            _FEE_RT_SCALE = _svc.fee_rate_for(pos.symbol) * 2
                                            _be_moved = False
                                            if not is_short:
                                                _be_sl = entry * (1 + _FEE_RT_SCALE)
                                                if sl_price is None or _be_sl > sl_price:
                                                    await _svc.update_position_sl_tp(pos.id, stop_loss_price=_be_sl)
                                                    sl_price = _be_sl
                                                    _be_moved = True
                                            else:
                                                _be_sl = entry * (1 - _FEE_RT_SCALE)
                                                if sl_price is None or _be_sl < sl_price:
                                                    await _svc.update_position_sl_tp(pos.id, stop_loss_price=_be_sl)
                                                    sl_price = _be_sl
                                                    _be_moved = True
                                            # Team chat notification
                                            _so_trader_cfg = agent_config.get("trader_config", {}) if isinstance(agent_config.get("trader_config"), dict) else {}
                                            _so_trader_name = _so_trader_cfg.get("name", agent_config.get("name", "Trader"))
                                            _so_trader_avatar = _so_trader_cfg.get("avatar", agent_config.get("avatar", "🤖"))
                                            try:
                                                await team_chat.log_scale_out(
                                                    trader_name=_so_trader_name,
                                                    trader_avatar=_so_trader_avatar,
                                                    agent_name=pos.agent_id,
                                                    symbol=pos.symbol,
                                                    side=pos_side,
                                                    close_pct=_close_pct,
                                                    close_quantity=_result.get("closed_quantity", 0),
                                                    close_price=current_price,
                                                    realised_pnl=_result.get("net_pnl", 0),
                                                    remaining_quantity=_result.get("remaining_quantity", 0),
                                                    sl_moved_to_breakeven=_be_moved,
                                                    tranche_label=f"{_lvl['pct_of_tp']:.0%} TP",
                                                )
                                            except Exception as _tc_err:
                                                logger.debug(f"team_chat.log_scale_out error: {_tc_err}")

                            if _levels_dirty:
                                # Persist updated triggered flags
                                from app.database import AsyncSessionLocal
                                from app.models import Position as _PosModel
                                async with AsyncSessionLocal() as _db:
                                    _pos_db = await _db.get(_PosModel, pos.id)
                                    if _pos_db:
                                        _pos_db.scale_out_levels = json.dumps(_scale_levels)
                                        await _db.commit()
                                # Refresh pos.quantity so full-exit check uses reduced qty
                                positions_refreshed = await _svc.get_positions(pos.symbol, agent_id=pos.agent_id)
                                _refreshed = next((p for p in positions_refreshed if p.id == pos.id), None)
                                if _refreshed:
                                    pos = _refreshed
                        except Exception as _so_err:
                            logger.debug(f"Scale-out processing error for {pos.symbol}: {_so_err}")

                    position_dict = {
                        'side': pos_side,
                        'entry_price': entry,
                        'stop_loss': sl_price,
                        'take_profit': tp_price,
                        'highest_price': highest,
                    }

                    _liq_check = risk_manager.check_liquidation_risk(
                        side=pos_side,
                        current_price=current_price,
                        liquidation_price=getattr(pos, 'liquidation_price', None),
                        liquidation_buffer_pct=risk_config.liquidation_buffer_pct,
                    )
                    if _liq_check and _liq_check.get('action') == 'FORCE_CLOSE':
                        check = RiskCheckResult(
                            allowed=True,
                            action="exit",
                            reason=(
                                f"Liquidation protection triggered at ${current_price:.2f} "
                                f"({_liq_check['distance_pct']:.2f}% from liquidation ${_liq_check['liquidation_price']:.2f})"
                            ),
                        )
                    else:
                        check = risk_manager.check_exit(position_dict, current_price, risk_config)

                    if is_short:
                        pnl_pct = ((entry - current_price) / entry) * 100
                    else:
                        pnl_pct = ((current_price - entry) / entry) * 100
                    direction = "SHORT" if is_short else "LONG"
                    logger.info(
                        f"SL/TP monitor: {pos.symbol} {direction} | price=${current_price:.2f} entry=${entry:.2f} "
                        f"SL=${sl_price:.2f} TP=${tp_price:.2f} trail={trailing_pct}% wm=${highest:.2f} pnl={pnl_pct:+.2f}% → {check.action}"
                    )

                    if check.action == "exit":
                        logger.info(
                            f"Position exit triggered for {pos.symbol} {direction}: {check.reason} "
                            f"(entry: ${entry:.2f}, current: ${current_price:.2f})"
                        )
                        try:
                            # Re-fetch current position state before exit so P&L is accurate
                            # even if a scale-out fired earlier in this same monitoring loop
                            # iteration (which would leave pos.quantity / pos.realized_pnl stale).
                            _fresh_pos = None
                            if hasattr(_svc, "get_position"):
                                try:
                                    _fresh_pos = await _svc.get_position(pos.id)
                                except Exception:
                                    pass
                            _close_qty = (_fresh_pos.quantity if _fresh_pos else pos.quantity) or pos.quantity
                            _close_entry = (_fresh_pos.entry_price if _fresh_pos else entry) or entry
                            _scale_out_pnl = (
                                (_fresh_pos.realized_pnl or 0.0) if _fresh_pos else (pos.realized_pnl or 0.0)
                            )

                            # Exit: for live positions use close_position (reduce-only);
                            # for paper use place_order which correctly closes via P&L netting.
                            exit_side = "buy" if is_short else "sell"
                            if _pos_is_paper:
                                await _svc.place_order(
                                    symbol=pos.symbol,
                                    side=exit_side,
                                    quantity=_close_qty,
                                    price=current_price,
                                    agent_id=pos.agent_id,
                                )
                            else:
                                await _svc.close_position(pos.id)
                            action_word = "covered" if is_short else "sold"
                            logger.info(f"SL/TP exit executed: {action_word} {_close_qty} {pos.symbol} @ ${current_price:.2f}")

                            # Net P&L for this specific close (after fees on _close_qty only).
                            # This matches exactly the net_pnl shown in the closed trades history
                            # for this exit row. Scale-out P&L was already reported when each
                            # scale-out fired; don't include it here to avoid double-counting.
                            _fee_rate = _svc.fee_rate_for(pos.symbol)
                            _entry_fee = (_close_entry * _close_qty) * _fee_rate
                            _exit_fee = (current_price * _close_qty) * _fee_rate
                            if is_short:
                                _final_pnl = (_close_entry - current_price) * _close_qty - _entry_fee - _exit_fee
                            else:
                                _final_pnl = (current_price - _close_entry) * _close_qty - _entry_fee - _exit_fee

                            # Full position P&L (for metrics/win-rate): includes scale-out profits
                            pnl = _final_pnl + _scale_out_pnl
                            risk_manager.record_pnl(pnl)

                            # Record close time for post-close cooldown
                            _cooldown_key = f"{pos.agent_id}:{pos.symbol}"
                            self._recent_closes[_cooldown_key] = datetime.now()

                            if pos.agent_id and pos.agent_id in self._agent_metrics:
                                m = self._agent_metrics[pos.agent_id]
                                m.total_pnl += pnl
                                # Determine exit reason before recording
                                _exit_type_pre = "trailing-stop" if "Trailing" in check.reason else (
                                    "take-profit" if "Take-profit" in check.reason else "stop-loss"
                                )
                                # Add this close to the agent_runs buffer so win rate is consistent
                                self._agent_runs.append(AgentRun(
                                    agent_id=pos.agent_id,
                                    timestamp=datetime.now(),
                                    symbol=pos.symbol,
                                    signal="sell" if not is_short else "buy",
                                    confidence=0,
                                    price=current_price,
                                    executed=True,
                                    pnl=pnl,
                                    exit_reason=_exit_type_pre,
                                ))
                                # Win rate is updated by _record_run when pnl is set

                            # Log to team chat
                            from app.services.team_chat import team_chat
                            import re as _re
                            exit_type = "trailing-stop" if "Trailing" in check.reason else (
                                "take-profit" if "Take-profit" in check.reason else "stop-loss"
                            )
                            # Use _final_pnl (this close only) so the figure matches the
                            # corresponding row in the closed trades history exactly.
                            # Scale-out P&L was already posted when each scale-out fired.
                            _tc_entry_notional = (_close_entry * _close_qty) if (_close_entry and _close_qty) else 0
                            _tc_pnl_pct = (_final_pnl / _tc_entry_notional * 100) if _tc_entry_notional else 0
                            _tc_sign = "+" if _final_pnl >= 0 else "-"
                            pnl_str = f"{_tc_sign}${abs(_final_pnl):.2f} ({_tc_sign}{abs(_tc_pnl_pct):.2f}%)"
                            # Extract trigger price from reason, dropping the raw pnl % which
                            # would contradict the net figure when fees exceed the price move.
                            _trigger_match = _re.search(r'\$[\d.]+', check.reason)
                            _trigger_info = f" @ ${_trigger_match.group()[1:]}" if _trigger_match else ""
                            await team_chat.add_message(
                                agent_role="execution_coordinator",
                                content=f"📊 **{exit_type.upper()}** {pos.symbol} ({direction}): {pnl_str}{_trigger_info}",
                                message_type="trade",
                            )

                            # Telegram alert — use the right alert type per exit reason
                            if exit_type == "take-profit":
                                asyncio.create_task(_send_telegram(telegram_service.alert_take_profit_hit(
                                    symbol=pos.symbol, side=direction.lower(), pnl=pnl,
                                )))
                            else:
                                asyncio.create_task(_send_telegram(telegram_service.alert_position_closed(
                                    symbol=pos.symbol, side=direction.lower(), pnl=pnl,
                                    close_reason=exit_type.replace("-", " ").title(),
                                )))

                            # Phase 9.2 — update trader drawdown after every close
                            if pos.agent_id:
                                _agent_cfg_dd = self._enabled_agents.get(pos.agent_id, {})
                                _trader_id_dd = _agent_cfg_dd.get("trader_id") or _agent_cfg_dd.get("_trader_id_pre")
                                if _trader_id_dd:
                                    _trader_agents_dd = [
                                        a["id"] for a in self._enabled_agents.values()
                                        if a.get("trader_id") == _trader_id_dd
                                        or a.get("_trader_id_pre") == _trader_id_dd
                                    ]
                                    asyncio.create_task(
                                        _run_drawdown_check(_trader_id_dd, _trader_agents_dd, _is_paper_mode())
                                    )

                        except Exception as e:
                            logger.error(f"Failed to execute SL/TP exit for {pos.symbol}: {e}")

                except Exception as e:
                    logger.warning(f"Position monitor error for {pos.symbol}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Position monitoring failed: {e}")

    # ------------------------------------------------------------------
    # Autonomous SL/TP Review (Fund Manager + TA confluence)
    # ------------------------------------------------------------------

    async def _review_open_position_levels(self):
        """Fund Manager reviews SL/TP on all open positions using TA confluence
        and market context.  Runs during team analysis (every 5 min).
        Adjustments are persisted to DB so the 60-second position monitor
        picks them up immediately."""
        try:
            positions = list(await paper_trading.get_positions() or [])
            if not _is_paper_mode():
                try:
                    from app.services.live_trading import live_trading as _lt_rev
                    positions += list(await _lt_rev.get_positions() or [])
                except Exception:
                    pass
            if not positions:
                return

            confluence = self._current_confluence_scores or {}
            risk_assessment = self._current_risk_assessment
            analyst_report = self._current_analyst_report

            # Build concise market context once
            market_ctx_lines = []
            if analyst_report:
                regime = getattr(analyst_report, 'market_regime', None)
                if regime:
                    market_ctx_lines.append(
                        f"Market regime: {regime.regime} | Sentiment: {regime.sentiment}"
                    )
            if risk_assessment:
                market_ctx_lines.append(
                    f"Portfolio risk: {risk_assessment.risk_level} | "
                    f"Exposure: {getattr(risk_assessment, 'exposure_pct', 'N/A')}%"
                )
            market_ctx = "\n".join(market_ctx_lines) or "No broader market context available."

            # Build per-position summaries and cache current SL/TP
            position_blocks = []
            pos_current_levels: dict = {}  # {pos_id: {"sl": float|None, "tp": float|None, "trader_id": str|None}}
            for pos in positions:
                try:
                    current_price = await paper_trading.fetch_current_price(pos.symbol)
                except Exception:
                    current_price = pos.current_price or pos.entry_price or 0
                entry = pos.entry_price or 0
                if entry <= 0:
                    continue

                pnl_pct = ((current_price - entry) / entry) * 100 if entry else 0
                sl = getattr(pos, 'stop_loss_price', None)
                tp = getattr(pos, 'take_profit_price', None)

                # Resolve trader ownership via position's agent_id → _enabled_agents → trader_id
                agent_id_for_pos = getattr(pos, 'agent_id', None)
                trader_id_for_pos = None
                if agent_id_for_pos and agent_id_for_pos in self._enabled_agents:
                    trader_id_for_pos = self._enabled_agents[agent_id_for_pos].get("_trader_id_pre")

                # Compute how far price has travelled toward TP (0–100%)
                _side_str = str(getattr(getattr(pos, 'side', None), 'value', getattr(pos, 'side', 'long'))).lower()
                _is_short = _side_str in ("sell", "short")
                if tp and entry:
                    if _is_short:
                        _tp_range = entry - tp
                        _tp_progress = ((entry - current_price) / _tp_range * 100) if _tp_range else 0
                    else:
                        _tp_range = tp - entry
                        _tp_progress = ((current_price - entry) / _tp_range * 100) if _tp_range else 0
                    _tp_progress_str = f"TP_progress={_tp_progress:.0f}%"
                else:
                    _tp_progress_str = "TP_progress=N/A"

                # Hours since position opened
                from datetime import datetime, timezone as _tz
                _open_dt = getattr(pos, 'created_at', None)
                if _open_dt:
                    try:
                        _now_utc = datetime.now(_tz.utc)
                        _open_utc = _open_dt.replace(tzinfo=_tz.utc) if _open_dt.tzinfo is None else _open_dt
                        _hrs_open = (_now_utc - _open_utc).total_seconds() / 3600
                        _age_str = f"open_hrs={_hrs_open:.1f}"
                    except Exception:
                        _age_str = "open_hrs=?"
                else:
                    _age_str = "open_hrs=?"

                pos_current_levels[pos.id] = {
                    "sl": sl, "tp": tp, "price": current_price,
                    "entry": entry,
                    "is_short": _is_short,
                    "trader_id": trader_id_for_pos,
                    "symbol": pos.symbol,
                }

                # TA confluence for this symbol
                ta = confluence.get(pos.symbol, {})
                ta_line = (
                    f"TA signal={ta.get('signal','N/A')}, "
                    f"confluence={ta.get('score',0):.0%}, "
                    f"alignment={ta.get('alignment','N/A')}"
                ) if ta else "No TA data"

                position_blocks.append(
                    f"- {pos.symbol} | side={getattr(pos.side, 'value', pos.side)} "
                    f"entry={fmt_price(entry)} now={fmt_price(current_price)} pnl={pnl_pct:+.2f}% "
                    f"SL={fmt_price(sl) if sl else 'NONE'} "
                    f"TP={fmt_price(tp) if tp else 'NONE'} "
                    f"| {_tp_progress_str} {_age_str} | {ta_line} "
                    f"| id={pos.id}"
                )

            if not position_blocks:
                return

            from app.services.llm import llm_service

            system_prompt = (
                "You are Sarah Chen, the Fund Manager of an AI crypto trading fund. "
                "You are reviewing open positions and deciding whether their stop-loss "
                "and take-profit levels should be adjusted based on the latest technical "
                "analysis, market regime, and risk context.\n\n"
                "RULES:\n"
                "1. Only adjust levels when there is a clear reason (TA signal change, "
                "support/resistance shift, regime change, position well in profit).\n"
                "2. Never widen a stop-loss beyond the original entry risk (e.g. if entry "
                "was $100 and SL was $97, don't move SL below $97).\n"
                "3. For profitable positions, consider tightening SL to lock in gains — "
                "BUT the new SL must be at least 1.5% away from the current price for a long "
                "(or 1.5% above current price for a short). Never place a new SL within 1% of "
                "current price — it will trigger immediately and close the trade unnecessarily.\n"
                "4. If TA confluence is bearish for a long position (or bullish for a short), tighten SL.\n"
                "5. TP ADJUSTMENTS — both directions are valid:\n"
                "   a) EXTEND TP: only if TA confluence is strongly aligned with the trade direction AND "
                "the position has significant momentum (TP_progress > 50%).\n"
                "   b) REDUCE TP: consider lowering TP in any of these situations:\n"
                "      - TA signal has flipped AGAINST the trade direction (e.g. TA=bearish for a long)\n"
                "      - Market regime has changed to 'ranging' or 'consolidation' after entry\n"
                "      - Price has been open > 48 hours (open_hrs > 48) and TP_progress < 30% "
                "(target is stalling — take what the market is offering)\n"
                "      - TP_progress > 80% (price is nearly there — lower TP to current price + small buffer "
                "to lock in the gain rather than risk a reversal)\n"
                "   When reducing TP for a long, do NOT reduce below current price + 0.5%. "
                "When reducing TP for a short, do NOT reduce above current price - 0.5%.\n"
                "6. If no change is warranted, return an empty adjustments array.\n"
                "7. Always include a brief reason per adjustment.\n"
                "8. CRITICAL: Before proposing any SL, check the current price shown in the "
                "position data. If your proposed SL is within 1% of current price, do NOT "
                "propose it — the position would be stopped out the moment the adjustment "
                "is applied.\n\n"
                "Return ONLY valid JSON:\n"
                '{"adjustments": [\n'
                '  {"position_id": "...", "symbol": "...", '
                '"new_stop_loss": <number|null>, "new_take_profit": <number|null>, '
                '"reason": "..."}\n'
                '], "summary": "one-line overall summary"}\n\n'
                "IMPORTANT: Values must be plain numbers (e.g. 67500.00), NOT strings with $ signs."
            )

            user_prompt = (
                f"MARKET CONTEXT:\n{market_ctx}\n\n"
                f"OPEN POSITIONS:\n" + "\n".join(position_blocks)
            )

            raw = await llm_service._call_llm_text(
                system_prompt, user_prompt, temperature=0.3, max_tokens=1200
            )

            # Parse JSON response
            import json as _json
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = _json.loads(cleaned)
            adjustments = data.get("adjustments", [])
            summary = data.get("summary", "")

            if not adjustments:
                logger.info("SL/TP Review: No adjustments needed")
                return

            adjusted_count = 0
            def _safe_float(v):
                """Parse float from LLM output, stripping $, commas, whitespace."""
                if v is None:
                    return None
                if isinstance(v, (int, float)):
                    return float(v)
                return float(str(v).replace("$", "").replace(",", "").strip())

            for adj in adjustments:
                pid = adj.get("position_id")
                if not pid:
                    continue
                new_sl = _safe_float(adj.get("new_stop_loss"))
                new_tp = _safe_float(adj.get("new_take_profit"))
                reason = adj.get("reason", "")
                symbol = adj.get("symbol", "?")

                # Compare with current values — skip if change < 0.3% of price
                old = pos_current_levels.get(pid, {})
                ref_price = old.get("price", 1)
                min_delta = ref_price * 0.003  # 0.3% minimum change

                kwargs = {}
                if new_sl is not None:
                    old_sl = old.get("sl")
                    if old_sl is None or abs(new_sl - old_sl) >= min_delta:
                        # ── Safety gate: reject SL changes that would trigger immediately ──
                        # A new SL within 0.8% of current price for a long (or 0.8% below
                        # current for a short) is almost certain to fire the moment it's
                        # applied, closing the trade without any actual reversal occurring.
                        _side = getattr(
                            next((p for p in self._open_positions if p.id == pid), None),
                            "side", None
                        )
                        _is_short_pos = str(getattr(_side, "value", _side)).lower() in ("sell", "short")
                        _MIN_SL_DISTANCE = 0.008  # 0.8% minimum gap between SL and current price
                        if _is_short_pos:
                            # Short: SL is above price — must be at least 0.8% above current
                            _sl_too_close = new_sl < ref_price * (1 + _MIN_SL_DISTANCE)
                        else:
                            # Long: SL is below price — must be at least 0.8% below current
                            _sl_too_close = new_sl > ref_price * (1 - _MIN_SL_DISTANCE)
                        if _sl_too_close:
                            logger.warning(
                                f"SL/TP Review: REJECTED {symbol} SL→${new_sl:.4f} "
                                f"(current=${ref_price:.4f}) — proposed SL is within 0.8% of "
                                f"current price and would trigger immediately"
                            )
                            await team_chat.add_message(
                                agent_role="fund_manager",
                                content=(
                                    f"⚠️ **SL proposal auto-cancelled — {symbol}:** Proposed SL "
                                    f"${new_sl:.4f} is within 0.8% of current price "
                                    f"${ref_price:.4f} and would close the position immediately. "
                                    f"No adjustment made."
                                ),
                                message_type="alert",
                            )
                            new_sl = None  # cancel the SL part of this adjustment
                        else:
                            kwargs["stop_loss_price"] = new_sl
                if new_tp is not None:
                    old_tp = old.get("tp")
                    if old_tp is not None:
                        # Cap TP extensions to +25% above the original TP — prevents the LLM
                        # from dreaming up targets that are 2× away.  Also floor reductions at
                        # 50% of original TP so TP isn't collapsed below meaningful profit.
                        _max_tp = old_tp * 1.25
                        _min_tp = old_tp * 0.50
                        if new_tp > _max_tp:
                            logger.info(
                                f"SL/TP Review: TP extension capped ${new_tp:.4f}→${_max_tp:.4f} "
                                f"(+25% max above original ${old_tp:.4f})"
                            )
                            new_tp = round(_max_tp, 6)
                        elif new_tp < _min_tp:
                            logger.info(
                                f"SL/TP Review: TP reduction floored ${new_tp:.4f}→${_min_tp:.4f} "
                                f"(50% min of original ${old_tp:.4f})"
                            )
                            new_tp = round(_min_tp, 6)
                    # Fee-profitable floor: TP must be far enough from entry to cover
                    # round-trip fees + a net profit minimum, so no LLM-proposed TP
                    # can result in a guaranteed loss on exit.
                    _tp_entry_price = old.get("entry", ref_price)
                    _tp_is_short = old.get("is_short", False)
                    _tp_min_dist = 0.002 + 0.003  # 0.2% round-trip fees + 0.3% net profit
                    if _tp_is_short:
                        _tp_profit_floor = _tp_entry_price * (1 - _tp_min_dist)
                        if new_tp > _tp_profit_floor:
                            logger.info(
                                f"SL/TP Review: TP profit-floor enforced {symbol} SHORT "
                                f"${new_tp:.4f}→${_tp_profit_floor:.4f} "
                                f"(min {_tp_min_dist:.2%} from entry)"
                            )
                            new_tp = round(_tp_profit_floor, 6)
                    else:
                        _tp_profit_floor = _tp_entry_price * (1 + _tp_min_dist)
                        if new_tp < _tp_profit_floor:
                            logger.info(
                                f"SL/TP Review: TP profit-floor enforced {symbol} LONG "
                                f"${new_tp:.4f}→${_tp_profit_floor:.4f} "
                                f"(min {_tp_min_dist:.2%} from entry)"
                            )
                            new_tp = round(_tp_profit_floor, 6)
                    if old_tp is None or abs(new_tp - old_tp) >= min_delta:
                        kwargs["take_profit_price"] = new_tp
                if not kwargs:
                    continue

                # ── PROPOSAL FLOW: consult the responsible trader first ─────
                trader_id = old.get("trader_id")
                trader_obj = next((t for t in self._traders if t["id"] == trader_id), None) if self._traders else None

                proposal_parts = []
                if "stop_loss_price" in kwargs:
                    old_sl = old.get("sl")
                    proposal_parts.append(
                        f"SL: {'$'+f'{old_sl:.4f}' if old_sl else 'NONE'} → ${new_sl:.4f}"
                    )
                if "take_profit_price" in kwargs:
                    old_tp = old.get("tp")
                    proposal_parts.append(
                        f"TP: {'$'+f'{old_tp:.4f}' if old_tp else 'NONE'} → ${new_tp:.4f}"
                    )
                proposal_str = " | ".join(proposal_parts)

                trader_name_for_proposal = trader_obj["name"] if trader_obj else "Assigned Trader"
                trader_cfg_pre = trader_obj.get("config", {}) if trader_obj and isinstance(trader_obj.get("config"), dict) else {}
                trader_avatar_pre = trader_cfg_pre.get("avatar", "🤖")

                # Post Sarah's proposal to team chat
                await team_chat.add_message(
                    agent_role="fund_manager",
                    content=(
                        f"📋 **SL/TP Proposal — {symbol}:** I'm recommending an adjustment "
                        f"to levels on this position.\n"
                        f"**Proposed:** {proposal_str}\n"
                        f"**Reason:** {reason}\n\n"
                        f"@{trader_name_for_proposal} — does this conflict with your strategy thesis? "
                        f"Please confirm or object."
                    ),
                    message_type="allocation",
                )

                # If we have a trader, ask their LLM for a response
                trader_approved = True  # default: approve if no trader to consult
                trader_response_text = ""
                if trader_obj:
                    try:
                        trader_llm = await trader_service.get_trader_llm(trader_obj)
                        trader_name = trader_obj.get("name", "Trader")
                        trader_style = trader_cfg_pre.get("style", "moderate risk")
                        trader_system = (
                            f"You are {trader_name}, a crypto fund trader with style: {trader_style}. "
                            f"The Fund Manager (Sarah Chen) is proposing to adjust stop-loss or take-profit "
                            f"levels on one of your open positions. "
                            f"You must decide whether this adjustment is compatible with your current strategy thesis.\n\n"
                            f"Reply with a JSON object:\n"
                            f'{{"approved": true|false, "response": "brief message to the team explaining your decision"}}\n\n'
                            f"Approve if the adjustment preserves your thesis or locks in profits sensibly. "
                            f"Object (set approved=false) if:\n"
                            f"- The proposed SL is too close to current price (within 1%) and would close the position immediately\n"
                            f"- It would prematurely close a position before your thesis plays out\n"
                            f"- It contradicts your entry reasoning\n"
                            f"- A TP reduction is proposed but the original thesis is still intact "
                            f"(e.g. TA hasn't flipped, regime hasn't changed, position is progressing normally)\n"
                            f"- A TP extension is proposed but there is no momentum or TA support for it\n"
                            f"Be concise (1-2 sentences)."
                        )
                        trader_user = (
                            f"Position: {symbol} | {proposal_str}\n"
                            f"Current price: ${ref_price:.4f}\n"
                            f"Sarah's reason: {reason}\n\n"
                            f"Current market context: {market_ctx}"
                        )
                        import json as _json_inner
                        raw_response = await trader_llm._call_llm_text(
                            trader_system, trader_user,
                            temperature=0.4, max_tokens=300,
                        )
                        cleaned_r = raw_response.strip()
                        if cleaned_r.startswith("```"):
                            cleaned_r = cleaned_r.split("\n", 1)[-1].rsplit("```", 1)[0]
                        parsed = _json_inner.loads(cleaned_r)
                        trader_approved = bool(parsed.get("approved", True))
                        trader_response_text = parsed.get("response", "")
                    except Exception as e:
                        logger.warning(f"SL/TP Review: trader consultation failed ({e}), defaulting to approve")
                        trader_approved = True
                        trader_response_text = "Consultation unavailable — adjustment approved by default."

                    # Post trader's response to team chat
                    trader_name = trader_obj.get("name", "Trader")
                    decision_emoji = "✅" if trader_approved else "⛔"
                    await team_chat.add_message(
                        agent_role=f"trader_{trader_name.lower().replace(' ', '_')}",
                        content=f"{decision_emoji} **{trader_name}:** {trader_response_text}",
                        message_type="trade_intent",
                        metadata={"_override_name": trader_name, "_override_avatar": trader_avatar_pre},
                    )

                if not trader_approved:
                    logger.info(
                        f"SL/TP Review: {symbol} adjustment REJECTED by trader {trader_obj.get('name', '?')} — {trader_response_text}"
                    )
                    continue

                # Apply the approved adjustment — route to correct backend (paper or live)
                result = await trading_service.update_position_sl_tp(pid, **kwargs)
                if result:
                    adjusted_count += 1
                    logger.info(f"SL/TP Review: {symbol} {proposal_str} — approved & applied")
                else:
                    logger.warning(f"SL/TP Review: Failed to update position {pid} — not found")

            if adjusted_count > 0:
                await team_chat.add_message(
                    agent_role="fund_manager",
                    content=(
                        f"✅ **SL/TP Review complete:** {adjusted_count} adjustment(s) "
                        f"agreed and applied. {summary}"
                    ),
                    message_type="allocation",
                )

            logger.info(
                f"SL/TP Review complete: {adjusted_count}/{len(adjustments)} adjusted"
            )

        except _json.JSONDecodeError:
            logger.warning("SL/TP Review: LLM returned invalid JSON, skipping")
        except Exception as e:
            logger.error(f"SL/TP Review failed: {e}", exc_info=True)

    async def _fetch_agents_from_db(self) -> List[dict]:
        """Fetch all agents from database for team tier decisions"""
        from app.models import Agent as DBAgent
        from app.database import AsyncSessionLocal
        from sqlalchemy import select as sa_select
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_select(DBAgent))
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
                        "stop_loss_pct": a.config.get("stop_loss_pct", 3.5),
                        "take_profit_pct": a.config.get("take_profit_pct", 7.0),
                        "trailing_stop_pct": a.config.get("trailing_stop_pct", 3.0),
                        "timeframe": a.config.get("timeframe", "1h"),
                        "trader_id": a.trader_id,
                    }
                    for a in agents
                ]
        except Exception as e:
            logger.error(f"Team Tier: Failed to fetch agents from DB: {e}")
            return []

    def _build_agent_metrics_list(self, agents: List[dict]) -> List[dict]:
        """Build agent metrics list from in-memory metrics + agent config"""
        agent_name_map = {a["id"]: a["name"] for a in agents}
        agent_enabled_map = {a["id"]: a.get("is_enabled", True) for a in agents}
        metrics_list = []
        for agent_id, metrics in self._agent_metrics.items():
            metrics_list.append({
                "agent_id": agent_id,
                "agent_name": agent_name_map.get(agent_id, agent_id),
                "name": agent_name_map.get(agent_id, agent_id),
                "total_runs": metrics.total_runs,
                "successful_runs": metrics.successful_runs,
                "actual_trades": getattr(metrics, "actual_trades", 0) or 0,
                "winning_trades": getattr(metrics, "winning_trades", 0) or 0,
                "total_pnl": metrics.total_pnl,
                "win_rate": metrics.win_rate,
                "last_run": metrics.last_run.isoformat() if metrics.last_run else None,
                "is_enabled": agent_enabled_map.get(agent_id, True),
                "strategy_type": next(
                    (a["strategy_type"] for a in agents if a["id"] == agent_id), "unknown"
                ),
            })
        return metrics_list

    async def _get_current_positions(self) -> List[dict]:
        """Fetch current open positions (paper + live) for risk assessment"""
        try:
            positions = list(await paper_trading.get_positions() or [])
            if not _is_paper_mode():
                try:
                    from app.services.live_trading import live_trading as _lt_pos
                    positions += list(await _lt_pos.get_positions() or [])
                except Exception:
                    pass
            return [
                {
                    "symbol": p.symbol,
                    "side": p.side.value if hasattr(p.side, 'value') else str(p.side),
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": getattr(p, 'current_price', p.entry_price),
                    "unrealized_pnl": getattr(p, 'unrealized_pnl', None),
                    "leverage": getattr(p, 'leverage', 1.0) or 1.0,
                    "margin_used": getattr(p, 'margin_used', None),
                    "notional": (p.quantity or 0) * (getattr(p, 'current_price', None) or p.entry_price or 0),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Team Tier: Failed to fetch positions: {e}")
            return []

    async def _run_team_analysis(self):
        """
        Team Decision Tier: Runs every 5 minutes
        Updates constraints that ALL agents will respect for the next 5 minutes
        """
        try:
            logger.info("Team Tier: Running team analysis (research + portfolio + risk)")

            # Fetch shared data once for all team members
            agents_list = await self._fetch_agents_from_db()
            agent_metrics = self._build_agent_metrics_list(agents_list)
            current_positions = await self._get_current_positions()

            # Compute daily P&L from DB (robust across restarts)
            daily_pnl = await self._compute_daily_pnl()
            # Sync to risk manager so in-memory tracker stays accurate
            risk_manager._check_daily_reset()
            risk_manager._daily_pnl['today'] = daily_pnl

            # Calculate real total capital: live Phemex balance or paper DB balance + open positions
            positions_value = sum(
                p.get("quantity", 0) * p.get("current_price", p.get("entry_price", 0))
                for p in current_positions
            )
            total_capital = await self._get_total_capital(positions_value=positions_value)
            self._total_capital = total_capital

            # 1. Research Analyst: Multi-symbol market analysis across all configured pairs
            try:
                try:
                    from app.api.routes.settings import get_trading_prefs
                    _ra_symbols = list(get_trading_prefs().trading_pairs) or None
                except Exception:
                    _ra_symbols = None
                analyst_report = await research_analyst.analyze_markets(symbols=_ra_symbols)
                self._current_analyst_report = analyst_report
                logger.info(f"Team Tier: Analyst report - Market {analyst_report.market_regime.regime}, "
                           f"Sentiment: {analyst_report.market_regime.sentiment}")
                await team_chat.log_analyst_report(analyst_report)
            except Exception as e:
                logger.error(f"Team Tier: Research analyst failed: {e}")

            # 2. Portfolio Manager: Reallocation based on analyst + real agent performance
            market_condition = None
            confluence_scores = None
            try:
                market_condition = await fund_manager.analyze_market()

                # Gather unique trading symbols: agent pairs + configured global pairs
                all_symbols = set()
                for a in agents_list:
                    pairs = a.get("trading_pairs") or a.get("config", {}).get("trading_pairs", [])
                    all_symbols.update(pairs)
                # Also include all pairs from the global trading config
                try:
                    from app.api.routes.settings import get_trading_prefs
                    all_symbols.update(get_trading_prefs().trading_pairs)
                except Exception:
                    pass
                if not all_symbols:
                    all_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"}

                # Get Technical Analyst confluence scores so FM can reconcile signals.
                # Use the most common agent timeframe so scores are relevant to the
                # strategies that will consume them.
                try:
                    from collections import Counter
                    tf_counts = Counter(a.get("timeframe", "1h") for a in agents_list)
                    dominant_tf = tf_counts.most_common(1)[0][0] if tf_counts else "1h"
                    confluence_scores = await technical_analyst.get_confluence_scores(list(all_symbols), timeframe=dominant_tf)
                    self._current_confluence_scores = confluence_scores
                    logger.info(f"Team Tier: TA confluence for {len(confluence_scores)} symbols (tf={dominant_tf})")
                except Exception as e:
                    logger.warning(f"Team Tier: TA confluence failed, FM will proceed without: {e}")

                # When traders exist James allocates to traders (not directly to agents).
                # Direct agent allocation only runs when there are no traders.
                if not self._traders:
                    portfolio_decision = await fund_manager.make_allocation_decision(
                        agents=agents_list,
                        agent_metrics=agent_metrics,
                        market_condition=market_condition,
                        confluence_scores=confluence_scores,
                    )
                    self._current_allocation = portfolio_decision.allocation_pct
                    self._current_allocation_reasoning = portfolio_decision.reasoning
                    logger.info(f"Team Tier: Portfolio manager updated allocation for {len(agents_list)} agents (no traders)")
                    await team_chat.log_portfolio_decision(portfolio_decision, agents_list)
            except Exception as e:
                logger.error(f"Team Tier: Portfolio manager failed: {e}")

            # 2.1 Trader Layer: James allocates to traders, each trader sub-allocates to their strategies
            try:
                if self._traders:
                    # ── Pre-fetch fees per agent so allocation scores use gross P&L ──
                    # gross_pnl = net_pnl (AgentMetricRecord) + total_fees_paid (Trade)
                    # Allocation should reward price-movement ability, not penalise a
                    # trader twice for fee drag (the autopilot already handles fee drag).
                    _all_agent_ids = [a["id"] for a in agents_list if a.get("trader_id") and a.get("id")]
                    _fees_by_agent: dict = {}
                    if _all_agent_ids:
                        try:
                            async with get_async_session() as _fdb:
                                from sqlalchemy import func as _sqlfunc
                                _fee_rows = await _fdb.execute(
                                    select(Trade.agent_id, _sqlfunc.coalesce(_sqlfunc.sum(Trade.fee), 0.0).label("f"))
                                    .where(
                                        Trade.agent_id.in_(_all_agent_ids),
                                        Trade.status == OrderStatus.FILLED,
                                        Trade.is_paper.is_(_is_paper_mode()),
                                    )
                                    .group_by(Trade.agent_id)
                                )
                                _fees_by_agent = {r.agent_id: float(r.f) for r in _fee_rows.fetchall()}
                        except Exception as _fe:
                            logger.debug(f"Fees-by-agent pre-fetch failed: {_fe}")

                    # Build trader performance summary for James
                    trader_perf_list = []
                    for t in self._traders:
                        t_agents = [a for a in agents_list if a.get("trader_id") == t["id"]]
                        t_metrics = [m for m in agent_metrics
                                     if m.get("agent_id") in {a["id"] for a in t_agents}]
                        perf = trader_service.get_trader_performance(t, t_agents, t_metrics)
                        _t_fees = sum(_fees_by_agent.get(a["id"], 0.0) for a in t_agents)
                        _gross_pnl = (perf.total_pnl or 0.0) + _t_fees
                        trader_perf_list.append({
                            "trader_id": perf.trader_id,
                            "trader_name": perf.trader_name,
                            "agent_count": perf.agent_count,
                            "total_pnl": perf.total_pnl,       # net (after fees)
                            "gross_pnl": _gross_pnl,           # before fees — used for allocation scoring
                            "total_fees": _t_fees,
                            "win_rate": perf.win_rate,
                            "total_trades": perf.total_trades,
                            "sharpe_ratio": getattr(perf, "sharpe_ratio", None),
                        })

                    # James (PM) decides trader allocations via LLM
                    trader_alloc_pct = await fund_manager.make_trader_allocation_decision(
                        traders=self._traders,
                        trader_performance=trader_perf_list,
                        market_condition=market_condition or await fund_manager.analyze_market(),
                        total_capital=total_capital,
                        confluence_scores=confluence_scores,
                    )

                    # ── Phase 9.1: Consistency & Sharpe gating ──────────────
                    try:
                        from app.services.consistency_scorer import compute_consistency
                        import asyncio as _asyncio
                        _is_paper = _is_paper_mode()
                        _cscores = {}
                        _tasks = [
                            compute_consistency(
                                t["id"],
                                [a["id"] for a in agents_list if a.get("trader_id") == t["id"]],
                                is_paper=_is_paper,
                            )
                            for t in self._traders if t.get("is_enabled", True)
                        ]
                        _results = await _asyncio.gather(*_tasks, return_exceptions=True)
                        for _t, _cr in zip(
                            [t for t in self._traders if t.get("is_enabled", True)], _results
                        ):
                            if isinstance(_cr, Exception):
                                continue
                            _cscores[_t["id"]] = _cr
                            proposed = trader_alloc_pct.get(_t["id"], 33.3)
                            prev = _t.get("allocation_pct", 33.3)

                            # ── Probation rule: negative P&L + win rate < 40% ──────────
                            # A trader who is actively losing money should be capped at the
                            # minimum allocation immediately — do not wait for the LLM or
                            # Sharpe gate to act.  This prevents a -$200 trader from holding
                            # 33% of the fund just because the LLM returned equal allocations.
                            _t_perf = next(
                                (p for p in trader_perf_list if p.get("trader_id") == _t["id"]),
                                {},
                            )
                            _t_net_pnl  = float(_t_perf.get("total_pnl") or 0)
                            _t_win_rate = float(_t_perf.get("win_rate") or 0)
                            _t_trades   = int(_t_perf.get("total_trades") or 0)
                            _on_probation = (
                                _t_net_pnl < 0
                                and _t_win_rate < 0.40
                                and _t_trades >= 10          # need a real sample first
                            )
                            if _on_probation:
                                trader_alloc_pct[_t["id"]] = trader_service.MIN_ALLOCATION_PCT
                                prev_flag_prob = self._consistency_flags.get(_t["id"])
                                if prev_flag_prob != "PROBATION":
                                    _prob_name = _t.get("name", _t["id"])
                                    asyncio.create_task(team_chat.add_message(
                                        agent_role="risk_manager",
                                        content=(
                                            f"🔴 **Allocation Probation — {_prob_name}:** "
                                            f"Net P&L ${_t_net_pnl:+.2f} with {_t_win_rate:.0%} win rate "
                                            f"after {_t_trades} trades. Capital capped at "
                                            f"{trader_service.MIN_ALLOCATION_PCT:.0f}% (minimum floor) "
                                            f"until performance recovers above 40% win rate and positive P&L."
                                        ),
                                        message_type="alert",
                                    ))
                                self._consistency_flags[_t["id"]] = "PROBATION"
                                continue  # skip Sharpe gating — already at floor
                            elif self._consistency_flags.get(_t["id"]) == "PROBATION":
                                # Coming off probation — announce recovery
                                _prob_name = _t.get("name", _t["id"])
                                asyncio.create_task(team_chat.add_message(
                                    agent_role="risk_manager",
                                    content=(
                                        f"✅ **Probation lifted — {_prob_name}:** "
                                        f"Performance has recovered (WR {_t_win_rate:.0%}, "
                                        f"P&L ${_t_net_pnl:+.2f}). Normal allocation rules resume."
                                    ),
                                    message_type="alert",
                                ))

                            # 40% Rule: block capital increase
                            if _cr.consistency_flag == "INCONSISTENT":
                                trader_alloc_pct[_t["id"]] = min(proposed, prev)

                            # Sharpe gate multiplier
                            trader_alloc_pct[_t["id"]] = max(
                                trader_service.MIN_ALLOCATION_PCT,
                                min(
                                    trader_service.MAX_ALLOCATION_PCT,
                                    trader_alloc_pct[_t["id"]] * _cr.sharpe_multiplier,
                                ),
                            )

                            # Team chat alert on newly INCONSISTENT traders
                            prev_flag = self._consistency_flags.get(_t["id"])
                            if _cr.consistency_flag == "INCONSISTENT" and prev_flag != "INCONSISTENT":
                                _name = _t.get("name", _t["id"])
                                asyncio.create_task(team_chat.add_message(
                                    agent_role="risk_manager",
                                    content=(
                                        f"⚠️ **Consistency Gate triggered — {_name}**: "
                                        f"a single trade represents {_cr.offending_trade_pct:.0%} of "
                                        f"period profit (40% rule exceeded). "
                                        f"Capital increase blocked until distribution improves."
                                    ),
                                    message_type="alert",
                                ))
                            self._consistency_flags[_t["id"]] = _cr.consistency_flag

                        # Re-normalize after gating adjustments.
                        # IMPORTANT: skip if Sharpe multipliers already created a meaningful
                        # spread — re-normalizing a 50/30/20 split back to 33/33/33 defeats
                        # the entire purpose of performance-based allocation.
                        _gated_vals = list(trader_alloc_pct.values())
                        _gated_spread = (max(_gated_vals) - min(_gated_vals)) if _gated_vals else 0
                        _total = sum(trader_alloc_pct.values())
                        if _total > 0 and _gated_spread < 5.0:
                            # Spread is still flat — normalize so allocations sum to 100
                            trader_alloc_pct = {
                                tid: pct / _total * 100
                                for tid, pct in trader_alloc_pct.items()
                            }
                        elif _total > 0 and abs(_total - 100.0) > 5.0:
                            # Spread is meaningful — only normalize if total has drifted >5% from 100
                            trader_alloc_pct = {
                                tid: pct / _total * 100
                                for tid, pct in trader_alloc_pct.items()
                            }
                    except Exception as _ce:
                        logger.warning(f"9.1 consistency gating failed (using raw allocations): {_ce}")
                    # ─────────────────────────────────────────────────────────

                    self._trader_allocations = trader_alloc_pct

                    # Persist trader allocations to DB
                    async with get_async_session() as session:
                        for tid, pct in trader_alloc_pct.items():
                            await trader_service.update_trader_allocation(session, tid, pct)

                    # Each trader sub-allocates its portion to its own strategies
                    for t in self._traders:
                        if not t.get("is_enabled", True):
                            continue
                        t_agents = [a for a in agents_list if a.get("trader_id") == t["id"]]
                        t_metrics = [m for m in agent_metrics
                                     if m.get("agent_id") in {a["id"] for a in t_agents}]
                        trader_capital = total_capital * trader_alloc_pct.get(t["id"], 33.3) / 100

                        sub_alloc = await trader_service.allocate_to_agents(
                            trader=t,
                            trader_agents=t_agents,
                            agent_metrics=t_metrics,
                            trader_capital=trader_capital,
                        )
                        self._trader_agent_allocations[t["id"]] = sub_alloc

                        # Compute each strategy's fund-level allocation %:
                        # trader_pct is % of total fund (0-100)
                        # agent_pct is % of trader's capital (0-100)
                        # fund-level = trader_pct × agent_pct / 100
                        for aid, agent_pct in sub_alloc.items():
                            fund_level_pct = trader_alloc_pct.get(t["id"], 33.3) * agent_pct / 100
                            self._current_allocation[aid] = fund_level_pct
                            logger.debug(
                                f"Allocation cascade: {t.get('name','?')} ({trader_alloc_pct.get(t['id'],33.3):.1f}%) "
                                f"→ agent {aid[:8]} ({agent_pct:.1f}% of trader) "
                                f"= {fund_level_pct:.2f}% of fund"
                            )

                    logger.info(
                        f"Team Tier: James allocated to {len(self._trader_allocations)} traders — "
                        + ", ".join(f"{t.get('name','?')}={trader_alloc_pct.get(t['id'],0):.1f}%"
                                    for t in self._traders if t.get("is_enabled", True))
                    )
                    await team_chat.log_trader_allocation(
                        trader_alloc_pct=trader_alloc_pct,
                        traders=[t for t in self._traders if t.get("is_enabled", True)],
                    )
            except Exception as e:
                logger.error(f"Team Tier: Trader allocation failed (using PM direct fallback): {e}")

            # 2.5 Strategy Review: FM + TA joint evaluation (every 20 minutes)
            try:
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 1200:
                    if market_condition is None:
                        market_condition = await fund_manager.analyze_market()
                    review = await strategy_review_service.run_strategy_review(
                        agents=agents_list,
                        agent_metrics=agent_metrics,
                        market_condition=market_condition,
                        analyst_report=self._current_analyst_report,
                    )
                    logger.info(f"Team Tier: Strategy review — {len(review.proposed_actions)} actions proposed")
                    await team_chat.log_strategy_review(review)

                    # Auto-execute approved actions
                    if review.proposed_actions:
                        await self._execute_strategy_actions(review.proposed_actions, agents_list)

                    # 2.6 Trader Strategy Reviews: Each trader reviews its own agents
                    for t in self._traders:
                        if not t.get("is_enabled", True):
                            continue
                        try:
                            t_agents = [a for a in agents_list if a.get("trader_id") == t["id"]]
                            t_metrics = [m for m in agent_metrics
                                         if m.get("agent_id") in {a["id"] for a in t_agents}]
                            mc_dict = {
                                "trend": market_condition.trend if market_condition else "unknown",
                                "volatility": market_condition.volatility if market_condition else "unknown",
                                "momentum": market_condition.momentum if market_condition else "unknown",
                            }
                            actions = await trader_service.manage_agents(
                                trader=t,
                                trader_agents=t_agents,
                                agent_metrics=t_metrics,
                                market_condition=mc_dict,
                                confluence_scores=confluence_scores,
                                analyst_report=self._current_analyst_report,
                            )
                            if actions:
                                await self._execute_trader_strategy_actions(actions, t, agents_list)
                        except Exception as te:
                            logger.error(f"Trader {t['name']} strategy review failed: {te}")
            except Exception as e:
                logger.error(f"Team Tier: Strategy review failed: {e}")

            # 3. Risk Manager: Portfolio-level risk check with real positions + P&L
            try:
                from app.api.routes.settings import get_risk_limits
                _risk_limits = get_risk_limits()
                risk_assessment = await risk_manager.generate_risk_assessment(
                    current_positions=current_positions,
                    daily_pnl=daily_pnl,
                    total_capital=total_capital,
                    max_daily_loss_pct=_risk_limits.max_daily_loss_pct,
                )
                self._current_risk_assessment = risk_assessment
                logger.info(f"Team Tier: Risk assessment - Level: {risk_assessment.risk_level}, "
                           f"Daily PnL: ${(risk_assessment.daily_pnl or 0):+.2f}")
                await team_chat.log_risk_assessment(risk_assessment)
            except Exception as e:
                logger.error(f"Team Tier: Risk manager failed: {e}")

            # 3.5 Fund Manager SL/TP Review: Adjust open position levels
            #     based on TA confluence + market context + risk assessment
            try:
                await self._review_open_position_levels()
            except Exception as e:
                logger.error(f"Team Tier: SL/TP review failed: {e}")

            # ── Cache daily fee budget ratio for per-trade interval & gate use ──
            # Refreshes every 15 min (team cycle) so individual trade paths don't
            # need their own DB query on every agent run.
            try:
                _fee_snap = await self._get_daily_fee_pressure()
                self._cached_budget_ratio = float(_fee_snap.get("budget_used_ratio", 0.0) or 0.0)
            except Exception:
                pass

            # 4. Execution Coordinator: Optimize order timing
            try:
                execution_plan = await execution_coordinator.optimize_execution_plan([])
                self._current_execution_plan = execution_plan
                logger.info(f"Team Tier: Execution coordinator - {execution_plan.pending_orders_count} pending orders")
                await team_chat.log_execution_plan(execution_plan)
            except Exception as e:
                logger.error(f"Team Tier: Execution coordinator failed: {e}")

            # 5. Trade Retrospective (every 20 minutes alongside CIO)
            # Run BEFORE CIO so Victoria receives the latest learning data.
            _retro_result = None
            try:
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 1200:
                    from app.services.trade_retrospective import trade_retrospective
                    retro = await trade_retrospective.analyze_recent_trades(agents_list)
                    if retro:
                        self._current_trade_insights = retro
                        _retro_result = retro
                        logger.info(f"Team Tier: Trade retrospective — {len(retro.get('trade_analyses', []))} trades reviewed")
                        # Feed insights back to team chat
                        if retro.get("summary"):
                            await team_chat.add_message(
                                agent_role="trade_analyst",
                                content=f"📈 **Trade Retrospective**: {retro['summary']}",
                                message_type="analysis",
                            )
                        # Auto-execute parameter adjustments
                        if retro.get("parameter_adjustments"):
                            await self._apply_retrospective_adjustments(retro["parameter_adjustments"], agents_list)
            except Exception as e:
                logger.error(f"Team Tier: Trade retrospective failed: {e}")

            # 6. CIO Report (less frequent, every 20 minutes)
            try:
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 1200:
                    cio_report = await cio_agent.generate_fund_report(
                        agent_metrics=agent_metrics,
                        trade_insights=_retro_result or self._current_trade_insights,
                    )
                    self._current_cio_report = cio_report
                    logger.info(f"Team Tier: CIO report - Sentiment: {cio_report.cio_sentiment}")
                    await team_chat.log_cio_report(cio_report)

                    # 6.1 Execute CIO strategic recommendations
                    if cio_report.strategic_recommendations:
                        cio_actions = self._map_cio_recommendations(
                            cio_report.strategic_recommendations, agents_list
                        )
                        if cio_actions:
                            logger.info(f"Team Tier: Executing {len(cio_actions)} CIO recommendation(s)")
                            await self._execute_strategy_actions(cio_actions, agents_list)
            except Exception as e:
                logger.error(f"Team Tier: CIO report failed: {e}")

        except Exception as e:
            logger.error(f"Team analysis tier failed: {e}")

    async def _maybe_generate_daily_report(self):
        """Generate a daily report snapshot once per day (not repeatedly)."""
        try:
            now = datetime.now()
            today = now.date().isoformat()
            # Only generate once per calendar day
            if self._last_daily_report and self._last_daily_report.date() == now.date():
                return

            logger.info("Generating daily report snapshot")
            await daily_report_service.generate_daily_report(force=True)
            self._last_daily_report = now

            await team_chat.add_message(
                agent_role="cio",
                content=f"📊 Daily report for {today} has been compiled with latest fund metrics.",
                message_type="recommendation",
            )
        except Exception as e:
            logger.error(f"Daily report generation failed: {e}")

    async def _maybe_send_daily_email(self):
        """Send the daily summary email once at 5pm (configurable)."""
        try:
            from app.config import settings as cfg
            from app.services.email_service import email_service

            if not cfg.mail_server_api_key:
                return

            now = datetime.now()
            target_hour = cfg.mail_daily_hour  # default 17 (5pm)
            today_str = now.strftime("%Y-%m-%d")

            # Already sent today?
            if self._last_daily_email_date == today_str:
                return

            # Not yet 5pm?
            if now.hour < target_hour:
                return

            logger.info("Sending daily summary email")
            report = await daily_report_service.generate_daily_report(force=True)
            if report:
                ok = await email_service.send_daily_summary(report)
                if ok:
                    self._last_daily_email_date = today_str
                    await team_chat.add_message(
                        agent_role="cio",
                        content="📧 Daily summary email dispatched to the trading team.",
                        message_type="recommendation",
                    )
                    # Also send Telegram daily report alert
                    asyncio.create_task(_send_telegram(telegram_service.alert_daily_report(
                        date_str=today_str,
                        total_pnl=report.get("total_pnl", 0.0),
                        daily_return_pct=report.get("daily_return_pct", 0.0),
                        trades_opened=report.get("trades_opened", 0),
                        trades_closed=report.get("trades_closed", 0),
                        best_agent=report.get("best_agent_id"),
                        portfolio_value=report.get("portfolio_value", 0.0),
                    )))
        except Exception as e:
            logger.error(f"Daily email failed: {e}")

    async def _run_enabled_agents(self):
        # Clear the per-cycle trade buffer so Alex starts fresh each pass
        self._cycle_trades = []

        for agent_id, config in self._enabled_agents.items():
            try:
                interval = config.get('run_interval_seconds', 3600)

                # Trend-following strategies (momentum, breakout, ema_crossover) need to
                # catch moves early — use a 5-minute floor so they never miss a candle close.
                # Mean reversion and grid are range-bound and don't need fast polling.
                _s_poll = config.get('strategy_type', 'momentum')
                if _s_poll in ('momentum', 'breakout', 'ema_crossover'):
                    interval = min(interval, 300)   # max 5-minute poll for trend strategies

                # ── Session-aware interval scaling ────────────────────────────
                # London/NY overlap (13:00–17:00 UTC) is the highest-liquidity window.
                # Halve the interval so agents fire twice as often during peak hours.
                # Outside active sessions, double the interval to save LLM budget.
                _sess_now = self._get_market_session_info()
                _hhmm = _sess_now.get("utc_hhmm", 0)
                _in_overlap = 1300 <= _hhmm < 1700          # London/NY overlap
                _in_active  = _in_overlap or (800 <= _hhmm < 1300)  # London + pre-overlap
                if _in_overlap:
                    interval = max(60, interval // 2)        # 2× faster during overlap
                elif not _in_active:
                    interval = interval * 2                  # 0.5× outside active windows

                # ── Fee-pressure interval scaling ─────────────────────────────
                # As the daily fee budget depletes, slow all agents proportionally.
                # This distributes trade opportunities across the day rather than
                # exhausting the budget in the first few hours.
                # Budget 0–20%: no change | 50%: ×2.1 | 80%: ×3.25 | 100%: ×4.0
                _fee_ratio = self._cached_budget_ratio
                if _fee_ratio > 0.20:
                    _fee_slowdown = 1.0 + (_fee_ratio - 0.20) * 3.75
                    interval = int(interval * _fee_slowdown)
                    logger.debug(
                        f"Fee-pressure scaling: {config.get('name')} interval ×{_fee_slowdown:.1f} "
                        f"(budget {_fee_ratio:.0%} used)"
                    )

                last_run = config.get('_last_run')

                if last_run:
                    time_since_run = (datetime.now() - last_run).total_seconds()
                    if time_since_run < interval:
                        continue

                # GATE 1: Check portfolio-level risk from Risk Manager
                if self._current_risk_assessment:
                    if self._current_risk_assessment.risk_level == "danger":
                        logger.warning(f"Skipping agent {config.get('name')} ({agent_id}): "
                                     f"portfolio risk level is DANGER")
                        await team_chat.log_agent_gate_block(
                            config.get('name', agent_id), "portfolio risk level is DANGER"
                        )
                        continue

                # GATE 2: Check if agent is within allocation from Portfolio Manager
                allocation_pct = self._current_allocation.get(agent_id, config.get('allocation_percentage', 10))
                if allocation_pct <= 0:
                    logger.info(f"Skipping agent {config.get('name')} ({agent_id}): "
                               f"allocation is 0% (disabled by portfolio manager)")
                    continue

                logger.info(f"Running automated agent: {config.get('name')} "
                           f"(allocation: {(allocation_pct or 0):.1f}%)")

                # Read paper/live mode from settings
                try:
                    from app.api.routes.settings import get_trading_prefs
                    use_paper_mode = get_trading_prefs().paper_trading_default
                except Exception:
                    use_paper_mode = True

                _s_type = config.get('strategy_type', 'momentum')

                # ── Grid strategy: separate execution path ─────────────────
                if _s_type == 'grid':
                    try:
                        await self._run_grid_agent(
                            agent_id=agent_id,
                            config=config,
                            allocation_pct=allocation_pct,
                            use_paper=use_paper_mode,
                        )
                    except Exception as ge:
                        logger.error(f"Grid agent {agent_id} error: {ge}", exc_info=True)
                    config['_last_run'] = datetime.now()
                    continue  # skip normal run_agent path
                # Per-strategy SL/TP defaults that reflect each strategy's behaviour.
                # All profiles enforce a minimum 2:1 R/R ratio (TP ≥ 2×SL).
                # Agents can override via their own config.
                _STRATEGY_RR = {
                    # strategy:       (sl_pct, tp_pct)
                    "momentum":        (2.0,  5.0),   # 2.5:1 — ride trend but cut losses fast
                    "trend_following": (3.0,  9.0),   # 3.0:1 — wider stops, bigger targets
                    "breakout":        (2.5,  7.0),   # 2.8:1 — clean break or out
                    "mean_reversion":  (1.5,  3.5),   # 2.3:1 — small moves, tight stops
                    "scalping":        (1.0,  2.5),   # 2.5:1 — very fast, very tight
                    "grid":            (3.0,  6.0),   # 2.0:1 — balanced grid
                    "ai":              (2.5,  6.0),   # 2.4:1 — LLM-guided, moderate
                }
                _default_sl, _default_tp = _STRATEGY_RR.get(_s_type, (2.5, 6.0))

                # Agent config overrides defaults; enforce minimum 2:1 R/R either way
                _sl = config.get('stop_loss_pct', _default_sl) or _default_sl
                _tp = config.get('take_profit_pct', _default_tp) or _default_tp
                if _tp < _sl * 2.0:
                    _tp = round(_sl * 2.0, 1)  # enforce 2:1 minimum

                result = await self.run_agent(
                    agent_id=config['id'],
                    name=config.get('name', ''),
                    strategy_type=_s_type,
                    trading_pairs=get_trading_prefs().trading_pairs or ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    allocation_pct=allocation_pct,
                    max_position=config.get('max_position_size', 0.1),
                    stop_loss_pct=_sl,
                    take_profit_pct=_tp,
                    trailing_stop_pct=config.get('trailing_stop_pct', 3.0),
                    use_paper=use_paper_mode,
                    timeframe=config.get('timeframe', '1h'),
                    cycle_trades=self._cycle_trades,
                )

                config['_last_run'] = datetime.now()

            except Exception as e:
                logger.error(f"Error running agent {agent_id}: {e}")
                asyncio.create_task(_send_telegram(telegram_service.alert_agent_error(
                    agent_name=config.get('name', agent_id), error=str(e)
                )))

    async def _run_grid_agent(
        self,
        agent_id: str,
        config: dict,
        allocation_pct: float,
        use_paper: bool,
    ) -> None:
        """
        Dedicated execution path for grid strategy agents.

        Each call:
        1. Fetches indicators for the primary symbol
        2. Checks exit conditions on any active grid → cancel + close if triggered
        3. If no active grid and market is ranging → initialise a new grid
        4. Places orders at pending levels near current price (up to MAX_OPEN_LEVELS concurrent)
        5. Checks filled levels for counter-order placement
        6. Posts team-chat updates on key events
        """
        from app.services.grid_engine import grid_engine
        import pandas as pd

        MAX_OPEN_LEVELS = config.get('max_grid_levels', 5)
        trading_pairs = config.get('trading_pairs', [])
        timeframe = config.get('timeframe', '15m')
        agent_name = config.get('name', agent_id[:8])

        if not trading_pairs:
            logger.warning(f"Grid agent {agent_id}: no trading pairs configured")
            return

        symbol = trading_pairs[0]  # grid agents trade one symbol

        # ── Fetch market data ───────────────────────────────────────────────
        try:
            klines = await self.phemex.get_klines(symbol, timeframe, 200)
            data = klines.get('data', klines) if isinstance(klines, dict) else klines
            if not data or len(data) < 50:
                logger.warning(f"Grid agent {agent_id}: insufficient klines for {symbol}")
                return

            df_data = [{
                'time': k[0] / 1000,
                'open': float(k[2]), 'high': float(k[3]),
                'low': float(k[4]), 'close': float(k[5]),
                'volume': float(k[7]),
            } for k in data]
            df = pd.DataFrame(df_data).sort_values('time')
            close = df['close']
            high = df['high']
            low = df['low']
            current_price = float(close.iloc[-1])

            bb = self.indicator_service.calculate_bollinger_bands(close)
            atr_series = self.indicator_service.calculate_atr(high, low, close)
            sma20_series = self.indicator_service.calculate_sma(close, 20) if len(df) >= 20 else None
            sma50_series = self.indicator_service.calculate_sma(close, 50) if len(df) >= 50 else None

            atr = float(atr_series.iloc[-1]) if atr_series is not None and len(atr_series) > 0 else 0.0
            bb_upper = float(bb['upper'].iloc[-1]) if bb is not None else current_price * 1.03
            bb_lower = float(bb['lower'].iloc[-1]) if bb is not None else current_price * 0.97
            sma_20 = float(sma20_series.iloc[-1]) if sma20_series is not None and len(sma20_series) > 0 else None
            sma_50 = float(sma50_series.iloc[-1]) if sma50_series is not None and len(sma50_series) > 0 else None
            prev_sma_20 = float(sma20_series.iloc[-2]) if sma20_series is not None and len(sma20_series) > 1 else None
            prev_sma_50 = float(sma50_series.iloc[-2]) if sma50_series is not None and len(sma50_series) > 1 else None

        except Exception as e:
            logger.error(f"Grid agent {agent_id}: failed to fetch market data: {e}")
            return

        # ── Check existing grid ─────────────────────────────────────────────
        active_grid = await grid_engine.get_active_grid(agent_id, symbol)

        if active_grid:
            exit_reason = grid_engine.check_exit_conditions(
                grid=active_grid,
                current_price=current_price,
                current_atr=atr,
                sma_20=sma_20,
                sma_50=sma_50,
                prev_sma_20=prev_sma_20,
                prev_sma_50=prev_sma_50,
            )

            if exit_reason:
                logger.info(f"Grid agent {agent_id}: cancelling grid — {exit_reason}")
                _, position_ids = await grid_engine.cancel_grid(active_grid.id, exit_reason)

                # Close all open positions associated with this grid
                for pos_id in position_ids:
                    try:
                        await trading_service.close_position(pos_id)
                    except Exception as ce:
                        logger.warning(f"Grid: failed to close position {pos_id}: {ce}")

                try:
                    await team_chat.add_message(
                        agent_role="trading_agent",
                        content=(
                            f"🔲 **Grid cancelled** — {symbol} ({agent_name})\n"
                            f"Reason: {exit_reason}\n"
                            f"Closed {len(position_ids)} open level(s)."
                        ),
                        message_type="trade_intent",
                        metadata={"agent_id": agent_id, "symbol": symbol},
                    )
                except Exception:
                    pass
                self._record_run(agent_id, symbol, "hold", 0.0, current_price, False, error=f"Grid cancelled: {exit_reason[:60]}")
                return

            # ── Try rebalance if price has drifted ─────────────────────────
            grid_centre = (active_grid.grid_low + active_grid.grid_high) / 2
            half_range = (active_grid.grid_high - active_grid.grid_low) / 2
            if abs(current_price - grid_centre) > 0.5 * half_range:
                await grid_engine.rebalance_grid(active_grid.id, current_price, atr, bb_upper, bb_lower)
                active_grid = await grid_engine.get_active_grid(agent_id, symbol)

        else:
            # ── No active grid — check if market is suitable ────────────────
            # Ranging condition: ATR < 3% of price AND price within BB bands
            atr_pct = atr / current_price * 100 if current_price else 99
            price_in_bands = bb_lower < current_price < bb_upper
            bb_width_pct = (bb_upper - bb_lower) / current_price * 100 if current_price else 99

            if atr_pct > 3.0 or not price_in_bands or bb_width_pct > 15:
                logger.info(
                    f"Grid agent {agent_id}: market not suitable for grid on {symbol} "
                    f"(ATR={atr_pct:.2f}%, BB width={bb_width_pct:.1f}%, in bands={price_in_bands})"
                )
                self._record_run(agent_id, symbol, "hold", 0.0, current_price, False,
                                 error="Market not ranging — grid not initialised")
                return

            # Compute grid capital from allocation
            try:
                from app.api.routes.settings import get_trading_prefs
                _prefs = get_trading_prefs()
                total_fund = self._total_capital or 10000
            except Exception:
                total_fund = self._total_capital or 10000

            grid_capital = total_fund * allocation_pct / 100
            n_levels = config.get('grid_levels', 10)

            active_grid = await grid_engine.initialise_grid(
                agent_id=agent_id,
                symbol=symbol,
                current_price=current_price,
                atr=atr,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                capital=grid_capital,
                n_levels=n_levels,
            )

            if not active_grid:
                self._record_run(agent_id, symbol, "hold", 0.0, current_price, False,
                                 error="Grid initialisation failed")
                return

            try:
                await team_chat.add_message(
                    agent_role="trading_agent",
                    content=(
                        f"🔲 **Grid initialised** — {symbol} ({agent_name})\n"
                        f"Range: ${active_grid.grid_low:.4f} – ${active_grid.grid_high:.4f} | "
                        f"{n_levels} levels | spacing {active_grid.grid_spacing_pct:.2f}% | "
                        f"capital ${grid_capital:.2f}"
                    ),
                    message_type="trade_intent",
                    metadata={"agent_id": agent_id, "symbol": symbol},
                )
            except Exception:
                pass

        if not active_grid:
            return

        # ── Place orders at pending levels ──────────────────────────────────
        open_count = await grid_engine.count_open_levels(active_grid)
        if open_count < MAX_OPEN_LEVELS:
            slots = MAX_OPEN_LEVELS - open_count
            pending = await grid_engine.get_pending_levels(active_grid, current_price)
            pending = pending[:slots]  # don't exceed concurrent limit

            for level in pending:
                try:
                    # Grid levels use the level price directly as SL/TP:
                    # Buy level: SL = grid_low - 0.5%, TP = next level up
                    # Sell level: SL = grid_high + 0.5%, TP = next level down
                    spacing = active_grid.grid_spacing_pct / 100 * current_price
                    if level.side == OrderSide.BUY:
                        sl = active_grid.grid_low * 0.995
                        tp = level.price + spacing
                    else:
                        sl = active_grid.grid_high * 1.005
                        tp = level.price - spacing

                    if use_paper:
                        order = await paper_trading.place_order(
                            symbol=symbol,
                            side=level.side,
                            quantity=level.quantity,
                            price=level.price,
                            agent_id=agent_id,
                            stop_loss_price=sl,
                            take_profit_price=tp,
                        )
                    else:
                        from app.services.live_trading import live_trading as _grid_live_svc
                        order = await _grid_live_svc.place_order(
                            symbol=symbol,
                            side=level.side,
                            quantity=level.quantity,
                            price=level.price,
                            agent_id=agent_id,
                            stop_loss_price=sl,
                            take_profit_price=tp,
                        )
                    if order:
                            await grid_engine.mark_level_open(level.id, order.id if hasattr(order, 'id') else str(order))
                            logger.info(
                                f"Grid {symbol}: placed {level.side.value} @ {level.price:.4f} "
                                f"qty={level.quantity:.6f} level_idx={level.level_index}"
                            )
                except Exception as oe:
                    logger.warning(f"Grid {agent_id}: failed to place order at level {level.price}: {oe}")

        # ── Check open levels: confirm entries + detect external closes ─────────
        # Pre-fetch position set ONCE to avoid N+1 DB calls inside the loop.
        from app.models import GridLevelStatus
        try:
            all_positions = list(await trading_service.get_positions(symbol=symbol, agent_id=agent_id))
            open_position_ids = {p.id for p in all_positions}
        except Exception as _pe:
            logger.warning(f"Grid {agent_id}: failed to fetch positions: {_pe}")
            open_position_ids = set()

        open_levels = await grid_engine.get_open_levels(active_grid)
        for level in open_levels:
            if not level.position_id:
                continue
            try:
                if level.status == GridLevelStatus.open:
                    # Entry order placed; check if the position is now live (entry confirmed)
                    if level.position_id in open_position_ids:
                        counter = await grid_engine.on_fill(level.id, level.price, level.position_id)
                        if counter:
                            # Place the counter order immediately rather than waiting for the next tick
                            spacing = active_grid.grid_spacing_pct / 100 * current_price
                            if counter.side == OrderSide.BUY:
                                c_sl = active_grid.grid_low * 0.995
                                c_tp = counter.price + spacing
                            else:
                                c_sl = active_grid.grid_high * 1.005
                                c_tp = counter.price - spacing
                            try:
                                if use_paper:
                                    c_order = await paper_trading.place_order(
                                        symbol=symbol, side=counter.side,
                                        quantity=counter.quantity, price=counter.price,
                                        agent_id=agent_id,
                                        stop_loss_price=c_sl, take_profit_price=c_tp,
                                    )
                                else:
                                    from app.services.live_trading import live_trading as _grid_live_svc
                                    c_order = await _grid_live_svc.place_order(
                                        symbol=symbol, side=counter.side,
                                        quantity=counter.quantity, price=counter.price,
                                        agent_id=agent_id,
                                        stop_loss_price=c_sl, take_profit_price=c_tp,
                                    )
                                if c_order:
                                    await grid_engine.mark_level_open(
                                        counter.id,
                                        c_order.id if hasattr(c_order, 'id') else str(c_order),
                                    )
                                    logger.info(
                                        f"Grid {symbol}: counter {counter.side.value} "
                                        f"@ {counter.price:.4f} placed after entry fill"
                                    )
                            except Exception as _co:
                                logger.warning(f"Grid {agent_id}: counter order placement failed: {_co}")

                elif level.status in (GridLevelStatus.filled, GridLevelStatus.counter_placed):
                    # Entry was confirmed; if the position is gone it was closed externally (SL/TP hit)
                    if level.position_id not in open_position_ids:
                        try:
                            closed_trades = await trading_service.get_closed_trades(symbol=symbol)
                        except Exception:
                            closed_trades = []
                        matching = next(
                            (t for t in closed_trades
                             if t.get('entry_order_id') == level.position_id
                             or t.get('position_id') == level.position_id),
                            None,
                        )
                        exit_price = matching.get('exit_price', current_price) if matching else current_price
                        pnl = await grid_engine.close_level(level.id, exit_price)
                        if pnl is not None:
                            self._record_run(
                                agent_id, symbol,
                                "buy" if level.side == OrderSide.BUY else "sell",
                                0.7, exit_price, True, pnl=pnl,
                            )
                            logger.info(
                                f"Grid {symbol}: level {level.level_index} closed externally "
                                f"@ {exit_price:.4f} pnl={pnl:+.4f}"
                            )
            except Exception as ce:
                logger.debug(f"Grid: level check error for {level.id}: {ce}")

        self._record_run(agent_id, symbol, "hold", 0.5, current_price, False)

    async def run_agent(
        self,
        agent_id: str,
        name: str,
        strategy_type: str,
        trading_pairs: List[str],
        allocation_pct: float,
        max_position: float,
        stop_loss_pct: float = 3.5,
        take_profit_pct: float = 7.0,
        trailing_stop_pct: Optional[float] = None,
        use_paper: bool = True,
        timeframe: str = "1h",
        cycle_trades: Optional[List[CycleTradeRecord]] = None,
    ) -> AgentRun:
        timestamp = datetime.now()
        
        if not trading_pairs:
            return AgentRun(
                agent_id=agent_id,
                timestamp=timestamp,
                symbol="",
                signal="hold",
                confidence=0,
                price=0,
                executed=False,
                error="No trading pairs configured"
            )
        
        # ── Scan ALL pairs, pick the best opportunity ──────────────────────
        # Watchlisted symbols (near-miss from the previous cycle) are prepended
        # so they get re-evaluated first; expired entries are pruned here.
        _now = datetime.now()
        _watch_prefix: list = []
        for _wkey, _wentry in list(self._setup_watchlist.items()):
            _wage, _wagent = _wkey.split(":", 1)
            if _wage != agent_id:
                continue
            if (_now - _wentry["at"]).total_seconds() > self._WATCHLIST_TTL_SECS:
                del self._setup_watchlist[_wkey]
                continue
            _wsym = _wkey.split(":", 1)[1]
            if _wsym in trading_pairs:
                _watch_prefix.append(_wsym)

        _scan_order = _watch_prefix + [s for s in trading_pairs if s not in _watch_prefix]
        if _watch_prefix:
            logger.debug(f"Agent {agent_id}: prioritising watchlisted symbols {_watch_prefix}")

        best_symbol = None
        best_confidence = 0.0
        best_signal = "hold"
        best_df = None
        best_reasoning = ""

        for candidate_symbol in _scan_order:
            try:
                klines = await self.phemex.get_klines(candidate_symbol, timeframe, 200)
                data = klines.get('data', klines) if isinstance(klines, dict) else klines
                if not data or len(data) < 50:
                    continue

                import pandas as pd
                df_data = [{
                    'time': k[0] / 1000,
                    'open': float(k[2]), 'high': float(k[3]),
                    'low': float(k[4]), 'close': float(k[5]),
                    'volume': float(k[7]),
                } for k in data]
                df = pd.DataFrame(df_data).sort_values('time')

                market_context = self._build_market_context(agent_id, candidate_symbol)

                # ── Multi-timeframe alignment ─────────────────────────────────
                # Fetch higher timeframe trend and inject into market_context so
                # the indicator service's context adjustment block can use it.
                try:
                    htf_trend = await self._get_htf_trend(candidate_symbol, timeframe)
                    if htf_trend and market_context is not None:
                        market_context["htf_trend"] = htf_trend
                    elif htf_trend:
                        market_context = {"htf_trend": htf_trend}
                except Exception:
                    pass  # HTF fetch failure must not block the trade cycle

                # ── Regime gate ───────────────────────────────────────────────
                # Skip this agent entirely if the current market regime is in the
                # strategy's avoid_conditions list (from registry.yaml).
                _current_regime = market_context.get("regime", "") if market_context else ""
                if _current_regime:
                    import app.strategies as _sr
                    _strat_def = _sr.get(strategy_type)
                    _avoid = _strat_def.get("avoid_conditions", []) if _strat_def else []
                    if _current_regime in _avoid:
                        logger.info(
                            f"Regime gate: skipping {name} ({strategy_type}) — "
                            f"regime '{_current_regime}' is in avoid_conditions {_avoid}"
                        )
                        continue  # skip to next candidate_symbol
                # ── End regime gate ───────────────────────────────────────────

                # ── Multi-timeframe confluence hard gate ──────────────────────
                # Block entry when timeframes actively disagree (alignment=mixed)
                # AND the overall confluence score is weak (< 0.4).
                # Scalping is exempt — it operates on very short TFs where 4h/1d
                # divergence is expected and irrelevant.
                if strategy_type != "scalping" and market_context:
                    _ta_align = market_context.get("ta_alignment", "unknown")
                    _ta_score = market_context.get("ta_confluence_score", 1.0)
                    _htf = market_context.get("htf_trend", "neutral")
                    try:
                        from app.api.routes.settings import get_trading_gates
                        _gates = get_trading_gates()
                        _mtf_block_score = _gates.mtf_confluence_block_score
                    except Exception:
                        _mtf_block_score = 0.40
                    # Gate fires only when BOTH conditions are true: mixed alignment + low score
                    if _ta_align == "mixed" and _ta_score < _mtf_block_score:
                        logger.info(
                            f"MTF gate: skipping {name} on {candidate_symbol} — "
                            f"timeframes disagree (alignment={_ta_align}, score={_ta_score:.2f})"
                        )
                        continue
                    # Hard veto: HTF directly opposes the agent's home timeframe trend
                    # (only when we have a score to anchor confidence — not on cold start)
                    if _htf and _ta_score > 0 and _ta_align != "unknown":
                        _regime_bullish = _current_regime in ("trending_up",)
                        _regime_bearish = _current_regime in ("trending_down",)
                        if (_regime_bullish and _htf == "bearish") or \
                           (_regime_bearish and _htf == "bullish"):
                            # HTF directly contradicts intraday regime — skip
                            logger.info(
                                f"MTF gate: skipping {name} on {candidate_symbol} — "
                                f"HTF ({_htf}) contradicts intraday regime ({_current_regime})"
                            )
                            continue
                # ── End MTF confluence gate ───────────────────────────────────

                if strategy_type == 'ai':
                    from app.services.llm import llm_service
                    # Calculate full indicator set for AI strategy (same as indicator-based strategies)
                    _ai_close = df['close']
                    _ai_high  = df['high']  if 'high'  in df.columns else _ai_close
                    _ai_low   = df['low']   if 'low'   in df.columns else _ai_close
                    _ai_vol   = df['volume'] if 'volume' in df.columns else None
                    _ai_bb    = self.indicator_service.calculate_bollinger_bands(_ai_close) if len(df) >= 20 else None
                    _ai_macd  = self.indicator_service.calculate_macd(_ai_close) if len(df) >= 26 else None
                    _ai_sma20 = self.indicator_service.calculate_sma(_ai_close, 20) if len(df) >= 20 else None
                    _ai_sma50 = self.indicator_service.calculate_sma(_ai_close, 50) if len(df) >= 50 else None
                    _ai_sma200 = self.indicator_service.calculate_sma(_ai_close, 200) if len(df) >= 200 else None
                    _ai_atr   = self.indicator_service.calculate_atr(_ai_high, _ai_low, _ai_close) if len(df) >= 14 else None
                    _ai_vol_sma = self.indicator_service.calculate_volume_sma(_ai_vol) if _ai_vol is not None and len(df) >= 20 else None
                    indicators_dict = {
                        'rsi':        float(self.indicator_service.calculate_rsi(_ai_close).iloc[-1]) if len(df) >= 14 else None,
                        'macd':       float(_ai_macd['macd'].iloc[-1])   if _ai_macd is not None else None,
                        'macd_signal': float(_ai_macd['signal'].iloc[-1]) if _ai_macd is not None else None,
                        'bb_upper':   float(_ai_bb['upper'].iloc[-1])    if _ai_bb is not None else None,
                        'bb_middle':  float(_ai_bb['middle'].iloc[-1])   if _ai_bb is not None else None,
                        'bb_lower':   float(_ai_bb['lower'].iloc[-1])    if _ai_bb is not None else None,
                        'sma_20':     float(_ai_sma20.iloc[-1])          if _ai_sma20 is not None else None,
                        'sma_50':     float(_ai_sma50.iloc[-1])          if _ai_sma50 is not None else None,
                        'sma_200':    float(_ai_sma200.iloc[-1])         if _ai_sma200 is not None else None,
                        'atr':        float(_ai_atr.iloc[-1])            if _ai_atr is not None else None,
                        'volume':     float(_ai_close.index[-1]) if len(df) > 0 else None,
                        'volume_sma': float(_ai_vol_sma.iloc[-1])        if _ai_vol_sma is not None else None,
                    }

                    # ── LLM pre-filter: skip the LLM when indicators say hold ──
                    # Run the deterministic indicator engine first (already have the df,
                    # cost is near-zero). Only invoke the LLM when indicators show a
                    # directional signal with >= 50% conviction — roughly 60-80% of
                    # cycles will be skipped, cutting AI-agent LLM calls dramatically.
                    _pre_config = {
                        'strategy': 'momentum',
                        'indicators_config': self._enabled_agents.get(agent_id, {}).get('indicators_config', {}),
                    }
                    _pre_result = self.indicator_service.generate_signal(df, _pre_config, market_context=market_context)
                    _pre_sig = _pre_result.signal.value if _pre_result.signal else 'hold'
                    _pre_conf = _pre_result.confidence

                    if _pre_sig == 'hold' or _pre_conf < 0.50:
                        logger.debug(
                            f"AI agent {name} on {candidate_symbol}: "
                            f"indicator pre-filter skipped LLM (sig={_pre_sig}, conf={_pre_conf:.2f})"
                        )
                        sig = 'hold'
                        conf = _pre_conf
                        reas = 'Indicator pre-filter: no directional signal — LLM skipped'
                    else:
                        # Indicators show conviction — call LLM for final confirmation
                        team_context = await self._build_team_context(agent_id, candidate_symbol, timeframe)

                        # Build trader persona for system prompt
                        _agent_cfg = self._enabled_agents.get(agent_id, {})
                        _trader_id = _agent_cfg.get("trader_id")
                        _trader_obj = next((t for t in self._traders if t.get("id") == _trader_id), None) if _trader_id else None
                        _trader_cfg = (_trader_obj or {}).get("config", {})
                        agent_context = {
                            "trader_name":         (_trader_obj or {}).get("name", "Portfolio Manager"),
                            "trader_bio":          _trader_cfg.get("bio", ""),
                            "trader_style":        _trader_cfg.get("style", "Balanced and disciplined."),
                            "risk_tolerance":      _trader_cfg.get("risk_tolerance", "moderate"),
                            "preferred_strategies": _trader_cfg.get("preferred_strategies", [strategy_type]),
                            "agent_name":          name,
                            "strategy_type":       strategy_type,
                        }

                        llm_result = await llm_service.generate_signal(
                            indicators_dict,
                            {'current': float(df['close'].iloc[-1])},
                            team_context=team_context,
                            agent_context=agent_context,
                        )
                        sig = llm_result.action
                        conf = llm_result.confidence
                        reas = llm_result.reasoning
                else:
                    _agent_cfg_ind = self._enabled_agents.get(agent_id, {})
                    _ind_config = {
                        'strategy': strategy_type,
                        'indicators_config': _agent_cfg_ind.get('indicators_config', {}),
                    }
                    signal_result = self.indicator_service.generate_signal(df, _ind_config, market_context=market_context)
                    sig = signal_result.signal.value if signal_result.signal else 'hold'
                    conf = signal_result.confidence
                    reas = getattr(signal_result, 'reasoning', '')

                if sig in ('buy', 'sell') and conf > best_confidence:
                    best_symbol = candidate_symbol
                    best_confidence = conf
                    best_signal = sig
                    best_df = df
                    best_reasoning = reas

                # ── Watchlist update ──────────────────────────────────────────
                # Near-miss: add to watchlist so next cycle prioritises this symbol.
                # Above the upper bound: remove (setup either traded or expired strong).
                _wk = f"{agent_id}:{candidate_symbol}"
                if sig in ('buy', 'sell') and self._WATCHLIST_CONF_LOW <= conf <= self._WATCHLIST_CONF_HIGH:
                    self._setup_watchlist[_wk] = {"confidence": conf, "signal": sig, "at": datetime.now()}
                    logger.debug(f"Watchlist ADD {candidate_symbol} conf={conf:.2f} sig={sig}")
                elif conf > self._WATCHLIST_CONF_HIGH or sig == 'hold':
                    self._setup_watchlist.pop(_wk, None)

            except Exception as e:
                logger.warning(f"Skipping {candidate_symbol} for agent {name}: {e}")
                continue

        # If no pair produced a tradeable signal, return hold for the first pair
        if best_symbol is None or best_signal == 'hold':
            symbol = trading_pairs[0]
            try:
                klines = await self.phemex.get_klines(symbol, timeframe, 200)
                data = klines.get('data', klines) if isinstance(klines, dict) else klines
                current_price = float(data[-1][5]) if data else 0
            except Exception:
                current_price = 0
            self._record_run(agent_id, symbol, "hold", best_confidence, current_price, False)
            return AgentRun(
                agent_id=agent_id, timestamp=timestamp, symbol=symbol,
                signal="hold", confidence=best_confidence, price=current_price,
                executed=False,
            )

        symbol = best_symbol
        signal = best_signal
        confidence = best_confidence
        reasoning = best_reasoning
        df = best_df
        current_price = df['close'].iloc[-1]
        team_context = await self._build_team_context(agent_id, symbol, timeframe)

        if len(trading_pairs) > 1:
            logger.info(f"Agent {name}: scanned {len(trading_pairs)} pairs, "
                       f"best opportunity: {signal.upper()} {symbol} ({confidence:.0%})")

        # Resolve trader identity for chat messages
        _agent_cfg_pre = self._enabled_agents.get(agent_id, {})
        _trader_id_pre = _agent_cfg_pre.get("trader_id")
        _trader_name = "Portfolio Manager"
        _trader_avatar = "💼"
        if _trader_id_pre:
            _trader_obj = next((t for t in self._traders if t.get("id") == _trader_id_pre), None)
            if _trader_obj:
                _trader_name = _trader_obj.get("name", _trader_name)
                _cfg = _trader_obj.get("config") or {}
                _trader_avatar = _cfg.get("avatar", _trader_avatar) if isinstance(_cfg, dict) else _trader_avatar

        try:
            executed = False
            pnl = None
            _effective_min_entry_conf = None
            _effective_conf_floor = None
            _min_net_tp_pct = 0.50

            try:
                from app.api.routes.settings import get_trading_gates as _get_gates
                _entry_gates = _get_gates()
                _min_entry_conf = _entry_gates.min_entry_confidence
                _ta_veto_conf = _entry_gates.ta_veto_confidence
                _conf_ref = _entry_gates.confidence_size_reference
                _conf_floor = _entry_gates.confidence_size_floor
            except Exception:
                _min_entry_conf = 0.60
                _ta_veto_conf = 0.75
                _conf_ref = 0.78
                _conf_floor = 0.25

            _effective_min_entry_conf = _min_entry_conf
            _effective_conf_floor = _conf_floor

            if signal in ('buy', 'sell'):
                _fee_pressure = await self._get_daily_fee_pressure()
                _budget_used = float(_fee_pressure.get("budget_used_ratio", 0.0) or 0.0)

                # ── Check whether this agent already has open positions ────────
                # If so, skip fee-guard threshold raises: the agent is already
                # deployed and performing — adding friction penalises good agents.
                # Only brand-new entries (no open position on any symbol for this
                # agent) should be gated by poor fund-wide coverage metrics.
                _agent_has_open_pos = False
                try:
                    _open_pos = await paper_trading.get_positions(agent_id=agent_id)
                    _agent_has_open_pos = len(_open_pos) > 0
                except Exception:
                    pass

                if _budget_used >= 1.0:
                    _reason = (
                        f"Daily fee budget exhausted: ${_fee_pressure['daily_fees_paid']:.2f} paid today "
                        f"({float(_fee_pressure['daily_fees_pct']):.2f}% of capital) vs "
                        f"{float(_fee_pressure['max_daily_fees_pct']):.2f}% limit"
                    )
                    logger.info(f"Fee budget gate blocked {name} {signal.upper()} {symbol}: {_reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="Daily fee budget exhausted")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name,
                        trader_avatar=_trader_avatar,
                        agent_name=name,
                        symbol=symbol,
                        side=signal,
                        reason=f"{_reason}. Preserving capital for higher-quality setups after the UTC reset.",
                    )
                    return AgentRun(
                        agent_id=agent_id,
                        timestamp=datetime.now(),
                        symbol=symbol,
                        signal="hold",
                        confidence=0,
                        price=current_price,
                        executed=False,
                        error="Daily fee budget exhausted",
                    )

                if _budget_used >= 0.5 and not _agent_has_open_pos:
                    _pressure = min(1.0, max(0.0, (_budget_used - 0.5) / 0.5))
                    _effective_min_entry_conf = min(0.90, _min_entry_conf + (0.10 * _pressure))
                    _effective_conf_floor = max(0.15, _conf_floor - (0.10 * _pressure))
                    _min_net_tp_pct = 0.50 + (0.50 * _pressure)

                _fee_guard_active = bool(_fee_pressure.get("fee_coverage_guard_active", False))
                if _fee_guard_active and not _agent_has_open_pos:
                    _ratio = float(_fee_pressure.get("fee_coverage_ratio", 0.0) or 0.0)
                    _target = float(_fee_pressure.get("fee_coverage_min_ratio", 2.5) or 2.5)
                    _deficit = min(1.0, max(0.0, (_target - _ratio) / max(_target, 0.01)))

                    _effective_min_entry_conf = min(0.92, _effective_min_entry_conf + (0.12 * _deficit))
                    _effective_conf_floor = max(0.10, _effective_conf_floor - (0.10 * _deficit))
                    _min_net_tp_pct = max(_min_net_tp_pct, 0.75 + (0.75 * _deficit))

                    # If coverage has materially deteriorated, allow only top-tier setups.
                    if _deficit >= 0.55 and confidence < 0.85:
                        _reason = (
                            f"Fee coverage guard: net-edge/fees ratio {_ratio:.2f}x below target {_target:.2f}x. "
                            f"Pausing medium-quality entries until edge improves."
                        )
                        logger.info(f"Fee coverage guard blocked {name} {signal.upper()} {symbol}: {_reason}")
                        self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="Fee coverage guard")
                        await team_chat.log_trade_blocked(
                            trader_name=_trader_name,
                            trader_avatar=_trader_avatar,
                            agent_name=name,
                            symbol=symbol,
                            side=signal,
                            reason=_reason,
                        )
                        return AgentRun(
                            agent_id=agent_id,
                            timestamp=datetime.now(),
                            symbol=symbol,
                            signal="hold",
                            confidence=0,
                            price=current_price,
                            executed=False,
                            error="Fee coverage guard",
                        )

            # ── Direction-aware whale gate ────────────────────────────────────
            # If smart-money is overwhelmingly positioned AGAINST the proposed
            # direction, zero out confidence to block the entry.
            # This gate can be disabled via Settings → Trade Gates → Whale Entry Gate
            # to test strategy performance without whale signal influence.
            _wsp = getattr(self._current_risk_assessment, 'whale_short_pct', None) \
                   if self._current_risk_assessment else None
            _whale_gate_on = True
            try:
                _whale_gate_on = _get_gates().whale_entry_gate_enabled
            except Exception:
                pass
            if _whale_gate_on and _wsp is not None and signal in ('buy', 'sell'):
                _whale_long_pct = 1.0 - _wsp
                if signal == 'buy' and _wsp >= 0.70:
                    logger.warning(
                        f"WHALE GATE blocked {name} BUY {symbol}: "
                        f"{_wsp:.0%} of whale notional is SHORT"
                    )
                    await team_chat.log_agent_gate_block(
                        name, f"whale smart-money {_wsp:.0%} SHORT opposes LONG entry"
                    )
                    confidence = 0.0
                elif signal == 'sell' and _whale_long_pct >= 0.70:
                    logger.warning(
                        f"WHALE GATE blocked {name} SELL {symbol}: "
                        f"{_whale_long_pct:.0%} of whale notional is LONG"
                    )
                    await team_chat.log_agent_gate_block(
                        name, f"whale smart-money {_whale_long_pct:.0%} LONG opposes SHORT entry"
                    )
                    confidence = 0.0

            # ── US Open confirmation window gate ─────────────────────────────────────────
            # Between the end of the blackout and 14:15 UTC, the US session
            # direction is establishing but not yet clean. Require elevated
            # confidence to filter out noise trades during this transition.
            if signal in ('buy', 'sell'):
                _us_gate_sess = self._get_market_session_info()
                if _us_gate_sess["in_confirmation"] and confidence < _us_gate_sess["confirmation_confidence"]:
                    logger.info(
                        f"US OPEN CONFIRMATION gate blocked {name} {signal.upper()} {symbol}: "
                        f"{confidence:.0%} confidence < {_us_gate_sess['confirmation_confidence']:.0%} required"
                    )
                    await team_chat.log_agent_gate_block(
                        name,
                        f"US open confirmation window (13:30–14:15 UTC): "
                        f"need {_us_gate_sess['confirmation_confidence']:.0%} confidence to enter, "
                        f"got {confidence:.0%}. Direction not yet confirmed — standing by.",
                    )
                    confidence = 0.0

            # ── London open fake-out window ───────────────────────────────────────────────
            # 08:30–09:00 UTC: London open typically produces a spike that reverses
            # before the real intraday direction establishes. Penalise confidence
            # to require stronger conviction; block if below minimum threshold.
            # Only applied to intraday strategies (momentum, breakout, ema_crossover).
            if signal in ('buy', 'sell') and confidence > 0:
                _lo_sess = _us_gate_sess  # reuse already-fetched session info
                _lo_intraday_strategies = ('momentum', 'breakout', 'ema_crossover', 'ai', 'default')
                if _lo_sess.get("in_london_fakeout") and strategy_type in _lo_intraday_strategies:
                    _lo_penalty  = _lo_sess["london_fakeout_penalty"]
                    _lo_min_conf = _lo_sess["london_fakeout_min_conf"]
                    _pre_penalty_conf = confidence
                    confidence = max(confidence * (1.0 - _lo_penalty), 0.01)
                    if confidence < _lo_min_conf:
                        logger.info(
                            f"LONDON FAKEOUT gate dampened {name} {signal.upper()} {symbol}: "
                            f"{_pre_penalty_conf:.0%} → {confidence:.0%} (min {_lo_min_conf:.0%} required)"
                        )
                        await team_chat.log_agent_gate_block(
                            name,
                            f"London open fake-out window (08:30–09:00 UTC): direction not yet "
                            f"confirmed — spike may reverse. Confidence {_pre_penalty_conf:.0%} → "
                            f"{confidence:.0%} after -{_lo_penalty:.0%} penalty (need {_lo_min_conf:.0%}).",
                        )
                        confidence = 0.0
                    else:
                        logger.info(
                            f"LONDON FAKEOUT penalty applied {name} {signal.upper()} {symbol}: "
                            f"{_pre_penalty_conf:.0%} → {confidence:.0%} (-{_lo_penalty:.0%}), trade proceeds"
                        )

            # ── Overnight dead zone gate ──────────────────────────────────────────────────
            # 20:00–00:00 UTC: NY winding down, Asia not yet active. Volume is thin,
            # spreads widen, false signals are common. Dampen confidence for trending
            # strategies; mean_reversion and grid are less affected.
            if signal in ('buy', 'sell') and confidence > 0:
                _dz_sess = _us_gate_sess  # reuse session info
                _dz_trending_strategies = ('momentum', 'breakout', 'ema_crossover', 'ai', 'default')
                if _dz_sess.get("in_dead_zone") and strategy_type in _dz_trending_strategies:
                    _dz_penalty  = _dz_sess["dead_zone_penalty"]
                    _dz_min_conf = _dz_sess["dead_zone_min_conf"]
                    _pre_dz_conf = confidence
                    confidence = max(confidence * (1.0 - _dz_penalty), 0.01)
                    if confidence < _dz_min_conf:
                        logger.info(
                            f"DEAD ZONE gate dampened {name} {signal.upper()} {symbol}: "
                            f"{_pre_dz_conf:.0%} → {confidence:.0%} (min {_dz_min_conf:.0%} required)"
                        )
                        await team_chat.log_agent_gate_block(
                            name,
                            f"Overnight dead zone (20:00–00:00 UTC): thin volume, unreliable signals. "
                            f"Confidence {_pre_dz_conf:.0%} → {confidence:.0%} after -{_dz_penalty:.0%} "
                            f"penalty (need {_dz_min_conf:.0%} to enter). Standing by for active session.",
                        )
                        confidence = 0.0
                    else:
                        logger.info(
                            f"DEAD ZONE penalty applied {name} {signal.upper()} {symbol}: "
                            f"{_pre_dz_conf:.0%} → {confidence:.0%} (-{_dz_penalty:.0%}), trade proceeds"
                        )

            if signal in ['buy', 'sell'] and confidence >= _effective_min_entry_conf:
                # Announce trade intent to the team before gates run
                await team_chat.log_trade_intent(
                    trader_name=_trader_name,
                    trader_avatar=_trader_avatar,
                    agent_name=name,
                    symbol=symbol,
                    side=signal,
                    strategy=strategy_type,
                    confidence=confidence,
                    reasoning=reasoning or f"Signal generated by {strategy_type} strategy",
                )

                # ── Fetch technical analysis early (for TP scaling & checks) ───
                # We need confluence data for confluence-aware TP scaling.
                # Check cache first to avoid redundant API calls.
                _ta_cache_key = f"{symbol}:{timeframe}"
                technical_report = None
                if _ta_cache_key in self._ta_cache:
                    technical_report, _cached_at = self._ta_cache[_ta_cache_key]
                    if (datetime.now() - _cached_at).total_seconds() >= 300:  # 5-min cache TTL
                        # Cache expired — re-fetch
                        technical_report = await technical_analyst.analyze(symbol, timeframe=timeframe)
                        self._ta_cache[_ta_cache_key] = (technical_report, datetime.now())
                    # else: cache still valid, use it
                else:
                    technical_report = await technical_analyst.analyze(symbol, timeframe=timeframe)
                    self._ta_cache[_ta_cache_key] = (technical_report, datetime.now())

                # ── ATR-scaled TP% ─────────────────────────────────────────────
                # Scale the base TP% by the ratio of current ATR to the 20-period
                # average ATR. This prevents the same flat % from being used in
                # calm low-volatility markets (where it's unreachable) and in
                # explosive high-volatility markets (where it's too tight).
                #
                # Multiplier is clamped to [0.6, 1.5] to prevent extremes:
                #   low vol  → narrower TP (e.g. 6% → 4%) — avoids sitting forever
                #   high vol → wider TP   (e.g. 6% → 8%) — rides the move properly
                _original_tp_pct = take_profit_pct
                try:
                    if df is not None and len(df) >= 20:
                        import pandas as _pd_tp
                        _high = df['high'].astype(float)
                        _low  = df['low'].astype(float)
                        _close = df['close'].astype(float)
                        _tr = _pd_tp.concat([
                            _high - _low,
                            (_high - _close.shift(1)).abs(),
                            (_low  - _close.shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        _atr_current = float(_tr.iloc[-14:].mean()) if len(_tr) >= 14 else None
                        _atr_avg20   = float(_tr.iloc[-34:-14].mean()) if len(_tr) >= 34 else _atr_current
                        if _atr_current and _atr_avg20 and _atr_avg20 > 0:
                            _atr_ratio = _atr_current / _atr_avg20
                            _atr_mult = max(0.6, min(1.5, _atr_ratio))
                            take_profit_pct = round(take_profit_pct * _atr_mult, 2)
                            if abs(_atr_mult - 1.0) > 0.05:
                                logger.info(
                                    f"ATR-scaled TP: {name}/{symbol} "
                                    f"ATR ratio={_atr_ratio:.2f} → mult={_atr_mult:.2f} "
                                    f"TP {_original_tp_pct:.1f}%→{take_profit_pct:.1f}%"
                                )
                except Exception as _atr_err:
                    logger.debug(f"ATR scaling skipped ({_atr_err}) — using base TP {take_profit_pct:.1f}%")

                # ── Confluence-aware TP scaling (post-ATR) ───────────────────
                # Scale TP based on multi-timeframe confluence score:
                #   Strong (≥80%)  → 1.4–1.6× (let big trends run)
                #   Medium (40–80%) → 1.0–1.2× (ATR-scaled only, no boost)
                #   Weak (≤40%)    → 0.75–0.9× (tighter targets, close faster)
                #
                # Weak trades exiting faster raises avg win size; strong trades
                # with room to run inflates winners relative to SL-capped losers.
                # This targets a better loss ratio (from current 3:1 toward 1.5:1).
                _pre_conf_tp = take_profit_pct
                if technical_report and technical_report.multi_timeframe:
                    _conf = technical_report.multi_timeframe.confluence_score
                    if _conf is not None:
                        if _conf >= 0.80:
                            # Strong: boost to 1.4–1.6× range
                            # Linear scale from 1.4 at 80% to 1.6 at 100%
                            _conf_mult = min(1.4 + (_conf - 0.80) * 2, 1.6)
                        elif _conf <= 0.40:
                            # Weak: shrink to 0.75–0.9× range
                            # Linear scale from 0.9 at 40% down to 0.75 at 0%
                            _conf_mult = max(0.9 - (0.40 - _conf) * 0.375, 0.75)
                        else:
                            # Medium: linear interpolation 1.0–1.2× between 40%–80%
                            _conf_mult = 1.0 + (_conf - 0.40) * (0.2 / 0.40)
                        
                        take_profit_pct = round(take_profit_pct * _conf_mult, 2)
                        if abs(_conf_mult - 1.0) > 0.05:  # log only if material change
                            logger.info(
                                f"Confluence TP scale: {name}/{symbol} "
                                f"conf={_conf:.0%} → mult={_conf_mult:.2f}x "
                                f"TP {_pre_conf_tp:.1f}% → {take_profit_pct:.1f}%"
                            )

                # ── Recent price range sanity check ───────────────────────────
                # If TP% > the actual 20-candle high-low swing range and the
                # regime is NOT trending, reduce TP to 80% of the recent range
                # so we're targeting a realistic move rather than a phantom one.
                #
                # Directional strategies (momentum/trend/breakout/ai) need room to
                # run, so we enforce a stricter 3:1 R/R floor here — the range cap
                # can never push their TP below 3× their SL.
                # Mean-reversion and scalping retain the original 1.5:1 floor.
                _is_directional = strategy_type in ("momentum", "trend_following", "breakout", "ai")
                _range_rr_floor = 3.0 if _is_directional else 1.5
                try:
                    if df is not None and len(df) >= 20 and _current_regime not in ("trending_up", "trending_down"):
                        _recent = df.iloc[-20:]
                        _range_pct = (float(_recent['high'].max()) - float(_recent['low'].min())) / float(_recent['close'].iloc[-1]) * 100
                        if take_profit_pct > _range_pct and _range_pct > 0.5:
                            _range_capped_tp = round(_range_pct * 0.80, 2)
                            if _range_capped_tp > stop_loss_pct * _range_rr_floor:  # preserve R/R floor
                                logger.info(
                                    f"Range sanity cap: {name}/{symbol} "
                                    f"TP {take_profit_pct:.1f}% > 20-candle range {_range_pct:.1f}% "
                                    f"(regime={_current_regime}) → capped to {_range_capped_tp:.1f}%"
                                )
                                take_profit_pct = _range_capped_tp
                except Exception as _range_err:
                    logger.debug(f"Range sanity check skipped ({_range_err})")

                # Minimum profit gate: reject trades where TP doesn't cover round-trip fees
                # Use spot fee rate for USDT pairs, contract rate for coin-margined
                # Phemex spot taker: 0.1% | Phemex contract taker: 0.06%
                _is_spot = symbol.endswith("USDT")
                _taker_fee_pct = 0.10 if _is_spot else 0.06
                round_trip_fee_pct = _taker_fee_pct * 2
                net_tp_pct = take_profit_pct - round_trip_fee_pct
                if net_tp_pct < _min_net_tp_pct:
                    logger.warning(
                        f"Trade skipped: TP {take_profit_pct}% minus fees {round_trip_fee_pct}% = "
                        f"{net_tp_pct:.2f}% net — below {_min_net_tp_pct:.2f}% minimum"
                    )
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="TP too low after fees")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=f"TP {take_profit_pct}% minus fees = {net_tp_pct:.2f}% net — below {_min_net_tp_pct:.2f}% minimum",
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="TP too low after fees"
                    )

                # ── EV coverage gate: expected edge must cover fees by minimum multiple ──
                # EV% = (win_rate × TP%) − ((1 − win_rate) × SL%)
                # Coverage = EV% / round-trip-fee%
                # Default 3.0×: passes all normal momentum/trend/breakout (50% WR / 2:1 R:R ≈ 12×)
                # but blocks marginal scalping, ATR-squeezed targets, and below-breakeven WR setups.
                # Uses the backtest win rate if available; falls back to a neutral 50% prior.
                try:
                    from app.api.routes.settings import get_trading_gates as _ev_gates_fn
                    _min_ev_cov = float(getattr(_ev_gates_fn(), 'min_trade_ev_coverage_ratio', 3.0) or 3.0)
                except Exception:
                    _min_ev_cov = 3.0

                _ev_win_rate = float(getattr(_bt_result, 'win_rate', 0.50) or 0.50) if '_bt_result' in dir() and _bt_result else 0.50
                _ev_pct = (_ev_win_rate * take_profit_pct) - ((1.0 - _ev_win_rate) * stop_loss_pct)
                _ev_coverage = _ev_pct / round_trip_fee_pct if round_trip_fee_pct > 0 else 99.0

                if _ev_coverage < _min_ev_cov:
                    _ev_reason = (
                        f"EV coverage {_ev_coverage:.1f}× below {_min_ev_cov:.1f}× minimum "
                        f"(WR={_ev_win_rate:.0%}, TP={take_profit_pct:.1f}%, "
                        f"SL={stop_loss_pct:.1f}%, rt-fee={round_trip_fee_pct:.2f}%). "
                        f"Trade does not earn enough edge to justify the fee cost."
                    )
                    logger.info(f"EV coverage gate blocked {name} {signal.upper()} {symbol}: {_ev_reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="EV coverage too low")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal, reason=_ev_reason,
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="EV coverage too low",
                    )

                # Each agent is fully isolated via the (user_id, agent_id, symbol) unique constraint.
                # Different agents — even on the same symbol — operate independently and should not
                # block each other. No cross-agent conflict gate needed here.

                # POST-CLOSE COOLDOWN: prevent whipsaw re-entries on the same symbol
                # within 15 minutes of a position close. Avoids the pattern where an
                # agent closes a loss, then immediately re-enters on the same noisy signal.
                _cooldown_key = f"{agent_id}:{symbol}"
                _last_close = self._recent_closes.get(_cooldown_key)
                if _last_close:
                    _seconds_since_close = (datetime.now() - _last_close).total_seconds()
                    # Dynamic cooldown: scales with fee budget consumption so re-entries
                    # are slower when the budget is already partially depleted.
                    # 0% budget: 15 min base | 50%: ~22 min | 80%: ~34 min | 100%: 45 min (cap 60 min)
                    _effective_cooldown = int(
                        self._POST_CLOSE_COOLDOWN_SECONDS * max(1.0, 1.0 + self._cached_budget_ratio * 2.0)
                    )
                    _effective_cooldown = min(3600, _effective_cooldown)
                    if _seconds_since_close < _effective_cooldown:
                        _mins_left = (_effective_cooldown - _seconds_since_close) / 60
                        logger.info(
                            f"Post-close cooldown: {name}/{symbol} — "
                            f"{_mins_left:.0f}m remaining before re-entry allowed"
                        )
                        self._record_run(agent_id, symbol, signal, confidence, current_price, False,
                                        error=f"Post-close cooldown ({_mins_left:.0f}m left)")
                        return AgentRun(
                            agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                            signal="hold", confidence=0, price=current_price,
                            executed=False, error=f"Post-close cooldown ({_mins_left:.0f}m left)",
                        )

                if _is_paper_mode():
                    balances = await paper_trading.get_all_balances()
                    usdt_balance = next((b.available for b in balances if b.asset == "USDT"), 50_000.0)
                else:
                    try:
                        from app.services.live_trading import live_trading as _lt_bal
                        _live_bal = await _lt_bal.get_balance()
                        usdt_balance = _live_bal.get("available", 0.0) or 50_000.0
                    except Exception:
                        balances = await paper_trading.get_all_balances()
                        usdt_balance = next((b.available for b in balances if b.asset == "USDT"), 50_000.0)

                # Strategy-specific position size multipliers.
                # Scalping/mean-reversion work on small price moves — a full allocation
                # produces positions so large that fees overwhelm the expected P&L.
                _STRATEGY_SIZE_MULT = {
                    "momentum":       1.00,
                    "trend_following": 0.80,
                    "breakout":        0.70,
                    "mean_reversion":  0.40,
                    "scalping":        0.30,
                    "grid":            0.50,
                }
                _size_mult = _STRATEGY_SIZE_MULT.get(strategy_type, 1.0)

                # ── Strategy-level retrospective confidence adjustment ──────────
                # If the trade retrospective has learned that this strategy type is
                # systematically over- or under-performing across ALL its agents,
                # apply a strategy-wide confidence multiplier. This enforces
                # cross-agent learning: not just per-agent SL/TP adjustments.
                _strategy_insights = (
                    self._current_trade_insights.get("strategy_insights", {})
                    if self._current_trade_insights else {}
                )
                _s_insight = _strategy_insights.get(strategy_type)
                if _s_insight:
                    _s_adj = _s_insight.get("confidence_adj", 0.0)
                    if _s_adj != 0.0:
                        _before = confidence
                        confidence = max(0.0, min(1.0, confidence * (1.0 + _s_adj)))
                        logger.debug(
                            f"Strategy retro adj: {strategy_type} {_s_adj:+.0%} → "
                            f"confidence {_before:.2f}→{confidence:.2f} "
                            f"({_s_insight.get('confidence_adj_reason', '')})"
                        )

                # ── Confidence-scaled position sizing ─────────────────────────
                # Scale position size proportionally to signal confidence so that
                # high-conviction signals get full size and weak signals trade small.
                # Formula: conf_mult = clamp(confidence / ref, floor, 1.0)
                _size_ref = max(_effective_min_entry_conf + 0.01, _conf_ref)
                if confidence >= _size_ref:
                    _conf_mult = 1.0
                else:
                    _progress = max(0.0, min(1.0, (confidence - _effective_min_entry_conf) / (_size_ref - _effective_min_entry_conf)))
                    _conf_mult = _effective_conf_floor + ((1.0 - _effective_conf_floor) * (_progress ** 1.35))

                # Size position based on total fund capital (not just available USDT).
                # allocation_pct is a % of the total fund; using only available USDT
                # would produce shrinking positions as capital gets deployed.
                total_fund = self._total_capital or usdt_balance
                target_position_value = total_fund * allocation_pct / 100 * _size_mult * _conf_mult

                # Cap at 95% of available USDT to avoid overdrafts
                position_value = min(target_position_value, usdt_balance * 0.95)
                current_positions = await self._get_current_positions()
                leverage_meta = await self._determine_leverage(
                    side=signal,
                    confidence=confidence,
                    entry_price=current_price,
                    base_position_value=position_value,
                    total_capital=total_fund,
                    current_positions=current_positions,
                )
                leverage = float(leverage_meta.get("leverage", 1.0) or 1.0)
                margin_used = float(leverage_meta.get("margin_used", position_value) or position_value)
                leveraged_notional = float(leverage_meta.get("leveraged_notional", position_value) or position_value)
                liquidation_price = leverage_meta.get("liquidation_price")
                quantity = leveraged_notional / current_price if current_price > 0 else 0.0

                logger.debug(
                    f"Sizing {name} ({strategy_type}, {_size_mult:.0%} strat, {_conf_mult:.0%} conf, {leverage:.1f}x lev): "
                    f"total_fund=${total_fund:.0f}, alloc={allocation_pct:.2f}%, "
                    f"target=${target_position_value:.0f}, available=${usdt_balance:.0f}, margin=${margin_used:.0f}, "
                    f"notional=${leveraged_notional:.0f}, qty={quantity:.4f} @ ${current_price}"
                )

                # ── Sell-to-close existing long: bypasses sizing gates ────────
                # When selling to close an existing long position, we use the
                # position's quantity — no USDT capital required. Skip the
                # minimum notional / underfund checks since they only apply
                # to new entries.
                _closing_existing = False
                if signal == 'sell':
                    positions = list(await trading_service.get_positions(symbol=symbol, agent_id=agent_id))
                    long_pos = next((p for p in positions if p.symbol == symbol and p.side == OrderSide.BUY), None)
                    if long_pos:
                        quantity = long_pos.quantity
                        _closing_existing = True

                # ── Minimum notional gate ─────────────────────────────────────
                # If the available USDT balance is so low that the actual position
                # value falls well below the target, the trade is not worth placing:
                # - The P&L impact is negligible (< $10 notional = noise)
                # - It pollutes strategy performance stats with meaningless results
                # - It means capital is nearly fully deployed — better to wait
                #
                # Hard floor: $10 minimum notional for any new position.
                # Soft floor: reject if actual position < 25% of target
                # (means available balance is <26% of what this agent expects).
                #
                # NOTE: These gates ONLY apply to NEW entries — closing an
                # existing position doesn't need USDT capital.
                _MIN_NOTIONAL = 10.0  # USD
                _UNDERFUND_RATIO = 0.25
                if not _closing_existing and position_value < _MIN_NOTIONAL:
                    _skip_reason = (
                        f"Position value ${position_value:.2f} is below minimum notional "
                        f"${_MIN_NOTIONAL:.0f} — available USDT balance too low "
                        f"(${usdt_balance:.2f} available, ${target_position_value:.0f} targeted)"
                    )
                    logger.info(f"Micro-trade skipped: {name}/{symbol} — {_skip_reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="Below minimum notional")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=f"Available USDT (${usdt_balance:.2f}) is below the minimum notional (${_MIN_NOTIONAL:.0f}) — fund is nearly fully deployed. Waiting for capital to free up.",
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="Below minimum notional",
                    )
                if not _closing_existing and position_value < target_position_value * _UNDERFUND_RATIO:
                    _skip_reason = (
                        f"Position value ${position_value:.2f} is only "
                        f"{position_value/target_position_value:.0%} of target "
                        f"${target_position_value:.0f} — fund is nearly fully deployed"
                    )
                    logger.info(f"Underfunded trade skipped: {name}/{symbol} — {_skip_reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="Fund nearly fully deployed")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=f"Fund nearly fully deployed — only ${usdt_balance:.2f} USDT available vs ${target_position_value:.0f} targeted. Sitting on hands until positions close.",
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="Fund nearly fully deployed",
                    )

                if signal == 'sell' and not _closing_existing:
                    # Open a short position — quantity already calculated above
                    pass
                
                from app.api.routes.settings import get_risk_limits
                _limits = get_risk_limits()
                risk_config = RiskConfig(
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    max_daily_loss=_limits.max_daily_loss_pct,
                    total_capital=total_fund,
                    max_position_size=leveraged_notional if not _closing_existing else quantity * current_price,
                    max_open_positions=_limits.max_open_positions,
                    max_exposure=total_fund * _limits.exposure_threshold_pct / 100,
                    leverage=leverage,
                    max_leveraged_notional_pct=getattr(_limits, 'max_leveraged_notional_pct', 200.0),
                    liquidation_buffer_pct=getattr(_limits, 'liquidation_buffer_pct', 12.5),
                )
                
                risk_check = risk_manager.check_trade(
                    side=signal,
                    quantity=quantity,
                    entry_price=current_price,
                    risk_config=risk_config,
                    current_positions=current_positions,
                )
                
                if not risk_check.allowed:
                    logger.warning(f"Trade rejected by risk manager: {risk_check.reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=risk_check.reason)
                    await team_chat.log_risk_decision(
                        agent_name=name, symbol=symbol, side=signal,
                        allowed=False, reason=risk_check.reason,
                    )
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=risk_check.reason,
                    )
                    if risk_check.action == "stop":
                        asyncio.create_task(_send_telegram(telegram_service.alert_daily_loss_limit(
                            daily_loss_pct=abs(risk_manager.get_daily_pnl()),
                            limit_pct=_limits.max_daily_loss_pct,
                        )))
                    else:
                        asyncio.create_task(_send_telegram(telegram_service.alert_trade_rejected(
                            agent_name=name, symbol=symbol, side=signal,
                            reason=risk_check.reason, rejected_by="Elena (Risk Manager)",
                        )))
                    return AgentRun(
                        agent_id=agent_id,
                        timestamp=datetime.now(),
                        symbol=symbol,
                        signal="hold",
                        confidence=0,
                        price=current_price,
                        executed=False,
                        error=risk_check.reason
                    )

                # ── Technical analysis already fetched above (line ~3440) for TP scaling ───
                # Confluence data is used for dynamic concentration limits (below)
                # and for confluence-aware TP scaling (above).

                # ── Dynamic concentration limit (based on confluence & regime) ────────────
                # Base limit: 40%. Boost up to 65% if multi-TF confluence >= 0.70 (strong alignment).
                # This allows capital to flow to high-conviction setups in trending markets.
                _base_conc_pct = getattr(_limits, 'max_directional_concentration_pct', 40.0)
                _max_conc_pct = _base_conc_pct  # start with base
                _conc_boost_source = None

                if technical_report.multi_timeframe and technical_report.multi_timeframe.confluence_score:
                    _conf = technical_report.multi_timeframe.confluence_score
                    # Boost formula: for every 0.10 above 0.70, add ~5% to the limit (capped at 65%)
                    if _conf >= 0.70:
                        _boost = min((_conf - 0.70) * 50, 25.0)  # max +25% boost
                        _max_conc_pct = min(_base_conc_pct + _boost, 65.0)  # cap at 65%
                        _conc_boost_source = f"MTF confluence {_conf:.0%}"
                    elif _conf <= 0.40:
                        # Weak alignment → tighten limit to 30%
                        _max_conc_pct = 30.0
                        _conc_boost_source = f"weak MTF confluence {_conf:.0%}"

                if _conc_boost_source:
                    logger.info(
                        f"Dynamic concentration limit: {_base_conc_pct:.0f}% base → "
                        f"{_max_conc_pct:.0f}% ({_conc_boost_source})"
                    )

                # ── Correlation / concentration limit ─────────────────────────
                # Prevent piling into the same asset/direction across all agents.
                # Applies to BOTH longs and shorts — previously only longs were checked,
                # allowing multiple agents to freely pile into the same short position.
                # Settings: max 2 open positions same symbol, max dynamic % fund in one direction.
                if signal in ('buy', 'sell'):
                    _all_open = list(await trading_service.get_positions())
                    _is_long_signal = (signal == 'buy')
                    _target_side = OrderSide.BUY if _is_long_signal else OrderSide.SELL
                    _direction_label = "LONG" if _is_long_signal else "SHORT"

                    _same_sym_positions = [
                        p for p in _all_open
                        if p.symbol == symbol and p.side == _target_side
                    ]
                    _max_same = getattr(_limits, 'max_same_asset_positions', 2)
                    if len(_same_sym_positions) >= _max_same:
                        _corr_reason = (
                            f"Correlation limit: already {len(_same_sym_positions)} open {_direction_label} "
                            f"positions on {symbol} (max {_max_same})"
                        )
                        logger.info(f"Concentration gate: {_corr_reason}")
                        self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=_corr_reason)
                        await team_chat.log_trade_blocked(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            reason=_corr_reason,
                        )
                        return AgentRun(
                            agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                            signal="hold", confidence=0, price=current_price,
                            executed=False, error=_corr_reason,
                        )
                    # Total directional concentration check (now with dynamic limit)
                    _dir_value = sum(
                        p.quantity * (p.current_price or p.entry_price or 0)
                        for p in _all_open if p.side == _target_side
                    )
                    _new_dir_pct = (_dir_value + position_value) / total_fund * 100 if total_fund > 0 else 0
                    if _new_dir_pct > _max_conc_pct:
                        _conc_reason = (
                            f"Directional concentration limit: adding this position would put "
                            f"{_new_dir_pct:.0f}% of fund in {_direction_label} (max {_max_conc_pct:.0f}%)"
                        )
                        if _conc_boost_source:
                            _conc_reason += f" [dynamic: {_conc_boost_source}]"
                        logger.info(f"Concentration gate: {_conc_reason}")
                        self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=_conc_reason)
                        await team_chat.log_trade_blocked(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            reason=_conc_reason,
                        )
                        return AgentRun(
                            agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                            signal="hold", confidence=0, price=current_price,
                            executed=False, error=_conc_reason,
                        )
                # ── End correlation limit ─────────────────────────────────────
                # (technical_report already fetched above for dynamic limits)
                # Only veto if TA has OPPOSITE signal (not just different) with high confidence
                ta_signal = technical_report.overall_signal
                # Log Marcus's TA response for this symbol
                await team_chat.log_ta_confluence(
                    symbol=symbol,
                    ta_signal=ta_signal or "hold",
                    ta_confidence=technical_report.confidence or 0.0,
                    patterns=technical_report.patterns or [],
                    support_levels=technical_report.price_levels.support if technical_report.price_levels else [],
                    resistance_levels=technical_report.price_levels.resistance if technical_report.price_levels else [],
                    trade_signal=signal,
                )
                is_opposite = (signal == 'buy' and ta_signal == 'sell') or (signal == 'sell' and ta_signal == 'buy')
                if is_opposite and technical_report.confidence > _ta_veto_conf:
                    logger.warning(f"Trade rejected by technical analyst: signal {signal} conflicts with TA {ta_signal} (conf: {technical_report.confidence})")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=f"Technical analyst disagrees: {ta_signal}")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=f"Marcus flagged opposing TA signal ({ta_signal.upper()}) with {technical_report.confidence:.0%} confidence",
                    )
                    asyncio.create_task(_send_telegram(telegram_service.alert_ta_veto(
                        agent_name=name, symbol=symbol, intended_side=signal,
                        ta_signal=ta_signal, ta_confidence=technical_report.confidence or 0.0,
                    )))
                    return AgentRun(
                        agent_id=agent_id,
                        timestamp=datetime.now(),
                        symbol=symbol,
                        signal="hold",
                        confidence=0,
                        price=current_price,
                        executed=False,
                        error=f"Technical analyst disagrees: {ta_signal}"
                    )

                # Risk manager approves — post confirmation before order placement
                await team_chat.log_risk_decision(
                    agent_name=name, symbol=symbol, side=signal,
                    allowed=True, reason="Position sizing within limits",
                    sl_price=risk_check.stop_loss_price,
                    tp_price=risk_check.take_profit_price,
                )
                
                # Default SL/TP from risk manager (percentage-based)
                adjusted_sl = risk_check.stop_loss_price
                adjusted_tp = risk_check.take_profit_price
                _is_short_signal = signal.lower() == 'sell'

                if technical_report.patterns:
                    best_pattern = max(technical_report.patterns, key=lambda p: p.confidence)
                    if best_pattern.stop_loss and best_pattern.take_profit_1:
                        # SL: take the MORE CONSERVATIVE of TA vs risk manager
                        # For longs SL is below entry → tighter = higher → max()
                        # For shorts SL is above entry → tighter = lower → min()
                        if _is_short_signal:
                            adjusted_sl = min(risk_check.stop_loss_price, best_pattern.stop_loss) if risk_check.stop_loss_price else best_pattern.stop_loss
                        else:
                            adjusted_sl = max(risk_check.stop_loss_price, best_pattern.stop_loss) if risk_check.stop_loss_price else best_pattern.stop_loss
                        # TP: use TA price as a CEILING, not a floor — take the
                        # more conservative of (risk manager TP, TA TP1).
                        # For longs TP is above entry → conservative = lower → min()
                        # For shorts TP is below entry → conservative = higher → max()
                        if risk_check.take_profit_price and best_pattern.take_profit_1:
                            if _is_short_signal:
                                adjusted_tp = max(risk_check.take_profit_price, best_pattern.take_profit_1)
                            else:
                                adjusted_tp = min(risk_check.take_profit_price, best_pattern.take_profit_1)
                        elif best_pattern.take_profit_1:
                            adjusted_tp = best_pattern.take_profit_1
                        tp2 = best_pattern.take_profit_2
                        logger.info(f"TA levels applied (conservative): SL ${best_pattern.stop_loss:.2f}, TP1 ${best_pattern.take_profit_1:.2f} → using ${adjusted_tp:.2f}" + (f", TP2 ${tp2:.2f}" if tp2 else ""))

                # ── Snap TP/SL to structural levels (S/R, Fibonacci, pivots) ──
                # This replaces arbitrary percentage targets with levels where
                # real buying/selling pressure exists on the chart.
                _pl = technical_report.price_levels
                if _pl and (_pl.support or _pl.resistance or _pl.fibonacci_retracements):
                    from app.services.technical_analyst import snap_tp_to_structure, snap_sl_to_structure
                    _old_tp = adjusted_tp
                    _old_sl = adjusted_sl
                    if adjusted_tp:
                        adjusted_tp = snap_tp_to_structure(
                            adjusted_tp, _pl, current_price, is_short=_is_short_signal,
                        )
                    if adjusted_sl:
                        adjusted_sl = snap_sl_to_structure(
                            adjusted_sl, _pl, current_price, is_short=_is_short_signal,
                        )
                    if adjusted_tp != _old_tp or adjusted_sl != _old_sl:
                        logger.info(
                            f"S/R snap: TP ${_old_tp:.4f}→${adjusted_tp:.4f}, "
                            f"SL ${_old_sl:.4f}→${adjusted_sl:.4f} (structural levels)"
                        )
                
                if technical_report.price_levels.support or technical_report.price_levels.resistance:
                    nearest_support = min(technical_report.price_levels.support, key=lambda x: abs(x - current_price)) if technical_report.price_levels.support else None
                    nearest_res = min(technical_report.price_levels.resistance, key=lambda x: abs(x - current_price)) if technical_report.price_levels.resistance else None
                    s_str = fmt_price(nearest_support) if nearest_support is not None else "N/A"
                    r_str = fmt_price(nearest_res) if nearest_res is not None else "N/A"
                    logger.info(f"Key levels - Support: {s_str}, Resistance: {r_str}")
                
                if risk_check.stop_loss_price:
                    tp_str = f"${risk_check.take_profit_price:.2f}" if risk_check.take_profit_price is not None else "N/A"
                    logger.info(f"Risk levels - SL: ${risk_check.stop_loss_price:.2f}, TP: {tp_str}")

                # ── Alex Liu: Pre-execution conflict check ───────────────────────
                # Before any order fires, check whether this trade conflicts with or
                # over-concentrates relative to trades already executed this cycle.
                _alex_cycle = cycle_trades if cycle_trades is not None else self._cycle_trades
                _conflict = execution_coordinator.check_intended_trade(
                    agent_id=agent_id,
                    agent_name=name,
                    symbol=symbol,
                    side=signal,
                    quantity=quantity,
                    cycle_trades=_alex_cycle,
                )
                if _conflict.chat_message:
                    await team_chat.add_message(
                        agent_role="execution_coordinator",
                        content=_conflict.chat_message,
                        message_type="warning" if _conflict.verdict != "approved" else "decision",
                        mentions=[f"@{_trader_name}"],
                    )
                if not _conflict.approved:
                    logger.warning(
                        f"[Alex] Trade BLOCKED for {name}/{symbol} {signal.upper()}: {_conflict.reason}"
                    )
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False,
                                     error=f"Execution conflict: {_conflict.reason}")
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error=f"Execution conflict: {_conflict.reason}",
                    )
                if _conflict.size_multiplier != 1.0:
                    quantity = quantity * _conflict.size_multiplier
                    logger.info(
                        f"[Alex] Position size reduced ×{_conflict.size_multiplier} "
                        f"for {name}/{symbol}: {_conflict.reason}"
                    )
                # ─────────────────────────────────────────────────────────────────

                # ── Pre-trade backtest sanity check ───────────────────────────────
                # Run (or serve cached) backtest for this agent+symbol before placing the order.
                # Rejects trades where the strategy has no historical edge on this symbol.
                # The gate always applies — re-entry after a stop-out is no exception;
                # a losing thesis should not be re-entered just because SL was tight.
                _bt_cache_key = f"{agent_id}:{symbol}"
                _bt_cache_ttl = 0.25  # hours (15 min) — keeps backtest signal fresh on short timeframes
                _cached = self._backtest_cache.get(_bt_cache_key)
                _bt_result = None
                _bt_stale = True

                if _cached:
                    _bt_result, _bt_cached_at = _cached
                    _age_hours = (datetime.now() - _bt_cached_at).total_seconds() / 3600
                    _bt_stale = _age_hours >= _bt_cache_ttl

                if True:  # gate always runs (re-entry exemption removed)
                    if _bt_stale:
                        try:
                            from app.services.backtest import BacktestConfig as _BT_Config
                            _bt_config = _BT_Config(
                                symbol=symbol,
                                interval=timeframe,
                                initial_balance=10000.0,
                                position_size_pct=0.1,
                                stop_loss_pct=stop_loss_pct / 100,
                                take_profit_pct=take_profit_pct / 100,
                                strategy=strategy_type,
                                candle_limit=2000,
                            )
                            _bt_result = await self.backtest_engine.run_backtest(_bt_config)
                            self._backtest_cache[_bt_cache_key] = (_bt_result, datetime.now())
                            logger.info(
                                f"[Backtest] {name}/{symbol}: {_bt_result.total_trades} trades, "
                                f"WR={_bt_result.win_rate:.0%}, net={_bt_result.net_pnl:+.2f}"
                            )
                        except Exception as _bt_err:
                            logger.warning(f"[Backtest] Pre-trade check failed for {name}/{symbol}: {_bt_err} — allowing trade through")
                            _bt_result = None  # Fail open: don't block trade on backtest error

                    if _bt_result is not None:
                        # ── Three-tier backtest assessment ────────────────────────────────
                        # HARD BLOCK  — strategy genuinely untested or catastrophically bad.
                        #               No point risking capital with zero evidence of edge.
                        # SOFT REDUCE — strategy is losing in backtest but not disastrously.
                        #               Cut position size by 50%; let the live signal decide.
                        #               Live market conditions differ from the recent lookback.
                        # PASS        — backtest shows edge; proceed at full sizing.
                        # ─────────────────────────────────────────────────────────────────
                        _bt_hard_block   = False
                        _bt_size_penalty = 1.0   # multiplier applied to position size
                        _bt_warn_msg     = None

                        if _bt_result.total_trades < 5:
                            _bt_hard_block = True
                            _bt_fail_reason = f"only {_bt_result.total_trades} backtest trades (need ≥5 to establish edge)"
                        elif _bt_result.win_rate < 0.35:
                            # At 2:1 R/R, break-even requires ≥33% WR. Anything below 35% has no edge.
                            _bt_hard_block = True
                            _bt_fail_reason = f"backtest win rate {_bt_result.win_rate:.0%} is below the 35% break-even threshold for this strategy's R/R ratio"
                        elif _bt_result.net_pnl <= 0:
                            # Positive WR but still net negative — size penalty
                            _bt_size_penalty = 0.50
                            _bt_warn_msg = (
                                f"Backtest shows limited edge on {symbol} "
                                f"(WR={_bt_result.win_rate:.0%}, net={_bt_result.net_pnl:+.2f}) "
                                f"— reducing position size by 50% and proceeding cautiously 📉"
                            )

                        if _bt_hard_block:
                            logger.warning(f"Trade HARD BLOCKED by backtest: {name}/{symbol} — {_bt_fail_reason}")
                            self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=f"Backtest: {_bt_fail_reason}")
                            await team_chat.log_trade_blocked(
                                trader_name=_trader_name, trader_avatar=_trader_avatar,
                                agent_name=name, symbol=symbol, side=signal,
                                reason=f"Strategy has no historical edge on {symbol}: {_bt_fail_reason}",
                            )
                            return AgentRun(
                                agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                                signal="hold", confidence=0, price=current_price,
                                executed=False, error=f"Pre-trade backtest hard block: {_bt_fail_reason}",
                            )
                        elif _bt_warn_msg:
                            logger.warning(f"Trade soft-reduced by backtest: {name}/{symbol} — position ×50%")
                            # Halve the already-computed position value by adjusting quantity below
                            quantity = quantity * _bt_size_penalty
                            await team_chat.add_message(
                                agent_role="risk_manager",
                                content=_bt_warn_msg,
                                message_type="warning",
                                mentions=[f"@{_trader_name}"],
                            )
                        else:
                            logger.info(f"Backtest PASS: {name}/{symbol} WR={_bt_result.win_rate:.0%} net={_bt_result.net_pnl:+.2f}")
                # ─────────────────────────────────────────────────────────────────

                # ── Hourly trade frequency gate ───────────────────────────────────
                # Soft fund-level cap: > max_trades_per_hour requires ≥0.85 confidence
                # to override. Prevents the budget from being consumed by a burst of
                # medium-quality entries in the first hour of the active session.
                _hour_bucket = datetime.now().strftime("%Y-%m-%dT%H")
                if _hour_bucket != self._trades_hour_bucket:
                    self._trades_this_hour = 0
                    self._trades_hour_bucket = _hour_bucket
                try:
                    from app.api.routes.settings import get_trading_gates as _hr_gates_fn
                    _max_per_hour = int(getattr(_hr_gates_fn(), 'max_trades_per_hour', 4) or 4)
                except Exception:
                    _max_per_hour = 4

                if self._trades_this_hour >= _max_per_hour and confidence < 0.85:
                    _hr_reason = (
                        f"Hourly trade limit reached: {self._trades_this_hour}/{_max_per_hour} "
                        f"trades this hour. Confidence {confidence:.0%} < 85% required to override. "
                        f"Preserving fee budget for higher-conviction setups."
                    )
                    logger.info(f"Hourly limit gate blocked {name} {signal.upper()} {symbol}: {_hr_reason}")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False,
                                     error="Hourly trade limit")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal, reason=_hr_reason,
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="Hourly trade limit",
                    )
                # ─────────────────────────────────────────────────────────────────

                if use_paper and paper_trading._enabled:
                    try:
                        # Look up trader_id from agent config
                        _agent_cfg = self._enabled_agents.get(agent_id, {})
                        _trader_id = _agent_cfg.get("trader_id")
                        order = await paper_trading.place_order(
                            symbol=symbol,
                            side=signal,
                            quantity=quantity,
                            price=current_price,
                            agent_id=agent_id,
                            trader_id=_trader_id,
                            stop_loss_price=adjusted_sl,
                            take_profit_price=adjusted_tp,
                            trailing_stop_pct=trailing_stop_pct,
                            leverage=leverage,
                            margin_used=margin_used,
                            liquidation_price=liquidation_price,
                        )
                        executed = True
                        self._trades_this_hour += 1  # hourly frequency gate counter
                        # Bust the shared TA cache so no other agent fires on the same
                        # stale signal that just triggered this trade.  Forces a fresh
                        # analysis call before any subsequent entry on this pair/timeframe.
                        self._ta_cache.pop(f"{symbol}:{timeframe}", None)

                        # ── Scale-out levels ──────────────────────────────────────────
                        # Scale-out profiles are strategy-specific.
                        #
                        # Directional strategies (momentum / trend_following / breakout / ai):
                        #   NO early scale-outs — let the full move play out.
                        #   The breakeven + profit-lock SL progression protects capital
                        #   once 30%/50% of TP is reached.  Cutting early was capping
                        #   winners at 0.5–1.8% while SL losses remained at full size,
                        #   destroying effective R/R in practice.
                        #
                        # Mean-reversion + scalping:
                        #   Price typically snaps back then reverses — take profit early.
                        #   Two tranches reduce exposure before the inevitable reversal.
                        #
                        # Grid: symmetric partial exits on both sides of the range.
                        _SCALE_PROFILES: Dict[str, List] = {
                            # strategy: [(pct_of_tp, close_pct), ...]
                            # Directional — no early exits; ride to full TP
                            "momentum":        [],
                            "trend_following": [],
                            "breakout":        [],
                            "ai":              [],
                            # Reverting / fast — take partial profit, remainder trails
                            "mean_reversion":  [(0.50, 0.40), (0.80, 0.35)],
                            "scalping":        [(0.50, 0.60)],  # fast: 60% off at half-way, trail rest
                            # Grid: symmetric partial exits
                            "grid":            [(0.50, 0.40), (0.80, 0.35)],
                        }
                        _scale_pairs = _SCALE_PROFILES.get(strategy_type, [])
                        _scale_levels = [
                            {"pct_of_tp": pct, "close_pct": cpct, "triggered": False}
                            for pct, cpct in _scale_pairs
                        ]

                        # Write scale-out levels to position in DB
                        try:
                            from app.database import AsyncSessionLocal
                            from app.models import Position as _Pos
                            async with AsyncSessionLocal() as _db:
                                _pos = await _db.scalar(
                                    select(_Pos).where(
                                        _Pos.agent_id == agent_id,
                                        _Pos.symbol == symbol,
                                    )
                                )
                                if _pos:
                                    _pos.scale_out_levels = json.dumps(_scale_levels)
                                    await _db.commit()
                        except Exception as _sol_err:
                            logger.debug(f"Scale-out level write failed: {_sol_err}")

                        sl_str = f"${adjusted_sl:.2f}" if adjusted_sl else "N/A"
                        tp_str = f"${adjusted_tp:.2f}" if adjusted_tp else "N/A"
                        logger.info(f"Paper trade executed: {signal} {quantity} {symbol} @ {current_price} | SL: {sl_str} TP: {tp_str}")
                        # Register with Alex's cycle buffer so later agents see this trade
                        execution_coordinator.record_cycle_trade(
                            agent_id=agent_id, agent_name=name, symbol=symbol,
                            side=signal, quantity=quantity,
                            cycle_trades=_alex_cycle,
                        )
                        await team_chat.log_trade_executed(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            quantity=quantity, price=current_price,
                            sl_price=adjusted_sl, tp_price=adjusted_tp,
                        )
                        asyncio.create_task(_send_telegram(telegram_service.alert_trade_executed(
                            trader_name=_trader_name, agent_name=name,
                            symbol=symbol, side=signal, quantity=quantity, price=current_price,
                            sl_price=adjusted_sl, tp_price=adjusted_tp, is_paper=True,
                        )))
                    except Exception as e:
                        logger.error(f"Paper trade failed: {e}", exc_info=True)
                        await team_chat.log_trade_blocked(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            reason=f"Paper trade execution error: {type(e).__name__}: {e}",
                        )
                elif not use_paper:
                    try:
                        from app.services.live_trading import live_trading as _live_svc
                        _agent_cfg_live = self._enabled_agents.get(agent_id, {})
                        _trader_id_live = _agent_cfg_live.get("trader_id")
                        order = await _live_svc.place_order(
                            symbol=symbol,
                            side=signal,
                            quantity=quantity,
                            price=current_price,
                            agent_id=agent_id,
                            trader_id=_trader_id_live,
                            stop_loss_price=adjusted_sl,
                            take_profit_price=adjusted_tp,
                            trailing_stop_pct=trailing_stop_pct,
                            leverage=leverage,
                            margin_used=margin_used,
                            liquidation_price=liquidation_price,
                        )
                        if order is None:
                            raise RuntimeError("live_trading.place_order returned None — check balance/price")
                        executed = True
                        self._trades_this_hour += 1  # hourly frequency gate counter
                        # Bust the shared TA cache (same reason as paper path above).
                        self._ta_cache.pop(f"{symbol}:{timeframe}", None)

                        # ── Record traded symbol in agent history ──────────────────────
                        try:
                            from app.database import AsyncSessionLocal
                            from app.models import Agent as _AgentHistModelLive
                            async with AsyncSessionLocal() as _hist_db_live:
                                _hist_ag_live = await _hist_db_live.get(_AgentHistModelLive, agent_id)
                                if _hist_ag_live:
                                    _hist_cfg_live = dict(_hist_ag_live.config) if isinstance(_hist_ag_live.config, dict) else {}
                                    _hist_pairs_live = _hist_cfg_live.get("trading_pairs", [])
                                    if symbol not in _hist_pairs_live:
                                        _hist_pairs_live.append(symbol)
                                        _hist_cfg_live["trading_pairs"] = _hist_pairs_live
                                        _hist_ag_live.config = _hist_cfg_live
                                        await _hist_db_live.commit()
                        except Exception as _hist_err_live:
                            logger.debug(f"Could not update traded pairs for {agent_id}: {_hist_err_live}")

                        is_short_entry = signal.lower() == 'sell'
                        direction = "SHORT" if is_short_entry else "LONG"
                        sl_str = f"${adjusted_sl:.2f}" if adjusted_sl else "N/A"
                        tp_str = f"${adjusted_tp:.2f}" if adjusted_tp else "N/A"
                        logger.info(f"LIVE {direction} trade executed: {signal} {quantity} {symbol} @ {current_price} | SL: {sl_str} TP: {tp_str}")

                        # Attach scale-out levels to live position (same schedule as paper)
                        _live_scale_pairs = _SCALE_PROFILES.get(strategy_type, [])
                        _live_scale_levels = [
                            {"pct_of_tp": pct, "close_pct": cpct, "triggered": False}
                            for pct, cpct in _live_scale_pairs
                        ]
                        try:
                            from app.database import AsyncSessionLocal
                            from app.models import Position as _PosLive
                            async with AsyncSessionLocal() as _db:
                                _live_pos = await _db.scalar(
                                    select(_PosLive).where(
                                        _PosLive.agent_id == agent_id,
                                        _PosLive.symbol == symbol,
                                        _PosLive.is_paper == False,  # noqa: E712
                                    )
                                )
                                if _live_pos:
                                    _live_pos.scale_out_levels = json.dumps(_live_scale_levels)
                                    await _db.commit()
                        except Exception as _sol_err:
                            logger.debug(f"Live scale-out level write failed: {_sol_err}")

                        # Register with Alex's cycle buffer
                        execution_coordinator.record_cycle_trade(
                            agent_id=agent_id, agent_name=name, symbol=symbol,
                            side=signal, quantity=quantity,
                            cycle_trades=_alex_cycle,
                        )
                        await team_chat.log_trade_executed(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            quantity=quantity, price=current_price,
                            sl_price=adjusted_sl, tp_price=adjusted_tp,
                        )
                        asyncio.create_task(_send_telegram(telegram_service.alert_trade_executed(
                            trader_name=_trader_name, agent_name=name,
                            symbol=symbol, side=signal, quantity=quantity, price=current_price,
                            sl_price=adjusted_sl, tp_price=adjusted_tp, is_paper=False,
                        )))
                    except Exception as e:
                        logger.error(f"LIVE trade failed: {e}", exc_info=True)
                        await team_chat.log_trade_blocked(
                            trader_name=_trader_name, trader_avatar=_trader_avatar,
                            agent_name=name, symbol=symbol, side=signal,
                            reason=f"Live trade execution error: {type(e).__name__}: {e}",
                        )
                else:
                    # Neither paper nor live mode is active/configured — intent was posted
                    # but no execution path is available. Post an explanation to team chat.
                    _no_exec_reason = (
                        "Paper trading is disabled and no live API credentials are configured. "
                        "Enable paper trading or add Phemex API keys in Settings."
                        if not use_paper
                        else "Paper trading engine is currently disabled."
                    )
                    logger.warning(f"Trade intent not executed — no active execution mode: {_no_exec_reason}")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=_no_exec_reason,
                    )
            
            self._record_run(agent_id, symbol, signal, confidence, current_price, executed, pnl, use_paper=use_paper)
            
            return AgentRun(
                agent_id=agent_id,
                timestamp=timestamp,
                symbol=symbol,
                signal=signal,
                confidence=confidence,
                price=current_price,
                executed=executed,
                pnl=pnl
            )
            
        except Exception as e:
            logger.error(f"Agent run failed: {e}", exc_info=True)
            self._record_run(agent_id, symbol, "hold", 0, 0, False, error=str(e))
            # Post to team chat so the intent isn't left dangling without resolution
            try:
                _agent_cfg_err = self._enabled_agents.get(agent_id, {})
                _trader_name_err = "Portfolio Manager"
                _trader_avatar_err = "💼"
                _trader_id_err = _agent_cfg_err.get("trader_id")
                if _trader_id_err and self._traders:
                    _t_err = next((t for t in self._traders if t.get("id") == _trader_id_err), None)
                    if _t_err:
                        _trader_name_err = _t_err.get("name", _trader_name_err)
                        _cfg_err = _t_err.get("config") or {}
                        _trader_avatar_err = _cfg_err.get("avatar", _trader_avatar_err) if isinstance(_cfg_err, dict) else _trader_avatar_err
                await team_chat.log_trade_blocked(
                    trader_name=_trader_name_err,
                    trader_avatar=_trader_avatar_err,
                    agent_name=self._enabled_agents.get(agent_id, {}).get("name", agent_id),
                    symbol=symbol or "?",
                    side="unknown",
                    reason=f"Internal error during trade execution: {type(e).__name__}: {e}",
                )
            except Exception:
                pass  # Never let error reporting crash the scheduler
            
            return AgentRun(
                agent_id=agent_id,
                timestamp=timestamp,
                symbol=symbol,
                signal="hold",
                confidence=0,
                price=0,
                executed=False,
                error=str(e)
            )
    
    def _record_run(
        self,
        agent_id: str,
        symbol: str,
        signal: str,
        confidence: float,
        price: float,
        executed: bool,
        pnl: Optional[float] = None,
        error: Optional[str] = None,
        strategy_type: Optional[str] = None,
        use_paper: bool = True,
    ):
        run = AgentRun(
            agent_id=agent_id,
            timestamp=datetime.now(),
            symbol=symbol,
            signal=signal,
            confidence=confidence,
            price=price,
            executed=executed,
            pnl=pnl,
            error=error,
        )
        self._agent_runs.append(run)
        if len(self._agent_runs) > 1000:
            self._agent_runs = self._agent_runs[-500:]

        if agent_id not in self._agent_metrics:
            self._agent_metrics[agent_id] = AgentMetrics(agent_id=agent_id)
        metrics = self._agent_metrics[agent_id]
        metrics.total_runs += 1
        metrics.last_run = datetime.now()
        if error:
            metrics.failed_runs += 1
        else:
            metrics.successful_runs += 1
        if signal == 'buy':
            metrics.buy_signals += 1
        elif signal == 'sell':
            metrics.sell_signals += 1
        else:
            metrics.hold_signals += 1
        if pnl is not None:
            metrics.actual_trades += 1
            if pnl > 0:
                metrics.winning_trades += 1
            metrics.total_pnl += pnl
            metrics.avg_pnl = metrics.total_pnl / metrics.actual_trades if metrics.actual_trades > 0 else 0
        # Win rate = winning closed trades / all closed trades
        # Uses persistent counters — not the prunable _agent_runs buffer
        if metrics.actual_trades > 0:
            metrics.win_rate = metrics.winning_trades / metrics.actual_trades

        asyncio.create_task(self._persist_run(run, metrics, strategy_type, use_paper))

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
                stmt = pg_insert(AgentMetricRecord).values(
                    agent_id=metrics.agent_id,
                    is_paper=_is_paper_mode(),
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
        except Exception as e:
            logger.error(f"Failed to persist agent run: {e}")

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
            logger.error(f"Failed to fetch all metrics from DB: {e}")
        return list(self._agent_metrics.values())

    async def get_recent_runs(self, agent_id: Optional[str] = None, limit: int = 50) -> List[AgentRun]:
        from app.models import AgentRunRecord
        try:
            async with get_async_session() as db:
                query = (
                    select(AgentRunRecord)
                    .order_by(desc(AgentRunRecord.timestamp))
                    .limit(limit)
                )
                if agent_id:
                    query = query.where(AgentRunRecord.agent_id == agent_id)
                rows = (await db.execute(query)).scalars().all()
                return [
                    AgentRun(
                        agent_id=r.agent_id,
                        timestamp=r.timestamp,
                        symbol=r.symbol,
                        signal=r.signal,
                        confidence=r.confidence,
                        price=r.price,
                        executed=r.executed,
                        pnl=r.pnl,
                        error=r.error,
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Failed to fetch recent runs from DB: {e}")
        runs = self._agent_runs
        if agent_id:
            runs = [r for r in runs if r.agent_id == agent_id]
        return runs[-limit:]

    def _map_cio_recommendations(
        self,
        recommendations: List,
        agents_list: List[Dict],
    ) -> List:
        """Convert CIO StrategicRecommendation objects into StrategyActionProposal
        objects that _execute_strategy_actions can consume.

        When traders are present, allocation changes target traders (not agents directly).
        Structural changes (enable/disable/create) still target agents.
        """
        from app.services.strategy_review import StrategyActionProposal

        agents_by_name = {a.get("name", "").lower(): a for a in agents_list}
        agents_by_id = {a["id"]: a for a in agents_list}

        # Build a trader lookup by name for allocation redirects
        traders_by_name = {t.get("name", "").lower(): t for t in (self._traders or [])}
        traders_by_id = {t["id"]: t for t in (self._traders or [])}
        has_traders = bool(self._traders)

        mapped = []

        ACTION_MAP = {
            "enable_agent": "enable_agent",
            "disable_agent": "disable_agent",
            "pause_strategy": "disable_agent",
            "increase_allocation": "adjust_params",
            "reduce_allocation": "adjust_params",
            "reduce_risk": "adjust_params",
            "add_new_strategy": "create_agent",
            "diversify": "create_agent",
        }

        for rec in recommendations:
            action_type = ACTION_MAP.get(rec.recommendation)
            if not action_type:
                continue
            # Only act on high-confidence CIO recommendations
            try:
                from app.api.routes.settings import get_trading_gates as _get_gates_cio
                _cio_min_conf = _get_gates_cio().min_entry_confidence
            except Exception:
                _cio_min_conf = 0.6
            if rec.confidence < _cio_min_conf:
                continue

            target_id = None
            target_name = rec.target
            # Resolve target to agent id — try exact match first, then by strategy_type
            if rec.target and rec.target != "portfolio":
                agent = agents_by_id.get(rec.target) or agents_by_name.get(rec.target.lower())
                if not agent:
                    # Target may be a strategy type (e.g. "ai", "momentum") — pick the
                    # best-performing *enabled* agent of that strategy type
                    matching = [
                        a for a in agents_list
                        if a.get("strategy_type", "").lower() == rec.target.lower()
                        and a.get("is_enabled", False)  # only consider enabled agents
                    ]
                    if matching:
                        agent = max(matching, key=lambda a: a.get("allocation_percentage", 0))
                if agent:
                    target_id = agent["id"]
                    target_name = agent.get("name", rec.target)

            params: dict = {}
            if rec.recommendation == "increase_allocation":
                # When traders are present CIO adjusts trader allocations, not agent allocations.
                if has_traders:
                    # Try to resolve a trader from the target; fall back to the trader
                    # who owns the target agent.
                    trader = (
                        traders_by_name.get((rec.target or "").lower())
                        or traders_by_id.get(rec.target)
                    )
                    if not trader and target_id:
                        target_agent = agents_by_id.get(target_id, {})
                        trader = traders_by_id.get(target_agent.get("trader_id", ""))
                    if trader:
                        params["trader_id"] = trader["id"]
                        params["trader_allocation_change_pct"] = 5
                        target_id = trader["id"]
                        target_name = trader.get("name", rec.target)
                    else:
                        logger.info(
                            f"CIO: increase_allocation skipped — traders present but "
                            f"no trader resolved for target '{rec.target}'"
                        )
                        continue
                else:
                    # Don't increase allocation to a disabled agent — it can't trade anyway.
                    resolved = agents_by_id.get(target_id) if target_id else None
                    if resolved and not resolved.get("is_enabled", True):
                        logger.info(
                            f"CIO: Skipping increase_allocation for '{target_name}' — agent is disabled. "
                            f"Use enable_agent first."
                        )
                        continue
                    params["allocation_change_pct"] = 5
            elif rec.recommendation in ("reduce_allocation", "reduce_risk"):
                if has_traders:
                    trader = (
                        traders_by_name.get((rec.target or "").lower())
                        or traders_by_id.get(rec.target)
                    )
                    if not trader and target_id:
                        target_agent = agents_by_id.get(target_id, {})
                        trader = traders_by_id.get(target_agent.get("trader_id", ""))
                    if trader:
                        params["trader_id"] = trader["id"]
                        params["trader_allocation_change_pct"] = -5
                        target_id = trader["id"]
                        target_name = trader.get("name", rec.target)
                    else:
                        params["allocation_change_pct"] = -5  # fallback to agent-level
                else:
                    params["allocation_change_pct"] = -5
            elif rec.recommendation in ("add_new_strategy", "diversify"):
                # All supported strategy types for diversification
                ALL_STRATEGIES = ["momentum", "mean_reversion", "breakout", "scalping", "trend_following", "grid"]
                existing = {a.get("strategy_type") for a in agents_list}

                # Try to extract a specific strategy from the rationale
                found_stype = None
                for stype in ALL_STRATEGIES:
                    if stype.replace("_", " ") in rec.rationale.lower() or stype in rec.rationale.lower():
                        found_stype = stype
                        break

                if not found_stype:
                    # No specific strategy named — auto-pick the best missing
                    # strategy for diversification
                    missing = [s for s in ALL_STRATEGIES if s not in existing]
                    if missing:
                        found_stype = missing[0]
                        logger.info(f"CIO: Diversification — auto-selected '{found_stype}' "
                                    f"(missing from {len(existing)} existing strategies)")
                    else:
                        logger.info(f"CIO: Skipping add_new_strategy — all strategy types already covered")
                        continue

                if found_stype in existing:
                    logger.info(f"CIO: Skipping add_new_strategy for '{found_stype}' — already exists")
                    continue
                params["strategy_type"] = found_stype

            proposal = StrategyActionProposal(
                action=action_type,
                target_agent_id=target_id,
                target_agent_name=target_name,
                strategy_type=params.get("strategy_type"),
                params=params,
                rationale=f"[CIO] {rec.rationale}",
                initiated_by="cio",
                confluence_score=rec.confidence,
            )
            mapped.append(proposal)

        return mapped

    async def _apply_retrospective_adjustments(
        self,
        adjustments: List[Dict],
        agents_list: List[Dict],
    ):
        """Apply parameter adjustments recommended by the trade retrospective.

        After writing new SL/TP to the DB we also:
        1. Update the in-memory _enabled_agents config so the *next* trade dispatch
           uses the new values without waiting for a restart.
        2. Invalidate the backtest cache for that agent so the pre-trade check
           re-runs with the retro-learned SL/TP rather than stale bootstrap values.
        """
        from app.models import Agent as DBAgent

        for adj in adjustments:
            agent_id = adj.get("agent_id")
            if not agent_id:
                continue
            try:
                new_sl: Optional[float] = None
                new_tp: Optional[float] = None
                _old_sl: Optional[float] = None
                _old_tp: Optional[float] = None

                async with get_async_session() as db:
                    agent = await db.get(DBAgent, agent_id)
                    if not agent:
                        continue
                    changed = []
                    if "stop_loss_pct" in adj and adj["stop_loss_pct"]:
                        new_sl = max(0.5, min(10.0, adj["stop_loss_pct"]))
                        _old_sl = (agent.config or {}).get("stop_loss_pct")
                        agent.config = {**(agent.config or {}), "stop_loss_pct": new_sl}
                        changed.append(f"SL→{new_sl:.1f}%")
                    if "take_profit_pct" in adj and adj["take_profit_pct"]:
                        new_tp = max(1.0, min(20.0, adj["take_profit_pct"]))
                        # Enforce 2:1 R/R minimum on retro-recommended TP too
                        if new_sl and new_tp < new_sl * 2.0:
                            new_tp = round(new_sl * 2.0, 1)
                        _old_tp = (agent.config or {}).get("take_profit_pct")
                        agent.config = {**(agent.config or {}), "take_profit_pct": new_tp}
                        changed.append(f"TP→{new_tp:.1f}%")
                    if changed:
                        await db.commit()
                        logger.info(f"Retrospective: adjusted {agent.name} — {', '.join(changed)}")
                        from app.services.team_chat import team_chat
                        await team_chat.add_message(
                            agent_role="trade_analyst",
                            content=f"🔧 **Parameter Adjustment** {agent.name}: {', '.join(changed)} — {adj.get('reason', '')}",
                            message_type="decision",
                        )

                # ── Point 4: feed new SL/TP back into live config + backtest cache ──
                if new_sl is not None or new_tp is not None:
                    # 4a. Update in-memory agent config so next dispatch uses new values
                    if agent_id in self._enabled_agents:
                        live_cfg = self._enabled_agents[agent_id]
                        if new_sl is not None:
                            live_cfg["stop_loss_pct"] = new_sl
                        if new_tp is not None:
                            live_cfg["take_profit_pct"] = new_tp

                    # 4b. Only evict the backtest cache when the SL/TP change is
                    # meaningful (>1%).  Minor tweaks don't materially change the
                    # backtest result, and evicting after every stopped-out trade
                    # would force a fresh backtest that reflects choppy recent
                    # conditions and could incorrectly block valid re-entries.
                    _sl_delta = abs((new_sl or 0) - (_old_sl or new_sl or 0))
                    _tp_delta = abs((new_tp or 0) - (_old_tp or new_tp or 0))
                    _significant_change = _sl_delta > 1.0 or _tp_delta > 1.0

                    if _significant_change:
                        pairs = []
                        if agent_id in self._enabled_agents:
                            pairs = self._enabled_agents[agent_id].get("trading_pairs", [])
                        else:
                            for a in agents_list:
                                if a.get("id") == agent_id:
                                    pairs = a.get("trading_pairs", [])
                                    break
                        for sym in pairs:
                            cache_key = f"{agent_id}:{sym}"
                            if cache_key in self._backtest_cache:
                                del self._backtest_cache[cache_key]
                                logger.debug(
                                    f"Retrospective: evicted backtest cache {cache_key} "
                                    f"(SL Δ{_sl_delta:.1f}%, TP Δ{_tp_delta:.1f}%)"
                                )

            except Exception as e:
                logger.warning(f"Retrospective adjustment failed for {agent_id}: {e}")

    async def _execute_strategy_actions(
        self,
        actions: List,
        agents_list: List[Dict],
    ):
        """Auto-execute strategy actions proposed by the FM + TA review."""
        from app.models import Agent as DBAgent, StrategyAction as StrategyActionRecord

        agents_by_id = {a['id']: a for a in agents_list}

        for action in actions:
            try:
                result_msg = ""

                if action.action == "disable_agent" and action.target_agent_id:
                    # Guard: require minimum runs before allowing a disable.
                    # Low-sample win rates are unreliable — don't kill agents after 3 trades.
                    _metrics = self._agent_metrics.get(action.target_agent_id)
                    try:
                        from app.api.routes.settings import get_trading_gates as _get_gates_dis
                        _min_runs = _get_gates_dis().min_runs_before_disable
                    except Exception:
                        _min_runs = 15
                    if _metrics and _metrics.total_runs < _min_runs:
                        logger.info(
                            f"Strategy action: skipping disable of {action.target_agent_name} "
                            f"— only {_metrics.total_runs} runs (min {_min_runs} required)"
                        )
                        result_msg = f"Disable skipped — insufficient data ({getattr(_metrics,'total_runs',0)} runs)"
                    else:
                        # Disable in DB + unregister from scheduler
                        async with get_async_session() as db:
                            agent = await db.get(DBAgent, action.target_agent_id)
                            if agent and agent.is_enabled:
                                agent.is_enabled = False
                                await db.commit()
                                self.unregister_agent(action.target_agent_id)
                                result_msg = f"Disabled agent {action.target_agent_name}"
                                logger.info(f"Strategy action: {result_msg}")
                            else:
                                result_msg = "Agent already disabled or not found"

                elif action.action == "enable_agent" and action.target_agent_id:
                    async with get_async_session() as db:
                        agent = await db.get(DBAgent, action.target_agent_id)
                        if agent and not agent.is_enabled:
                            agent.is_enabled = True
                            await db.commit()
                            agent_config = agents_by_id.get(action.target_agent_id)
                            if agent_config:
                                agent_config['is_enabled'] = True
                                self.register_agent(agent_config)
                                # Run bootstrap so the re-enabled agent passes the pre-trade
                                # backtest gate on its first run rather than firing blind.
                                await self._bootstrap_from_backtest(agent_config)
                                # Impose a one-cycle cooldown so the agent doesn't trade in
                                # the same scheduler pass it was just enabled in.
                                agent_config['_last_run'] = datetime.now()
                            result_msg = f"Enabled agent {action.target_agent_name}"
                            logger.info(f"Strategy action: {result_msg}")
                        else:
                            result_msg = "Agent already enabled or not found"

                elif action.action == "create_agent":
                    # Create a new agent in the DB — with duplicate guard and agent cap
                    async with get_async_session() as db:
                        # Check agent cap: max 4 enabled agents per trader
                        _trader_id_for_cap = action.trader_id if hasattr(action, 'trader_id') else None
                        if _trader_id_for_cap:
                            _trader_agent_count = (await db.execute(
                                select(DBAgent).where(
                                    DBAgent.trader_id == _trader_id_for_cap,
                                    DBAgent.is_enabled == True,
                                )
                            )).scalars().all()
                            if len(_trader_agent_count) >= 4:
                                result_msg = f"Skipped — trader already has {len(_trader_agent_count)} active agents (cap: 4)"
                                logger.info(f"Agent cap: {result_msg}")
                                await db.close()
                                # continue to next action
                                results.append({"action": action.action, "result": result_msg})
                                continue

                        # Check for existing agent with same strategy type
                        strategy = action.strategy_type or "momentum"
                        existing = (await db.execute(
                            select(DBAgent).where(DBAgent.strategy_type == strategy)
                        )).scalars().all()
                        if existing:
                            names = ", ".join(a.name for a in existing)
                            result_msg = f"Skipped — agent(s) with strategy '{strategy}' already exist: {names}"
                            logger.info(f"Strategy action: {result_msg}")
                        else:
                            from app.models import User
                            from app.api.routes.settings import get_risk_limits, get_trading_prefs
                            user = await db.scalar(select(User).limit(1))
                            if not user:
                                result_msg = "No user found to own new agent"
                            else:
                                # Strategy-specific configuration
                                risk = get_risk_limits()
                                prefs = get_trading_prefs()
                                trading_pairs = prefs.trading_pairs or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"]

                                import app.strategies as strategy_registry
                                STRATEGY_PROFILES = strategy_registry.strategy_profiles({
                                    "default_stop_loss_pct": risk.default_stop_loss_pct,
                                    "default_take_profit_pct": risk.default_take_profit_pct,
                                })
                                # Apply per-strategy multipliers that respect the user's risk settings
                                for _k, _p in STRATEGY_PROFILES.items():
                                    if _k == "momentum":
                                        pass  # uses defaults as-is
                                    elif _k == "mean_reversion":
                                        _p["stop_loss_pct"] = max(risk.default_stop_loss_pct, 2.5)
                                        _p["take_profit_pct"] = risk.default_take_profit_pct * 0.75
                                    elif _k == "breakout":
                                        _p["stop_loss_pct"] = risk.default_stop_loss_pct * 1.2
                                        _p["take_profit_pct"] = risk.default_take_profit_pct * 1.5
                                    elif _k == "scalping":
                                        _p["stop_loss_pct"] = max(risk.default_stop_loss_pct * 0.5, 0.5)
                                        _p["take_profit_pct"] = max(risk.default_take_profit_pct * 0.5, 1.0)
                                    elif _k == "trend_following":
                                        _p["stop_loss_pct"] = risk.default_stop_loss_pct * 1.5
                                        _p["take_profit_pct"] = risk.default_take_profit_pct * 2.0
                                    elif _k == "ema_crossover":
                                        _p["take_profit_pct"] = risk.default_take_profit_pct * 1.1


                                profile = STRATEGY_PROFILES.get(strategy, STRATEGY_PROFILES["momentum"])
                                agent_name = action.target_agent_name or profile["name"]
                                # Avoid generic names like "portfolio"
                                if agent_name.lower() in ("portfolio", "none", "n/a", ""):
                                    agent_name = profile["name"]

                                # Use the symbol specifically proposed by strategy_review (action.params["symbol"]).
                                # Fall back to the global prefs list only when no symbol was proposed.
                                proposed_symbol = (action.params or {}).get("symbol")
                                if proposed_symbol:
                                    trading_pairs = [proposed_symbol]
                                    # Canonicalise name: "BTC_Momentum Rider" etc.
                                    sym_prefix = proposed_symbol.replace("USDT", "").replace("USD", "")
                                    agent_name = f"{sym_prefix}_{profile['name']}"

                                new_agent = DBAgent(
                                    user_id=user.id,
                                    name=agent_name,
                                    strategy_type=strategy,
                                    config={
                                        "trading_pairs": trading_pairs,
                                        "auto_created": True,
                                        "created_by": action.initiated_by or "strategy_review",
                                        "stop_loss_pct": profile["stop_loss_pct"],
                                        "take_profit_pct": profile["take_profit_pct"],
                                        "trailing_stop_pct": profile["trailing_stop_pct"],
                                        "indicators_config": profile["indicators_config"],
                                        "description": profile["description"],
                                    },
                                    is_enabled=True,
                                    allocation_percentage=10.0,
                                    max_position_size=risk.max_position_size_pct / 100 * self._total_capital,
                                )
                                db.add(new_agent)
                                await db.commit()
                                await db.refresh(new_agent)

                                # Register for scheduling
                                agent_config = {
                                    'id': new_agent.id,
                                    'name': new_agent.name,
                                    'strategy_type': new_agent.strategy_type,
                                    'trading_pairs': trading_pairs,
                                    'is_enabled': True,
                                    'allocation_percentage': 10.0,
                                    'max_position_size': new_agent.max_position_size,
                                    'risk_limit': 100.0,
                                    'stop_loss_pct': profile["stop_loss_pct"],
                                    'take_profit_pct': profile["take_profit_pct"],
                                    'trailing_stop_pct': profile["trailing_stop_pct"],
                                    'trader_id': new_agent.trader_id,
                                }
                                self.register_agent(agent_config)
                                await self._bootstrap_from_backtest(agent_config)
                                result_msg = (
                                    f"Created agent '{agent_name}' ({strategy}) "
                                    f"with {len(trading_pairs)} pairs, "
                                    f"SL={profile['stop_loss_pct']}% TP={profile['take_profit_pct']}%"
                                )
                                logger.info(f"Strategy action: {result_msg}")

                elif action.action == "adjust_params" and action.target_agent_id:
                    # Check if this is a trader-level allocation change (when traders are present)
                    trader_change = action.params.get("trader_allocation_change_pct", 0)
                    agent_change = action.params.get("allocation_change_pct", 0)

                    if trader_change != 0:
                        # Adjust trader's fund-level allocation
                        async with get_async_session() as db:
                            from app.models import Trader as DBTrader
                            trader_row = await db.get(DBTrader, action.target_agent_id)
                            if trader_row:
                                current_pct = self._trader_allocations.get(trader_row.id, 33.3)
                                new_pct = max(15.0, min(50.0, current_pct + trader_change))
                                await trader_service.update_trader_allocation(db, trader_row.id, new_pct)
                                self._trader_allocations[trader_row.id] = new_pct
                                result_msg = f"Adjusted {trader_row.name} trader allocation to {new_pct:.1f}%"
                                logger.info(f"CIO action: {result_msg}")
                            else:
                                result_msg = "Trader not found"
                    elif agent_change != 0:
                        async with get_async_session() as db:
                            agent = await db.get(DBAgent, action.target_agent_id)
                            if agent:
                                # Don't increase allocation for a disabled agent
                                if not agent.is_enabled and agent_change > 0:
                                    result_msg = f"Skipped — '{agent.name}' is disabled; enable it before increasing allocation"
                                    logger.info(f"Strategy action: {result_msg}")
                                else:
                                    new_alloc = max(5.0, min(40.0, agent.allocation_percentage + agent_change))
                                    agent.allocation_percentage = new_alloc
                                    await db.commit()
                                    result_msg = f"Adjusted {action.target_agent_name} allocation to {new_alloc:.1f}%"
                                    logger.info(f"Strategy action: {result_msg}")
                            else:
                                result_msg = "Agent not found"
                    else:
                        result_msg = "No allocation change specified"
                elif action.action == "adjust_params" and not action.target_agent_id:
                    result_msg = f"Skipped — could not resolve target '{action.target_agent_name}' to an agent or trader"
                    logger.warning(f"CIO adjust_params skipped: target '{action.target_agent_name}' not found")

                # Persist the action record
                try:
                    async with get_async_session() as db:
                        record = StrategyActionRecord(
                            action=action.action,
                            target_agent_id=action.target_agent_id,
                            target_agent_name=action.target_agent_name,
                            strategy_type=action.strategy_type,
                            params=action.params,
                            rationale=action.rationale,
                            initiated_by=action.initiated_by,
                            confluence_score=action.confluence_score,
                            backtest_net_pnl=action.backtest_net_pnl,
                            executed=bool(result_msg and "not found" not in result_msg.lower() and "skipped" not in result_msg.lower()),
                            execution_result=result_msg,
                        )
                        db.add(record)
                        await db.commit()
                except Exception as persist_err:
                    logger.debug(f"Failed to persist strategy action: {persist_err}")

                # Announce in team chat
                if result_msg:
                    await team_chat.add_message(
                        agent_role="portfolio_manager",
                        content=f"🔄 Strategy Action: {result_msg}. Rationale: {action.rationale[:120]}",
                        message_type="decision",
                        mentions=["@technical_analyst", "@cio"],
                    )

            except Exception as e:
                logger.error(f"Failed to execute strategy action {action.action}: {e}")

    async def _execute_trader_strategy_actions(
        self,
        actions: List[dict],
        trader: dict,
        agents_list: List[Dict],
    ):
        """Execute strategy actions proposed by a specific trader."""
        from app.models import Agent as DBAgent, User

        for action in actions:
            try:
                act = action.get("action", "")
                result_msg = ""

                if act == "create_agent":
                    async with get_async_session() as db:
                        user = await db.scalar(select(User).limit(1))
                        if not user:
                            continue

                        # Check agent limit per trader (max 4)
                        trader_agents = [a for a in agents_list if a.get("trader_id") == trader["id"]]
                        if len(trader_agents) >= 4:
                            logger.info(f"Trader {trader['name']}: skipping create — already has 4 agents")
                            continue

                        from app.api.routes.settings import get_risk_limits, get_trading_prefs
                        risk = get_risk_limits()
                        prefs = get_trading_prefs()
                        trading_pairs = action.get("trading_pairs") or prefs.trading_pairs or [
                            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"
                        ]
                        strategy = action.get("strategy_type", "momentum")

                        # Derive a canonical name from the primary symbol + strategy so the
                        # name always reflects what the agent actually trades, regardless of
                        # what the LLM proposed (e.g. avoid "XRP_Momentum" trading SANDUSDT).
                        primary_pair = trading_pairs[0] if trading_pairs else "BTC"
                        sym_prefix = primary_pair.replace("USDT", "").replace("USD", "")
                        STRATEGY_DISPLAY = {
                            "momentum": "Momentum", "mean_reversion": "MeanRev",
                            "breakout": "Breakout", "scalping": "Scalper",
                            "trend_following": "TrendFollower", "grid": "Grid",
                        }
                        canonical_name = f"{sym_prefix}_{STRATEGY_DISPLAY.get(strategy, strategy.title())}"
                        agent_name = canonical_name

                        new_agent = DBAgent(
                            user_id=user.id,
                            trader_id=trader["id"],
                            name=agent_name,
                            strategy_type=strategy,
                            config={
                                "trading_pairs": trading_pairs,
                                "auto_created": True,
                                "created_by": f"trader_{trader['name']}",
                                "stop_loss_pct": action.get("stop_loss_pct", risk.default_stop_loss_pct),
                                "take_profit_pct": action.get("take_profit_pct", risk.default_take_profit_pct),
                                "trailing_stop_pct": action.get("trailing_stop_pct", 3.0),
                            },
                            is_enabled=True,
                            allocation_percentage=10.0,
                            max_position_size=risk.max_position_size_pct / 100 * self._total_capital,
                        )
                        db.add(new_agent)
                        await db.commit()
                        await db.refresh(new_agent)

                        agent_config = {
                            "id": new_agent.id,
                            "name": new_agent.name,
                            "strategy_type": strategy,
                            "trading_pairs": trading_pairs,
                            "is_enabled": True,
                            "allocation_percentage": 10.0,
                            "max_position_size": new_agent.max_position_size,
                            "trader_id": trader["id"],
                            "stop_loss_pct": action.get("stop_loss_pct", risk.default_stop_loss_pct),
                            "take_profit_pct": action.get("take_profit_pct", risk.default_take_profit_pct),
                            "trailing_stop_pct": action.get("trailing_stop_pct", 3.0),
                        }
                        self.register_agent(agent_config)
                        await self._bootstrap_from_backtest(agent_config)
                        result_msg = (
                            f"Trader {trader['name']} created agent '{agent_name}' ({strategy})"
                        )
                        logger.info(f"Trader strategy action: {result_msg}")

                elif act == "disable_agent":
                    agent_id = action.get("agent_id")
                    if agent_id:
                        # Guard: require minimum runs before disabling
                        _metrics = self._agent_metrics.get(agent_id)
                        try:
                            from app.api.routes.settings import get_trading_gates as _get_gates_td
                            _min_runs = _get_gates_td().min_runs_before_disable
                        except Exception:
                            _min_runs = 15
                        if _metrics and _metrics.total_runs < _min_runs:
                            logger.info(
                                f"Trader {trader['name']}: skipping disable — "
                                f"agent has only {_metrics.total_runs} runs (min {_min_runs})"
                            )
                        else:
                            async with get_async_session() as db:
                                agent = await db.get(DBAgent, agent_id)
                                if agent and agent.is_enabled and agent.trader_id == trader["id"]:
                                    agent.is_enabled = False
                                    await db.commit()
                                    self.unregister_agent(agent_id)
                                    result_msg = f"Trader {trader['name']} disabled agent {agent.name}"
                                    logger.info(f"Trader strategy action: {result_msg}")

                elif act == "enable_agent":
                    agent_id = action.get("agent_id")
                    if agent_id:
                        async with get_async_session() as db:
                            agent = await db.get(DBAgent, agent_id)
                            if agent and not agent.is_enabled and agent.trader_id == trader["id"]:
                                agent.is_enabled = True
                                await db.commit()
                                config = next((a for a in agents_list if a["id"] == agent_id), None)
                                if config:
                                    config["is_enabled"] = True
                                    self.register_agent(config)
                                    await self._bootstrap_from_backtest(config)
                                    config['_last_run'] = datetime.now()
                                result_msg = f"Trader {trader['name']} enabled agent {agent.name}"
                                logger.info(f"Trader strategy action: {result_msg}")

                if result_msg:
                    await team_chat.add_message(
                        agent_role="portfolio_manager",
                        content=f"🔄 {result_msg}. Reason: {action.get('reason', '—')[:120]}",
                        message_type="decision",
                    )
            except Exception as e:
                logger.error(f"Trader {trader['name']} action {action.get('action')} failed: {e}")

    def get_traders(self) -> List[dict]:
        """Return the in-memory list of traders (for API endpoints)."""
        return list(self._traders)

    def get_trader_allocations(self) -> Dict[str, float]:
        """Return current trader allocation percentages."""
        return dict(self._trader_allocations)

    def record_pnl_from_external_close(
        self,
        agent_id: str,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        exit_price: float,
    ) -> None:
        """
        Called by position_sync when Phemex closes a live position server-side
        (exchange SL/TP hit). Updates in-memory metrics + persists to DB so
        P&L and win rates stay accurate without waiting for a scheduler restart.
        """
        is_long = str(side).lower() in ("buy", "long")
        fee_rate = 0.0006  # Phemex perpetual taker fee
        entry_fee = entry_price * quantity * fee_rate
        exit_fee = exit_price * quantity * fee_rate
        if is_long:
            pnl = (exit_price - entry_price) * quantity - entry_fee - exit_fee
        else:
            pnl = (entry_price - exit_price) * quantity - entry_fee - exit_fee

        agent_cfg = self._enabled_agents.get(agent_id, {})
        strategy_type = agent_cfg.get("strategy_type")
        self._record_run(
            agent_id=agent_id,
            symbol=symbol,
            signal="sell" if is_long else "buy",
            confidence=0,
            price=exit_price,
            executed=True,
            pnl=round(pnl, 4),
            strategy_type=strategy_type,
            use_paper=False,
        )
        logger.info(
            f"ExternalClose: recorded live P&L for agent={agent_id} "
            f"{symbol} {'LONG' if is_long else 'SHORT'} P&L=${pnl:+.4f}"
        )


agent_scheduler = AgentScheduler()
