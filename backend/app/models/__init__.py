from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum, JSON, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum

from app.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="user", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="user", cascade="all, delete-orphan")
    balances = relationship("Balance", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    phemex_api_key = Column(String(255), nullable=False)
    phemex_api_secret = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="api_keys")


class TradingPair(Base):
    __tablename__ = "trading_pairs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    symbol = Column(String(20), unique=True, nullable=False)
    base_asset = Column(String(10), nullable=False)
    quote_asset = Column(String(10), nullable=False)
    is_enabled = Column(Boolean, default=True)
    min_quantity = Column(Float, default=0.001)
    max_quantity = Column(Float, default=1000000)
    tick_size = Column(Float, default=0.01)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    agents = relationship("AgentPair", back_populates="trading_pair", cascade="all, delete-orphan")
    klines = relationship("Kline", back_populates="trading_pair", cascade="all, delete-orphan")


class Trader(Base):
    """A competing trader in the fund, each backed by a different LLM."""
    __tablename__ = "traders"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False, unique=True)
    llm_provider = Column(String(50), nullable=False, default="openrouter")
    llm_model = Column(String(150), nullable=False)
    allocation_pct = Column(Float, default=33.3)
    is_enabled = Column(Boolean, default=True)
    config = Column(JSON, default=dict)
    performance_metrics = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    agents = relationship("Agent", back_populates="trader", cascade="all, delete-orphan")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    trader_id = Column(String(36), ForeignKey("traders.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(100), nullable=False)
    strategy_type = Column(String(50), nullable=False)
    config = Column(JSON, default=dict)
    is_enabled = Column(Boolean, default=True)
    allocation_percentage = Column(Float, default=10.0)
    max_position_size = Column(Float, default=1000.0)
    risk_limit = Column(Float, default=100.0)
    run_interval_seconds = Column(Integer, default=300)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="agents")
    trader = relationship("Trader", back_populates="agents")
    pairs = relationship("AgentPair", back_populates="agent", cascade="all, delete-orphan")
    signals = relationship("AgentSignal", back_populates="agent", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="agent", cascade="all, delete-orphan")


class AgentPair(Base):
    __tablename__ = "agent_pairs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=False)
    trading_pair_id = Column(String(36), ForeignKey("trading_pairs.id"), nullable=False)

    agent = relationship("Agent", back_populates="pairs")
    trading_pair = relationship("TradingPair", back_populates="agents")


class SignalType(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class AgentSignal(Base):
    __tablename__ = "agent_signals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=False)
    trading_pair_id = Column(String(36), ForeignKey("trading_pairs.id"), nullable=False)
    signal_type = Column(Enum(SignalType), nullable=False)
    confidence = Column(Float, default=0.0)
    price = Column(Float, nullable=False)
    indicators = Column(JSON, default=dict)
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    agent = relationship("Agent", back_populates="signals")


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    trader_id = Column(String(36), ForeignKey("traders.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(20), nullable=False)
    side = Column(Enum(OrderSide), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    phemex_order_id = Column(String(100), nullable=True)
    is_paper = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    filled_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="trades")
    agent = relationship("Agent", back_populates="trades")


class Position(Base):
    __tablename__ = "positions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    agent_id = Column(String(36), ForeignKey("agents.id"), nullable=True)
    symbol = Column(String(20), nullable=False)
    side = Column(Enum(OrderSide), nullable=False)
    quantity = Column(Float, default=0.0)
    entry_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)
    trailing_stop_pct = Column(Float, nullable=True)
    is_paper = Column(Boolean, default=True)
    phemex_order_id = Column(String(100), nullable=True)  # live order tracking {"pct_of_tp": 0.33, "close_pct": 0.33, "triggered": false}
    scale_out_levels = Column(Text, nullable=True)
    # Grid trading: link this position to a specific grid level
    grid_id = Column(String(36), nullable=True)
    grid_level_id = Column(String(36), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "agent_id", "symbol", name="uq_position_user_agent_symbol"),
    )


class Balance(Base):
    __tablename__ = "balances"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    asset = Column(String(10), nullable=False)
    available = Column(Float, default=0.0)
    locked = Column(Float, default=0.0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="balances")


class Kline(Base):
    __tablename__ = "klines"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    trading_pair_id = Column(String(36), ForeignKey("trading_pairs.id"), nullable=False)
    interval = Column(String(10), nullable=False)
    open_time = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)
    quote_volume = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    trading_pair = relationship("TradingPair", back_populates="klines")

    __table_args__ = (
        Index("idx_kline_pair_interval_time", "trading_pair_id", "interval", "open_time"),
    )


class AgentRunRecord(Base):
    __tablename__ = "agent_run_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    symbol = Column(String(20), nullable=False)
    signal = Column(String(10), nullable=False)
    confidence = Column(Float, default=0.0)
    price = Column(Float, default=0.0)
    executed = Column(Boolean, default=False)
    pnl = Column(Float, nullable=True)
    error = Column(Text, nullable=True)
    strategy_type = Column(String(50), nullable=True)
    use_paper = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_agent_run_agent_id_ts", "agent_id", "timestamp"),
    )


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


