# Phemex AI Trader — Future Roadmap

## LLM Cost Reduction — Phase 2: Tiered Team Analysis

**Priority:** Medium  
**Status:** Enhancement (Phase 1 already implemented)  
**Complexity:** Small — `agent_scheduler.py` only

Phase 1 (implemented) cut team analysis from every 5 min to every 15 min, saving ~24 LLM calls/hour. Phase 2 skips individual LLM calls within `_run_team_analysis()` when nothing meaningful has changed.

### What needs to happen

1. **New state vars** — add `_last_research_at: Optional[datetime]`, `_last_allocation_at: Optional[datetime]`, `_last_position_count: int` to `__init__`
2. **Research Analyst gate** — inside `_run_team_analysis()`, only call `research_analyst.analyze_markets()` if >30 min elapsed OR the TA confluence signal distribution shifted >15% since the last run (compare dominant signal counts). Otherwise re-use `self._current_analyst_report`.
3. **Allocation gate** — only call `fund_manager.make_trader_allocation_decision()` if >30 min elapsed OR `_last_position_count` changed (a trade opened or closed). TA confluence scores still refresh every 15-min cycle (no LLM, just math).
4. **Estimated saving** — ~16 additional LLM calls/hour on top of Phase 1 (down to ~4/hour for the team tier on quiet sessions).

---

## LLM Cost Reduction — Phase 4: SL/TP Trader Consultation Auto-Approve Gate

**Priority:** Medium  
**Status:** Enhancement  
**Complexity:** Small — `_run_sl_tp_review()` in `agent_scheduler.py`

Currently every SL/TP review propsal invokes the responsible trader's LLM to approve or reject. Most routine tightenings (SL moves slightly away from price, TP stays the same) don't need an opinion — the math is obvious.

### What needs to happen

1. **Rule-based auto-approve gate** — before calling `trader_llm._call_llm_text()` (around line 2371), check:
   - SL shift is < 1.5% of current price, AND
   - TP shift is < 5% of current price, AND
   - Direction is sensible (SL is moving away from current price, TP is moving toward profit side)
   - If all three are true → auto-approve, log it, skip LLM
2. **Only consult LLM for unusual adjustments** — large moves, directional flips, or proposals that fail the safety checks above.
3. **Team chat transparency** — when auto-approving, post a brief note: `"Auto-approved: routine SL tighten — no trader consultation needed."`
4. **Estimated saving** — depends on open position count; typically 3–10 LLM calls/review cycle eliminated.

---

## Leverage Trading Support

**Priority:** High  
**Status:** Planned  
**Complexity:** Large — touches ~7 components end-to-end

The Settings UI already has a leverage slider (1–50x, default 1x), but the value is not wired into any trading logic. All trades currently execute as spot (1x).

### What needs to happen

1. **DB Models** — Add `leverage` column to Position and Trade models
2. **Paper Trading Engine** — `place_order()` accepts leverage, applies multiplier to effective position size; margin = notional / leverage
3. **Quantity Calculation** — Scheduler computes: `quantity = (balance × allocation_pct × leverage) / price`
4. **Risk Manager** — Account for leveraged exposure in `check_trade()`, adjust max drawdown thresholds
5. **Position Monitoring** — Implement liquidation price calculation; auto-close positions that hit liquidation threshold
6. **Agent Config** — Add per-agent leverage field to Agent model, API schema, and AgentsPage UI
7. **Phemex API Client** — Send leverage parameters when placing real (non-paper) orders
8. **Frontend** — Display leverage on trade history, open positions, and portfolio views

### Considerations

- Paper trading must simulate margin and liquidation to be realistic
- Risk manager should enforce `max_leverage` from global settings as a ceiling
- Per-agent leverage allows conservative agents (1–3x) alongside aggressive ones (10x+)
- Unrealised P&L and portfolio value calculations must account for leveraged positions

---

## Per-Agent Position Isolation

**Priority:** High  
**Status:** ✅ Implemented  
**Complexity:** Medium — primarily paper trading engine + scheduler

Currently, positions are tracked **per symbol only**. When multiple agents trade the same pair (e.g. BTCUSDT), their buys merge into a single blended position. This makes individual agent P&L attribution unreliable and distorts win rate, allocation decisions, and performance metrics.

### The problem

