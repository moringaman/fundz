# Phemex AI Trader - Implementation Plan

## Project Overview

An AI-powered crypto trading application that uses technical indicators (RSI, Bollinger Bands, Moving Averages, MACD) to automate trading decisions on Phemex exchange.

### Tech Stack
- **Frontend**: React + TypeScript + Vite
- **UI**: Radix UI, shadcn/ui, lightweight-charts
- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL + SQLAlchemy
- **Data**: Phemex REST API + WebSocket
- **ML/Indicators**: pandas, ta-lib

---

## Phase 1: Foundation & Infrastructure

### 1.1 Project Setup
- [ ] Initialize project structure (frontend/, backend/, docs/)
- [ ] Set up Docker Compose (frontend, backend, postgres, redis)
- [ ] Configure environment variables (.env.example)
- [ ] Set up logging (structured JSON logs)
- [ ] Create .gitignore with proper Python/Node exclusions

### 1.2 Database Schema
- [ ] Design SQLAlchemy models:
  - `User` (id, api_key, api_secret, created_at)
  - `TradingPair` (symbol, base, quote, enabled)
  - `Agent` (id, name, strategy_type, config_json, enabled)
  - `Trade` (id, agent_id, symbol, side, quantity, price, status, created_at)
  - `Position` (id, agent_id, symbol, side, quantity, entry_price, current_pnl)
  - `Balance` (user_id, asset, available, locked, updated_at)
  - `AgentSignal` (agent_id, symbol, signal_type, confidence, created_at)
- [ ] Run migrations
- [ ] Seed initial data

### 1.3 Backend Core
- [ ] FastAPI app setup with CORS
- [ ] Database connection (SQLAlchemy + asyncpg)
- [ ] User authentication (API key management)
- [ ] Health check endpoint
- [ ] Error handling middleware

**Deliverable**: Running backend with DB, health endpoint responds

---

## Phase 2: Phemex Integration

### 2.1 API Client
- [ ] Phemex REST client wrapper
  - Authentication (HMAC-SHA256)
  - Rate limiting
  - Retry logic with exponential backoff
- [ ] WebSocket client for real-time data
  - Connection management
  - Auto-reconnection
  - Heartbeat/ping-pong

### 2.2 Market Data Endpoints
- [ ] GET /api/market/klines - Historical klines
- [ ] GET /api/market/ticker - 24hr ticker
- [ ] GET /api/market/orderbook - Order book depth
- [ ] GET /api/market/trades - Recent trades
- [ ] WebSocket /ws/market - Live kline stream

### 2.3 Trading Endpoints
- [ ] POST /api/trading/order - Place order
- [ ] DELETE /api/trading/order/:id - Cancel order
- [ ] GET /api/trading/orders - Open orders
- [ ] GET /api/trading/positions - Open positions
- [ ] GET /api/trading/balance - Account balance

### 2.4 Sync Service
- [ ] Background task to sync balances on startup
- [ ] WebSocket to update balances in real-time
- [ ] Position tracking (entry price, P&L)

**Deliverable**: Backend can fetch data and execute test trades

---

## Phase 3: Technical Indicators & Features

### 3.1 Feature Engine
- [ ] Calculate RSI (14-period default)
- [ ] Calculate Bollinger Bands (20, 2 std)
- [ ] Calculate Moving Averages (SMA 20, 50, 200)
- [ ] Calculate MACD (12, 26, 9)
- [ ] Calculate Volume SMA
- [ ] Calculate ATR (Average True Range)

### 3.2 Signal Generator
- [ ] RSI signals (oversold < 30, overbought > 70)
- [ ] Bollinger Band signals (breakout, mean reversion)
- [ ] MA crossover signals (golden cross, death cross)
- [ ] MACD divergence signals
- [ ] Combined signal confidence score

### 3.3 Data Storage
- [ ] Store historical klines in PostgreSQL
- [ ] Backfill historical data on startup
- [ ] Incremental updates via WebSocket
- [ ] Data retention policy (configurable)

**Deliverable**: Feature engine computes all indicators, signals generated

---

## Phase 4: Agent System

### 4.1 Agent Framework
- [ ] Base Agent class
- [ ] Agent registry (available strategies)
- [ ] Agent configuration UI model

### 4.2 Agent Types

#### Rule-Based Agent
- [ ] Define trading rules (configurable thresholds)
- [ ] Example: "Buy when RSI < 30 AND price near BB lower"
- [ ] Configurable parameters per agent

