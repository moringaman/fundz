from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import asyncio
import logging

from sqlalchemy import select, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.clients.phemex import PhemexClient
from app.config import settings
from app.database import get_async_session
from app.services.indicators import IndicatorService
from app.services.paper_trading import paper_trading
from app.services.backtest import BacktestEngine
from app.services.risk_manager import risk_manager, RiskConfig
from app.models import OrderSide, OrderStatus
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

        return ctx if ctx else None

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

        # Restore persisted metrics from DB so allocation decisions survive restarts
        await self._load_metrics_from_db()

        # Auto-register all enabled agents from the database
        await self._auto_register_agents()

        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Agent scheduler started")
        asyncio.create_task(_send_telegram(telegram_service.alert_automation_started()))
    async def _load_metrics_from_db(self):
        """Restore agent metrics from DB into memory so performance history
        survives scheduler restarts and doesn't reset to zero."""
        try:
            from app.database import get_async_session
            from app.models import AgentMetricRecord
            from sqlalchemy import select

            async with get_async_session() as session:
                result = await session.execute(select(AgentMetricRecord))
                records = result.scalars().all()

            loaded = 0
            for rec in records:
                self._agent_metrics[rec.agent_id] = AgentMetrics(
                    agent_id=rec.agent_id,
                    total_runs=rec.total_runs or 0,
                    successful_runs=rec.successful_runs or 0,
                    failed_runs=rec.failed_runs or 0,
                    total_pnl=rec.total_pnl or 0.0,
                    buy_signals=rec.buy_signals or 0,
                    sell_signals=rec.sell_signals or 0,
                    hold_signals=rec.hold_signals or 0,
                    win_rate=rec.win_rate if rec.win_rate is not None else None,
                    avg_pnl=rec.avg_pnl or 0.0,
                    last_run=rec.last_run,
                )
                loaded += 1

            if loaded:
                logger.info(f"Scheduler: restored metrics for {loaded} strategies from DB")
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

            # Sync all agents' trading_pairs with the globally configured pairs
            try:
                from app.api.routes.settings import get_trading_prefs
                global_pairs = get_trading_prefs().trading_pairs
                if global_pairs:
                    from app.models import Agent as DBAgentModel
                    async with get_async_session() as sync_session:
                        result = await sync_session.execute(select(DBAgentModel))
                        all_db_agents = result.scalars().all()
                        updated = 0
                        for db_agent in all_db_agents:
                            cfg = dict(db_agent.config) if isinstance(db_agent.config, dict) else {}
                            existing_pairs = cfg.get("trading_pairs", [])
                            if set(existing_pairs) != set(global_pairs):
                                cfg["trading_pairs"] = global_pairs
                                db_agent.config = cfg
                                updated += 1
                        if updated:
                            await sync_session.commit()
                            logger.info(f"Synced trading pairs on {updated} agents → {global_pairs}")
                    # Refresh agents list after update
                    agents = await self._fetch_agents_from_db()
            except Exception as e:
                logger.warning(f"Could not sync agent trading pairs: {e}")

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
        _BOOTSTRAP_RR = {
            "momentum":        (2.0,  5.0),
            "trend_following": (3.0,  9.0),
            "breakout":        (2.5,  7.0),
            "mean_reversion":  (1.5,  3.5),
            "scalping":        (1.0,  2.5),
            "grid":            (3.0,  6.0),
            "ai":              (2.5,  6.0),
        }
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
                candle_limit=1000,
            )
            result = await self.backtest_engine.run_backtest(config)

            # Seed the pre-trade backtest cache so the first trade doesn't re-run it
            self._backtest_cache[f"{agent_id}:{symbol}"] = (result, datetime.now())

            metrics = self._agent_metrics[agent_id]
            metrics.win_rate = result.win_rate
            metrics.total_pnl = result.net_pnl  # use net (after fees)
            metrics.avg_pnl = result.avg_trade_pnl

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
        self._enabled_agents[agent_id] = agent_config

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
        while self._running:
            try:
                # TEAM DECISION TIER (NEW): Run every 5 minutes (300 seconds)
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 300:
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

    async def _monitor_open_positions(self):
        """Check open positions against live prices and trigger SL/TP exits."""
        try:
            positions = await paper_trading.get_positions()
            if not positions:
                return

            for pos in positions:
                try:
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
                    sl_pct = agent_config.get('stop_loss_pct', 3.5) or 3.5
                    tp_pct = agent_config.get('take_profit_pct', 7.0) or 7.0
                    trailing_pct = (
                        getattr(pos, 'trailing_stop_pct', None)
                        or agent_config.get('trailing_stop_pct')
                        or 3.0  # default 3% trailing stop for all positions
                    )

                    stored_sl = getattr(pos, 'stop_loss_price', None)
                    stored_tp = getattr(pos, 'take_profit_price', None)

                    if is_short:
                        sl_price = stored_sl if stored_sl is not None else entry * (1 + sl_pct / 100)
                        tp_price = stored_tp if stored_tp is not None else entry * (1 - tp_pct / 100)
                    else:
                        sl_price = stored_sl if stored_sl is not None else entry * (1 - sl_pct / 100)
                        tp_price = stored_tp if stored_tp is not None else entry * (1 + tp_pct / 100)

                    # Update watermark: highest_price for longs, lowest_price for shorts
                    highest = getattr(pos, 'highest_price', None) or entry
                    if is_short:
                        if current_price < highest:
                            highest = current_price
                            await paper_trading.update_highest_price(pos.id, current_price, is_short=True)
                    else:
                        if current_price > highest:
                            highest = current_price
                            await paper_trading.update_highest_price(pos.id, current_price, is_short=False)

                    risk_config = RiskConfig(
                        stop_loss_pct=sl_pct,
                        take_profit_pct=tp_pct,
                        trailing_stop_pct=trailing_pct,
                    )

                    # ── Stage 1 & 2: Breakeven + profit-lock SL progression ──────
                    # Runs independently of trailing stop, using TP distance milestones.
                    #
                    # Stage 1 — BREAKEVEN (33% of way to TP reached):
                    #   Move SL to entry + round-trip fees (0.12%) so the trade
                    #   can never close at a loss from this point forward.
                    #
                    # Stage 2 — PROFIT LOCK (66% of way to TP reached):
                    #   Move SL to entry + 50% of current profit so at least
                    #   half the gain is guaranteed even if price reverses.
                    #
                    # Both stages only ever TIGHTEN the SL (never widen it).
                    # A minimum 0.2% price-move filter prevents DB thrashing.
                    # ─────────────────────────────────────────────────────────
                    _FEE_RT = 0.0012  # round-trip taker fee (0.06% × 2)
                    if entry and tp_price and sl_price is not None:
                        if is_short:
                            _full_move = entry - tp_price          # total move to TP (positive)
                            _current_move = entry - current_price  # how far price has moved (positive = good)
                        else:
                            _full_move = tp_price - entry
                            _current_move = current_price - entry

                        if _full_move > 0 and _current_move > 0:
                            _progress = _current_move / _full_move  # 0.0 → 1.0

                            if is_short:
                                _breakeven_sl = entry * (1 - _FEE_RT)   # just below entry (covers fees)
                                _lock_sl      = entry - (_current_move * 0.5)  # lock half profit

                                if _progress >= 0.66 and _lock_sl < sl_price:
                                    # Stage 2: price > 66% to TP — lock half profit
                                    if (sl_price - _lock_sl) >= current_price * 0.002:
                                        await paper_trading.update_position_sl_tp(pos.id, stop_loss_price=_lock_sl)
                                        logger.info(
                                            f"🔒 Profit lock: {pos.symbol} SHORT SL "
                                            f"${sl_price:.4f}→${_lock_sl:.4f} "
                                            f"(66% to TP, locking {_current_move * 0.5 / entry:.2%} profit)"
                                        )
                                        sl_price = _lock_sl
                                elif _progress >= 0.33 and _breakeven_sl < sl_price:
                                    # Stage 1: price > 33% to TP — move to breakeven
                                    if (sl_price - _breakeven_sl) >= current_price * 0.002:
                                        await paper_trading.update_position_sl_tp(pos.id, stop_loss_price=_breakeven_sl)
                                        logger.info(
                                            f"⚖️  Breakeven SL: {pos.symbol} SHORT "
                                            f"${sl_price:.4f}→${_breakeven_sl:.4f} "
                                            f"({_progress:.0%} to TP, fees covered)"
                                        )
                                        sl_price = _breakeven_sl
                            else:
                                _breakeven_sl = entry * (1 + _FEE_RT)   # just above entry (covers fees)
                                _lock_sl      = entry + (_current_move * 0.5)  # lock half profit

                                if _progress >= 0.66 and _lock_sl > sl_price:
                                    # Stage 2: price > 66% to TP — lock half profit
                                    if (_lock_sl - sl_price) >= current_price * 0.002:
                                        await paper_trading.update_position_sl_tp(pos.id, stop_loss_price=_lock_sl)
                                        logger.info(
                                            f"🔒 Profit lock: {pos.symbol} LONG SL "
                                            f"${sl_price:.4f}→${_lock_sl:.4f} "
                                            f"(66% to TP, locking {_current_move * 0.5 / entry:.2%} profit)"
                                        )
                                        sl_price = _lock_sl
                                elif _progress >= 0.33 and _breakeven_sl > sl_price:
                                    # Stage 1: price > 33% to TP — move to breakeven
                                    if (_breakeven_sl - sl_price) >= current_price * 0.002:
                                        await paper_trading.update_position_sl_tp(pos.id, stop_loss_price=_breakeven_sl)
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
                    if trailing_pct and highest:
                        if is_short:
                            # For shorts: trailing SL moves DOWN as price falls
                            ideal_sl = highest * (1 + trailing_pct / 100)
                            if sl_price is None or ideal_sl < sl_price:
                                # Only tighten (lower SL for shorts)
                                if sl_price is None or (sl_price - ideal_sl) >= current_price * 0.002:
                                    await paper_trading.update_position_sl_tp(
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
                            ideal_sl = highest * (1 - trailing_pct / 100)
                            if ideal_sl > entry and (sl_price is None or ideal_sl > sl_price):
                                # Only tighten (raise SL for longs), and only above entry
                                if sl_price is None or (ideal_sl - sl_price) >= current_price * 0.002:
                                    await paper_trading.update_position_sl_tp(
                                        pos.id, stop_loss_price=ideal_sl
                                    )
                                    logger.info(
                                        f"Trailing SL tightened: {pos.symbol} LONG "
                                        f"SL ${sl_price or 0:.2f}→${ideal_sl:.2f} "
                                        f"(high watermark ${highest:.2f}, trail {trailing_pct}%)"
                                    )
                                    sl_price = ideal_sl

                    position_dict = {
                        'side': pos_side,
                        'entry_price': entry,
                        'stop_loss': sl_price,
                        'take_profit': tp_price,
                        'highest_price': highest,
                    }

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
                            # Exit: sell for longs, buy-to-cover for shorts
                            exit_side = "buy" if is_short else "sell"
                            await paper_trading.place_order(
                                symbol=pos.symbol,
                                side=exit_side,
                                quantity=pos.quantity,
                                price=current_price,
                                agent_id=pos.agent_id,
                            )
                            action_word = "covered" if is_short else "sold"
                            logger.info(f"SL/TP exit executed: {action_word} {pos.quantity} {pos.symbol} @ ${current_price:.2f}")

                            # Calculate net P&L (after fees) — gross move minus round-trip taker fees
                            _fee_rate = 0.001 if pos.symbol.endswith("USDT") else 0.0006
                            _entry_fee = (entry * pos.quantity) * _fee_rate
                            _exit_fee = (current_price * pos.quantity) * _fee_rate
                            if is_short:
                                pnl = (entry - current_price) * pos.quantity - _entry_fee - _exit_fee
                            else:
                                pnl = (current_price - entry) * pos.quantity - _entry_fee - _exit_fee
                            risk_manager.record_pnl(pnl)

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
                                closed_runs = [r for r in self._agent_runs if r.agent_id == pos.agent_id and r.pnl is not None]
                                if closed_runs:
                                    m.win_rate = sum(1 for r in closed_runs if r.pnl > 0) / len(closed_runs)

                            # Log to team chat
                            from app.services.team_chat import team_chat
                            exit_type = "trailing-stop" if "Trailing" in check.reason else (
                                "take-profit" if "Take-profit" in check.reason else "stop-loss"
                            )
                            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                            await team_chat.add_message(
                                agent_role="execution_coordinator",
                                content=f"📊 **{exit_type.upper()}** {pos.symbol} ({direction}): {pnl_str} ({check.reason})",
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
            positions = await paper_trading.get_positions()
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

                pos_current_levels[pos.id] = {
                    "sl": sl, "tp": tp, "price": current_price,
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
                    f"TP={fmt_price(tp) if tp else 'NONE'} | {ta_line} "
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
                "3. For profitable positions, consider tightening SL to lock in gains.\n"
                "4. If TA confluence is bearish for a long position, tighten SL.\n"
                "5. If TA confluence is strongly bullish, consider extending TP.\n"
                "6. If no change is warranted, return an empty adjustments array.\n"
                "7. Always include a brief reason per adjustment.\n\n"
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
                        kwargs["stop_loss_price"] = new_sl
                if new_tp is not None:
                    old_tp = old.get("tp")
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
                            f"Object if it would prematurely close a position before your thesis plays out, "
                            f"or if it contradicts your entry reasoning. Be concise (1-2 sentences)."
                        )
                        trader_user = (
                            f"Position: {symbol} | {proposal_str}\n"
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

                # Apply the approved adjustment
                result = await paper_trading.update_position_sl_tp(pid, **kwargs)
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
        metrics_list = []
        for agent_id, metrics in self._agent_metrics.items():
            metrics_list.append({
                "agent_id": agent_id,
                "agent_name": agent_name_map.get(agent_id, agent_id),
                "total_runs": metrics.total_runs,
                "successful_runs": metrics.successful_runs,
                "total_pnl": metrics.total_pnl,
                "win_rate": metrics.win_rate,
                "last_run": metrics.last_run.isoformat() if metrics.last_run else None,
                "strategy_type": next(
                    (a["strategy_type"] for a in agents if a["id"] == agent_id), "unknown"
                ),
            })
        return metrics_list

    async def _get_current_positions(self) -> List[dict]:
        """Fetch current paper trading positions for risk assessment"""
        try:
            positions = await paper_trading.get_positions()
            return [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "unrealized_pnl": p.unrealized_pnl,
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

                portfolio_decision = await fund_manager.make_allocation_decision(
                    agents=agents_list,
                    agent_metrics=agent_metrics,
                    market_condition=market_condition,
                    confluence_scores=confluence_scores,
                )
                self._current_allocation = portfolio_decision.allocation_pct
                self._current_allocation_reasoning = portfolio_decision.reasoning
                logger.info(f"Team Tier: Portfolio manager updated allocation for {len(agents_list)} agents")
                await team_chat.log_portfolio_decision(portfolio_decision, agents_list)
            except Exception as e:
                logger.error(f"Team Tier: Portfolio manager failed: {e}")

            # 2.1 Trader Layer: James allocates to traders, each trader sub-allocates to their strategies
            try:
                if self._traders:
                    # Build trader performance summary for James
                    trader_perf_list = []
                    for t in self._traders:
                        t_agents = [a for a in agents_list if a.get("trader_id") == t["id"]]
                        t_metrics = [m for m in agent_metrics
                                     if m.get("agent_id") in {a["id"] for a in t_agents}]
                        perf = trader_service.get_trader_performance(t, t_agents, t_metrics)
                        trader_perf_list.append({
                            "trader_id": perf.trader_id,
                            "trader_name": perf.trader_name,
                            "agent_count": perf.agent_count,
                            "total_pnl": perf.total_pnl,
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

            # 4. Execution Coordinator: Optimize order timing
            try:
                execution_plan = await execution_coordinator.optimize_execution_plan([])
                self._current_execution_plan = execution_plan
                logger.info(f"Team Tier: Execution coordinator - {execution_plan.pending_orders_count} pending orders")
                await team_chat.log_execution_plan(execution_plan)
            except Exception as e:
                logger.error(f"Team Tier: Execution coordinator failed: {e}")

            # 5. CIO Report (less frequent, every 20 minutes)
            try:
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 1200:
                    cio_report = await cio_agent.generate_fund_report(
                        agent_metrics=agent_metrics,
                    )
                    self._current_cio_report = cio_report
                    logger.info(f"Team Tier: CIO report - Sentiment: {cio_report.cio_sentiment}")
                    await team_chat.log_cio_report(cio_report)

                    # 5.1 Execute CIO strategic recommendations
                    if cio_report.strategic_recommendations:
                        cio_actions = self._map_cio_recommendations(
                            cio_report.strategic_recommendations, agents_list
                        )
                        if cio_actions:
                            logger.info(f"Team Tier: Executing {len(cio_actions)} CIO recommendation(s)")
                            await self._execute_strategy_actions(cio_actions, agents_list)
            except Exception as e:
                logger.error(f"Team Tier: CIO report failed: {e}")

            # 6. Trade Retrospective (every 20 minutes alongside CIO)
            try:
                if self._last_team_analysis is None or \
                   (datetime.now() - self._last_team_analysis).total_seconds() >= 1200:
                    from app.services.trade_retrospective import trade_retrospective
                    retro = await trade_retrospective.analyze_recent_trades(agents_list)
                    if retro:
                        self._current_trade_insights = retro
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
                    trading_pairs=config.get('trading_pairs', []),
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
        best_symbol = None
        best_confidence = 0.0
        best_signal = "hold"
        best_df = None
        best_reasoning = ""

        for candidate_symbol in trading_pairs:
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

                if strategy_type == 'ai':
                    from app.services.llm import llm_service
                    indicators_dict = {
                        'rsi': float(self.indicator_service.calculate_rsi(df['close']).iloc[-1]) if len(df) >= 14 else None,
                        'macd': float(self.indicator_service.calculate_macd(df['close']).iloc[-1]['macd']) if len(df) >= 26 else None,
                        'bb_upper': float(self.indicator_service.calculate_bollinger_bands(df['close']).iloc[-1]['upper']) if len(df) >= 20 else None,
                        'bb_lower': float(self.indicator_service.calculate_bollinger_bands(df['close']).iloc[-1]['lower']) if len(df) >= 20 else None,
                    }
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
                    signal_result = self.indicator_service.generate_signal(df, {'strategy': strategy_type}, market_context=market_context)
                    sig = signal_result.signal.value if signal_result.signal else 'hold'
                    conf = signal_result.confidence
                    reas = getattr(signal_result, 'reasoning', '')

                if sig in ('buy', 'sell') and conf > best_confidence:
                    best_symbol = candidate_symbol
                    best_confidence = conf
                    best_signal = sig
                    best_df = df
                    best_reasoning = reas
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
            
            if signal in ['buy', 'sell'] and confidence >= 0.6:
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

                # Minimum profit gate: reject trades where TP doesn't cover round-trip fees
                # Use spot fee rate for USDT pairs, contract rate for coin-margined
                # Phemex spot taker: 0.1% | Phemex contract taker: 0.06%
                _is_spot = symbol.endswith("USDT")
                _taker_fee_pct = 0.10 if _is_spot else 0.06
                round_trip_fee_pct = _taker_fee_pct * 2
                net_tp_pct = take_profit_pct - round_trip_fee_pct
                if net_tp_pct < 0.5:
                    logger.warning(
                        f"Trade skipped: TP {take_profit_pct}% minus fees {round_trip_fee_pct}% = "
                        f"{net_tp_pct:.2f}% net — below 0.5% minimum"
                    )
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error="TP too low after fees")
                    await team_chat.log_trade_blocked(
                        trader_name=_trader_name, trader_avatar=_trader_avatar,
                        agent_name=name, symbol=symbol, side=signal,
                        reason=f"TP {take_profit_pct}% minus fees = {net_tp_pct:.2f}% net — below 0.5% minimum",
                    )
                    return AgentRun(
                        agent_id=agent_id, timestamp=datetime.now(), symbol=symbol,
                        signal="hold", confidence=0, price=current_price,
                        executed=False, error="TP too low after fees"
                    )

                # Each agent is fully isolated via the (user_id, agent_id, symbol) unique constraint.
                # Different agents — even on the same symbol — operate independently and should not
                # block each other. No cross-agent conflict gate needed here.

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

                # Size position based on total fund capital (not just available USDT).
                # allocation_pct is a % of the total fund; using only available USDT
                # would produce shrinking positions as capital gets deployed.
                total_fund = self._total_capital or usdt_balance
                target_position_value = total_fund * allocation_pct / 100 * _size_mult

                # Fee-aware cap: fees (round-trip) should not exceed 1/3 of expected P&L.
                # max_position_for_fee_ratio = expected_profit / (fee_rate * 2 * ratio)
                # Rearranged: max_notional = max where fees ≤ TP_pct / (2 * fee_rate * 3)
                _fee_ratio_cap = (take_profit_pct / 100) / (round_trip_fee_pct / 100 * 3)
                # _fee_ratio_cap is a multiplier; position_value * _fee_ratio_cap means
                # fees are exactly 1/3 of expected P&L at TP. We don't need to cap here
                # since take_profit_pct / round_trip_fee_pct already determines the ratio.
                # Instead cap position so fees never exceed 1/3 of gross TP dollar value:
                # max_notional such that (max_notional * round_trip_fee_pct/100) <= (max_notional * take_profit_pct/100) / 3
                # This simplifies to: round_trip_fee_pct <= take_profit_pct / 3  (always true when TP > fees*3)
                # We already ensured net_tp_pct >= 0.5 above, so just apply size_mult and USDT cap.

                # Cap at 95% of available USDT to avoid overdrafts
                position_value = min(target_position_value, usdt_balance * 0.95)
                quantity = position_value / current_price

                logger.debug(
                    f"Sizing {name} ({strategy_type}, {_size_mult:.0%} mult): "
                    f"total_fund=${total_fund:.0f}, alloc={allocation_pct:.2f}%, "
                    f"target=${target_position_value:.0f}, available=${usdt_balance:.0f}, "
                    f"final=${position_value:.0f}, qty={quantity:.4f} @ ${current_price}"
                )

                if signal == 'sell':
                    positions = await paper_trading.get_positions(symbol, agent_id=agent_id)
                    long_pos = next((p for p in positions if p.symbol == symbol and p.side == OrderSide.BUY), None)
                    if long_pos:
                        # Close the existing long position
                        quantity = long_pos.quantity
                    else:
                        # Open a short position — use the same sizing as a buy
                        pass  # quantity already calculated above
                
                from app.api.routes.settings import get_risk_limits
                _limits = get_risk_limits()
                risk_config = RiskConfig(
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    max_daily_loss=_limits.max_daily_loss_pct,
                    total_capital=total_fund,
                    max_position_size=position_value,
                    max_open_positions=_limits.max_open_positions,
                    max_exposure=total_fund * _limits.exposure_threshold_pct / 100,
                )
                
                risk_check = risk_manager.check_trade(
                    side=signal,
                    quantity=quantity,
                    entry_price=current_price,
                    risk_config=risk_config
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
                        # Daily loss limit hit — send a dedicated alert (once per day logic is inside telegram service)
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
                
                technical_report = await technical_analyst.analyze(symbol, timeframe=timeframe)
                # Cache result so next agent cycle on same symbol+timeframe doesn't re-fetch
                self._ta_cache[f"{symbol}:{timeframe}"] = (technical_report, datetime.now())
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
                if is_opposite and technical_report.confidence > 0.75:
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

                if technical_report.patterns:
                    best_pattern = max(technical_report.patterns, key=lambda p: p.confidence)
                    if best_pattern.stop_loss and best_pattern.take_profit_1:
                        # Use the MORE CONSERVATIVE of TA vs risk manager levels
                        adjusted_sl = min(risk_check.stop_loss_price, best_pattern.stop_loss) if risk_check.stop_loss_price else best_pattern.stop_loss
                        adjusted_tp = max(risk_check.take_profit_price, best_pattern.take_profit_1) if risk_check.take_profit_price else best_pattern.take_profit_1
                        tp2 = best_pattern.take_profit_2
                        logger.info(f"Technical analyst levels: SL ${best_pattern.stop_loss:.2f}, TP1 ${best_pattern.take_profit_1:.2f}, TP2 ${tp2:.2f}" if tp2 is not None else f"Technical analyst levels: SL ${best_pattern.stop_loss:.2f}, TP1 ${best_pattern.take_profit_1:.2f}")
                
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
                #
                # RE-ENTRY EXEMPTION: If the last trade on this symbol was stopped out within
                # 4 hours, bypass the backtest gate entirely.  A tight stop-loss being hit does
                # NOT invalidate the underlying thesis — blocking re-entry compounds the loss by
                # keeping the agent out of a potentially valid setup.
                _recent_stopout_ctx = (team_context or {}).get("recent_stopout")
                _bt_reentry_exempt = bool(_recent_stopout_ctx)
                if _bt_reentry_exempt:
                    logger.info(
                        f"[Backtest] Re-entry exemption for {name}/{symbol} — "
                        f"stopped out {_recent_stopout_ctx['minutes_ago']}m ago; skipping gate"
                    )

                _bt_cache_key = f"{agent_id}:{symbol}"
                _bt_cache_ttl = 4  # hours
                _cached = self._backtest_cache.get(_bt_cache_key)
                _bt_result = None
                _bt_stale = True

                if _cached:
                    _bt_result, _bt_cached_at = _cached
                    _age_hours = (datetime.now() - _bt_cached_at).total_seconds() / 3600
                    _bt_stale = _age_hours >= _bt_cache_ttl

                if not _bt_reentry_exempt:
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
                                candle_limit=1000,
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
                        elif _bt_result.win_rate < 0.20:
                            _bt_hard_block = True
                            _bt_fail_reason = f"backtest win rate {_bt_result.win_rate:.0%} is catastrophically low (< 20%)"
                        elif _bt_result.net_pnl <= 0 or _bt_result.win_rate < 0.35:
                            # Borderline — reduce size, allow trade
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
                        )
                        executed = True
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
                        logger.error(f"Paper trade failed: {e}")
                elif not use_paper and settings.phemex_api_key and settings.phemex_api_secret:
                    try:
                        # Determine if this is a short — use contract API for shorts, spot for longs
                        is_short_entry = signal.lower() == 'sell'
                        if is_short_entry:
                            # Use contract (perpetual futures) API for short positions
                            result = await self.phemex.place_contract_order(
                                symbol=symbol,
                                side="sell",
                                quantity=quantity,
                                order_type="Market",
                                stop_loss_price=adjusted_sl,
                                take_profit_price=adjusted_tp,
                            )
                        else:
                            result = await self.phemex.place_spot_order_with_sl_tp(
                                symbol=symbol,
                                side=signal,
                                quantity=quantity,
                                order_type="Market",
                                price=current_price,
                                stop_loss_price=adjusted_sl,
                                take_profit_price=adjusted_tp,
                            )
                        executed = True
                        direction = "SHORT" if is_short_entry else "LONG"
                        sl_str = f"${adjusted_sl:.2f}" if adjusted_sl else "N/A"
                        tp_str = f"${adjusted_tp:.2f}" if adjusted_tp else "N/A"
                        logger.info(f"LIVE {direction} trade executed: {signal} {quantity} {symbol} @ {current_price} | SL: {sl_str} TP: {tp_str}")
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

                        # Record live trade in DB for tracking
                        try:
                            from app.database import get_async_session
                            from app.models import Trade as DBTrade, OrderSide as DBOrderSide, OrderStatus as DBOrderStatus
                            async with get_async_session() as db:
                                db_trade = DBTrade(
                                    user_id="default-user",
                                    agent_id=agent_id,
                                    symbol=symbol,
                                    side=DBOrderSide.BUY if signal.lower() == "buy" else DBOrderSide.SELL,
                                    quantity=quantity,
                                    price=current_price,
                                    total=quantity * current_price,
                                    fee=quantity * current_price * 0.001,
                                    status=DBOrderStatus.FILLED,
                                    is_paper=False,
                                    phemex_order_id=result.get("data", {}).get("orderID"),
                                )
                                db.add(db_trade)
                                await db.commit()
                        except Exception as db_err:
                            logger.warning(f"Failed to record live trade in DB: {db_err}")

                        # Place trailing stop for both longs and shorts
                        if trailing_stop_pct and current_price:
                            try:
                                if is_short_entry:
                                    # Short trailing: trigger side is Buy (cover), offset positive
                                    offset = current_price * trailing_stop_pct / 100
                                    await self.phemex.place_trailing_stop_order(
                                        symbol=symbol,
                                        side="Buy",
                                        quantity=quantity,
                                        trailing_offset=offset,
                                    )
                                else:
                                    offset = -(current_price * trailing_stop_pct / 100)
                                    await self.phemex.place_trailing_stop_order(
                                        symbol=symbol,
                                        side="Sell",
                                        quantity=quantity,
                                        trailing_offset=offset,
                                    )
                                logger.info(f"Trailing stop placed: {symbol} offset=${abs(offset):.2f} ({trailing_stop_pct}%)")
                            except Exception as te:
                                logger.warning(f"Trailing stop placement failed (non-fatal): {te}")

                    except Exception as e:
                        logger.error(f"LIVE trade failed: {e}")
            
            self._record_run(agent_id, symbol, signal, confidence, current_price, executed, pnl)
            
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
            logger.error(f"Agent run failed: {e}")
            self._record_run(agent_id, symbol, "hold", 0, 0, False, error=str(e))
            
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
            metrics.total_pnl += pnl
            metrics.avg_pnl = metrics.total_pnl / metrics.successful_runs if metrics.successful_runs > 0 else 0
        # Win rate = winning closed trades / all closed trades (pnl is only set on position closes)
        closed_runs = [r for r in self._agent_runs if r.agent_id == agent_id and r.pnl is not None]
        if closed_runs:
            metrics.win_rate = sum(1 for r in closed_runs if r.pnl > 0) / len(closed_runs)

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
                    total_runs=metrics.total_runs,
                    successful_runs=metrics.successful_runs,
                    failed_runs=metrics.failed_runs,
                    total_pnl=metrics.total_pnl,
                    buy_signals=metrics.buy_signals,
                    sell_signals=metrics.sell_signals,
                    hold_signals=metrics.hold_signals,
                    win_rate=metrics.win_rate,
                    avg_pnl=metrics.avg_pnl,
                    last_run=metrics.last_run,
                ).on_conflict_do_update(
                    index_elements=["agent_id"],
                    set_=dict(
                        total_runs=metrics.total_runs,
                        successful_runs=metrics.successful_runs,
                        failed_runs=metrics.failed_runs,
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
                    select(AgentMetricRecord).where(AgentMetricRecord.agent_id == agent_id)
                )
                if row:
                    return AgentMetrics(
                        agent_id=row.agent_id,
                        total_runs=row.total_runs,
                        successful_runs=row.successful_runs,
                        failed_runs=row.failed_runs,
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
                rows = (await db.execute(select(AgentMetricRecord))).scalars().all()
                if rows:
                    return [
                        AgentMetrics(
                            agent_id=r.agent_id,
                            total_runs=r.total_runs,
                            successful_runs=r.successful_runs,
                            failed_runs=r.failed_runs,
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
        objects that _execute_strategy_actions can consume."""
        from app.services.strategy_review import StrategyActionProposal

        agents_by_name = {a.get("name", "").lower(): a for a in agents_list}
        agents_by_id = {a["id"]: a for a in agents_list}
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
            if rec.confidence < 0.6:
                continue

            target_id = None
            target_name = rec.target
            # Resolve target to agent id — try exact match first, then by strategy_type
            if rec.target and rec.target != "portfolio":
                agent = agents_by_id.get(rec.target) or agents_by_name.get(rec.target.lower())
                if not agent:
                    # Target may be a strategy type (e.g. "ai", "momentum") — pick the
                    # best-performing agent of that strategy type
                    matching = [
                        a for a in agents_list
                        if a.get("strategy_type", "").lower() == rec.target.lower()
                    ]
                    if matching:
                        agent = max(matching, key=lambda a: a.get("allocation_percentage", 0))
                if agent:
                    target_id = agent["id"]
                    target_name = agent.get("name", rec.target)

            params: dict = {}
            if rec.recommendation == "increase_allocation":
                params["allocation_change_pct"] = 5
            elif rec.recommendation in ("reduce_allocation", "reduce_risk"):
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
                            result_msg = f"Enabled agent {action.target_agent_name}"
                            logger.info(f"Strategy action: {result_msg}")
                        else:
                            result_msg = "Agent already enabled or not found"

                elif action.action == "create_agent":
                    # Create a new agent in the DB — with duplicate guard
                    async with get_async_session() as db:
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

                                STRATEGY_PROFILES = {
                                    "momentum": {
                                        "name": "Momentum Rider",
                                        "stop_loss_pct": risk.default_stop_loss_pct,
                                        "take_profit_pct": risk.default_take_profit_pct,
                                        "trailing_stop_pct": 3.0,
                                        "indicators_config": {
                                            "rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30,
                                            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                                        },
                                        "description": "Follows strong momentum using RSI + MACD alignment",
                                    },
                                    "mean_reversion": {
                                        "name": "Mean Reverter",
                                        "stop_loss_pct": max(risk.default_stop_loss_pct, 2.5),
                                        "take_profit_pct": risk.default_take_profit_pct * 0.75,
                                        "trailing_stop_pct": 2.0,
                                        "indicators_config": {
                                            "bb_period": 20, "bb_std": 2.0,
                                            "rsi_period": 14, "rsi_overbought": 75, "rsi_oversold": 25,
                                        },
                                        "description": "Buys oversold / sells overbought using Bollinger Bands + RSI",
                                    },
                                    "breakout": {
                                        "name": "Breakout Hunter",
                                        "stop_loss_pct": risk.default_stop_loss_pct * 1.2,
                                        "take_profit_pct": risk.default_take_profit_pct * 1.5,
                                        "trailing_stop_pct": 4.0,
                                        "indicators_config": {
                                            "atr_period": 14, "atr_multiplier": 1.5,
                                            "lookback_period": 20,
                                        },
                                        "description": "Detects and trades range breakouts with ATR-based stops",
                                    },
                                    "scalping": {
                                        "name": "Scalp Sniper",
                                        "stop_loss_pct": max(risk.default_stop_loss_pct * 0.5, 0.5),
                                        "take_profit_pct": max(risk.default_take_profit_pct * 0.5, 1.0),
                                        "trailing_stop_pct": 1.5,
                                        "indicators_config": {
                                            "rsi_period": 7, "rsi_overbought": 65, "rsi_oversold": 35,
                                            "ema_fast": 9, "ema_slow": 21,
                                        },
                                        "description": "Quick in-and-out trades on short-term signals",
                                    },
                                    "trend_following": {
                                        "name": "Trend Follower",
                                        "stop_loss_pct": risk.default_stop_loss_pct * 1.5,
                                        "take_profit_pct": risk.default_take_profit_pct * 2.0,
                                        "trailing_stop_pct": 5.0,
                                        "indicators_config": {
                                            "sma_fast": 20, "sma_slow": 50,
                                            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                                            "atr_period": 14,
                                        },
                                        "description": "Rides sustained trends with wide stops and extended targets",
                                    },
                                }

                                profile = STRATEGY_PROFILES.get(strategy, STRATEGY_PROFILES["momentum"])
                                agent_name = action.target_agent_name or profile["name"]
                                # Avoid generic names like "portfolio"
                                if agent_name.lower() in ("portfolio", "none", "n/a", ""):
                                    agent_name = profile["name"]

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
                    change = action.params.get("allocation_change_pct", 0)
                    if change != 0:
                        async with get_async_session() as db:
                            agent = await db.get(DBAgent, action.target_agent_id)
                            if agent:
                                new_alloc = max(5.0, min(40.0, agent.allocation_percentage + change))
                                agent.allocation_percentage = new_alloc
                                await db.commit()
                                result_msg = f"Adjusted {action.target_agent_name} allocation to {new_alloc:.1f}%"
                                logger.info(f"Strategy action: {result_msg}")
                            else:
                                result_msg = "Agent not found"
                    else:
                        result_msg = "No allocation change specified"
                elif action.action == "adjust_params" and not action.target_agent_id:
                    result_msg = f"Skipped — could not resolve target '{action.target_agent_name}' to an agent"
                    logger.warning(f"CIO adjust_params skipped: target '{action.target_agent_name}' not found in agents list")

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
                        agent_name = action.get("name", f"{trader['name']} {strategy.title()}")

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


agent_scheduler = AgentScheduler()
