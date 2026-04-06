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
        self._current_allocation: Dict[str, float] = {}
        self._current_risk_assessment = None
        self._current_analyst_report = None
        self._current_execution_plan = None
        self._current_cio_report = None
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    async def start(self):
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Agent scheduler started")
    
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

            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")

            await asyncio.sleep(60)

    async def _fetch_agents_from_db(self) -> List[dict]:
        """Fetch all agents from database for team tier decisions"""
        from app.models import DBAgent
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

            # 1. Research Analyst: Multi-symbol market analysis
            try:
                analyst_report = await research_analyst.analyze_markets()
                self._current_analyst_report = analyst_report
                logger.info(f"Team Tier: Analyst report - Market {analyst_report.market_regime.regime}, "
                           f"Sentiment: {analyst_report.market_regime.sentiment}")
            except Exception as e:
                logger.error(f"Team Tier: Research analyst failed: {e}")

            # 2. Portfolio Manager: Reallocation based on analyst + real agent performance
            try:
                market_condition = await fund_manager.analyze_market()
                portfolio_decision = await fund_manager.make_allocation_decision(
                    agents=agents_list,
                    agent_metrics=agent_metrics,
                    market_condition=market_condition,
                )
                self._current_allocation = portfolio_decision.allocation_pct
                logger.info(f"Team Tier: Portfolio manager updated allocation for {len(agents_list)} agents")
            except Exception as e:
                logger.error(f"Team Tier: Portfolio manager failed: {e}")

            # 3. Risk Manager: Portfolio-level risk check with real positions + P&L
            try:
                risk_assessment = await risk_manager.generate_risk_assessment(
                    current_positions=current_positions,
                    daily_pnl=daily_pnl,
                )
                self._current_risk_assessment = risk_assessment
                logger.info(f"Team Tier: Risk assessment - Level: {risk_assessment.risk_level}, "
                           f"Daily PnL: ${risk_assessment.daily_pnl:+.2f}")
            except Exception as e:
                logger.error(f"Team Tier: Risk manager failed: {e}")

            # 4. Execution Coordinator: Optimize order timing
            try:
                execution_plan = await execution_coordinator.optimize_execution_plan([])
                self._current_execution_plan = execution_plan
                logger.info(f"Team Tier: Execution coordinator - {execution_plan.pending_orders_count} pending orders")
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
            except Exception as e:
                logger.error(f"Team Tier: CIO report failed: {e}")

        except Exception as e:
            logger.error(f"Team analysis tier failed: {e}")

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
                        continue

                # GATE 2: Check if agent is within allocation from Portfolio Manager
                allocation_pct = self._current_allocation.get(agent_id, config.get('allocation_percentage', 10))
                if allocation_pct <= 0:
                    logger.info(f"Skipping agent {config.get('name')} ({agent_id}): "
                               f"allocation is 0% (disabled by portfolio manager)")
                    continue

                logger.info(f"Running automated agent: {config.get('name')} "
                           f"(allocation: {allocation_pct:.1f}%)")

                result = await self.run_agent(
                    agent_id=config['id'],
                    name=config.get('name', ''),
                    strategy_type=config.get('strategy_type', 'momentum'),
                    trading_pairs=config.get('trading_pairs', []),
                    allocation_pct=allocation_pct,  # <-- NOW DYNAMIC from Portfolio Manager
                    max_position=config.get('max_position_size', 0.1),
                    stop_loss_pct=config.get('stop_loss_pct', 2.0),
                    take_profit_pct=config.get('take_profit_pct', 4.0),
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
                    quantity = min(quantity, held)
                
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
                
                if risk_check.stop_loss_price:
                    logger.info(f"Risk levels - SL: ${risk_check.stop_loss_price:.2f}, TP: ${risk_check.take_profit_price:.2f}")
                
                if use_paper and paper_trading._enabled:
                    try:
                        order = await paper_trading.place_order(
                            symbol=symbol,
                            side=signal,
                            quantity=quantity,
                            price=current_price
                        )
                        executed = True
                        logger.info(f"Paper trade executed: {signal} {quantity} {symbol} @ {current_price}")
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


agent_scheduler = AgentScheduler()