- `place_order()` looks up positions by `(user_id, symbol)` — ignoring `agent_id`
- Agent A buys 0.1 BTC, Agent B buys 0.05 BTC → one 0.15 BTC position credited to Agent A
- Sell orders deduct from the shared position; realized P&L is attributed to the wrong agent
- Fund Manager allocation scores are based on inaccurate per-agent P&L

### What needs to happen

1. **Position lookup** — Filter by `(user_id, agent_id, symbol)` instead of just `(user_id, symbol)` in `place_order()` and `get_positions()`
2. **DB constraint** — Add unique constraint on `(user_id, agent_id, symbol)` to prevent accidental merging
3. **Sell logic** — Each agent can only sell its own position; scheduler checks agent-specific holdings
4. **Position monitoring** — SL/TP exit checks iterate per-agent positions independently
5. **Frontend** — Open positions table should show the owning agent per position
6. **Migration** — Existing blended positions may need manual cleanup or a one-time split

### Considerations

- This is a prerequisite for accurate allocation decisions by the Fund Manager
- Must be done before leverage trading (leveraged positions need clear per-agent ownership)
- Existing trade history with `agent_id` already recorded will remain valid

---

## Whale Alert Integration (CryptocurrencyAlerting.com)

**Priority:** Medium  
**Status:** Planned  
**Complexity:** Medium  
**Service:** [cryptocurrencyalerting.com](https://cryptocurrencyalerting.com/crypto-whale-tracker.html)

Integrate real-time whale transaction alerts into the Fund Manager's decision-making pipeline. Large on-chain movements (exchange deposits, withdrawals, stablecoin mints) are strong leading indicators that our agents should factor into trade timing and strategy.

### How it works

CryptocurrencyAlerting.com provides:
- **Webhook delivery** — HTTP POST with JSON payload whenever a whale alert fires
- **REST API** — Create/manage alert conditions programmatically (`POST /v1/alert-conditions`)
- **Whale alert payload example:**
  ```json
  {
    "type": "whale",
    "message": "26,127,990 USDC transferred from 0xa69b… to 0x6c0a…",
    "blockchain": "ETH",
    "currency": "USDC",
    "value": "26,120,151.82",
    "target_currency": "USD",
    "target_value": "26,127,990.21",
    "from": "0xa69b…",
    "to": "0x6c0a…"
  }
  ```
- **Volume spike alerts** — detect unusual trading volume surges on exchanges
- Auth via HTTP Basic Auth with API token; rate limit 2 req/sec

### What needs to happen

1. **Webhook endpoint** — Add `POST /api/webhooks/whale-alert` to receive inbound alerts from CryptocurrencyAlerting; validate origin, parse payload, store in a `whale_events` table
2. **Alert condition setup** — On startup or via Settings UI, use the REST API to register whale alert conditions for our trading pairs (BTC, ETH, etc.) with our webhook URL as the delivery channel
3. **Event classification** — LLM or rule-based classifier to interpret whale movements:
   - Large exchange deposits → potential sell pressure (bearish)
   - Large exchange withdrawals → accumulation signal (bullish)
   - Stablecoin mints → incoming buy pressure (bullish)
   - Large OTC / unknown wallet transfers → neutral, but noteworthy
4. **Fund Manager integration** — Feed classified whale events into the Fund Manager (James) context window so allocation decisions factor in whale activity
5. **Agent consultation** — Pass recent whale events to agents alongside TA data before trade execution, allowing agents to adjust confidence or hold off
6. **Frontend panel** — Whale activity feed on the Overview or Fund Team page showing recent large movements with bullish/bearish classification
7. **Settings** — API token config, minimum whale transaction size threshold, which pairs/blockchains to monitor

### Configuration

| Variable | Description |
|----------|-------------|
| `WHALE_ALERT_API_TOKEN` | CryptocurrencyAlerting.com API token |
| `WHALE_ALERT_MIN_USD` | Minimum USD value to track (e.g. $1M) |
| `WHALE_ALERT_WEBHOOK_SECRET` | Optional shared secret for webhook validation |

### Considerations

- Webhook endpoint must be publicly reachable (needs tunnelling in dev, e.g. ngrok)
- Rate-limit inbound webhooks to prevent spam
- Whale events should expire after 24h to keep context windows lean
- Pair the volume alert type with whale alerts for a more complete picture
- Consider tracking known exchange wallet addresses for deposit/withdrawal classification
- Paid plan required for webhook delivery ($3.99–$49/mo depending on alert volume)

---

## Browser Push Notifications

**Priority:** Medium  
**Status:** ✅ Implemented (Phase 1 — Web Notifications API)  
**Complexity:** Medium

Real-time browser notifications so users are alerted to key fund events even when the dashboard tab isn't in focus.

### Phase 1 (Implemented)

Uses the **Web Notifications API** triggered via existing WebSocket events. Notifications appear when the tab is in the background.

**Components built:**
- `frontend/src/lib/notifications.ts` — NotificationService: permission handling, preference storage (localStorage), event classification, browser notification dispatch
- `frontend/src/hooks/useBrowserNotifications.ts` — WS event listener that triggers notifications when tab is hidden
- Settings page "Notifications" tab — per-event toggle UI with master switch, permission request, test notification button

**Supported event types:**
| Event | Default | Notification |
|-------|---------|-------------|
| Trade executed | ✅ ON | "Trade Executed: Agent bought 0.05 BTC..." |
| Position closed (SL/TP) | ✅ ON | "Position Closed: Stop-loss ETHUSDT..." (persistent) |
| Risk alert | ✅ ON | "⚠️ Risk Alert: Elena flags danger..." (persistent) |
| Portfolio rebalance | ❌ OFF | "Portfolio Rebalanced: James reallocated..." |
| Daily report ready | ✅ ON | "📊 Daily Report Ready" |
| Agent error | ✅ ON | "⚠️ Agent Issue: Agent failed..." |

**Preferences:** Stored in `localStorage` key `px_notification_prefs`. Each event type can be independently toggled.

### Phase 2 (Future — Full Push via Service Worker)

For notifications even when the browser is closed:

1. **Service Worker** — Register for push event handling
2. **VAPID keys** — Generate and configure `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CONTACT_EMAIL`
3. **Backend push service** — `notification_service.py` using `pywebpush` library
4. **Push subscription storage** — DB table for user push endpoints
5. **Rate limiting** — Batch notifications within 1-min windows
- Falls back gracefully if user denies permission — in-app bell still works

---

## UI Event Sounds

**Priority:** Low  
**Status:** Planned  
**Complexity:** Small

Subtle audio cues to give the dashboard a live trading-floor feel. Sounds play in-browser when key events arrive via WebSocket, so users get immediate feedback without watching the screen.

### Sound map

| Event | Sound style | Notes |
|-------|-------------|-------|
| Trade opened (buy) | Short ascending chime | Distinct from sell |
| Trade closed (sell / SL / TP) | Short descending tone | Slightly different for SL hit vs TP hit |
| Agent chat message | Soft notification ping | Like a messaging app |
| Whale alert received | Deep low tone | Weighty, attention-grabbing |
| Daily summary ready | Gentle bell | Non-urgent |
| Position liquidated / large loss | Alert klaxon (short) | Urgent but not obnoxious |
| Portfolio milestone (new high) | Celebration ding | Positive reinforcement |

### What needs to happen

1. **Audio assets** — Source or generate short royalty-free sound effects (< 1s each, MP3/OGG); store in `frontend/public/sounds/`
2. **Sound service** — Create a `useSoundEffects` hook or `SoundService` singleton that pre-loads Audio objects and exposes `play('trade-open')` etc.
3. **Wire into WebSocket events** — Call sound service from existing WS message handlers in `useMarketStream` or a new `useEventStream` hook
4. **Settings toggle** — Add a Sound section to Settings page with master volume slider + per-event on/off toggles; persist to localStorage
5. **Browser autoplay policy** — First sound must follow a user gesture; show a one-time "Enable sounds" prompt or unmute button in the header

### Considerations

- Keep sounds short (< 1 second) and low-key — this runs all day, annoying sounds will get muted immediately
- Respect system volume; use Web Audio API `GainNode` for app-level volume control
- Debounce rapid-fire events (e.g. multiple trades within seconds) to avoid sound stacking
- Mute automatically when browser tab is hidden (`document.visibilityState`) — optional, configurable
- Pairs naturally with browser push notifications — sound for foreground, push for background

---

## Additional Future Items

- [ ] Backtesting engine with historical data replay
- [ ] Multi-exchange support (Binance, Bybit)
- [ ] Advanced order types (trailing stop, OCO, TWAP)
- [ ] Agent performance leaderboard and analytics dashboard
- [ ] Mobile-responsive UI / PWA support
- [ ] WebSocket-based real-time P&L streaming
- [ ] Role-based access control and multi-user support
- [ ] Telegram / Discord alert integration
