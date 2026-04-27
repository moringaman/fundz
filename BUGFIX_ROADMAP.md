# Bugfix & Architectural Improvement Roadmap

Living document tracking known defects, architectural debt, and missed opportunities in the phemex-ai-trader decision pipeline. Sourced from the 2026-04-26 architectural review of position-opening logic.

**Status legend:** 🔴 not started · 🟡 in progress · 🟢 done · ⚪ deferred

---

## P0 — Outright Bugs (Fix Immediately)

### B-001 🟢 LLMService race condition: role-tagged calls clobber per-trader model
**Severity:** Critical · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

**Symptom**
OpenRouter dashboard showed nearly all calls hitting `openai/gpt-4o-mini` (the global `.env` `LLM_MODEL`) regardless of per-trader configuration (Claude / GPT-4o / Gemini). Multi-trader competition was a fiction.

**Root cause**
`LLMService._call_llm` and `_call_llm_text` mutated `self.model`, `self.temperature`, `self.max_tokens` whenever a `role` argument was passed. Per-trader instances are cached in `TraderService._trader_llm_cache`, so any role-tagged call permanently overwrote the cached trader's model. With async parallel team-tier calls (Research Analyst + TA + Risk Manager etc. firing concurrently) the model field also raced — last writer wins.

**Files**
- `backend/app/services/llm.py` — `_call_llm`, `_call_llm_text`, `_call_openai`, `_call_anthropic`

**Fix**
Resolve `(model, temperature, max_tokens)` as call-local variables via new `_resolve_call_config(role)` helper. Pass them as parameters into `_call_openai` / `_call_anthropic`. Never mutate `self.*` from a request path.

**Verification**
- [ ] Unit test: parallel `gather` of multiple roles on a shared instance preserves each call's model
- [ ] OpenRouter dashboard shows distribution across configured trader models (Claude / GPT-4o / Gemini)
- [ ] Per-trader log line `Trader LLM initialized: <name> → <provider>/<model>` matches the model used in subsequent role-tagged calls

---

### B-002 🟢 AI strategy passes timestamp index as `volume` to the LLM
**Severity:** High · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

**Symptom**
The `ai` strategy sent a unix-timestamp-shaped integer (~1.7e9) to the LLM in the `volume` field of every signal request. The LLM was reasoning about volume on garbage data.

**Root cause**
`agent_scheduler.py:3996` had `'volume': float(_ai_close.index[-1])` — `_ai_close` is the close-price Series so `.index[-1]` returns the timestamp index, not volume.

**Files**
- `backend/app/services/agent_scheduler.py:3996`

**Fix**
Replaced with `float(_ai_vol.iloc[-1]) if _ai_vol is not None and len(df) > 0 else None`.

**Verification**
- [ ] Log a sample AI strategy `indicators_dict` and confirm `volume` is in the realistic range for the symbol (e.g. BTC ~10–1000)
- [ ] Compare LLM reasoning before/after — should reference plausible volume figures

---

### B-003 🔴 Backtest results don't reflect live trading reality
**Severity:** High · **Found:** 2026-04-26

**Symptom**
`_bootstrap_from_backtest` (agent_scheduler.py:890) seeds new agent metrics from `backtest.py` results, but the live system layers in TA veto, regime gate, MTF gate, P&L suspension, James allocation, execution_coordinator slippage. Backtests are systematically too optimistic — every additional gate can only reject signals, never improve them.

**Root cause**
Backtest harness (`app/services/backtest.py`) runs strategy signal generation in isolation. It does not simulate the team-tier gating chain.

**Files**
- `backend/app/services/backtest.py`
- `backend/app/services/backtest_walkforward.py`
- `backend/app/services/backtest_sensitivity.py`

**Fix plan**
1. Extract gate-chain logic from `agent_scheduler.run_agent` into a pure function `apply_trade_gates(signal, market_context, regime, ta_confluence, htf_trend) -> (allow, reason)`
2. Have backtest engine invoke the same function on each candle
3. Persist a `gate_rejected` count alongside `trades_executed` in backtest results
4. Surface `gate_rejected_pct` in the UI so users see expected live attrition

**Verification**
- [ ] Replay a known-profitable historical period in backtest with vs without gates — gated PnL should match live PnL within tolerance
- [ ] Strategy review's `_bootstrap_from_backtest` uses gated metrics

---

## P1 — Architectural Flaws (Fix this Quarter)