class AnalystReport(Base):
    """Research Analyst reports on market conditions and opportunities"""
    __tablename__ = "analyst_reports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    analyst_id = Column(String(50), default="ResearchAnalyst")
    market_analysis = Column(JSON, default=dict)  # {trend, volatility, rsi, momentum, recommendation}
    opportunities = Column(JSON, default=list)  # List of opportunities
    symbols_analyzed = Column(JSON, default=list)  # ["BTCUSDT", "ETHUSDT", ...]
    sector_leadership = Column(JSON, default=dict)  # {symbol: "leader"|"laggard"}
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_analyst_report_timestamp", "timestamp"),
    )


class PortfolioDecision(Base):
    """Portfolio Manager allocation decisions"""
    __tablename__ = "portfolio_decisions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decision_type = Column(String(50), nullable=False)  # "allocation", "rebalancing", "attribution"
    allocation = Column(JSON, default=dict)  # {agent_id: capital_amount}
    allocation_pct = Column(JSON, default=dict)  # {agent_id: percentage}
    reasoning = Column(Text, nullable=True)
    based_on_analyst_report_id = Column(String(36), ForeignKey("analyst_reports.id"), nullable=True)
    expected_return_pct = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_portfolio_decision_timestamp", "timestamp"),
    )


class RiskAssessmentRecord(Base):
    """Risk Manager portfolio risk assessments"""
    __tablename__ = "risk_assessments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    risk_level = Column(String(20), nullable=False)  # "safe", "caution", "danger"
    daily_pnl = Column(Float, nullable=False)
    portfolio_exposure = Column(Float, default=0.0)
    max_daily_loss_limit = Column(Float, default=5.0)
    exposure_pct_of_capital = Column(Float, default=0.0)
    largest_position_symbol = Column(String(20), nullable=True)
    largest_position_size = Column(Float, default=0.0)
    concentration_risk = Column(String(20), default="low")  # "low", "medium", "high"
    recommendations = Column(JSON, default=list)  # List of recommendation strings
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_risk_assessment_timestamp", "timestamp"),
    )


class ExecutionPlan(Base):
    """Execution Coordinator order execution plans"""
    __tablename__ = "execution_plans"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pending_orders_count = Column(Integer, default=0)
    execution_sequence = Column(JSON, default=list)  # List of order IDs in sequence
    priorities = Column(JSON, default=list)  # List of priority objects
    aggregate_slippage_estimate = Column(Float, default=0.0)
    recommended_action = Column(String(50), nullable=False)  # "execute_all", "batch_execute", "wait"
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_execution_plan_timestamp", "timestamp"),
    )


class CIOReport(Base):
    """CIO Agent fund health and performance reports"""
    __tablename__ = "cio_reports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    period = Column(String(20), nullable=False)  # "daily", "weekly", "monthly"
    fund_performance = Column(JSON, default=dict)  # {total_return_pct, total_pnl, win_rate, ...}
    agent_leaderboard = Column(JSON, default=list)  # Ranked agents with performance
    strategy_performance = Column(JSON, default=dict)  # {strategy_type: contribution_pct}
    risk_metrics = Column(JSON, default=dict)  # {risk_level, daily_pnl, ...}
    strategic_recommendations = Column(JSON, default=list)  # List of recommendations
    executive_summary = Column(Text, nullable=True)
    cio_sentiment = Column(String(50), nullable=False)  # "very_bullish" to "very_bearish"
    cio_reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_cio_report_timestamp", "timestamp"),
        Index("idx_cio_report_period", "period"),
    )


class AgentDecision(Base):
    """Team agent decisions for inter-agent visibility"""
    __tablename__ = "agent_decisions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    agent_id = Column(String(50), nullable=False)  # Agent that made the decision
    decision_type = Column(String(50), nullable=False)  # "signal", "risk_adjustment", "allocation_change"
    decision_data = Column(JSON, default=dict)  # Decision-specific data
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_agent_decision_agent_id_timestamp", "agent_id", "timestamp"),
    )


