# Complete PnL Flow Trace: Agent Scheduler

## Executive Summary

**The Critical Issue:** `actual_trades` only counts **closed positions** (where PnL was calculated), not entries. With 125 filled paper trades, you have 125 **entries** but likely **0 exits**, so `actual_trades = 0`.

---

## 1. Data Structures

### AgentRun (Entry point for all trading activity)
[agent_scheduler.py:53-65]
```python
@dataclass
class AgentRun:
    agent_id: str
    timestamp: datetime
    symbol: str
    signal: str                    # "buy", "sell", "hold"
    confidence: float
    price: float
    executed: bool                 # True if order was placed
    pnl: Optional[float] = None    # ⚠️ CRITICAL: Only set on EXIT, not entry
    error: Optional[str] = None
    exit_reason: Optional[str] = None  # "stop-loss" | "take-profit" | "trailing-stop"
```

### AgentMetrics (What you see in the dashboard)
[agent_scheduler.py:68-85]
```python
@dataclass
class AgentMetrics:
    agent_id: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    actual_trades: int = 0    # ⚠️ ONLY counts runs where pnl is NOT None
    winning_trades: int = 0   # closed positions where pnl > 0
    total_pnl: float = 0.0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    last_run: Optional[datetime] = None
    win_rate: Optional[float] = None  # None until at least one trade closes
    avg_pnl: float = 0.0
```

---

## 2. Where Agent Entries Are Created

### Flow: Agent Evaluation → Trade Execution

**Start: [agent_scheduler.py:2800-2900]**
```python
async def _run_enabled_agents(self):
    """Main scheduler loop that runs enabled agents on their schedule"""
    for agent_id, config in self._enabled_agents.items():
        if config.get('_last_run') and (now - config['_last_run']).seconds < interval:
            continue
        
        # Non-grid agents go through run_agent()
        if _s_type != 'grid':
            result = await self.run_agent(
                agent_id=config['id'],
                name=config.get('name', ''),
                strategy_type=_s_type,
                trading_pairs=config.get('trading_pairs', []),
                # ... more params
                use_paper=use_paper_mode,
            )
```

**Entry Signal Generation: [agent_scheduler.py:3300-3400]**

The agent evaluates all trading pairs and picks the best signal:

```python
# Multi-symbol signal evaluation
for candidate_symbol in trading_pairs:
    if best_symbol is None or best_signal == 'hold':
        # Run strategy on this symbol
        if strategy_type == 'ai':
            llm_result = await llm_service.generate_signal(...)
            sig = llm_result.action        # "buy", "sell", or "hold"
            conf = llm_result.confidence
        else:
            signal_result = self.indicator_service.generate_signal(df, ...)
            sig = signal_result.signal.value
            conf = signal_result.confidence
        
        if sig in ('buy', 'sell') and conf > best_confidence:
            best_symbol = candidate_symbol
            best_confidence = conf
            best_signal = sig

# If no buy/sell signal from any pair → record as hold
if best_symbol is None or best_signal == 'hold':
    self._record_run(agent_id, symbol, "hold", best_confidence, current_price, False)
    # ↑ executed=False, pnl=None (default)
```

**Entry Execution: Paper vs Live [agent_scheduler.py:4240-4260]**

```python
# PAPER TRADING PATH
if use_paper and paper_trading._enabled:
    try:
        order = await paper_trading.place_order(
            symbol=symbol,
            side=signal,  # "buy" or "sell"
            quantity=quantity,
            price=current_price,
            agent_id=agent_id,
            trader_id=_trader_id,
            stop_loss_price=adjusted_sl,
            take_profit_price=adjusted_tp,
            trailing_stop_pct=trailing_stop_pct,
        )
        executed = True  # ✓ Order was placed
        
        # ... register scale-out levels, post to team chat, etc.
        
    except Exception as e:
        logger.error(f"Paper trade failed: {e}")

# LIVE TRADING PATH  
elif not use_paper and live_trading._enabled:
    try:
        order = await live_trading.place_order(
            symbol=symbol,
            side=signal,  # "buy" or "sell"
            quantity=quantity,
            price=current_price,
            agent_id=agent_id,
            trader_id=_trader_id,
            stop_loss_price=adjusted_sl,
            take_profit_price=adjusted_tp,
        )
        executed = True  # ✓ Order was placed
        
    except Exception as e:
        logger.error(f"LIVE trade failed: {e}")
```

### Record the Entry: [agent_scheduler.py:4415]

After execution attempt, record the entry **with NO PnL**:

```python
self._record_run(
    agent_id, symbol, signal, confidence, current_price, 
    executed,  # True if order placed
    pnl,       # ⚠️ STILL NONE at entry! Defaults to None
    use_paper=use_paper
)
```

---

## 3. Where PnL Gets Set on AgentRun (EXITS ONLY)

### Position Monitoring: [agent_scheduler.py:1550-1820]

This runs on **every scheduler loop iteration** (60-second tick):

```python
async def _monitor_open_positions(self):
    """Check SL/TP triggers on all open positions"""
    
    for pos in trading_service.get_positions(...):
        # Calculate current P&L percentage
        current_price = market_data[pos.symbol].price
        
        if is_long:
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - current_price) / pos.entry_price
        
        # Check exit triggers
        check = self._check_position_exit(
            pos=pos,
            current_price=current_price,
            pnl_pct=pnl_pct,
            # ... more params
        )
        
        if check.action == "exit":
            # ── CALCULATE PnL ────────────────────────────────────
            # Refresh position state (may have changed due to scale-out)
            _fresh_pos = await _svc.get_position(pos.id)
            _close_qty = _fresh_pos.quantity or pos.quantity
            _close_entry = _fresh_pos.entry_price or entry
            _scale_out_pnl = _fresh_pos.realized_pnl or pos.realized_pnl or 0.0
            
            # Close the position
            if _pos_is_paper:
                await _svc.place_order(
                    symbol=pos.symbol,
                    side=exit_side,  # opposite of entry
                    quantity=_close_qty,
                    price=current_price,
                    agent_id=pos.agent_id,
                )
            else:
                await _svc.close_position(pos.id)
            
            # Calculate net PnL (entry fee + exit fee + price move)
            _fee_rate = _svc.fee_rate_for(pos.symbol)
            _entry_fee = (_close_entry * _close_qty) * _fee_rate
            _exit_fee = (current_price * _close_qty) * _fee_rate
            
            if is_short:
                _final_pnl = (_close_entry - current_price) * _close_qty - _entry_fee - _exit_fee
            else:
                _final_pnl = (current_price - _close_entry) * _close_qty - _entry_fee - _exit_fee
            
            # Total P&L including scale-out profits
            pnl = _final_pnl + _scale_out_pnl  # ✓ PnL calculated!
            risk_manager.record_pnl(pnl)
            
            # ── RECORD EXIT WITH PnL ────────────────────────────
            if pos.agent_id and pos.agent_id in self._agent_metrics:
                m = self._agent_metrics[pos.agent_id]
                m.total_pnl += pnl
                
                # Append AgentRun DIRECTLY (bypasses _record_run)
                # This is where the EXIT gets recorded with pnl set
                self._agent_runs.append(AgentRun(
                    agent_id=pos.agent_id,
                    timestamp=datetime.now(),
                    symbol=pos.symbol,
                    signal="sell" if not is_short else "buy",
                    confidence=0,
                    price=current_price,
                    executed=True,
                    pnl=pnl,  # ✓ PnL IS SET HERE (exit only)
                    exit_reason=_exit_type_pre,
                ))
```

**KEY INSIGHT:** Position exits append directly to `_agent_runs` and update metrics in-line. **They do NOT call `_record_run()`.**

---

## 4. The Critical Counting Logic in `_record_run`

### [agent_scheduler.py:4465-4520]

```python
def _record_run(
    self,
    agent_id: str,
    symbol: str,
    signal: str,
    confidence: float,
    price: float,
    executed: bool,
    pnl: Optional[float] = None,  # ← Default is None
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
        pnl=pnl,  # ← None for entries, <value> for exits
        error=error,
    )
    self._agent_runs.append(run)
    
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
    
    # ⚠️ THIS IS THE KEY COUNTING LOGIC ⚠️
    if pnl is not None:  # ← Only true for EXITS
        metrics.actual_trades += 1       # ← Increment only if pnl exists
        if pnl > 0:
            metrics.winning_trades += 1
        metrics.total_pnl += pnl
        metrics.avg_pnl = metrics.total_pnl / metrics.actual_trades if metrics.actual_trades > 0 else 0
    
    # Win rate = winning closed trades / all closed trades
    if metrics.actual_trades > 0:
        metrics.win_rate = metrics.winning_trades / metrics.actual_trades
```

**The Counter Logic:**
- Entry record: `executed=True, pnl=None` → `actual_trades` NOT incremented
- Exit record: `executed=True, pnl=<value>` → `actual_trades` incremented by 1

---