### A-001 🔴 `agent_scheduler.py` is a 6,714-line God object
**Severity:** High · **Found:** 2026-04-26

Single file owns: orchestration, gates, leverage, cycles, monitoring, persistence, team analysis, retrospectives, daily reports, telegram, US-open sweeps, regime refits, fee pressure, circuit breakers, watchlist, agent bootstrap. ~30 methods on one class. Touching anything risks breaking everything.

**Fix plan** (incremental — one extraction per PR)
1. Extract gate chain → `app/services/trade_gates.py`
2. Extract leverage logic → `app/services/leverage_policy.py`
3. Extract position monitoring → `app/services/position_monitor.py`
4. Extract team analysis orchestration → `app/services/team_orchestrator.py`
5. Extract bootstrap & registration → `app/services/agent_bootstrap.py`
6. Leave `AgentScheduler` as a slim coordinator

**Acceptance:** `agent_scheduler.py` < 1500 lines, each extracted module independently testable.

---

### A-002 🟢 Decisions are gate chains, not probabilistic ensembles — Phase 1 shipped
**Severity:** High (alpha-leaving) · **Found:** 2026-04-26 · **Phase 1 shipped:** 2026-04-26

Every input was a binary veto. A signal at confidence 0.49 with five strong corroborating evidence streams was treated identically to one with none. No Bayesian aggregation.

**Fix shipped — Phase 1 (modulation, not replacement)**
1. ✅ `EvidenceVector` schema defined in `backend/app/services/signal_fusion.py` capturing eight evidence streams: `strategy_signal, ta_confluence, regime_alignment, htf_trend, pattern_strength, divergence, whale_flow, sentiment`. Each is a `(direction ∈ {-1,0,+1}, confidence ∈ [0,1])` tuple.
2. ✅ `fuse(evidence)` aggregates via signed log-odds (`Σ direction × confidence × weight`), applies sigmoid for calibrated probability, returns `FusedSignal(direction, fused_confidence, raw_confidence, log_odds, contributions, agreement_score)`.
3. ✅ Asymmetric flooring rule: fusion never *downgrades* a high-conviction strategy signal when other sources are merely silent (zero contribution). Only attenuates on active disagreement (negative contribution).
4. ✅ `build_evidence_vector` consumes only what `run_agent` already has — no new fetches, graceful degradation on missing inputs.
5. ✅ Wired into `agent_scheduler.run_agent` immediately after best-symbol selection (before downstream gates), so fused confidence flows through all subsequent sizing/leverage logic.
6. ✅ Every signal's evidence vector + fusion result persisted to `backend/logs/evidence_vectors.jsonl` (configurable via `SIGNAL_FUSION_LOG_PATH`) for offline weight refit.
7. ✅ Default weights seeded from architectural intuition (strategy_signal=1.5, ta_confluence=1.0, regime/pattern=0.6, htf_trend/divergence=0.4–0.5, whale=0.3, sentiment=0.2).
8. ✅ 14 unit tests covering: full agreement boost, full opposition flip, silent context no-downgrade, hold propagation, contribution arithmetic, zero-weight short-circuit, unit-interval bounds, agreement scoring, JSONL persistence, silent failure mode.

**Files**
- `backend/app/services/signal_fusion.py` (new, 270 lines)
- `backend/app/services/agent_scheduler.py` (wiring at line ~4180; tracking vars at line ~3886; capture in selection block at line ~4150)
- `backend/tests/test_signal_fusion.py` (new, 14 tests)

**Phase 2 (deferred — requires data)**
1. ⚪ Replace strategy direction selection with argmax over fused long/short probabilities once enough trades have accumulated in `evidence_vectors.jsonl`
2. ⚪ Nightly weight refit via logistic regression against realised PnL
3. ⚪ Per-strategy weight overlays (mean-reversion downweights momentum-style evidence and vice versa)

**Acceptance gate for Phase 2 promotion**
- ≥ 500 closed trades with persisted evidence vectors
- A/B paper trade fusion-on vs fusion-off for 14 days, fusion variant Sharpe improvement ≥ 5 %

---

### A-003 🟢 Team tier runs sequentially — parallelised
**Severity:** Medium · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

README diagram showed Research → TA → James → Risk → Strategy Review → CIO → Retrospective in strict order. Each is an LLM call. Most are independent.

**Fix shipped**
Refactored `_run_team_analysis` into three explicit phases preserving all existing error handling and side-effect ordering:

- **Phase 1 (parallel `asyncio.gather`)**: 5 calls — `_fetch_agents_from_db`, `_get_current_positions`, `_compute_daily_pnl`, `research_analyst.analyze_markets`, `fund_manager.analyze_market`. Each wrapped in `_safe_call` so one failure does not abort the gather.
- **Phase 1.5 (sequential, fast)**: `_get_total_capital` (depends on positions_value; non-LLM, sub-second).
- **Phase 2 (parallel `asyncio.gather`)**: 2 LLM calls — `technical_analyst.get_confluence_scores`, `risk_manager.generate_risk_assessment`. Both depend on phase-1 data.
- **Phase 3 (sequential)**: James allocation, consistency checks, strategy review (20-min cadence), trader strategy reviews, SL/TP review, fee-budget cache, execution coordinator, trade retrospective, CIO report. All have inter-dependencies and side-effect ordering that must be preserved.

Removed the now-redundant downstream Risk Manager block (was duplicating an LLM call already issued in Phase 2). Added `_team_elapsed` timing log at end of cycle for monitoring.

**Files**
- `backend/app/services/agent_scheduler.py:_run_team_analysis` (line ~2816)

**Verification**
- ✅ `agent_scheduler` imports cleanly
- ✅ All gather points present (`_safe_call`, `analyst_report_raw`, `confluence_scores_raw`, `risk_assessment_raw`)
- [ ] Live measurement: log line `Team Tier: completed in N.Ns` should drop from ~35–70 s to < 15 s after first restart

**Acceptance**
Median team-tier wall time drops from ~35–70 s to < 15 s, measurable from the new `Team Tier: completed in N.Ns` log line.

---

### A-004 🟢 Per-trader LLM config silently overridden by `.env` — fixed
**Severity:** Medium · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

`LLM_MODEL` env var was the de-facto default whenever a trader row lacked an explicit `llm_model`, because `get_trader_llm` did `trader.get("llm_model", settings.llm_model)`. Combined with B-001, this defeated the multi-trader premise entirely.

**Fix shipped — defence-in-depth at four layers**

1. ✅ **Service layer (primary fix)** — `TraderService.get_trader_llm` now raises `ValueError` with a detailed message naming the offending trader and explaining that `.env` is NOT a fallback for traders. No more silent substitution. Trims whitespace before checking emptiness.
2. ✅ **Module-import validation** — `_validate_default_traders()` runs at `trader_service` import time and raises `ValueError` if any `DEFAULT_TRADERS` entry has empty `name`, `llm_provider`, or `llm_model`. Catches typos in seed data before the system even boots.
3. ✅ **Boot-time DB validation** — new `validate_trader_configs(db_session)` async helper scans every persisted Trader row at scheduler boot and raises `RuntimeError` listing all offenders if any have empty LLM fields. Wired into `_auto_register_agents` immediately after `seed_default_traders`. The system **refuses to start** when misconfigured rather than silently misbehaving.
4. ✅ **API boundary validation** — `TraderCreate` now uses `Field(..., min_length=1)` plus a `field_validator` that rejects empty/whitespace-only values for `name`, `llm_provider`, `llm_model`. `TraderUpdate` allows `None` (no change) but rejects explicit blank strings. Stale defaults (`"anthropic/claude-sonnet-4"`) removed — caller must specify explicitly.

The Trader model already had `nullable=False` on both `llm_provider` and `llm_model` at the DB layer, so the schema itself enforces non-null; the new code layers handle the empty-string and missing-key cases that nullable=False does not catch.

**Files**
- `backend/app/services/trader_service.py` — `get_trader_llm` (fail-fast), `_validate_default_traders` (new), `validate_trader_configs` (new)
- `backend/app/services/agent_scheduler.py` — wired `validate_trader_configs` into boot path
- `backend/app/api/routes/traders.py` — Pydantic Field + field_validator on TraderCreate/TraderUpdate
- `backend/tests/test_trader_llm_config.py` — 12 new tests (fail-fast, default validation, API validation)

**Verification**
- ✅ 12/12 new unit tests pass
- ✅ All modules import cleanly
- ✅ `_validate_default_traders` runs at import time and confirmed catches missing-field and empty-string seed entries
- [ ] Live: starting the system with a manually-blanked DB row should now produce `RuntimeError: Refusing to start: 1 trader row(s) have empty LLM configuration...`

