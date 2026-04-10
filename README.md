# Phemex AI Trader

AI-powered cryptocurrency trading platform with a multi-agent fund management system. A hierarchy of specialised AI team members collaborate every 5 minutes to analyse markets, allocate capital, manage risk, and execute trades.

## Tech Stack

| Component | Technology |
|-----------|-------------|
| Frontend | React + TypeScript + Vite |
| Styling | Tailwind CSS + custom CSS |
| Charts | Lightweight Charts (TradingView) |
| State | Zustand + React Query |
| Backend | FastAPI (Python) |
| Database | PostgreSQL (SQLAlchemy async) |
| Cache | Redis |
| AI/LLM | Claude / GPT-4o / Gemini (configurable) |

## Architecture: Multi-Trader Fund

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           FUND MANAGEMENT HIERARCHY                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐   every 20 min    ┌──────────────────────────────────┐   │
│   │     CIO      │ ──────────────── ▶│  Strategic recommendations       │   │
│   │  (AI Agent)  │                   │  (enable/disable strategies,      │   │
│   └──────────────┘                   │   rebalance, fund health report)  │   │
│                                      └──────────────────────────────────┘   │
│                                                                              │
│   ┌──────────────┐   every 5 min     ┌──────────────────────────────────┐   │
│   │    James     │ ──────────────── ▶│  Capital allocation to TRADERS   │   │
│   │  Portfolio   │                   │  (15–50% per trader, LLM-driven) │   │
│   │   Manager    │                   └──────────────────────────────────┘   │
│   └──────────────┘                                                           │
│          │                                                                   │
│          │ allocates %                                                       │
│          ▼                                                                   │
│   ┌──────────────────────────────────────────────────────────────────┐       │
│   │                      TRADER LAYER                                │       │
│   │                                                                  │       │
│   │  ┌────────────┐   ┌────────────┐   ┌────────────┐              │       │
│   │  │  Trader α  │   │  Trader β  │   │  Trader γ  │              │       │
│   │  │ (Claude)   │   │ (GPT-4o)   │   │ (Gemini)   │              │       │
│   │  │            │   │            │   │            │              │       │
│   │  │ sub-alloc  │   │ sub-alloc  │   │ sub-alloc  │              │       │
│   │  │ to own     │   │ to own     │   │ to own     │              │       │
│   │  │ strategies │   │ strategies │   │ strategies │              │       │
│   │  └─────┬──────┘   └─────┬──────┘   └─────┬──────┘              │       │
│   └────────┼────────────────┼────────────────┼──────────────────────┘       │
│            │                │                │                               │
│            ▼                ▼                ▼                               │
│   ┌─────────────────────────────────────────────────────────────────┐        │
│   │                    STRATEGY LAYER                               │        │
│   │  [ Momentum ] [ Mean Reversion ] [ Breakout ] [ AI ] ...       │        │
│   │   (up to 4 per trader, performance-weighted allocation)        │        │
│   └─────────────────────────────────────────────────────────────────┘        │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Team Decision Flow

