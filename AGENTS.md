# Project Knowledge

## Common Operations

### YouTube Transcripts
```bash
python3 -c "
from youtube_transcript_api import YouTubeTranscriptApi as Y
for e in Y().fetch('VIDEO_ID'): print(e.text)
"
```

### Backend
- Run migrations: `cd backend && python -m alembic upgrade head`
- Check route registration: `cd backend && python3 -c "from app.api.routes.accumulation import router; print([r.path for r in router.routes])"`
- Verify module imports: `cd backend && python3 -c "from app.services.accumulation_service import accumulation_service; print('OK')"`
- Fast exit / position monitoring cycle is in `agent_scheduler.py` (not `paper_trading.py`)
- The API requires PostgreSQL (Docker compose or local)

### Frontend
- TypeScript check: `cd frontend && npx tsc --noEmit`
- Build: `cd frontend && npm run build`

## Architecture Notes
- All trade execution/signal/agent logic lives in `agent_scheduler.py` (8000+ lines)
- `paper_trading.py` handles order matching, balances, positions (thin DB layer)
- `technical_analyst.py` does pattern detection, TA reports, confluence scoring
- `gate_autopilot.py` auto-adjusts TradingGates based on performance + GMM regime
- `risk_manager.py` handles position-level risk checks (SL/TP, correlation concentration)
- `indicators.py` `generate_signal()` dispatches to strategy-specific signal methods
- Fee rates: Hyperliquid 0.035%/leg, Phemex contract 0.06%/leg, Alpaca 0%
- `fee_rate_for(symbol, venue)` — venue defaults to "hyperliquid" if not passed
- Strategies defined in `registry.yaml`; `ai_propose: true` enables auto-creation
- GMM regime labels: `risk_on`, `range`, `risk_off` (from `regime_states` table)
- Pattern detection includes: flag, triangle, head_shoulders, double/triple, cup_handle, wedge, rectangle, fair_value_gap
- `market_context` dict carries TA data (regime, ta_signal, ta_patterns, htf_trend, etc.) to `generate_signal()`
- `ALL_STRATEGIES` reads dynamically from `registry.yaml` (not hardcoded)

## Recent Changes
- Added `AccumulationExecutionRecord` table for DCA/VA/dip/scale-out execution history
- Added `dca_count`, `va_count`, `dip_count` to `AccumulationConfig` (backfilled in migration)
- Added `trading_venue` field to `TradingPreferences` (defaults to "hyperliquid")
- Fixed fee rate bug: `fee_rate_for()` was defaulting to Hyperliquid (0.035%) for breakeven SL calculations instead of using the agent's actual venue
- Gate autopilot now uses GMM regime as preemptive overlay + per-strategy weakest-link classification
- Added Fair Value Gap pattern detection + `fair_value_gap` strategy type
- `max_correlated_exposure_pct` is now dynamically adjusted per autopilot regime