**Acceptance achieved**
Starting the system with a trader missing `llm_model` now raises a loud, named, actionable error at boot — before any LLM calls or trades can happen.

---

### A-005 🟢 No correlation-aware concentration gate — fixed
**Severity:** Medium · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

The existing per-direction concentration gate flat-summed all longs (or shorts) across the fund. It treated `$X long across BTC + ETH + SOL` (correlation ≈ 0.85) the same as `$X long across BTC + uncorrelated alts` — but the former is one ~3×-leveraged crypto-beta bet, not three independent trades.

**Fix shipped — principal-component-equivalent exposure**

The new gate uses the standard portfolio-variance formula

  σ²_portfolio = Σᵢ Σⱼ ρᵢⱼ · wᵢ · wⱼ

where `wᵢ` is signed fractional notional (positive long, negative short) and ρ is the rolling 90-day correlation matrix of daily log-returns. The square root is the **effective concentration** as a fraction of capital:

- collapses to `Σ|wᵢ|` (raw sum) when correlations are 1.0
- shrinks toward `√Σwᵢ²` (RMS) when correlations approach 0
- correctly nets long/short on highly-correlated pairs

**Components shipped**

1. ✅ **`backend/app/services/correlation_service.py` (new, ~225 lines)**
   - `CorrelationMatrix` dataclass + `CorrelationService` singleton
   - `refresh(symbols)` fetches 90-day daily klines for all configured symbols, computes log-returns, builds correlation matrix via `np.corrcoef`. Skips symbols with < 30 observations. Cadence-guarded at 23 hours.
   - `compute_correlated_exposure(positions, intended_trade, total_capital)` returns `CorrelatedExposure(effective_pct, raw_long_pct, raw_short_pct, weighted_pairs)`
   - `lookup(s1, s2)` with sensible default `0.65` fallback for symbols not yet in the matrix (avoids cold-start zero-correlation false-permits)
   - `is_stale()` for refresh cadence check
2. ✅ **`risk_manager.check_correlation_concentration`** — returns `RiskCheckResult`. Gate disabled cleanly when limit ≥ 100. Reason string names the dominant correlated pair for debuggability.
3. ✅ **`RiskLimits.max_correlated_exposure_pct`** — new setting, default 30 %, range 5–100. Description documents the math and the disable-by-100 escape hatch.
4. ✅ **Wired into `run_agent`** in `agent_scheduler.py` immediately after the existing per-direction concentration gate. Translates `Position` ORM objects into the dict shape expected by the service. Logs to `team_chat.log_trade_blocked` and returns hold AgentRun on rejection (consistent with sibling gates).
5. ✅ **Daily refresh hook** — new `_maybe_refresh_correlation_matrix` mirrors `_maybe_refit_regime_models`. Called every scheduler-loop iteration; the service's own staleness check guards against more than one refresh per ~23 h. Pulls symbol set from trading_prefs + enabled-agent trading_pairs.
6. ✅ **18 unit tests** covering: perfect-correlation collapse, uncorrelated quadrature sum, realistic 0.85 case, long/short netting on perfectly-correlated pair, intended-trade addition, zero-capital edge case, fallback when no matrix, staleness logic, log-return correctness, refresh from synthetic correlated series (validates math via `np.random.default_rng` with shared driver), insufficient-data symbol skipping, gate-disabled-at-100 behaviour, real block decision, real allow decision.

**Files**
- `backend/app/services/correlation_service.py` (new)
- `backend/app/services/risk_manager.py` (`check_correlation_concentration` method)
- `backend/app/api/routes/settings.py` (`RiskLimits.max_correlated_exposure_pct`)
- `backend/app/services/agent_scheduler.py` (gate call in `run_agent`, refresh hook in scheduler loop, helper method)
- `backend/tests/test_correlation_concentration.py` (new, 18 tests)

**Verification**
- ✅ 18/18 new unit tests pass
- ✅ All modules import cleanly
- ✅ Combined roadmap test suites (A-002 + A-004 + A-005): 44/44 passing
- [ ] Live: after first 23 h, `Correlation matrix refreshed: N symbols, M obs, avg pairwise ρ=...` log line appears
- [ ] Live: synthetic test (open large BTC + ETH long, attempt SOL long) should now produce `Correlation gate: Correlation-weighted exposure X% > 30%...` rejection

**Acceptance achieved**
The gate now correctly identifies 3-of-a-kind crypto longs as a single concentrated bet and blocks/sizes-down accordingly, rather than counting them as three independent trades.