## 5. All 17 Places AgentRun Is Created

### WITH `pnl` Set (EXIT ONLY - 1 place):
**[1768] Position exit/SL/TP trigger:**
```python
self._agent_runs.append(AgentRun(
    agent_id=pos.agent_id,
    timestamp=datetime.now(),
    symbol=pos.symbol,
    signal="sell" if not is_short else "buy",
    confidence=0,
    price=current_price,
    executed=True,
    pnl=pnl,  # ✓ Only place pnl is set
    exit_reason=_exit_type_pre,
))
```

### WITH `pnl=None` (ENTRIES + FAILURES - 16 places):
All these call `_record_run()` which defaults pnl to None:

| Line | Context | executed | pnl |
|------|---------|----------|-----|
| 2973 | Grid cancelled | False | None |
| 2995 | Grid error | False | None |
| 3022 | Grid error | False | None |
| 3166 | Grid entry attempt | ? | None |
| 3178 | No grid active | False | None |
| 3383 | No best signal | False | None |
| 3681 | TP too low after fees | False | None |
| 3710 | Allocation exceeded | False | None |
| 3822 | Below minimum notional | False | None |
| 3840 | Fund fully deployed | False | None |
| 3877 | Risk manager block | False | None |
| 3953 | Correlation gate block | False | None |
| 3978 | Concurrency limit block | False | None |
| 4006 | TA veto | False | None |
| 4120 | Post-close cooldown | False | None |
| 4206 | Backtest hard block | False | None |
| 4415 | **Main entry execution** | **True** | **None** |
| 4430 | Exception handler | False | None |
| 5366 | Trader checkin | ? | None |

---

## 6. Paper Trading vs Live Trading Exact Code Flow

### PAPER TRADING ENTRY: [agent_scheduler.py:4240-4260]

```
run_agent()
  ↓
[Signal evaluation: buy/sell/hold]
  ↓
[Gate checks: whale, US open, London, etc.]
  ↓
[Pre-trade backtest cache check]
  ↓
if use_paper and paper_trading._enabled:
    order = await paper_trading.place_order(
        symbol=symbol,
        side=signal,
        quantity=quantity,
        price=current_price,
        agent_id=agent_id,
        trader_id=_trader_id,
        stop_loss_price=adjusted_sl,
        take_profit_price=adjusted_tp,
        trailing_stop_pct=trailing_stop_pct,
    )
    executed = True
    
    [Post to team chat, Telegram, etc.]
  ↓
_record_run(agent_id, symbol, signal, confidence, current_price, executed=True, pnl=None)
  ↓
AgentRun created with executed=True, pnl=None
  ↓
_persist_run() saves to DB asynchronously
```

### LIVE TRADING ENTRY: [agent_scheduler.py:4280-4340]

```
run_agent()
  ↓
[Same gates, backtest, etc.]
  ↓
elif not use_paper and live_trading._enabled:
    order = await live_trading.place_order(
        symbol=symbol,
        side=signal,
        quantity=quantity,
        price=current_price,
        agent_id=agent_id,
        trader_id=_trader_id,
        stop_loss_price=adjusted_sl,
        take_profit_price=adjusted_tp,
        trailing_stop_pct=trailing_stop_pct,
    )
    executed = True
    
    [Post to team chat, Telegram, etc.]
  ↓
_record_run(agent_id, symbol, signal, confidence, current_price, executed=True, pnl=None)
  ↓
AgentRun created with executed=True, pnl=None
  ↓
_persist_run() saves to DB asynchronously
```

### POSITION EXIT (Both Paper & Live): [agent_scheduler.py:1550-1820]

```
_monitor_open_positions() [runs every 60 seconds]
  ↓
for pos in all_open_positions:
    if current_price hits SL/TP:
        Fetch fresh position state
        ↓
        Close position:
          - paper: place_order(opposite_side)
          - live: close_position()
        ↓
        Calculate PnL:
          - entry_fee = entry_price × qty × fee_rate
          - exit_fee = current_price × qty × fee_rate
          - _final_pnl = (price_move - fees)
          - pnl = _final_pnl + scale_out_pnl
        ↓
        Update metrics in-place
        ↓
        Append AgentRun with pnl=<value>
          (bypasses _record_run, posts directly)
        ↓
        Post to team chat, Telegram
```

---

## 7. Why `actual_trades = 0` with 125 Filled Orders

### Current State