The scheduler runs a **Team Tier** every 5 minutes, then executes individual strategies. Below is the complete information flow between team members.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                    TEAM TIER  (every 5 minutes)                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

 ┌─────────────────────────────────────────────────────────────────────────┐
 │ 1. DATA GATHERING                                                       │
 │    Scheduler fetches: agents, metrics, positions, daily P&L, capital    │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │ shared context
                                  ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 2. RESEARCH ANALYST                                                    │
 │    Input:  live market data (multi-symbol)                             │
 │    Output: market regime (bullish/bearish/neutral), sentiment score    │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │ market regime
                                  ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 3. TECHNICAL ANALYST                                                   │
 │    Input:  all symbols traded by any strategy                          │
 │    Output: per-symbol confluence scores (signal direction, strength,   │
 │            pattern count, alignment)                                   │
 └──────────┬──────────────────────────────┬─────────────────────────────┘
            │ confluence scores             │ confluence scores
            ▼                              ▼
 ┌──────────────────────────┐   ┌──────────────────────────────────────────┐
 │ 4. JAMES — Portfolio Mgr │   │ 5. RISK MANAGER                          │
 │                          │   │                                          │
 │ Input:  analyst report,  │   │ Input:  positions, daily P&L, capital,   │
 │         agent metrics,   │   │         risk limits                      │
 │         TA confluence    │   │ Output: risk_level (safe/caution/danger) │
 │                          │   │         max_daily_loss, concentration    │
 │ Step A: allocate % to    │   │                                          │
 │ each TRADER (LLM prompt) │   │ ⚠ If risk_level == "danger":            │
 │                          │   │   ALL strategy execution is BLOCKED      │
 │ Step B: each trader      │   └──────────────────────────────────────────┘
 │ auto-sub-allocates its   │
 │ % to its own strategies  │
 │ (performance-weighted)   │
 │                          │
 │ Output:                  │
 │  • trader → % (15–50%)   │
 │  • strategy → fund %     │
 │    = trader% × strat%    │
 └──────────┬───────────────┘
            │ allocation %
            ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 6. STRATEGY REVIEW  (every 20 min, FM + TA joint)                      │
 │    Input:  agent metrics, market condition, TA confluence              │
 │    Output: proposals to enable / disable / adjust strategies           │
 │    → executes approved proposals immediately                           │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │
 ┌────────────────────────────────▼───────────────────────────────────────┐
 │ 7. TRADER STRATEGY REVIEWS  (every 20 min, per trader)                 │
 │    Input:  each trader's own strategy metrics                          │
 │    Output: trader proposes create / enable / disable strategies        │
 │            (max 4 per trader)                                          │
 │    → executes approved proposals immediately                           │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │
 ┌────────────────────────────────▼───────────────────────────────────────┐
 │ 8. FUND MANAGER — SL/TP REVIEW                                         │
 │    Input:  open positions, TA confluence, risk assessment              │
 │    Output: updated stop-loss / take-profit levels on open positions    │
 │            (conservative — never widens SL beyond original risk)       │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │
 ┌────────────────────────────────▼───────────────────────────────────────┐
 │ 9. CIO REPORT  (every 20 min)                                          │
 │    Input:  all agent metrics, fund performance baseline                │
 │    Output: agent leaderboard, strategy recommendations,                │
 │            CIO sentiment, executive summary                            │
 │    → high-confidence recommendations executed as strategy actions      │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │
 ┌────────────────────────────────▼───────────────────────────────────────┐
 │ 10. TRADE RETROSPECTIVE  (every 20 min)                                │
 │     Input:  recent closed trades, agent configs                        │
 │     Output: per-agent trade insights, parameter adjustments            │
 │             (stop-loss %, take-profit % updated in DB)                 │
 │     → insights injected into next strategy signal generation           │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │ team tier complete
                                  ▼

╔══════════════════════════════════════════════════════════════════════════════╗
║               STRATEGY EXECUTION TIER  (per strategy interval)               ║
╚══════════════════════════════════════════════════════════════════════════════╝

 For each enabled strategy (respects its own run_interval_seconds):

 ┌─────────────────────────────────────────────────────────────────────────┐
 │ GATE 1 — PORTFOLIO RISK                                                 │
 │   risk_level == "danger"  →  ✗ SKIP (all strategies blocked)           │
 │   risk_level == "caution" →  continue (reduced sizing)                 │
 │   risk_level == "safe"    →  continue                                  │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │ pass
 ┌────────────────────────────────▼────────────────────────────────────────┐
 │ GATE 2 — ALLOCATION CHECK                                               │
 │   allocation_pct == 0  →  ✗ SKIP (PM / trader disabled this strategy)  │
 │   allocation_pct  > 0  →  continue                                     │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │ pass
 ┌────────────────────────────────▼────────────────────────────────────────┐
 │ SIGNAL GENERATION                                                       │
 │   For each trading pair:                                                │
 │     • Fetch 1h OHLCV candles from Phemex                               │
 │     • AI mode:  LLM prompt with indicators + team context              │
 │     • Indicator mode: RSI / Bollinger / MACD / MA crossover rules      │
 │   Select best signal + confidence across all pairs                     │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │ signal (buy / sell / hold)
                                  ▼
                     hold ────────┤
                                  │ buy or sell
 ┌────────────────────────────────▼────────────────────────────────────────┐
 │ TRADE GATES  (all must pass before order is placed)                     │
 │                                                                         │
 │  • Min profit gate:   TP must cover fees (0.12% round trip) + 0.5%    │
 │  • Position conflict: no opposing position on same symbol              │
 │  • Risk manager:      trade-level check (daily loss, max positions,    │
 │                       concentration)                                   │
 │  • TA veto:           TA can reject if signal opposes high-confidence  │
 │                       technical confluence                              │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │ all gates pass
 ┌────────────────────────────────▼────────────────────────────────────────┐
 │ ORDER EXECUTION                                                         │
 │   Position size = (USDT balance × allocation%) / current price         │
 │   SL/TP = most conservative of: Risk Mgr level vs TA confluence level  │
 │                                                                         │
 │   Paper mode → paper_trading.place_order()                             │
 │   Live mode  → phemex.place_spot_order() / place_contract_order()     │
 │                                                                         │
 │   + trailing stop placed if configured                                 │
 └────────────────────────────────┬────────────────────────────────────────┘
                                  │
 ┌────────────────────────────────▼────────────────────────────────────────┐
 │ METRICS RECORDING                                                       │
 │   • Update in-memory AgentMetrics (instant)                            │
 │   • Async persist to DB: AgentRunRecord + AgentMetricRecord (upsert)   │
 │   • Feeds back into James's next allocation decision                   │
 └─────────────────────────────────────────────────────────────────────────┘
