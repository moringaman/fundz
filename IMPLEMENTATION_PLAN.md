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