---

### A-006 🔴 No stateful setup tracking across cycles
**Severity:** Medium · **Found:** 2026-04-26

`_setup_watchlist` is the only carry-over between cycles. Pattern-based and breakout strategies need multi-cycle state machines (compression → volume spike → confirmation candle). Re-deriving everything each cycle means entries get missed by 1+ cycles.

**Fix plan**
1. Introduce `app/services/setup_state_machine.py` — per (agent_id, symbol) FSM with states like `IDLE → COMPRESSION_DETECTED → VOLUME_SPIKE → ARMED → TRIGGERED`
2. Persist state in Redis with 24 h TTL
3. Pattern detectors (breakout, wyckoff, fractal, chart-pattern) emit state transitions, not just signals
4. `run_agent` checks state before re-detecting

**Acceptance:** Test: a known historical bull-flag breakout is caught on the trigger candle, not 1 cycle later.

---

### A-007 🟢 Patterns don't drive entry price — fixed
**Severity:** Medium · **Found:** 2026-04-26 · **Fixed:** 2026-04-26

Pattern detection in `technical_analyst.py` already produced `PatternSignal` objects carrying `entry_price`, `stop_loss`, and `take_profit_1/2` from pattern geometry — but these were used only for veto/boost reasoning. Order placement still passed `current_price` to `paper_trading.place_order` and `live_trading.place_order` as Market fills, ignoring the geometry. A bull-flag entry was filling halfway up the pole, a Wyckoff Spring at the rejection candle close instead of at the IB-low retest.

**Fix shipped — pattern geometry now drives order routing**

1. ✅ **`TradingSignal` extended** with three optional fields (`entry_type`, `entry_price`, `pattern_type`) — backward compatible, all default `None`. Documented contract: `entry_type ∈ {"Market", "Limit", "Stop"}` (Phemex API vocabulary), `entry_price` ignored when Market.
2. ✅ **`backend/app/services/pattern_entry.py` (new, ~110 lines)** — pure translation layer:
   - `select_pattern_entry(side, current_price, patterns, min_confidence, enabled)` picks the highest-confidence aligned pattern and returns an `OrderPlan(order_type, entry_price, pattern_type, pattern_confidence, rationale)`. Routing rules:
     - BUY, entry > current → **Stop** (breakout)
     - BUY, entry ≤ current → **Limit** (pullback / retest)
     - SELL, entry < current → **Stop** (breakdown)
     - SELL, entry ≥ current → **Limit** (rally / retest)
   - `can_fill_now(plan, side, current_price, tolerance_pct)` decides whether a paper order can fill immediately or must defer.
3. ✅ **Three new settings** in `RiskLimits`:
   - `pattern_entry_orders_enabled` (default `True`) — feature flag for clean rollback
   - `pattern_entry_min_confidence` (default `0.65`) — patterns below this stay on Market
   - `pattern_entry_tolerance_pct` (default `0.30`) — paper-mode fill band around the entry level
4. ✅ **Wired into `run_agent`** — after the existing concentration/correlation gates pass and before order placement, calls `select_pattern_entry` with the already-computed `technical_report.patterns`. Logs `Pattern entry routing: <pattern> → <Stop|Limit> @ <price>` for every pattern-routed trade.
5. ✅ **Plumbing through `trading_service` → `paper_trading`/`live_trading`** — `place_order` accepts `order_type`, `entry_price`, `pattern_type` kwargs. `TradingService` uses defensive `try/except TypeError` so backends that haven't been updated yet still work (live_trading.py future work).
6. ✅ **Paper-side defer semantics** — when `order_type` is Limit/Stop and `current_price` is outside the tolerance band, raises `PatternEntryDeferred` (new exception with full diagnostic context). `run_agent` catches it, adds the symbol to `_setup_watchlist` so next cycle prioritises it, logs to team chat, returns a non-executed `AgentRun` with hold signal. The next cycle re-evaluates against fresh candles.
7. ✅ **18 unit tests** covering: all four routing branches (Stop/Limit × buy/sell), no-pattern fallback, wrong-direction rejection, below-min-confidence rejection, disabled-flag short-circuit, multi-pattern best-confidence selection, zero-entry-price guard, can_fill_now decision logic for all order types, tolerance band behaviour, exception payload, schema backward compat.

