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
from app.services.research_analyst import research_analyst
from app.services.fund_manager import fund_manager
from app.services.cio_agent import cio_agent
from app.services.execution_coordinator import execution_coordinator
from app.services.technical_analyst import technical_analyst
from app.services.team_chat import team_chat
from app.services.daily_report import daily_report_service
from app.services.strategy_review import strategy_review_service

logger = logging.getLogger(__name__)


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
    win_rate: float = 0.0
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
    
    @property
    def is_running(self) -> bool:
        return self._running

    def get_current_allocation(self) -> Dict[str, float]:
        """Return the live allocation percentages computed by the Portfolio Manager."""
        return dict(self._current_allocation)

    def get_current_risk_assessment(self):
        """Return the latest risk assessment computed during team analysis."""
        return self._current_risk_assessment
    
    async def start(self):
        if self._running:
            return
        self._running = True

        # Auto-register all enabled agents from the database
        await self._auto_register_agents()

        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Agent scheduler started")

    async def _auto_register_agents(self):
        """Load enabled agents from DB and register them for automated trading.
        
        For agents with no trade history, run a quick backtest to seed their
        metrics so the portfolio manager gives them a fair allocation.
        """
        try:
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
        pairs = agent.get("trading_pairs", ["BTCUSDT"])
        symbol = pairs[0] if pairs else "BTCUSDT"
        strategy = agent.get("strategy_type", "momentum")

        try:
            from app.services.backtest import BacktestConfig
            config = BacktestConfig(
                symbol=symbol,
                interval="1h",
                initial_balance=10000.0,
                position_size_pct=0.1,
                stop_loss_pct=0.02,
                take_profit_pct=0.05,
                strategy=strategy,
            )
            result = await self.backtest_engine.run_backtest(config)

            metrics = self._agent_metrics[agent_id]
            metrics.win_rate = max(result.win_rate, 0.3)  # floor at 30%
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
                        interval="1h",
                        config_params={
                            "initial_balance": 10000.0,
                            "position_size_pct": 0.1,
                            "stop_loss_pct": 0.02,
                            "take_profit_pct": 0.05,
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
                        continue

                    entry = pos.entry_price or 0
                    if entry <= 0:
                        continue

                    # Use stored TA-informed SL/TP from position, fall back to agent config %
                    agent_config = self._enabled_agents.get(pos.agent_id, {})
                    sl_pct = agent_config.get('stop_loss_pct', 2.0) or 2.0
                    tp_pct = agent_config.get('take_profit_pct', 4.0) or 4.0
                    trailing_pct = (
                        getattr(pos, 'trailing_stop_pct', None)
                        or agent_config.get('trailing_stop_pct')
                    )

                    sl_price = getattr(pos, 'stop_loss_price', None) or entry * (1 - sl_pct / 100)
                    tp_price = getattr(pos, 'take_profit_price', None) or entry * (1 + tp_pct / 100)

                    # Update highest price watermark for trailing stop
                    highest = getattr(pos, 'highest_price', None) or entry
                    if current_price > highest:
                        highest = current_price
                        await paper_trading.update_highest_price(pos.id, current_price)

                    risk_config = RiskConfig(
                        stop_loss_pct=sl_pct,
                        take_profit_pct=tp_pct,
                        trailing_stop_pct=trailing_pct,
                    )

                    position_dict = {
                        'side': pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                        'entry_price': entry,
                        'stop_loss': sl_price,
                        'take_profit': tp_price,
                        'highest_price': highest,
                    }

                    check = risk_manager.check_exit(position_dict, current_price, risk_config)

                    if check.action == "exit":
                        logger.info(
                            f"Position exit triggered for {pos.symbol}: {check.reason} "
                            f"(entry: ${entry:.2f}, current: ${current_price:.2f})"
                        )
                        try:
                            await paper_trading.place_order(
                                symbol=pos.symbol,
                                side="sell",
                                quantity=pos.quantity,
                                price=current_price,
                                agent_id=pos.agent_id,
                            )
                            logger.info(f"SL/TP exit executed: sold {pos.quantity} {pos.symbol} @ ${current_price:.2f}")

                            # Update agent metrics
                            pnl = (current_price - entry) * pos.quantity
                            if pos.agent_id and pos.agent_id in self._agent_metrics:
                                m = self._agent_metrics[pos.agent_id]
                                m.total_runs += 1
                                if pnl > 0:
                                    m.successful_runs += 1
                                m.total_pnl += pnl
                                m.win_rate = m.successful_runs / max(m.total_runs, 1)

                            # Log to team chat
                            from app.services.team_chat import team_chat
                            exit_type = "trailing-stop" if "Trailing" in check.reason else (
                                "take-profit" if "Take-profit" in check.reason else "stop-loss"
                            )
                            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                            await team_chat.add_message(
                                agent_role="execution_coordinator",
                                content=f"📊 **{exit_type.upper()}** {pos.symbol}: {pnl_str} ({check.reason})",
                                message_type="trade",
                            )

                        except Exception as e:
                            logger.error(f"Failed to execute SL/TP exit for {pos.symbol}: {e}")

                except Exception as e:
                    logger.debug(f"Position monitor error for {pos.symbol}: {e}")

        except Exception as e:
            logger.error(f"Position monitoring failed: {e}")

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
            daily_pnl = risk_manager.get_daily_pnl()

            # Calculate real total capital (USDT balance + value of all positions)
            try:
                balances = await paper_trading.get_all_balances()
                usdt_balance = next((b.available for b in balances if b.asset == "USDT"), 10000.0)
                positions_value = sum(
                    p.get("quantity", 0) * p.get("current_price", p.get("entry_price", 0))
                    for p in current_positions
                )
                total_capital = usdt_balance + positions_value
            except Exception:
                total_capital = 10000.0

            # 1. Research Analyst: Multi-symbol market analysis
            try:
                analyst_report = await research_analyst.analyze_markets()
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

                # Gather unique trading symbols across all agents for TA confluence
                all_symbols = set()
                for a in agents_list:
                    pairs = a.get("trading_pairs") or a.get("config", {}).get("trading_pairs", [])
                    all_symbols.update(pairs)
                if not all_symbols:
                    all_symbols = {"BTCUSDT"}

                # Get Technical Analyst confluence scores so FM can reconcile signals
                try:
                    confluence_scores = await technical_analyst.get_confluence_scores(list(all_symbols))
                    logger.info(f"Team Tier: TA confluence for {len(confluence_scores)} symbols")
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
            except Exception as e:
                logger.error(f"Team Tier: Strategy review failed: {e}")

            # 3. Risk Manager: Portfolio-level risk check with real positions + P&L
            try:
                risk_assessment = await risk_manager.generate_risk_assessment(
                    current_positions=current_positions,
                    daily_pnl=daily_pnl,
                    total_capital=total_capital,
                )
                self._current_risk_assessment = risk_assessment
                logger.info(f"Team Tier: Risk assessment - Level: {risk_assessment.risk_level}, "
                           f"Daily PnL: ${(risk_assessment.daily_pnl or 0):+.2f}")
                await team_chat.log_risk_assessment(risk_assessment)
            except Exception as e:
                logger.error(f"Team Tier: Risk manager failed: {e}")

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
            except Exception as e:
                logger.error(f"Team Tier: CIO report failed: {e}")

        except Exception as e:
            logger.error(f"Team analysis tier failed: {e}")

    async def _maybe_generate_daily_report(self):
        """Generate a daily report every hour (updates with latest data)."""
        try:
            now = datetime.now()
            if self._last_daily_report and \
               (now - self._last_daily_report).total_seconds() < 3600:
                return  # already ran within the last hour

            logger.info("Generating daily report snapshot")
            await daily_report_service.generate_daily_report(force=True)
            self._last_daily_report = now

            await team_chat.add_message(
                agent_role="cio",
                content=f"Daily report for {now.strftime('%Y-%m-%d')} has been updated with the latest fund metrics.",
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
        except Exception as e:
            logger.error(f"Daily email failed: {e}")

    async def _run_enabled_agents(self):
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

                result = await self.run_agent(
                    agent_id=config['id'],
                    name=config.get('name', ''),
                    strategy_type=config.get('strategy_type', 'momentum'),
                    trading_pairs=config.get('trading_pairs', []),
                    allocation_pct=allocation_pct,  # <-- NOW DYNAMIC from Portfolio Manager
                    max_position=config.get('max_position_size', 0.1),
                    stop_loss_pct=config.get('stop_loss_pct', 2.0),
                    take_profit_pct=config.get('take_profit_pct', 4.0),
                    trailing_stop_pct=config.get('trailing_stop_pct'),
                    use_paper=True
                )

                config['_last_run'] = datetime.now()

            except Exception as e:
                logger.error(f"Error running agent {agent_id}: {e}")
    
    async def run_agent(
        self,
        agent_id: str,
        name: str,
        strategy_type: str,
        trading_pairs: List[str],
        allocation_pct: float,
        max_position: float,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 4.0,
        trailing_stop_pct: Optional[float] = None,
        use_paper: bool = True
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
        
        symbol = trading_pairs[0]
        
        try:
            klines = await self.phemex.get_klines(symbol, "1h", 200)
            
            data = klines.get('data', klines) if isinstance(klines, dict) else klines
            
            if not data or len(data) < 50:
                return AgentRun(
                    agent_id=agent_id,
                    timestamp=timestamp,
                    symbol=symbol,
                    signal="hold",
                    confidence=0,
                    price=0,
                    executed=False,
                    error="Insufficient market data"
                )
            
            import pandas as pd
            df_data = []
            for k in data:
                df_data.append({
                    'time': k[0] / 1000,
                    'open': float(k[2]),
                    'high': float(k[3]),
                    'low': float(k[4]),
                    'close': float(k[5]),
                    'volume': float(k[7]),
                })
            
            df = pd.DataFrame(df_data)
            df = df.sort_values('time')
            
            if strategy_type == 'ai':
                from app.services.llm import llm_service
                indicators_dict = {
                    'rsi': float(self.indicator_service.calculate_rsi(df['close']).iloc[-1]) if len(df) >= 14 else None,
                    'macd': float(self.indicator_service.calculate_macd(df['close']).iloc[-1]['macd']) if len(df) >= 26 else None,
                    'bb_upper': float(self.indicator_service.calculate_bollinger_bands(df['close']).iloc[-1]['upper']) if len(df) >= 20 else None,
                    'bb_lower': float(self.indicator_service.calculate_bollinger_bands(df['close']).iloc[-1]['lower']) if len(df) >= 20 else None,
                }
                llm_result = await llm_service.generate_signal(indicators_dict, {'current': float(df['close'].iloc[-1])})
                signal = llm_result.action
                confidence = llm_result.confidence
                reasoning = llm_result.reasoning
            else:
                signal_result = self.indicator_service.generate_signal(df, {'strategy': strategy_type})
                signal = signal_result.signal.value if signal_result.signal else 'hold'
                confidence = signal_result.confidence
            
            current_price = df['close'].iloc[-1]
            
            executed = False
            pnl = None
            
            if signal in ['buy', 'sell'] and confidence >= 0.3:
                balances = await paper_trading.get_all_balances()
                usdt_balance = next((b.available for b in balances if b.asset == "USDT"), 10000.0)
                quantity = (usdt_balance * allocation_pct / 100) / current_price

                if signal == 'sell':
                    positions = await paper_trading.get_positions(symbol)
                    held = next((p.quantity for p in positions if p.symbol == symbol), 0.0)
                    if held <= 0:
                        self._record_run(agent_id, symbol, signal, confidence, current_price, False)
                        return AgentRun(
                            agent_id=agent_id,
                            timestamp=timestamp,
                            symbol=symbol,
                            signal=signal,
                            confidence=confidence,
                            price=current_price,
                            executed=False,
                            error="No position to sell"
                        )
                    # Sell the full position (not a fraction)
                    quantity = held
                
                risk_config = RiskConfig(
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    max_daily_loss=5.0,
                    max_position_size=usdt_balance * allocation_pct / 100,
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
                
                technical_report = await technical_analyst.analyze(symbol)
                # Only veto if TA has OPPOSITE signal (not just different) with high confidence
                ta_signal = technical_report.overall_signal
                is_opposite = (signal == 'buy' and ta_signal == 'sell') or (signal == 'sell' and ta_signal == 'buy')
                if is_opposite and technical_report.confidence > 0.75:
                    logger.warning(f"Trade rejected by technical analyst: signal {signal} conflicts with TA {ta_signal} (conf: {technical_report.confidence})")
                    self._record_run(agent_id, symbol, signal, confidence, current_price, False, error=f"Technical analyst disagrees: {ta_signal}")
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
                    s_str = f"${nearest_support:,.0f}" if nearest_support is not None else "N/A"
                    r_str = f"${nearest_res:,.0f}" if nearest_res is not None else "N/A"
                    logger.info(f"Key levels - Support: {s_str}, Resistance: {r_str}")
                
                if risk_check.stop_loss_price:
                    tp_str = f"${risk_check.take_profit_price:.2f}" if risk_check.take_profit_price is not None else "N/A"
                    logger.info(f"Risk levels - SL: ${risk_check.stop_loss_price:.2f}, TP: {tp_str}")
                
                if use_paper and paper_trading._enabled:
                    try:
                        order = await paper_trading.place_order(
                            symbol=symbol,
                            side=signal,
                            quantity=quantity,
                            price=current_price,
                            agent_id=agent_id,
                            stop_loss_price=adjusted_sl,
                            take_profit_price=adjusted_tp,
                            trailing_stop_pct=trailing_stop_pct,
                        )
                        executed = True
                        sl_str = f"${adjusted_sl:.2f}" if adjusted_sl else "N/A"
                        tp_str = f"${adjusted_tp:.2f}" if adjusted_tp else "N/A"
                        logger.info(f"Paper trade executed: {signal} {quantity} {symbol} @ {current_price} | SL: {sl_str} TP: {tp_str}")
                    except Exception as e:
                        logger.error(f"Paper trade failed: {e}")
                elif not use_paper and settings.phemex_api_key and settings.phemex_api_secret:
                    try:
                        result = await self.phemex.place_order(
                            symbol=symbol,
                            side=signal,
                            quantity=quantity,
                            order_type="Market",
                            price=current_price
                        )
                        executed = True
                        logger.info(f"Real trade executed: {signal} {quantity} {symbol} @ {current_price}")
                    except Exception as e:
                        logger.error(f"Real trade failed: {e}")
            
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
        if metrics.successful_runs > 0:
            winning_trades = len([r for r in self._agent_runs if r.agent_id == agent_id and r.pnl and r.pnl > 0])
            metrics.win_rate = winning_trades / metrics.successful_runs

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
                    # Create a new agent in the DB
                    async with get_async_session() as db:
                        # Find the first user to own the agent
                        from app.models import User
                        user = await db.scalar(select(User).limit(1))
                        if not user:
                            result_msg = "No user found to own new agent"
                        else:
                            new_agent = DBAgent(
                                user_id=user.id,
                                name=action.target_agent_name or f"Auto-{action.strategy_type}",
                                strategy_type=action.strategy_type or "momentum",
                                config={
                                    "trading_pairs": [action.params.get("symbol", "BTCUSDT")],
                                    "auto_created": True,
                                    "created_by": "strategy_review",
                                },
                                is_enabled=True,
                                allocation_percentage=10.0,
                                max_position_size=1000.0,
                                risk_limit=100.0,
                            )
                            db.add(new_agent)
                            await db.commit()
                            await db.refresh(new_agent)

                            # Register for scheduling
                            agent_config = {
                                'id': new_agent.id,
                                'name': new_agent.name,
                                'strategy_type': new_agent.strategy_type,
                                'trading_pairs': [action.params.get("symbol", "BTCUSDT")],
                                'is_enabled': True,
                                'allocation_percentage': 10.0,
                                'max_position_size': 1000.0,
                                'risk_limit': 100.0,
                            }
                            self.register_agent(agent_config)
                            await self._bootstrap_from_backtest(agent_config)
                            result_msg = f"Created agent {new_agent.name} ({new_agent.id[:8]})"
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
                        result_msg = "No allocation change"

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
                            executed=bool(result_msg and "not found" not in result_msg.lower()),
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


agent_scheduler = AgentScheduler()
