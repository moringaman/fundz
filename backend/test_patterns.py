#!/usr/bin/env python3
"""Test chart pattern detection and breakout backtest."""

import asyncio
import json
from datetime import datetime

from app.services.technical_analyst import technical_analyst
from app.clients.phemex import PhemexClient
from app.config import settings


async def test_pattern_detection():
    print("=" * 60)
    print("CHART PATTERN DETECTION TEST")
    print("=" * 60)
    
    analyst = technical_analyst
    
    # Test on multiple symbols
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    for symbol in symbols:
        print(f"\n{'='*40}")
        print(f"Symbol: {symbol}")
        print(f"{'='*40}")
        
        report = await analyst.analyze(symbol=symbol, timeframe="1h")
        
        print(f"Current Price: {report.current_price}")
        print(f"Overall Signal: {report.overall_signal} ({report.confidence:.0%})")
        print(f"\nPatterns Detected ({len(report.patterns)}):")
        
        for p in report.patterns:
            print(f"  [{p.pattern_type}] {p.direction} conf={p.confidence:.0%}")
            print(f"    Entry: {p.entry_price}, SL: {p.stop_loss}, TP1: {p.take_profit_1}")
            print(f"    R:R = {p.risk_reward}:1")
            print(f"    → {p.reasoning[:80]}")
        
        if not report.patterns:
            print("  No patterns detected")
        
        print(f"\nKey Observations:")
        for obs in report.key_observations[:5]:
            print(f"  - {obs}")
    
    return True


async def test_breakout_timing():
    print("\n" + "=" * 60)
    print("BREAKOUT TIMING VERIFICATION")
    print("=" * 60)
    
    analyst = technical_analyst
    symbol = "BTCUSDT"
    
    report = await analyst.analyze(symbol=symbol, timeframe="1h")
    
    print(f"\nCurrent Price: {report.current_price}")
    
    # Check for breakout patterns specifically
    breakout_patterns = [p for p in report.patterns if "flag" in p.pattern_type or "triangle" in p.pattern_type]
    
    print(f"\nBreakout Patterns ({len(breakout_patterns)}):")
    for p in breakout_patterns:
        print(f"\nPattern: {p.pattern_type}")
        print(f"  Direction: {p.direction}")
        print(f"  Confidence: {p.confidence:.0%}")
        print(f"  Entry Price: {p.entry_price}")
        print(f"  Stop Loss: {p.stop_loss}")
        print(f"  Take Profit 1: {p.take_profit_1}")
        print(f"  Take Profit 2: {p.take_profit_2}")
        print(f"  Risk:Reward: {p.risk_reward}:1")
        
        # Verify timing is correct
        if p.direction == "bullish":
            if p.entry_price > p.stop_loss and p.take_profit_1 > p.entry_price:
                print(f"  ✓ Entry > SL < TP (correct for long)")
            else:
                print(f"  ✗ Timing issue detected!")
        else:
            if p.entry_price < p.stop_loss and p.take_profit_1 < p.entry_price:
                print(f"  ✓ Entry < SL > TP (correct for short)")
            else:
                print(f"  ✗ Timing issue detected!")
        
        # Calculate position size for $1000 assume risk
        risk_amount = 1000
        position_size = risk_amount / (p.entry_price - p.stop_loss)
        tp1_profit = (p.take_profit_1 - p.entry_price) * position_size
        tp2_profit = (p.take_profit_2 - p.entry_price) * position_size
        
        print(f"  Position size (~$1000 risk): {position_size:.4f} {symbol}")
        print(f"  TP1 profit: ${tp1_profit:.2f}")
        print(f"  TP2 profit: ${tp2_profit:.2f}")
    
    if not breakout_patterns:
        print("  No breakout patterns currently detected")
        print("  (This is normal - patterns only appear when they form)")
    
    return True


async def test_historical_backtest():
    print("\n" + "=" * 60)
    print("HISTORICAL BACKTEST (Flag Pattern)")
    print("=" * 60)
    
    phemex = PhemexClient(
        api_key=settings.phemex_api_key,
        api_secret=settings.phemex_api_secret,
        testnet=settings.phemex_testnet
    )
    
    symbol = "BTCUSDT"
    interval = "15m"
    bars = 200
    
    klines = await phemex.get_klines(symbol, interval, bars)
    
    data = klines if isinstance(klines, list) else klines.get('data', [])
    
    if not data:
        print("No historical data available")
        return False
    
    closes = [float(k[5]) for k in data]
    highs = [float(k[3]) for k in data]
    lows = [float(k[4]) for k in data]
    volumes = [float(k[7]) for k in data]
    timestamps = [k[0] for k in data]
    
    print(f"\nHistorical data: {len(closes)} candles")
    print(f"Price range: {min(closes):.2f} - {max(closes):.2f}")
    
    # Simple flag detection on historical data
    signals = []
    
    for i in range(20, len(closes) - 10):
        # Look for bull flag: up move + consolidation + breakout
        window = closes[i-20:i]
        pole_move = (window[-1] - window[0]) / window[0]
        
        if pole_move > 0.03:  # 3%+ pole
            flag_start = i
            flag_end = min(i + 10, len(closes))
            flag_window = closes[flag_start:flag_end]
            
            if len(flag_window) > 3:
                flag_range = max(flag_window) - min(flag_window)
                flag_range_pct = flag_range / window[0]
                
                if flag_range_pct < pole_move * 0.5:  # Tight flag
                    # Check for breakout
                    for j in range(flag_end, min(flag_end + 5, len(closes))):
                        if closes[j] > max(flag_window):
                            # Valid breakout!
                            entry = closes[j]
                            stop_loss = min(flag_window) * 0.998
                            target = entry + (window[-1] - window[0])  # Same as pole
                            
                            signals.append({
                                "idx": j,
                                "timestamp": timestamps[j],
                                "entry": entry,
                                "sl": stop_loss,
                                "tp": target,
                                "pole_size": pole_move,
                            })
                            break
    
    print(f"\nHistorical Bull Flag Signals: {len(signals)}")
    
    for sig in signals[:5]:
        dt = datetime.fromtimestamp(sig['timestamp']/1000)
        print(f"\n  {dt.strftime('%Y-%m-%d %H:%M')}")
        print(f"    Entry: {sig['entry']:.2f}")
        print(f"    SL: {sig['sl']:.2f}")
        print(f"    TP: {sig['tp']:.2f}")
        
        # Calculate P&L
        if sig['entry'] > sig['sl']:
            rr = (sig['tp'] - sig['entry']) / (sig['entry'] - sig['sl'])
            print(f"    Risk:Reward: {rr:.2f}:1")
    
    return True


async def main():
    await test_pattern_detection()
    await test_breakout_timing()
    await test_historical_backtest()


if __name__ == "__main__":
    asyncio.run(main())