```
Entry Orders (executed=True, pnl=None):
├─ Order 1: Buy ETH @ $2000, qty=1.0, executed=True, pnl=None
├─ Order 2: Sell BTC @ $45000, qty=0.5, executed=True, pnl=None
├─ Order 3: Buy SOL @ $150, qty=10, executed=True, pnl=None
├─ ...
└─ Order 125: executed=True, pnl=None

Counter Update: actual_trades += 1 only when pnl is NOT None
                ↓
                Since 125 entries have pnl=None → actual_trades NOT incremented
                ↓
                actual_trades = 0
```

### For `actual_trades` to increase

```
Position Closure (exit triggered):
├─ SL hit: Close position, calculate pnl = -250 USDT
├─ Record AgentRun with pnl=-250 (sets actual_trades += 1)
│   ↓
│   actual_trades = 1
│   
├─ TP hit: Close position, calculate pnl = +500 USDT
├─ Record AgentRun with pnl=+500 (sets actual_trades += 1)
│   ↓
│   actual_trades = 2
│   winning_trades = 1 (pnl > 0)
│   win_rate = 1/2 = 50%
```

---

## 8. What the Dashboard Shows (Metrics Endpoint)

### [agent_scheduler.py:2280-2310]

```python
def _build_agent_metrics_list(self, agents: List[dict]) -> List[dict]:
    """Build agent metrics list from in-memory metrics + agent config"""
    metrics_list = []
    for agent_id, metrics in self._agent_metrics.items():
        metrics_list.append({
            "agent_id": agent_id,
            "agent_name": agent_name_map.get(agent_id, agent_id),
            "total_runs": metrics.total_runs,           # All evaluations
            "successful_runs": metrics.successful_runs, # Non-error evaluations
            "actual_trades": getattr(metrics, "actual_trades", 0) or 0,  # ⚠️ Only closed
            "winning_trades": getattr(metrics, "winning_trades", 0) or 0,  # Closed + pnl>0
            "total_pnl": metrics.total_pnl,
            "win_rate": metrics.win_rate,  # None if actual_trades=0
            "last_run": metrics.last_run.isoformat() if metrics.last_run else None,
        })
```

### Example with 125 filled orders but 0 exits

```json
{
  "agent_id": "agent-momentum-1",
  "agent_name": "Momentum Scanner",
  "total_runs": 250,              // Two evaluations per hour
  "successful_runs": 125,         // 125 buy/sell signals executed
  "actual_trades": 0,             // ⚠️ NO EXITS YET
  "winning_trades": 0,
  "total_pnl": 0.0,
  "win_rate": null,               // null when actual_trades=0
  "last_run": "2025-04-17T15:30:00Z"
}
```

---

## 9. Key Differences: Entries vs Exits

| Aspect | Entry | Exit |
|--------|-------|------|
| **Trigger** | Agent signal evaluation (60s cadence) | SL/TP hit (60s monitoring) |
| **PnL Set?** | ❌ No (pnl=None) | ✓ Yes (pnl=calculated) |
| **Method** | `_record_run()` call | Direct append to `_agent_runs` |
| **actual_trades++?** | ❌ No | ✓ Yes |
| **Counts as "closed"?** | ❌ No | ✓ Yes |
| **Example** | "Bought 1 BTC" | "Sold 1 BTC for +$500" |

---

## 10. Fix Options

**Option A: Separate "Entries" Counter**
```python
# In AgentMetrics add:
entry_signals: int = 0      # All buy/sell entries
closed_trades: int = 0      # Only when position closes (pnl set)

# In _record_run:
if executed and pnl is None:
    metrics.entry_signals += 1
if pnl is not None:
    metrics.closed_trades += 1
```

**Option B: Clarify Documentation**
Update the comment at line 74:
```python
# Before:
actual_trades: int = 0    # closed positions only (pnl was set)

# After:
actual_trades: int = 0    # closed positions only (pnl was set)
                          # Does NOT include open entries—only when SL/TP/trailing-stop closes the position
```

**Option C: Add Entry Count to Metrics**
```python
# When _record_run called with executed=True, pnl=None:
if executed:
    metrics.executed_entries += 1
```

---

## Summary

```
125 Filled Paper Trades (Entries)
  ↓
Each entry: executed=True, pnl=None
  ↓
_record_run() checks: if pnl is not None → actual_trades++
  ↓
pnl=None → actual_trades++ SKIPPED
  ↓
actual_trades remains 0 until first position closes
  ↓
First position closes: pnl=calculated → actual_trades=1
```

The system is working as designed, but the naming (`actual_trades`) is misleading. A better name would be `closed_trades` or `closed_positions_with_pnl`.