#### ML Agent (Optional Phase 4b)
- [ ] Feature extraction pipeline
- [ ] Train model on historical data
- [ ] Inference endpoint for signals

### 4.3 Agent Execution
- [ ] Background task running each agent
- [ ] Configurable interval (1m, 5m, 15m, 1h)
- [ ] Signal generation on schedule
- [ ] Auto-trade option (with confirmation toggle)

### 4.4 Risk Management
- [ ] Max position size per agent
- [ ] Daily loss limit
- [ ] Max open orders
- [ ] Emergency stop (API key rotation recommended)

**Deliverable**: Agents run on schedule, generate tradeable signals

---

## Phase 5: Frontend Dashboard

### 5.1 Project Setup
- [ ] Initialize Vite + React + TypeScript
- [ ] Set up Tailwind CSS
- [ ] Install dependencies (radix, lightweight-charts, etc.)
- [ ] Configure shadcn/ui

### 5.2 Layout & Navigation
- [ ] Dashboard layout (sidebar, header, main content)
- [ ] Responsive design
- [ ] Theme toggle (light/dark)

### 5.3 Pages

#### Dashboard Home
- [ ] Portfolio summary (total balance, P&L)
- [ ] Active positions widget
- [ ] Recent trades widget
- [ ] Agent status overview

#### Trading Pairs
- [ ] List available trading pairs
- [ ] Enable/disable pairs for trading
- [ ] View 24hr stats

#### Agents
- [ ] Agent list with status
- [ ] Create/edit agent form
  - Select strategy type
  - Choose trading pairs
  - Set allocation percentage
  - Configure indicators & thresholds
- [ ] View agent signals history
- [ ] Enable/disable agents

#### Trading
- [ ] Live chart (lightweight-charts)
  - Candlestick + volume
  - Indicator overlays (BB, MA)
  - Trade markers
- [ ] Manual order entry
- [ ] Open orders list
- [ ] Trade history

#### Wallet/Balances
- [ ] Account balances by asset
- [ ] Available vs locked breakdown
- [ ] P&L history chart
- [ ] Deposit/withdraw (info only, no on-chain)

#### Settings
- [ ] API key management
- [ ] Risk limits configuration
- [ ] Notification preferences
- [ ] Theme settings

### 5.4 Real-time Updates
- [ ] WebSocket connection manager
- [ ] Live price updates on charts
- [ ] Order/balance auto-refresh

**Deliverable**: Fully functional React dashboard

---

## Phase 6: Testing & Deployment

### 6.1 Backend Tests
- [ ] Unit tests for indicators
- [ ] Unit tests for signal generation
- [ ] API endpoint tests (pytest)
- [ ] Mock Phemex API responses

### 6.2 Frontend Tests
- [ ] Component tests
- [ ] Integration tests
- [ ] E2E tests with Playwright

### 6.3 Deployment
- [ ] Docker build optimization
- [ ] Environment configs (dev, staging, prod)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Production deployment (Railway/Render/VPS)

### 6.4 Monitoring
- [ ] API request logging
- [ ] Error tracking (Sentry)
- [ ] Health dashboards

---

## Phase 7: Advanced (Future)

- [ ] Paper trading mode
- [ ] Multi-exchange support
- [ ] ML model training interface
- [ ] Telegram/Slack notifications
- [ ] Mobile app
- [ ] Portfolio rebalancing

---

## Implementation Order Summary