**Files**
- `backend/app/services/indicators.py` — `TradingSignal` extended (lines 14–32)
- `backend/app/services/pattern_entry.py` (new)
- `backend/app/services/paper_trading.py` — `PatternEntryDeferred` exception, `place_order` accepts new kwargs and applies `can_fill_now`
- `backend/app/services/trading_service.py` — `place_order` plumbs new kwargs with backwards-compat `try/except TypeError`
- `backend/app/services/agent_scheduler.py` — pattern selection block, deferral handling, `PatternEntryDeferred` import
- `backend/app/api/routes/settings.py` — three new RiskLimits fields with descriptions
- `backend/tests/test_pattern_entry.py` (new, 18 tests)

**Verification**
- ✅ 18/18 new unit tests pass
- ✅ Combined roadmap test suites: 62/62 (A-002 + A-004 + A-005 + A-007)
- ✅ All modules import cleanly
- ✅ Smoke test confirms `Bull flag breakout: Stop @ 50200 (bull_flag)` produces correct OrderPlan
- [ ] Live: log line `Pattern entry routing: bull_flag (78%) → Stop @ 50200 vs current 50000` should appear when patterns fire
- [ ] Live: deferred entries should produce `Pattern entry deferred — waiting for X price` in team chat instead of bad market fills

**Phase 2 (deferred — not in this fix)**
- ⚪ `live_trading.place_order` and `hl_live_trading.place_order` accept the new kwargs and pass through to `phemex.place_contract_order(order_type=...)`. Currently the trading_service tolerates the missing kwargs via try/except TypeError, so live mode falls back to Market until those backends are updated.
- ⚪ Track fill rates per `pattern_type` × `order_type` for backtesting weight tuning.
- ⚪ Add Wyckoff Spring / Upthrust pattern entries (currently the wyckoff strategy emits its own signal but doesn't surface as a `PatternSignal` in `technical_report.patterns`).

**Acceptance achieved**
- Bull flag detected with current price below flag high → order is a Stop-buy at the flag high (not a Market fill at current_price). If price hasn't reached the flag high, the trade defers until next cycle re-evaluates.
- Mean-reversion patterns above current → Limit-buy at the support level instead of Market at signal candle close.
- High-confidence patterns (≥0.65) override Market routing; weaker patterns fall back to Market preserving legacy behaviour.

---

### A-008 🔴 No execution intelligence — only market orders
**Severity:** Medium · **Found:** 2026-04-26

`execution_coordinator.optimize_execution_plan` exists and estimates slippage but the result isn't used to choose order type. With 0.12 % round-trip fees + slippage, the first 30–50 bps of every trade is conceded.

**Fix plan**
1. Add `recommended_order_type` and `recommended_limit_price` to `ExecutionPlan`
2. For size > $X, default to TWAP slicing (3–5 child orders over 60 s)
3. For pattern entries, use the pattern's natural level as a limit price
4. Track maker/taker fee ratio in metrics

**Acceptance:** Average fee + slippage cost per trade drops by ≥ 20 % on paper trading over 14 days.

---

## P2 — Missed Opportunities (Long-Term)

### O-001 ⚪ Per-strategy live-vs-backtest performance monitor
Surface drift between expected backtest performance and live results. Auto-disable strategies whose live Sharpe lags backtest by > 50 % over 30 days.

### O-002 ⚪ Multi-armed bandit allocation across (strategy, trader, regime)
Replace James's LLM-driven allocation (or augment it) with Thompson sampling over the discrete grid of (strategy, trader, regime) cells. LLM's role becomes interpretation/justification, not numeric allocation.

### O-003 ⚪ Order book microstructure
Currently decisions made on closed candles only. Add bid/ask imbalance, sweep detection, order-flow imbalance as gating inputs at execution time.

### O-004 ⚪ Liquidation heatmap / stop-hunt awareness
Crypto markets have systematic stop-hunts before pumps. Detect when nearby liquidity clusters are likely targets and time entries accordingly.

### O-005 ⚪ Adversarial regime detection
Detect when the bot is being adversely selected (e.g. consistently filled at unfavourable prints). Auto-throttle.

---

## How to update this document

1. New bug discovered → add P0 entry with B-NNN id, status 🔴, full root-cause writeup
2. Started work → flip to 🟡, link PR
3. Merged + verified → flip to 🟢, fill in `Fixed:` date, tick verification boxes
4. Decided not to do → flip to ⚪ with reason

**One source of truth.** Don't fork into per-bug markdown files — a single chronological list is easier to triage at standups.