```

---

## Team Member Summary

| Member | Role | Runs Every | Key Decision |
|--------|------|------------|-------------|
| Research Analyst | Market regime & sentiment | 5 min | bullish / bearish / neutral |
| Technical Analyst | Per-symbol confluence scoring | 5 min | signal strength per pair |
| James (Portfolio Manager) | Capital allocation to traders | 5 min | 15–50% per trader (LLM) |
| Trader Alpha (Claude) | Sub-allocates to own strategies | 5 min | strategy % within trader budget |
| Trader Beta (GPT-4o) | Sub-allocates to own strategies | 5 min | strategy % within trader budget |
| Trader Gamma (Gemini) | Sub-allocates to own strategies | 5 min | strategy % within trader budget |
| Risk Manager | Portfolio risk gating | 5 min | safe / caution / danger |
| Strategy Review (FM+TA) | Enable/disable strategies | 20 min | strategy lifecycle |
| CIO | Fund health & recommendations | 20 min | strategic direction |
| Trade Retrospective | Learn from closed trades | 20 min | SL/TP parameter tuning |
| Individual Strategies | Signal generation & execution | configurable | buy / sell / hold |

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Node.js 20+
- Python 3.11+

### Quick Start

```bash
git clone <repository-url>
cd phemex-ai-trader

cp backend/.env.example backend/.env

docker-compose up -d
```

### Access

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Development

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## Features

- Real-time market data from Phemex exchange
- Technical indicators: RSI, Bollinger Bands, MACD, SMA, EMA, ATR
- Multi-trader fund architecture with competing LLM-backed traders
- AI-driven capital allocation (James allocates to traders, traders to strategies)
- Portfolio risk gating with automatic circuit breaker
- Paper trading mode with full P&L tracking
- Short selling (contract orders)
- Trailing stops
- Team chat log of all AI decisions
- Daily fund report with email delivery
- Configurable per-strategy SL/TP, trailing stop, run interval

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| GET /health | Health check |
| GET /market/klines | Historical klines |
| GET /market/ticker | 24h ticker |
| GET /api/agents | List all strategies |
| POST /api/agents | Create strategy |
| GET /api/fund/team-status | Fund team health |
| GET /api/fund/trader-allocation | James's allocation to traders |
| GET /api/fund/traders/leaderboard | Trader performance ranking |
| GET /api/paper/positions | Open paper positions |
| GET /api/paper/trades | Paper order log |
| POST /api/paper/close/{id} | Manually close a position |
| GET /api/automation/status | Scheduler status |
| POST /api/automation/start | Start scheduler |
| POST /api/automation/stop | Stop scheduler |

## Configuration

Set environment variables in `backend/.env`:

```
PHEMEX_API_KEY=your_api_key
PHEMEX_API_SECRET=your_api_secret
PHEMEX_TESTNET=true
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/phemex_ai_trader
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
GOOGLE_API_KEY=your_google_key
```

## License

MIT