class TeamChatMessageRecord(Base):
    """Persisted team chat messages for conversation history."""
    __tablename__ = "team_chat_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(50), nullable=False)
    agent_name = Column(String(100), nullable=False)
    agent_role = Column(String(50), nullable=False)
    avatar = Column(String(10), default="🤖")
    content = Column(Text, nullable=False)
    message_type = Column(String(30), nullable=False)  # analysis, decision, warning, recommendation
    mentions = Column(JSON, default=list)
    extra_metadata = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_team_chat_created_at", "created_at"),
        Index("idx_team_chat_agent_role", "agent_role"),
    )


class DailyReport(Base):
    """End-of-day report aggregating all fund metrics and team discussions."""
    __tablename__ = "daily_reports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    report_date = Column(String(10), nullable=False, unique=True)  # YYYY-MM-DD
    generated_at = Column(DateTime(timezone=True), server_default=func.now())

    # Market conditions
    market_conditions = Column(JSON, default=dict)  # {regime, sentiment, volatility, top_opportunity, risk_level}

    # P&L metrics
    total_pnl = Column(Float, default=0.0)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    daily_return_pct = Column(Float, default=0.0)

    # Trade metrics
    trades_opened = Column(Integer, default=0)
    trades_closed = Column(Integer, default=0)
    total_buy_volume = Column(Float, default=0.0)
    total_sell_volume = Column(Float, default=0.0)
    open_positions_count = Column(Integer, default=0)

    # Team performance
    team_performance = Column(JSON, default=dict)  # {agent_id: {pnl, win_rate, signals, runs}}
    team_discussion_summary = Column(Text, nullable=True)
    team_message_count = Column(Integer, default=0)

    # Agent metrics
    agent_leaderboard = Column(JSON, default=list)  # [{agent_id, name, pnl, win_rate, rank}]
    best_agent_id = Column(String(36), nullable=True)
    worst_agent_id = Column(String(36), nullable=True)

    # Risk summary
    risk_summary = Column(JSON, default=dict)  # {avg_risk_level, danger_count, max_exposure_pct}

    # Portfolio state at end of day
    portfolio_value = Column(Float, default=0.0)
    portfolio_balances = Column(JSON, default=dict)  # {asset: amount}

    # CIO commentary
    cio_sentiment = Column(String(50), nullable=True)
    cio_summary = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_daily_report_date", "report_date"),
    )


class BacktestRecord(Base):
    """Persisted backtest results for historical tracking and strategy evaluation."""
    __tablename__ = "backtest_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(20), nullable=False)
    strategy = Column(String(50), nullable=False)
    interval = Column(String(10), default="1h")

    # Config params
    config_params = Column(JSON, default=dict)  # {initial_balance, position_size_pct, stop_loss_pct, ...}

    # Results
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    net_pnl = Column(Float, default=0.0)
    total_fees = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    avg_trade_pnl = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)

    # Extended data (stored as JSON for flexibility)
    equity_curve = Column(JSON, default=list)
    trades_data = Column(JSON, default=list)  # individual trade records

    # Source context
    source = Column(String(30), default="manual")  # manual, bootstrap, optimization, strategy_review
    candle_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_backtest_agent_id", "agent_id"),
        Index("idx_backtest_strategy", "strategy"),
        Index("idx_backtest_created_at", "created_at"),
    )


class StrategyAction(Base):
    """Log of automated strategy actions taken by fund manager + technical analyst cooperation."""
    __tablename__ = "strategy_actions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    action = Column(String(30), nullable=False)  # create_agent, disable_agent, enable_agent, adjust_params
    target_agent_id = Column(String(36), nullable=True)
    target_agent_name = Column(String(100), nullable=True)
    strategy_type = Column(String(50), nullable=True)
    params = Column(JSON, default=dict)
    rationale = Column(Text, nullable=True)
    initiated_by = Column(String(50), default="fund_manager")  # fund_manager, technical_analyst, cio
    confluence_score = Column(Float, nullable=True)
    backtest_net_pnl = Column(Float, nullable=True)
    executed = Column(Boolean, default=False)
    execution_result = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_strategy_action_created_at", "created_at"),
        Index("idx_strategy_action_action", "action"),
    )


class WhaleAddress(Base):
    """Watchlist of Hyperliquid whale addresses to track."""
    __tablename__ = "whale_addresses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    address = Column(String(100), unique=True, nullable=False)
    label = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    snapshots = relationship("WhaleSnapshot", back_populates="whale", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_whale_address_active", "is_active"),
    )


