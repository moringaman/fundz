# Phemex AI Trader - Learnings

## Conventions

- Backend uses FastAPI with async/await
- Frontend uses React + TypeScript with Vite
- API prefix is `/api`
- Paper trading is default mode for safety

## Gotchas

1. **Phemex API limitations**: Only ~5 candles for 1h timeframe → added Binance fallback
2. **CORS issues**: Fixed with nginx proxy in docker-compose
3. **Frontend .env**: Changed VITE_API_URL from localhost:8000 to /api for proxy
4. **Chart data sorting**: Phemex returns reverse order → sort ascending by timestamp
5. **Balance format**: Frontend was looking for wrong data format

## Patterns

- Toggle switches for on/off features
- Warning banners for destructive actions
- usePaper parameter to switch between paper/real trading

---
*Last Updated: 2026-04-05*