| Phase | Focus | Key Files |
|-------|-------|-----------|
| 1 | Infrastructure | docker-compose.yml, backend/main.py, models/ |
| 2 | Phemex API | clients/phemex.py, routes/market.py, routes/trading.py |
| 3 | Indicators | services/indicators.py, services/signals.py |
| 4 | Agents | services/agents/, background_tasks/ |
| 5 | Frontend | frontend/src/pages/*, components/ |
| 6 | Testing | tests/, e2e/, deploy/ |

---

## Success Criteria

- [ ] Can fetch historical + real-time market data
- [ ] Indicators calculate correctly
- [ ] Agents generate signals automatically
- [ ] Can execute trades via UI
- [ ] Dashboard shows live P&L
- [ ] Paper trading works before live money

---

## Phase 7: Multi-Trader Fund Architecture (Roadmap)

Introduce a **Trader** layer between Fund Manager and Agents. Three competing traders, each backed by a different LLM, managing their own agents and strategies. The Fund Manager allocates capital to traders based on performance.

```
CIO (strategic oversight)
  └─ Fund Manager (allocates capital to TRADERS)
       ├─ Trader 1 "Alpha" (Claude)   — own agents, own capital pool
       ├─ Trader 2 "Beta"  (GPT-4o)   — own agents, own capital pool
       └─ Trader 3 "Gamma" (Gemini)   — own agents, own capital pool
```

### 7.1 DB Schema & Trader Model
- [ ] `Trader` model (id, name, llm_provider, llm_model, allocation_pct, is_enabled, config, performance_metrics)
- [ ] Add `trader_id` FK to `Agent` and `PaperOrder` models
- [ ] Alembic migration
- [ ] Seed 3 default traders (Alpha/Claude, Beta/GPT-4o, Gamma/Gemini)

### 7.2 Trader Service
- [ ] `trader_service.py` — each trader has own LLM instance
- [ ] `manage_agents()` — trader's own strategy review cycle
- [ ] `generate_signals()` — trader decides which agents to run and when
- [ ] `get_performance()` — aggregate P&L, win rate, Sharpe per trader
- [ ] Capital-aware position sizing from trader's allocated budget

### 7.3 Fund Manager → Trader Allocation
- [ ] Refactor `make_allocation_decision()` to allocate to traders (not agents)
- [ ] Traders sub-allocate to their own agents within their budget
- [ ] Min 15% / max 50% per trader; rebalancing every 20 min

### 7.4 Scheduler Integration
- [ ] Each trader runs own strategy review with own LLM
- [ ] Agent execution uses trader's allocated capital
- [ ] Trader-level risk checks before agent-level
- [ ] `_enabled_agents` grouped by `trader_id`

### 7.5 API & Frontend
- [ ] CRUD endpoints: `/api/traders`
- [ ] Trader performance endpoint
- [ ] Dashboard: trader leaderboard card
- [ ] Agents page: group agents by trader
- [ ] Fund Team page: per-trader performance panel
- [ ] Settings: trader config (LLM model, risk limits)

### 7.6 Migration & Rollout
- [ ] Existing agents assigned to Trader 1 (Alpha)
- [ ] Traders 2 & 3 start empty, auto-create agents via strategy review
- [ ] All existing endpoints backward compatible
- [ ] Start with equal 33% allocation, let FM optimize


---

## Phase  Full Grid Trading Engine8 

### Background
The current `grid` strategy type uses Bollinger Bands to approximate ranging-market entry/exit levels but generates only one signal per run. A proper grid trader requires persistent multi-level order management.

### 8.1 Grid State Model
- [ ] `GridState` DB model (id, agent_id, symbol, grid_low, grid_high, grid_levels, grid_spacing_pct, active_orders JSON, created_at, updated_at)
- [ ] One row per active grid per symbol per agent
- [ ] Track which grid levels are occupied (open position at that level)

### 8.2 Grid Engine Service
- [ ] `grid_engine.py` service
- [ ] `initialise_grid(symbol, current_price, atr, n_levels= compute grid bounds and level prices using ATR-based spacing10)` 
- [ ] `get_next_action(grid_state, current_ return the next unfilled buy/sell level(s)price)` 
- [ ] `on_fill(grid_state, level,  mark level occupied, place counter-order at next levelside)` 
- [ ] `rebalance_grid(grid_state, current_ shift grid if price breaks out of rangeprice)` 
- [ ] Grid cancellation if trend detected (ATR spike or SMA crossover)

### 8.3 Scheduler Integration
- [ ] Separate execution path in `run_agent()` for `strategy_type == "grid"`
- [ ] Load/create `GridState` on first run for each (agent, symbol) pair
- [ ] Place multiple orders per cycle at unfilled levels (up to N open orders)
- [ ] Track each level's open position independently (not via global `_current_allocation`)
- [ ] Grid-specific SL = grid_low - buffer; TP per-level = next level up

### 8.4 Risk Controls
- [ ] Max capital per grid = agent's allocation% of fund
- [ ] Max concurrent grid levels = configurable (default 5)
- [ ] Auto-pause grid if daily loss limit reached
- [ ] Auto-cancel grid if market regime shifts to strongly trending (Risk Manager signal)
- [ ] James / traders informed of active grids in allocation prompt context

### 8.5 Frontend
- [ ] Grid visualisation on History / Trading page (show level lines on chart)
- [ ] Grid status panel on Strategies page (active levels, filled %, P&L per grid)
- [ ] Trader can assign a strategy to "grid mode" for specific pairs in ranging conditions

---

## Phase 9: Advanced Trader Performance & Risk Psychology

### Background
Institutional-grade improvements to how traders are rewarded, penalised, and constrained. Designed to replicate real fund incentive structures — consistency over luck, consequences for drawdown, and market-neutral exposure management.

### 9.1 Incentive Alignment — Consistency-Gated Capital

**The 40% Rule**: No single trade can account for >40% of a trader's total period profit, or they are flagged as INCONSISTENT and capital unlock is blocked.

**Sharpe Gating**: Capital can only increase when live rolling Sharpe ≥ 1.0. Below 0.5 triggers a reduction step.

- [ ] Create `backend/app/services/consistency_scorer.py`
  - `compute_consistency_score(trader_id)` — `1 - (max_single_trade_pnl / total_pnl)`
  - `check_40_percent_rule(trader_id)` — returns flag + offending trade
  - `compute_live_sharpe(trader_id, window=20)` — rolling Sharpe from `AgentRunRecord` pnl values
- [ ] Update `compute_trader_allocations()` in `trader_service.py`
  - Add consistency score as 4th factor (15% weight)
  - Hard block capital increase if `consistency_flag == "INCONSISTENT"`
  - Apply Sharpe gating multiplier: `{<0.5: 0.7, 0.5–1.0: 1.0, >=1.0: 1.3}`
- [ ] Add consistency flag warning to team chat when triggered
- [ ] Show consistency score badge on `TradersPage.tsx` (green/amber/red)
- [ ] Add consistency breakdown panel in trader detail view

### 9.2 Survival Bias — Drawdown Warnings & Trader "Firing"

Traders are terminated at -10% lifetime drawdown. A 3-tier warning system creates escalating pressure on the LLM's context window.

| Level | Drawdown | Response |
|-------|----------|----------|
| CAUTION | -5% | Elena warns in team chat |
| WARNING | -7% | "Pink Slip" injected into prompt; capital cut 50%; Telegram alert |
| TERMINATED | -10% | Trader disabled; history snapshotted; successor spawned |

**Evolution Loop**: Terminated traders leave a `TraderLegacy` record. Their successor receives an LLM-generated "What Not To Do" summary of the 5 worst trades — evolutionary pressure toward better strategies over time.

- [ ] Add `lifetime_peak_balance`, `lifetime_drawdown_pct`, `drawdown_warning_level` to `Trader` DB model
- [ ] Create `TraderLegacy` DB model (snapshot on termination)
- [ ] Write Alembic migration
- [ ] Create `backend/app/services/drawdown_monitor.py`
  - `update_trader_drawdown(trader_id)` — recalculates from closed trades
  - `check_warning_tiers(trader_id)` — returns tier, triggers side effects
  - `terminate_trader(trader_id)` — disables, snapshots to `TraderLegacy`, spawns successor
  - `spawn_successor(from_trader_id)` — new trader with evolution context in system prompt
- [ ] Wire `update_trader_drawdown()` into `agent_scheduler._record_run()` after each trade
- [ ] Inject Pink Slip warning text into trader system prompt when `warning_level == "warning"`
- [ ] Elena team chat messages at CAUTION and WARNING tiers
- [ ] Telegram alerts at WARNING and TERMINATED tiers
- [ ] Drawdown warning badges on `TradersPage.tsx` (skull icon at WARNING, greyed at TERMINATED)
- [ ] Evolution lineage in trader detail panel ("Successor of [name]")

### 9.3 Market Neutrality — Pair Trade Hedging

Protect the fund from crypto-wide drawdowns by enforcing hedge pairs and a macro hedge mode.

**Default hedge pairs**:
| Long | Hedge Short | Rationale |
|------|-------------|-----------|
| SOL, AVAX, MATIC | BTC or ETH | High altcoin-BTC correlation (0.7+) |
| UNI, AAVE, SUSHI | ETH | DeFi sector exposure |
| Spot asset | Perp future same asset | Delta-neutral, collect funding rate |

**Elena's 60% Rule**: If a trader's net directional exposure exceeds 60% long or short, Elena requires a hedge proposal or auto-reduces new position size by 40%.

**Macro Hedge Mode**: BTC 24h decline >5% triggers fund-wide requirement for a companion short on all new long positions.

- [ ] Create `backend/app/services/hedge_monitor.py`
  - `HEDGE_PAIRS` config dict
  - `compute_net_directional_exposure(trader_id)`
  - `check_hedge_requirement(trader_id)` — returns `{required, current_bias, message}`
  - `get_recommended_hedge(long_asset)`
  - `is_macro_hedge_mode()` — checks BTC 24h change
- [ ] Add `pair_trade_suggestion` field to `TechnicalAnalysis` response model
- [ ] Update `research_analyst.py` to include correlation-based pair trade suggestions
- [ ] Update `risk_manager.check_trade()` with hedge enforcement:
  - Reduce `allowed_quantity` by 40% when hedge required (soft enforcement)
  - Block new longs in MACRO_HEDGE_MODE unless companion short provided
- [ ] Add `pair_long_short` signal type to agent signal validation
- [ ] Update `agent_scheduler.run_agent()` to execute both legs of a pair trade atomically
- [ ] Update trader system prompts with pair trade vocabulary and examples
- [ ] Add hedge requirement warning to Elena's team chat pre-approval message
- [ ] `DashboardPage.tsx`: red "Macro Hedge Mode" banner when active
- [ ] `TradersPage.tsx`: show net directional exposure % per trader
- [ ] `HistoryPage.tsx`: show pair trade legs as linked rows

---

## Phase 10: Long-Term Accumulation Book

### Background
All current strategies use 1-hour candles with tight % stop-losses and single-entry position  unsuitable for macro accumulation. A long-term book requires its own capital ring-fence, wider drawdown tolerance, tranche-based DCA entries, and liquidation-safe position sizing. It runs alongside the active trading book without competing for the same capital.sizing 

### Core Principles
- **Separate capital bucket**: Configurable % of fund ring-fenced for accumulation (default 30%)20
- **Tranche-based entry**: Each position enters in N tranches (default 4), not all at once
- **DCA on dips**: Next tranche triggered when price drops X% below the previous entry (default 8%)
- **No % stop-losses**: Exits based on thesis invalidation (e.g., price closes below 200-week MA or macro reversal signal), not tight price targets
- **Wide drawdown tolerance**: Accumulation positions can tolerate -30% to -50% drawdown; this is set independently from the active trading drawdown limits
- **Liquidation-safe sizing**: Tranche size calculated so that liquidation price 50% below entry (for leveraged positions). Spot-only mode available (no liquidation possible)is 
- **Long horizon exits**: Macro profit targets (+50%, +100%, +200%), not short-term TPs
- **Higher timeframe TA**: Weekly and daily candles for entry signals, not 1h

---

### 10.1 Accumulation Capital Pool

The accumulation book is a ring-fenced capital allocation, funded at startup and replenished from active trading profits above a high-water mark.

- [ ] Add `accumulation_pct` field to `TradingPreferences` settings (default 25%)
- [ ] Add `AccumulationBalance` tracking in `PaperBalance` or as a separate JSON in settings:
  - `total_accumulation_capital`, `deployed_capital`, `reserved_capital`
- [ ] Fund Manager (`fund_manager.py`) is aware of accumulation  does NOT allocate short-term agents against itpool 
- [ ] Accumulation capital is replenished by transferring X% of active trading profits when fund PnL exceeds high-water mark
- [ ] Settings page: accumulation capital %, high-water mark replenishment toggle, max assets in accumulation book

---

### 10.2 Accumulation Position Model & Engine

- [ ] Add `AccumulationPosition` DB model (new table):
  - `id`, `symbol`, `asset_thesis` (text), `max_tranches` (int, default 4)
  - `tranches_deployed` (int), `avg_entry_price` (float)
  - `total_invested` (float), `current_value` (float)
  - `dca_trigger_pct` (float, default 8. % drop below last entry to trigger next tranche0) 
  - `tranche_size_usdt` ( fixed per-tranche size in USDTfloat) 
  - `target_1` / `target_2` / `target_3` ( macro exit targetsfloat) 
  - `invalidation_price` ( thesis invalidation level (NOT a % stop-loss)float) 
  - `leverage` (float, default 1.0 = spot)
  - `liquidation_price` (float,  calculated at entry if leverage > 1)nullable 
  - `status` (enum: `accumulating | paused | exiting | closed`)
  - `timeframe` (string, default ` candle timeframe for TA1w`) 
  - `trader_id` (FK), `created_at`, `updated_at`
- [ ] Create Alembic migration
- [ ] Create `backend/app/services/accumulation_engine.py`:
  - `open_accumulation(symbol, trader_id, thesis,  opens new accumulation position with tranche 1config)` 
  - `evaluate_dca_trigger( checks if price dropped enough to deploy next trancheposition)` 
  - `calculate_tranche_size(total_capital, max_tranches, current_ equal-weight by default, can be pyramid-weighted (smaller later tranches)tranche)` 
 enforce minimum 50% buffer
  - `check_invalidation(position, current_price, weekly_ returns True if thesis is brokencandles)` 
  - `evaluate_exit_targets(position, current_ checks macro targets, returns partial/full exit signalprice)` 
  - `get_higher_tf_signals(symbol,  fetches daily/weekly candles from `PhemexClient`timeframe)` 

---

### 10.3 Liquidation-Safe Position Sizing

This is the critical safety mechanism. Every accumulation entry must prove it won't be liquidated at any realistic price level.

**Sizing formula**:
```
tranche_size_usdt = accumulation_capital / max_tranches
quantity = tranche_size_usdt / entry_price
liquidation_price = entry_price * (1 - 1/leverage)   # for longs
required_liq_buffer = entry_price * 0.50              # must be 50%+ away
```
 **reduce leverage** until safe, or force spot.

- [ ] `validate_liq_safety(entry_price, leverage, quantity)` in `accumulation_engine. raises if unsafepy` 
- [ ] Auto-reduce leverage to make liq price safe (never allow unsafe entry)
- [ ] Store calculated `liquidation_price` on `AccumulationPosition` record
- [ ] Risk Manager (`risk_manager.py`) gains `check_accumulation_trade()` method with separate (wider) limits:
  - `max_drawdown_tolerance = 40%` (vs 10% for active trading)
  - No `max_open_positions` cap (accumulation runs indefinitely)
  - No daily loss check (accumulation ignores daily P&L swings)
  - Only hard gates: liq safety check + total accumulation capital % cap

---

### 10.4 Higher Timeframe Analysis

- [ ] Extend `research_analyst.py` to support multi-timeframe mode:
 fetches 1w + 1d candles (200 lookback)
  - Computes: 200-week MA, weekly RSI, long-term Bollinger Bands, volume profile
  - Returns accumulation signals: `strong_accumulation | accumulate | hold | reduce | exit`
- [ ] Weekly/daily signals used for thesis validation, NOT for short-term entry timing
- [ ] `accumulation_engine.check_invalidation()` uses weekly close below 200-week MA as default invalidation signal
- [ ] Add weekly/daily TA panel to `FundTeamPage.tsx` when accumulation positions exist

---

### 10.5 Scheduler Integration

- [ ] Add `_run_accumulation_cycle()` to `agent_scheduler.py`:
  - Runs on a slower schedule (every 4 hours, or configurable)
  - Iterates open `AccumulationPosition` records
 deploy next tranche if triggered
 close position if thesis broken
 partial/full exit at macro targets
- [ ] Accumulation cycle is independent of active trading  different cadence, different capital poolcycle 
- [ ] Accumulation positions appear in `Position` table with `position_type = "accumulation"` flag (new column) so they don't affect active risk calculations
- [ ] Trader can "sponsor" an accumulation  it appears in team chat as a long-term thesis proposal; Fund Manager approves/deniesidea 

---

### 10.6 Team Chat Integration

- [ ] Trader proposes accumulation in team chat: *"I want to accumulate SOL over 4 weeks. Thesis: Firedancer upgrade + institutional inflows. Invalidation: weekly close below $80. Target 1: $200, Target 2: $350."*
- [ ] Marcus provides weekly/daily TA confirmation or rebuttal
- [ ] Fund Manager approves capital allocation from accumulation pool
- [ ] Each DCA tranche fires a team chat message: *"Tranche 2/4 deployed for SOL accumulation at $145. Avg entry now $152. 2 tranches remain."*
- [ ] Telegram alert on: tranche deployed, invalidation triggered, target hit

---

### 10.7 Frontend

- [ ] **New "Accumulation" tab on `HistoryPage.tsx`** (alongside Paper/Live):
  - Shows open accumulation positions: symbol, tranches, avg entry, current price, unrealised P&L, targets, invalidation level
  - Shows DCA history: each tranche as a timeline row
  - Closed accumulation positions with full P&L summary
- [ ] **Accumulation card on `DashboardPage.tsx`**:
  - Total accumulation book value, total invested, unrealised gain, % of fund in accumulation
- [ ] **Accumulation panel on `TradersPage.tsx`** trader detail:
  - Active theses that trader has open
- [ ] **Settings tab**: Accumulation capital %, max assets, default tranches, DCA trigger %, leverage cap
- [ ] Visual indicator distinguishing accumulation positions from active trading positions (different colour/badge)

---

## Phase 11: Live Trading Parity

### Background

The system currently has a complete, battle-tested paper trading implementation with SL/TP progression, scale-out partial closes, trailing stops, LLM-driven position reviews, and full P&L tracking. The live trading path (`paper_trading_default=false`) can place real Phemex orders with SL/TP attached at entry, but **all active position management runs exclusively on paper positions**. This phase brings live trading to full parity with paper.

### Current State

| Feature | Paper | Live | Gap |
|---|---|---|---|
| Order entry (spot + contract) | ✅ | ✅ | None |
| SL/TP attached at entry | ✅ | ✅ | None |
| Risk gate before entry | ✅ | ✅ | None |
| Breakeven SL progression (33% to TP) | ✅ | ❌ | No live SL adjustment |
| Profit-lock SL (50% lock @ 66% to TP) | ✅ | ❌ | No live SL adjustment |
| Trailing stop watermark | ✅ | ⚠️ Initial placement only | No progressive tightening |
| Scale-out partial closes (3-tier) | ✅ | ❌ | No reduce-only orders |
| LLM-driven SL/TP amendments (FM review) | ✅ | ❌ | Monitor only checks paper |
| Position monitoring loop (60s) | ✅ | ❌ | `_monitor_open_positions()` fetches paper only |
| P&L tracking (win rate, agent stats) | ✅ | ❌ | No live P&L pipeline |
| Position sync from Phemex | N/A | ❌ | `position_sync.py` exists but orphaned |

### 11.1 Trading Service Abstraction Layer

- [ ] Create `backend/app/services/trading_service.py` — unified interface that routes to paper or live:
  - `async def place_order(symbol, side, qty, price, agent_id, sl, tp, trailing_stop) → OrderResult`
  - `async def close_position(position_id) → CloseResult`
  - `async def partial_close(position_id, qty, label) → PartialCloseResult`
  - `async def update_position_sl_tp(position_id, sl, tp, trailing_stop) → UpdateResult`
  - `async def get_positions(symbol?, agent_id?) → List[Position]`
  - `async def get_closed_trades(symbol?, limit?) → List[ClosedTrade]`
- [ ] Reads `paper_trading_default` setting at call time — routes to `PaperTradingService` or `LiveTradingService`
- [ ] All callers in `agent_scheduler.py` migrate from `paper_trading.*` to `trading_service.*`
- [ ] Common return types (`OrderResult`, `CloseResult`, etc.) so callers don't know or care which mode is active

### 11.2 Live Trading Service

- [ ] Create `backend/app/services/live_trading.py` — mirrors `PaperTradingService` public API using real Phemex client:
  - `place_order()` → calls `phemex.place_spot_order_with_sl_tp()` or `phemex.place_contract_order()` based on side
  - `close_position()` → places a reduce-only market order for the full remaining quantity
  - `partial_close(position_id, qty, label)` → places a reduce-only market order for `qty`; updates DB position `realized_pnl` and `scale_out_levels` identically to paper
  - `update_position_sl_tp()` → calls `phemex.amend_order()` to modify the server-side conditional orders; also updates local DB record so the monitoring loop has the latest values
  - `get_positions()` → queries local DB positions where `is_paper=False` (synced by position_sync)
  - `get_closed_trades()` → queries local DB closed trades where `is_paper=False`
- [ ] All mutations log to DB with `is_paper=False` and `phemex_order_id` for audit trail
- [ ] Fee rates pulled from Phemex account tier (or configurable override) rather than hardcoded paper rates

### 11.3 Position Sync Integration

- [ ] Wire `position_sync.py` into `agent_scheduler._scheduler_loop()` on a 30-second cadence
- [ ] Sync creates/updates local `Position` records from Phemex API response (`phemex.get_positions()`)
- [ ] Maps Phemex position fields → local DB fields: `symbol`, `side`, `quantity`, `entry_price`, `unrealized_pnl`, `leverage`
- [ ] Detects externally closed positions (Phemex SL/TP triggered server-side) and marks them closed in local DB
- [ ] Handles partial fills: if Phemex reports reduced quantity vs local record, infer a partial close occurred
- [ ] Reconciliation: compare local `stop_loss_price`/`take_profit_price` with Phemex conditional orders; flag drift
- [ ] Log sync summary to team chat every 5 minutes: positions synced, any discrepancies

### 11.4 Position Monitoring for Live

- [ ] Extend `_monitor_open_positions()` to fetch both paper AND live positions:
  - Paper: `paper_trading.get_positions()` (existing)
  - Live: `live_trading.get_positions()` (new, reads synced DB records)
- [ ] Apply identical SL/TP progression rules to live positions:
  - **Breakeven SL** at 33% to TP → `live_trading.update_position_sl_tp()`
  - **Profit-lock SL** at 66% to TP → `live_trading.update_position_sl_tp()`
  - **Trailing stop watermark** → `live_trading.update_position_sl_tp()`
- [ ] Apply identical scale-out logic to live positions:
  - Tranche 1 (25%) at 50% to TP → `live_trading.partial_close()`
  - Tranche 2 (35%) at 75% to TP → `live_trading.partial_close()`
  - Tranche 3 (40% runner) at full TP → `live_trading.close_position()`
- [ ] Exit triggers (risk manager `check_exit`) route to `live_trading.close_position()`
- [ ] SL/TP review (`_review_open_position_levels`) routes amendments to `live_trading.update_position_sl_tp()`

### 11.5 Live P&L Tracking

- [ ] `_record_run()` records live trade P&L identically to paper — `actual_trades` / `winning_trades` counters increment
- [ ] `get_agent_performance_from_db()` includes `is_paper=False` trades in aggregation (or filtered by mode)
- [ ] Leaderboard and trader detail pages show live performance when in live mode
- [ ] Daily performance chart (`/paper/performance-chart`) extended to support live mode data source
- [ ] Telegram alerts fire for ALL live trades: entry, scale-out, SL/TP hit, full exit

### 11.6 Circuit Breakers & Safety

- [ ] **Daily loss hard kill**: if cumulative live P&L for the day breaches `max_daily_loss_pct`, immediately:
  - Close all open live positions via reduce-only market orders
  - Disable all agents
  - Send Telegram + team chat critical alert
  - Require manual re-enable (no auto-recovery)
- [ ] **Position size sanity check**: before any live order, verify `quantity × price` does not exceed `max_position_size_pct` of actual Phemex account balance (not simulated)
- [ ] **Balance verification**: before live entry, call `phemex.get_account_balance()` and verify sufficient margin/balance; reject if insufficient
- [ ] **Order confirmation**: after placing a live order, poll `phemex.get_open_orders()` to confirm fill; retry or alert on timeout
- [ ] **Rate limiting**: cap live order frequency to max 1 order per agent per 60 seconds to prevent rapid-fire entries on volatile signals
- [ ] **Kill switch API endpoint**: `POST /live/emergency-stop` — closes all live positions, disables all agents, halts scheduler

### 11.7 Live API Routes

- [ ] Create `/live/positions` — GET open live positions (from synced DB)
- [ ] Create `/live/positions/{id}/close` — POST close a live position
- [ ] Create `/live/positions/{id}` — PATCH update SL/TP on live position
- [ ] Create `/live/orders` — GET recent live order history
- [ ] Create `/live/pnl` — GET live P&L summary
- [ ] Create `/live/performance-chart` — GET daily live performance metrics
- [ ] Create `/live/emergency-stop` — POST kill switch
- [ ] Create `/live/status` — GET sync status, last sync time, account balance, open order count

### 11.8 Configuration & Mode Switching

- [ ] Add `trading_mode` enum to settings: `paper` | `live` | `shadow` (shadow = live signals logged but not executed)
- [ ] Shadow mode: entire pipeline runs, orders are logged to DB with `is_shadow=True`, but no Phemex API calls made — useful for validating live readiness
- [ ] Mode switch requires confirmation: changing from paper → live triggers a confirmation dialog with checklist (API keys set, balance verified, risk limits reviewed)
- [ ] API keys validated on mode switch: test `phemex.get_account_balance()` before allowing live mode activation
- [ ] Live mode indicator in frontend header bar (red badge) so it's always visible

### 11.9 Frontend

- [ ] **Mode toggle** on Settings page: Paper / Shadow / Live with confirmation flow
- [ ] **Live positions panel** on Dashboard: real-time positions from `/live/positions`
- [ ] **Live P&L chart** alongside paper chart (or unified with mode filter)
- [ ] **Emergency stop button** in header (live mode only) — calls `/live/emergency-stop`
- [ ] **Sync status indicator**: last sync time, connection health, any reconciliation warnings
- [ ] **Paper vs Live comparison view**: side-by-side performance metrics to validate before switching