class WhaleSnapshot(Base):
    """Point-in-time snapshot of a whale's positions on Hyperliquid."""
    __tablename__ = "whale_snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    whale_id = Column(String(36), ForeignKey("whale_addresses.id", ondelete="CASCADE"), nullable=False)
    captured_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    positions = Column(JSON, default=list)
    account_value = Column(Float, nullable=True)
    total_notional = Column(Float, default=0.0)
    symbols_active = Column(JSON, default=list)

    whale = relationship("WhaleAddress", back_populates="snapshots")

    __table_args__ = (
        Index("idx_whale_snapshot_whale_captured", "whale_id", "captured_at"),
        Index("idx_whale_snapshot_captured_at", "captured_at"),
    )


from app.models import User, ApiKey, TradingPair, Trader, Agent, AgentPair, AgentSignal, Trade, Position, Balance, Kline
from app.models import SignalType, OrderSide, OrderStatus
from app.models import AgentRunRecord, AgentMetricRecord
from app.models import AnalystReport, PortfolioDecision, RiskAssessmentRecord, ExecutionPlan, CIOReport, AgentDecision
from app.models import TeamChatMessageRecord, DailyReport
from app.models import BacktestRecord, StrategyAction
from app.models import WhaleAddress, WhaleSnapshot


class AppSetting(Base):
    """Key-value store for persistent application settings."""
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Grid Trading Models ─────────────────────────────────────────────────────

class GridStatus(enum.Enum):
    active = "active"
    paused = "paused"
    cancelled = "cancelled"
    completed = "completed"


class GridLevelStatus(enum.Enum):
    pending = "pending"        # not yet placed
    open = "open"              # order placed, awaiting fill
    filled = "filled"          # position is open at this level
    counter_placed = "counter_placed"  # counter-order placed after fill
    closed = "closed"          # level fully closed with P&L realised


class GridState(Base):
    """One active grid per (agent, symbol). Tracks the overall grid lifecycle."""
    __tablename__ = "grid_states"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(20), nullable=False)
    status = Column(Enum(GridStatus), default=GridStatus.active, nullable=False)

    # Grid geometry
    grid_low = Column(Float, nullable=False)       # bottom of range
    grid_high = Column(Float, nullable=False)      # top of range
    grid_levels = Column(Integer, nullable=False)  # number of levels
    grid_spacing_pct = Column(Float, nullable=False)  # % between levels
    current_price_at_creation = Column(Float, nullable=False)
    regime_atr = Column(Float, nullable=True)       # ATR at creation (drift baseline)

    # Financial tracking
    total_invested = Column(Float, default=0.0)    # capital currently deployed
    realized_pnl = Column(Float, default=0.0)      # closed-level profits
    cancel_reason = Column(Text, nullable=True)    # populated when cancelled

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_grid_states_agent_symbol", "agent_id", "symbol"),
    )


class GridLevel(Base):
    """One row per price level within a grid."""
    __tablename__ = "grid_levels"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    grid_id = Column(String(36), ForeignKey("grid_states.id", ondelete="CASCADE"), nullable=False)
    level_index = Column(Integer, nullable=False)    # 0 = lowest level
    price = Column(Float, nullable=False)            # target price for this level
    side = Column(Enum(OrderSide), nullable=False)   # buy at low levels, sell at high
    status = Column(Enum(GridLevelStatus), default=GridLevelStatus.pending, nullable=False)
    quantity = Column(Float, nullable=False)

    # Populated on fill
    position_id = Column(String(36), nullable=True)  # links to positions.id
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_grid_levels_grid_id", "grid_id"),
    )



class StrategyOverride(Base):
    """
    Runtime overrides for strategy definitions sourced from registry.yaml.
    Only stores values that differ from YAML  the registry loaderdefaults 
    merges these on top of the base YAML definition at request time.
    """
    __tablename__ = "strategy_overrides"

    strategy_type = Column(String(64), primary_key=True)   # matches registry.yaml key
    enabled = Column(Boolean, default=True, nullable=False)
    display_order = Column(Integer, nullable=True)          # UI sort order override
    default_stop_loss_pct = Column(Float, nullable=True)    # None = use YAML default
    default_take_profit_pct = Column(Float, nullable=True)
    default_trailing_stop_pct = Column(Float, nullable=True)
    default_timeframe = Column(String(16), nullable=True)
    notes = Column(Text, nullable=True)                     # admin notes / reason for override

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
