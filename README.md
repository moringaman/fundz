# Phemex AI Trader

AI-powered cryptocurrency trading application using technical indicators (RSI, Bollinger Bands, MACD, Moving Averages) with automated agent trading on Phemex exchange.

## Tech Stack

| Component | Technology |
|-----------|-------------|
| Frontend | React + TypeScript + Vite |
| Styling | Tailwind CSS |
| Charts | Lightweight Charts (TradingView) |
| State | Zustand |
| Backend | FastAPI (Python) |
| Database | PostgreSQL |
| Cache | Redis |

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

- Real-time market data from Phemex
- Technical indicators: RSI, Bollinger Bands, MACD, SMA, EMA, ATR
- Signal generation based on indicator combinations
- Agent-based automated trading
- React dashboard with live charts
- Manual trading interface

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| GET /health | Health check |
| GET /market/klines | Historical klines |
| GET /market/ticker | 24h ticker |
| GET /market/orderbook | Order book |
| POST /trading/order | Place order |
| DELETE /trading/order/:id | Cancel order |
| GET /trading/orders | Open orders |
| GET /trading/balance | Account balance |

## Configuration

Set environment variables in `backend/.env`:

```
PHEMEX_API_KEY=your_api_key
PHEMEX_API_SECRET=your_api_secret
PHEMEX_TESTNET=true
DATABASE_URL=postgresql+asyncpg://...
```

## License

MIT
