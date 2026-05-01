from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
import numpy as np
import logging

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService
from app.utils import fmt_price

logger = logging.getLogger(__name__)

# Maps each primary timeframe to (primary, mid, high) analysis frames.
# Phemex interval strings: 1m 3m 5m 15m 30m 1h 2h 3h 4h 6h 12h 1d 3d 1w 1M
_TF_LADDER: Dict[str, Tuple[str, str, str]] = {
    "1m":  ("1m",  "5m",  "15m"),
    "3m":  ("3m",  "15m", "1h"),
    "5m":  ("5m",  "15m", "1h"),
    "15m": ("15m", "1h",  "4h"),
    "30m": ("30m", "1h",  "4h"),
    "1h":  ("1h",  "4h",  "1d"),
    "2h":  ("2h",  "4h",  "1d"),
    "3h":  ("3h",  "4h",  "1d"),
    "4h":  ("4h",  "1d",  "1w"),
    "6h":  ("6h",  "1d",  "1w"),
    "12h": ("12h", "1d",  "1w"),
    "1d":  ("1d",  "1w",  "1M"),
    "3d":  ("3d",  "1w",  "1M"),
    "1w":  ("1w",  "1M",  "1M"),
    "1M":  ("1M",  "1M",  "1M"),
}
_DEFAULT_LADDER = _TF_LADDER["1h"]


@dataclass
class PriceLevels:
    support: List[float]
    resistance: List[float]
    pivot_points: Dict[str, float]
    fibonacci_retracements: Dict[str, float]
    fibonacci_extensions: Dict[str, float]
    # Nearest psychologically significant round number — acts as a price magnet.
    # Retail anchors orders to these levels; institutions use them for stop-runs.
    # Shape: {"level": float, "distance_pct": float, "direction": "above"|"below"|"at"}
    round_number_proximity: Optional[Dict] = None

    # ── Structural-level helpers ──────────────────────────────────────────────
    def all_levels_above(self, price: float) -> List[float]:
        """Return all structural levels above *price*, sorted ascending."""
        levels: List[float] = []
        levels.extend(r for r in self.resistance if r > price)
        levels.extend(v for v in self.fibonacci_retracements.values() if v > price)
        levels.extend(v for v in self.fibonacci_extensions.values() if v > price)
        for k, v in self.pivot_points.items():
            if v > price and k.startswith("r"):
                levels.append(v)
        return sorted(set(round(l, 8) for l in levels))

    def all_levels_below(self, price: float) -> List[float]:
        """Return all structural levels below *price*, sorted descending."""
        levels: List[float] = []
        levels.extend(s for s in self.support if s < price)
        levels.extend(v for v in self.fibonacci_retracements.values() if v < price)
        levels.extend(v for v in self.fibonacci_extensions.values() if v < price)
        for k, v in self.pivot_points.items():
            if v < price and k.startswith("s"):
                levels.append(v)
        return sorted(set(round(l, 8) for l in levels), reverse=True)


def snap_tp_to_structure(
    candidate_tp: float,
    price_levels: PriceLevels,
    current_price: float,
    is_short: bool,
    max_adjust_pct: float = 0.25,
) -> float:
    """Snap a candidate TP to the nearest structural level.

    For LONGS  → find the nearest resistance/fib BELOW the candidate TP so
                 TP sits just before a ceiling where sellers congregate.
    For SHORTS → find the nearest support/fib ABOVE the candidate TP so
                 TP sits just above a floor where buyers congregate.

    If no structural level is close enough (within *max_adjust_pct* of the
    original candidate), the candidate is returned unchanged.

    A small 0.15 % margin is subtracted (longs) / added (shorts) so TP
    triggers just before the level, not right at it.
    """
    MARGIN = 0.0015  # 0.15 % shy of the structural level

    if is_short:
        # TP is BELOW entry for shorts — find support levels above TP
        # (i.e. between TP and entry) that could stall the decline.
        levels = price_levels.all_levels_below(current_price)
        # Levels below price, sorted descending — pick the first one that
        # is near (but at or above) the candidate, or the first one below.
        best = None
        for lvl in levels:
            if lvl < candidate_tp:
                continue  # level is farther than candidate — skip
            if lvl <= current_price:
                best = lvl
                break
        if best is None:
            # No level between candidate and price — pick closest below entry
            for lvl in levels:
                if lvl >= candidate_tp:
                    best = lvl
                    break
        if best and abs(best - candidate_tp) / max(candidate_tp, 1e-10) <= max_adjust_pct:
            return round(best * (1 + MARGIN), 8)  # just above support
    else:
        # TP is ABOVE entry for longs — find resistance levels below TP
        # that could cap the advance.
        levels = price_levels.all_levels_above(current_price)
        best = None
        for lvl in levels:
            if lvl > candidate_tp:
                continue  # level is farther than candidate — skip
            best = lvl  # last one still below or at candidate
        if best is None:
            # No level between entry and candidate — pick closest above entry
            for lvl in levels:
                if lvl <= candidate_tp:
                    best = lvl
        if best and abs(best - candidate_tp) / max(candidate_tp, 1e-10) <= max_adjust_pct:
            return round(best * (1 - MARGIN), 8)  # just below resistance

    return candidate_tp


def snap_sl_to_structure(
    candidate_sl: float,
    price_levels: PriceLevels,
    current_price: float,
    is_short: bool,
    max_widen_pct: float = 0.15,
) -> float:
    """Snap a candidate SL past the nearest structural level.

    For LONGS  → SL should sit just BELOW the nearest support beneath
                 entry, so normal support bounces don't trigger the stop.
    For SHORTS → SL should sit just ABOVE the nearest resistance above
                 entry, so normal resistance probes don't trigger the stop.

    Only widens the SL (never tightens it) — if the structural level
    is farther out than *max_widen_pct* from the candidate, keep the
    original. A 0.20 % buffer is added past the level.
    """
    BUFFER = 0.0020  # 0.20 % past the structural level

    if is_short:
        # SL is ABOVE entry for shorts — find resistance above entry
        levels = price_levels.all_levels_above(current_price)
        best = None
        for lvl in levels:
            # Pick the first resistance above current_price
            if lvl >= candidate_sl:
                best = lvl
                break
        if best is None and levels:
            best = levels[0]  # closest above
        if best:
            ideal = round(best * (1 + BUFFER), 8)  # just above
            # Only widen (raise) the SL, and only within max_widen_pct
            if ideal > candidate_sl and (ideal - candidate_sl) / max(candidate_sl, 1e-10) <= max_widen_pct:
                return ideal
    else:
        # SL is BELOW entry for longs — find support below entry
        levels = price_levels.all_levels_below(current_price)
        best = None
        for lvl in levels:
            # Pick the first support below current_price
            if lvl <= candidate_sl:
                best = lvl
                break
        if best is None and levels:
            best = levels[0]  # closest below
        if best:
            ideal = round(best * (1 - BUFFER), 8)  # just below
            # Only widen (lower) the SL, and only within max_widen_pct
            if ideal < candidate_sl and (candidate_sl - ideal) / max(candidate_sl, 1e-10) <= max_widen_pct:
                return ideal

    return candidate_sl


def _psychological_grid(price: float) -> float:
    """Return the psychological level grid size for a given price.

    Round numbers (00 endings) and halfway marks (50) cluster orders.
    The grid adapts to the instrument's price scale:
      BTC @ 48k   → 500   (levels at 48,000, 48,500, 49,000…)
      ETH @ 3k    → 50    (levels at 3,000, 3,050, 3,100…)
      SOL @ 120   → 5     (levels at 120, 125, 130…)
      XRP @ 0.50  → 0.01  (levels at 0.50, 0.51, 0.52…)
      DOGE @ 0.08 → 0.005 (levels at 0.080, 0.085, 0.090…)
    """
    if price >= 250000:  return 5000
    if price >= 50000:   return 500
    if price >= 10000:   return 100
    if price >= 2500:    return 50
    if price >= 500:     return 10
    if price >= 100:     return 5
    if price >= 25:      return 1.0
    if price >= 5:       return 0.50
    if price >= 1:       return 0.10
    if price >= 0.10:    return 0.01
    if price >= 0.01:    return 0.001
    if price >= 0.001:   return 0.0001
    if price >= 0.0001:  return 0.00001
    return 0.000001


def snap_to_psychological(
    candidate_price: float,
    entry_price: float,
    is_sl: bool,
    margin_pct: float = 0.002,
    max_adjust_pct: float = 0.01,
) -> float:
    """Snap a TP or SL price relative to the nearest psychological round number.

    Psychological levels (round numbers) act as price magnets because retail
    traders cluster orders and stops at these obvious prices.

    * For **take-profit**: sit *just before* the level so the order fills
      before price hits the wall where liquidity reverses.
    * For **stop-loss**:   sit *just past*  the level so noise/wicks to the
      round number don't stop you out prematurely.

    Only adjusts if the candidate is within *max_adjust_pct* of a psychological
    level.  *margin_pct* controls how far before/past the level the snapped
    price lands.
    """
    grid = _psychological_grid(candidate_price)
    nearest_psych = round(candidate_price / grid) * grid
    dist_pct = abs(candidate_price - nearest_psych) / max(candidate_price, 1e-10)
    if dist_pct > max_adjust_pct:
        return candidate_price

    margin = nearest_psych * margin_pct
    toward_entry = 1 if entry_price > candidate_price else -1

    if is_sl:
        # SL: move further from entry (past the level)
        snapped = nearest_psych - margin if toward_entry > 0 else nearest_psych + margin
    else:
        # TP: move closer to entry (just before the level)
        snapped = nearest_psych + margin if toward_entry > 0 else nearest_psych - margin

    return round(snapped, 8)


def _is_near_resistance(current_price: float, price_levels: Optional[PriceLevels], proximity_pct: float = 0.005) -> bool:
    """Check if current price is within proximity_pct of nearest resistance (0.5% default)."""
    if not price_levels or not price_levels.resistance:
        return False
    nearest_res = min(price_levels.resistance, key=lambda x: abs(x - current_price))
    distance_pct = abs(nearest_res - current_price) / max(nearest_res, 1e-10)
    return distance_pct <= proximity_pct


def _is_near_support(current_price: float, price_levels: Optional[PriceLevels], proximity_pct: float = 0.005) -> bool:
    """Check if current price is within proximity_pct of nearest support (0.5% default)."""
    if not price_levels or not price_levels.support:
        return False
    nearest_sup = min(price_levels.support, key=lambda x: abs(x - current_price))
    distance_pct = abs(current_price - nearest_sup) / max(nearest_sup, 1e-10)
    return distance_pct <= proximity_pct


@dataclass
class PatternSignal:
    pattern_type: str
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    reasoning: str
    timeframe: str = "1h"  # Timeframe the pattern was detected on


@dataclass
class MultiTimeframeAnalysis:
    symbol: str
    timeframe_1h: Dict[str, Any]
    timeframe_4h: Dict[str, Any]
    timeframe_1d: Dict[str, Any]
    alignment: str
    trend_confirmation: bool
    confluence_score: float
    trade_setup: Optional[PatternSignal]
    # Actual timeframe labels used for primary/mid/high tiers
    tf_primary: str = "1h"
    tf_mid: str = "4h"
    tf_high: str = "1d"


@dataclass
class TechnicalAnalystReport:
    timestamp: datetime
    symbol: str
    current_price: float
    price_levels: PriceLevels
    patterns: List[PatternSignal]
    multi_timeframe: Optional[MultiTimeframeAnalysis]
    overall_signal: str
    confidence: float
    key_observations: List[str]


class TechnicalAnalyst:
    def __init__(self):
        self.indicator_service = IndicatorService()
        self.phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet
        )
    
    async def analyze(self, symbol: str = "BTCUSDT", timeframe: str = "1h") -> TechnicalAnalystReport:
        try:
            tf_primary, tf_mid, tf_high = _TF_LADDER.get(timeframe, _DEFAULT_LADDER)
            # EXPANDED candle counts for robust indicator calculation and multi-timeframe confirmation
            # Requires: RSI (14), MACD (26), EMA (50), SMA (200) for full reliability
            bars_primary = 500  # EXPANDED from 200: ~20.8 days on 1h, ~3.5 days on 15m, ~83 days on 4h
            bars_mid = 300      # EXPANDED from 200: mid-frame trend confirmation
            bars_high = 150     # EXPANDED from 100: longer-term structural support

            klines_primary = await self.phemex.get_klines(symbol, tf_primary, bars_primary)
            klines_mid     = await self.phemex.get_klines(symbol, tf_mid,     bars_mid)
            klines_high    = await self.phemex.get_klines(symbol, tf_high,    bars_high)

            data_primary = self._parse_klines(klines_primary)
            data_mid     = self._parse_klines(klines_mid)
            data_high    = self._parse_klines(klines_high)

            if data_primary is None or (isinstance(data_primary, pd.DataFrame) and data_primary.empty) or (not isinstance(data_primary, pd.DataFrame) and not data_primary):
                return self._empty_report(symbol)
            # Require 200+ candles for accurate RSI, MACD, and trend analysis
            if len(data_primary) < 200:
                return self._empty_report(symbol)

            current_price = data_primary['close'].iloc[-1]

            price_levels = self._calculate_price_levels(data_primary)
            patterns = self._identify_patterns(data_primary, current_price, price_levels, tf_primary)
            multi_tf = self._analyze_multitimeframe(data_primary, data_mid, data_high, current_price, symbol, tf_primary, tf_mid, tf_high)

            signal, confidence = self._generate_overall_signal(
                patterns, multi_tf, price_levels, current_price
            )

            observations = self._generate_observations(
                price_levels, patterns, multi_tf, current_price
            )

            # Additive: append Hyperliquid whale observations (graceful degradation)
            try:
                from app.services.whale_intelligence import whale_intelligence
                whale_report = await whale_intelligence.fetch_whale_report()
                if whale_report is not None:
                    coin = whale_intelligence.symbol_to_coin(symbol)
                    bias = whale_report.coin_biases.get(coin)
                    observations.extend(whale_intelligence.build_ta_observations(symbol, bias))
            except Exception:
                pass  # TA continues without whale data

            return TechnicalAnalystReport(
                timestamp=datetime.utcnow(),
                symbol=symbol,
                current_price=current_price,
                price_levels=price_levels,
                patterns=patterns,
                multi_timeframe=multi_tf,
                overall_signal=signal,
                confidence=confidence,
                key_observations=observations
            )

        except Exception as e:
            logger.error(f"Technical analysis failed: {e}")
            return self._empty_report(symbol)

    def _parse_klines(self, klines) -> pd.DataFrame:
        data = klines.get('data', klines) if isinstance(klines, dict) else klines
        if not data:
            return pd.DataFrame()
        
        df_data = []
        for k in data:
            df_data.append({
                'time': k[0] / 1000,
                'open': float(k[2]),
                'high': float(k[3]),
                'low': float(k[4]),
                'close': float(k[5]),
                'volume': float(k[7]),
            })
        
        df = pd.DataFrame(df_data)
        return df.sort_values('time')

    def _calculate_price_levels(self, df: pd.DataFrame) -> PriceLevels:
        recent = df.tail(50)
        
        highs = recent['high'].values
        lows = recent['low'].values
        
        support_levels = []
        resistance_levels = []
        
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                resistance_levels.append(highs[i])
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                support_levels.append(lows[i])
        
        support_levels = sorted(support_levels)[:5] if support_levels else []
        resistance_levels = sorted(resistance_levels, reverse=True)[:5] if resistance_levels else []
        
        current = df['close'].iloc[-1]
        swing_high = recent['high'].max()
        swing_low = recent['low'].min()
        swing_range = swing_high - swing_low

        fib_ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
        fib_retracements = {}
        fib_extensions = {}
        
        for ratio in fib_ratios:
            retracement = swing_high - (swing_range * ratio)
            fib_retracements[f"{int(ratio*100)}%"] = retracement
            
            extension = swing_low + (swing_range * ratio)
            fib_extensions[f"{int(ratio*100)}%"] = extension

        pivot_data = self._calculate_pivot_points(df.tail(20))

        # Detect nearest psychological round-number level — retail and algo orders
        # cluster at these prices, creating predictable support/resistance magnets.
        current_price = float(df['close'].iloc[-1])
        round_prox = self._detect_round_number_proximity(current_price)

        return PriceLevels(
            support=support_levels,
            resistance=resistance_levels,
            pivot_points=pivot_data,
            fibonacci_retracements=fib_retracements,
            fibonacci_extensions=fib_extensions,
            round_number_proximity=round_prox,
        )

    def _detect_round_number_proximity(self, price: float) -> Optional[Dict]:
        """Find the nearest psychological round-number price level within 2%.

        Retail traders anchor limit orders and mental stops to these levels.
        Institutions exploit this predictability for stop-runs (sweeps) and
        liquidity grabs — exactly the setups Wyckoff and mean-reversion trade.

        Candidate generation scales with price magnitude so $1k BTC levels
        don't show up on a $2 altcoin and vice versa.
        """
        if price <= 0:
            return None

        candidates: List[float] = []
        # Generate round-number candidates at multiple scales proportional to price.
        # Arrr — the multipliers below cover the full crypto price spectrum.
        # The logic: find the order of magnitude, then generate multiples at 1×, 2×, 5×, 10× that unit.
        import math
        magnitude = 10 ** math.floor(math.log10(price))
        for unit_multiplier in (1, 2, 5, 10):
            unit = magnitude * unit_multiplier
            # Nearest multiple below and above price
            lower = math.floor(price / unit) * unit
            upper = lower + unit
            if lower > 0:
                candidates.append(float(lower))
            candidates.append(float(upper))

        # Also include half-magnitudes (e.g. $50k, $150k) — widely watched in crypto
        half_unit = magnitude * 5
        half_lower = math.floor(price / half_unit) * half_unit
        candidates.append(float(half_lower))
        candidates.append(float(half_lower + half_unit))

        # Pick the closest candidate within 2% of current price
        _THRESHOLD = 0.02
        best: Optional[Dict] = None
        for level in candidates:
            if level <= 0:
                continue
            distance_pct = abs(price - level) / level
            if distance_pct <= _THRESHOLD:
                if best is None or distance_pct < best["distance_pct"]:
                    direction = "at" if distance_pct < 0.001 else ("above" if level > price else "below")
                    best = {
                        "level": round(level, 8),
                        "distance_pct": round(distance_pct, 6),
                        "direction": direction,
                    }

        return best

    def _calculate_pivot_points(self, df: pd.DataFrame) -> Dict[str, float]:
        if len(df) < 2:
            return {}
        
        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]
        last_close = df['close'].iloc[-1]
        last_open = df['open'].iloc[-1]

        pivot = (last_high + last_low + last_close) / 3
        r1 = 2 * pivot - last_low
        s1 = 2 * pivot - last_high
        r2 = pivot + (last_high - last_low)
        s2 = pivot - (last_high - last_low)
        r3 = last_high + 2 * (pivot - last_low)
        s3 = last_low - 2 * (last_high - pivot)

        return {
            "pivot": pivot,
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "s1": s1,
            "s2": s2,
            "s3": s3
        }

    def _identify_patterns(self, df: pd.DataFrame, current_price: float, price_levels: Optional[PriceLevels] = None, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        
        closes = df['close']
        highs = df['high']
        lows = df['low']
        volumes = df['volume']
        
        rsi = self.indicator_service.calculate_rsi(closes).iloc[-1]
        macd_data = self.indicator_service.calculate_macd(closes)
        macd = macd_data['macd'].iloc[-1]
        macd_signal = macd_data['signal'].iloc[-1]
        bb = self.indicator_service.calculate_bollinger_bands(closes)
        
        bb_upper = bb['upper'].iloc[-1]
        bb_middle = bb['middle'].iloc[-1]
        bb_lower = bb['lower'].iloc[-1]

        # Pre-compute nearest structural targets for MACD patterns
        # so TP1/TP2 sit at real chart levels, not arbitrary % offsets.
        _levels_above: List[float] = price_levels.all_levels_above(current_price) if price_levels else []
        _levels_below: List[float] = price_levels.all_levels_below(current_price) if price_levels else []

        # Configurable entry block distance to opposing structure (support/resistance).
        _sr_block_pct = 0.005  # fallback: 0.5%
        try:
            from app.api.routes.settings import get_trading_gates
            _sr_block_pct = float(get_trading_gates().sr_proximity_block_pct)
        except Exception:
            pass

        # Chart Pattern Detections (investingoal.com patterns)
        # Each pattern tagged with the timeframe it was detected on
        flag_patterns = self._detect_flag_pattern(df, current_price, timeframe)
        patterns.extend(flag_patterns)
        
        triangle_patterns = self._detect_triangle_patterns(df, current_price, timeframe)
        patterns.extend(triangle_patterns)
        
        hs_patterns = self._detect_head_shoulders(df, current_price, timeframe)
        patterns.extend(hs_patterns)
        
        dt_patterns = self._detect_double_triple(df, current_price, timeframe)
        patterns.extend(dt_patterns)
        
        cup_patterns = self._detect_cup_handle(df, current_price, timeframe)
        patterns.extend(cup_patterns)
        
        wedge_patterns = self._detect_wedge(df, current_price, timeframe)
        patterns.extend(wedge_patterns)
        
        rectangle_patterns = self._detect_rectangle(df, current_price, timeframe)
        patterns.extend(rectangle_patterns)

        # Existing patterns below

        if rsi < 35 and current_price <= bb_lower:
            # Oversold bounce — bullish
            # ✓ Block entry if price is too close to resistance (avoid long at top of range)
            if not _is_near_resistance(current_price, price_levels, proximity_pct=_sr_block_pct):
                # SL: just below nearest support (or BB lower × 0.98 fallback)
                _sl_candidates = [s for s in _levels_below if s < bb_lower]
                _sl = _sl_candidates[0] * 0.998 if _sl_candidates else bb_lower * 0.98
                # TP1: nearest resistance above price (or BB middle fallback)
                _tp1 = _levels_above[0] * 0.9985 if _levels_above else bb_middle
                # TP2: second resistance or BB upper
                _tp2 = _levels_above[1] * 0.9985 if len(_levels_above) > 1 else bb_upper
                _risk = abs(current_price - _sl)
                rr = abs(_tp1 - current_price) / _risk if _risk > 0 else 3.0
                patterns.append(PatternSignal(
                    pattern_type="oversold_bounce",
                    direction="bullish",
                    confidence=0.75,
                    entry_price=current_price,
                    stop_loss=_sl,
                    take_profit_1=_tp1,
                    take_profit_2=_tp2,
                    risk_reward=round(rr, 2),
                    reasoning=f"RSI oversold ({rsi:.1f}) + price at lower BB. TP targets at structural levels."
                ))

        if rsi > 65 and current_price >= bb_upper:
            # Overbought reversal — bearish
            # ✓ Block entry if price is too close to support (avoid short at bottom of range)
            if not _is_near_support(current_price, price_levels, proximity_pct=_sr_block_pct):
                # SL: just above nearest resistance (or BB upper × 1.02 fallback)
                _sl_candidates = [r for r in _levels_above if r > bb_upper]
                _sl = _sl_candidates[0] * 1.002 if _sl_candidates else bb_upper * 1.02
                # TP1: nearest support below price (or BB middle fallback)
                _tp1 = _levels_below[0] * 1.0015 if _levels_below else bb_middle
                # TP2: second support or BB lower
                _tp2 = _levels_below[1] * 1.0015 if len(_levels_below) > 1 else bb_lower
                _risk = abs(_sl - current_price)
                rr = abs(current_price - _tp1) / _risk if _risk > 0 else 3.0
                patterns.append(PatternSignal(
                    pattern_type="overbought_reversal",
                    direction="bearish",
                    confidence=0.75,
                    entry_price=current_price,
                    stop_loss=_sl,
                    take_profit_1=_tp1,
                    take_profit_2=_tp2,
                    risk_reward=round(rr, 2),
                    reasoning=f"RSI overbought ({rsi:.1f}) + price at upper BB. TP targets at structural levels."
                ))

        if macd > macd_signal and macd > 0:
            # MACD bullish — use nearest resistance for TP, nearest support for SL
            # ✓ Block entry if price is too close to resistance (avoid long at top of range)
            if not _is_near_resistance(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl = _levels_below[0] * 0.998 if _levels_below else current_price * 0.97
                _tp1 = _levels_above[0] * 0.9985 if _levels_above else current_price * 1.05
                _tp2 = _levels_above[1] * 0.9985 if len(_levels_above) > 1 else current_price * 1.08
                _risk = abs(current_price - _sl)
                rr = abs(_tp1 - current_price) / _risk if _risk > 0 else 2.0
                patterns.append(PatternSignal(
                    pattern_type="macd_bullish_cross",
                    direction="bullish",
                    confidence=0.6,
                    entry_price=current_price,
                    stop_loss=_sl,
                    take_profit_1=_tp1,
                    take_profit_2=_tp2,
                    risk_reward=round(rr, 2),
                    reasoning="MACD bullish crossover above zero line. Targets at structural levels."
                ))

        if macd < macd_signal and macd < 0:
            # MACD bearish — use nearest support for TP, nearest resistance for SL
            # ✓ Block entry if price is too close to support (avoid short at bottom of range)
            if not _is_near_support(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl = _levels_above[0] * 1.002 if _levels_above else current_price * 1.03
                _tp1 = _levels_below[0] * 1.0015 if _levels_below else current_price * 0.95
                _tp2 = _levels_below[1] * 1.0015 if len(_levels_below) > 1 else current_price * 0.92
                _risk = abs(_sl - current_price)
                rr = abs(current_price - _tp1) / _risk if _risk > 0 else 2.0
                patterns.append(PatternSignal(
                    pattern_type="macd_bearish_cross",
                    direction="bearish",
                    confidence=0.6,
                    entry_price=current_price,
                    stop_loss=_sl,
                    take_profit_1=_tp1,
                    take_profit_2=_tp2,
                    risk_reward=round(rr, 2),
                    reasoning="MACD bearish crossover below zero line. Targets at structural levels."
                ))

        # ── EMA 8/21 crossover (leading — fires ~4 candles before MACD) ─────
        # The 8/21 EMA cross is a well-established early-entry signal that fires
        # significantly before the MACD (12/26/9) confirms the same move. Base
        # confidence 0.55 boosted to 0.70 when MACD direction already agrees.
        try:
            ema8  = self.indicator_service.calculate_ema(closes, 8)
            ema21 = self.indicator_service.calculate_ema(closes, 21)
            _ema8_now,  _ema21_now  = ema8.iloc[-1],  ema21.iloc[-1]
            _ema8_prev, _ema21_prev = ema8.iloc[-2],  ema21.iloc[-2]
            _ema_bull_cross = (_ema8_prev <= _ema21_prev) and (_ema8_now > _ema21_now)
            _ema_bear_cross = (_ema8_prev >= _ema21_prev) and (_ema8_now < _ema21_now)
            _ema_conf = 0.55
            if _ema_bull_cross and macd > macd_signal: _ema_conf = 0.70
            if _ema_bear_cross and macd < macd_signal: _ema_conf = 0.70
            if _ema_bull_cross and not _is_near_resistance(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl  = _levels_below[0] * 0.998 if _levels_below else current_price * 0.97
                _tp1 = _levels_above[0] * 0.9985 if _levels_above else current_price * 1.04
                _tp2 = _levels_above[1] * 0.9985 if len(_levels_above) > 1 else current_price * 1.07
                _risk = abs(current_price - _sl)
                patterns.append(PatternSignal(
                    pattern_type="ema8_21_bull_cross", direction="bullish", confidence=_ema_conf,
                    entry_price=current_price, stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                    risk_reward=round(abs(_tp1 - current_price) / _risk if _risk > 0 else 2.0, 2),
                    reasoning=f"EMA 8 crossed above EMA 21 (early signal). MACD {'confirms' if _ema_conf > 0.55 else 'pending'}.",
                ))
            elif _ema_bear_cross and not _is_near_support(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl  = _levels_above[0] * 1.002 if _levels_above else current_price * 1.03
                _tp1 = _levels_below[0] * 1.0015 if _levels_below else current_price * 0.96
                _tp2 = _levels_below[1] * 1.0015 if len(_levels_below) > 1 else current_price * 0.93
                _risk = abs(_sl - current_price)
                patterns.append(PatternSignal(
                    pattern_type="ema8_21_bear_cross", direction="bearish", confidence=_ema_conf,
                    entry_price=current_price, stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                    risk_reward=round(abs(current_price - _tp1) / _risk if _risk > 0 else 2.0, 2),
                    reasoning=f"EMA 8 crossed below EMA 21 (early signal). MACD {'confirms' if _ema_conf > 0.55 else 'pending'}.",
                ))
        except Exception:
            pass

        # ── RSI divergence (leading — fires BEFORE price reverses) ───────────
        # Bullish: price lower-low but RSI higher-low = selling pressure exhausting.
        # Bearish: price higher-high but RSI lower-high = buying pressure exhausting.
        # Both fire before lagging confirmation, giving pre-emptive entry.
        try:
            _div = self.indicator_service.detect_divergence(closes, lookback=20)
            if _div["bullish_divergence"] and not _is_near_resistance(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl  = _levels_below[0] * 0.997 if _levels_below else current_price * 0.97
                _tp1 = _levels_above[0] * 0.9985 if _levels_above else bb_middle
                _tp2 = _levels_above[1] * 0.9985 if len(_levels_above) > 1 else bb_upper
                _risk = abs(current_price - _sl)
                patterns.append(PatternSignal(
                    pattern_type="rsi_bullish_divergence", direction="bullish", confidence=0.65,
                    entry_price=current_price, stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                    risk_reward=round(abs(_tp1 - current_price) / _risk if _risk > 0 else 2.5, 2),
                    reasoning=_div["divergence_reason"] + " — reversal likely imminent.",
                ))
            elif _div["bearish_divergence"] and not _is_near_support(current_price, price_levels, proximity_pct=_sr_block_pct):
                _sl  = _levels_above[0] * 1.003 if _levels_above else current_price * 1.03
                _tp1 = _levels_below[0] * 1.0015 if _levels_below else bb_middle
                _tp2 = _levels_below[1] * 1.0015 if len(_levels_below) > 1 else bb_lower
                _risk = abs(_sl - current_price)
                patterns.append(PatternSignal(
                    pattern_type="rsi_bearish_divergence", direction="bearish", confidence=0.65,
                    entry_price=current_price, stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                    risk_reward=round(abs(current_price - _tp1) / _risk if _risk > 0 else 2.5, 2),
                    reasoning=_div["divergence_reason"] + " — rollover likely imminent.",
                ))
        except Exception:
            pass

        # ── Candlestick reversal patterns (first-bar entry) ───────────────────
        # Engulfing / morning_star / hammer fire on the first candle of a reversal,
        # well before RSI or MACD confirm the same move.
        try:
            _cp = self.indicator_service.calculate_candle_patterns(df['open'], highs, lows, closes)
            _strong_bull = {"bullish_engulfing", "morning_star"}
            _strong_bear = {"bearish_engulfing", "evening_star"}
            if _cp.get("pattern_signal") == "buy" and abs(_cp.get("pattern_weight", 0)) >= 0.08:
                _cp_names = _cp.get("bullish_patterns", [])
                _cp_conf  = 0.68 if any(p in _strong_bull for p in _cp_names) else 0.62
                if not _is_near_resistance(current_price, price_levels, proximity_pct=_sr_block_pct):
                    _sl  = _levels_below[0] * 0.998 if _levels_below else current_price * 0.97
                    _tp1 = _levels_above[0] * 0.9985 if _levels_above else bb_middle
                    _tp2 = _levels_above[1] * 0.9985 if len(_levels_above) > 1 else bb_upper
                    _risk = abs(current_price - _sl)
                    patterns.append(PatternSignal(
                        pattern_type="candle_" + "_".join(_cp_names[:2]), direction="bullish",
                        confidence=_cp_conf, entry_price=current_price,
                        stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                        risk_reward=round(abs(_tp1 - current_price) / _risk if _risk > 0 else 2.0, 2),
                        reasoning=f"Bullish candle reversal: {', '.join(_cp_names)}. First-bar entry before lagging indicators confirm.",
                    ))
            elif _cp.get("pattern_signal") == "sell" and abs(_cp.get("pattern_weight", 0)) >= 0.08:
                _cp_names = _cp.get("bearish_patterns", [])
                _cp_conf  = 0.68 if any(p in _strong_bear for p in _cp_names) else 0.62
                if not _is_near_support(current_price, price_levels, proximity_pct=_sr_block_pct):
                    _sl  = _levels_above[0] * 1.002 if _levels_above else current_price * 1.03
                    _tp1 = _levels_below[0] * 1.0015 if _levels_below else bb_middle
                    _tp2 = _levels_below[1] * 1.0015 if len(_levels_below) > 1 else bb_lower
                    _risk = abs(_sl - current_price)
                    patterns.append(PatternSignal(
                        pattern_type="candle_" + "_".join(_cp_names[:2]), direction="bearish",
                        confidence=_cp_conf, entry_price=current_price,
                        stop_loss=_sl, take_profit_1=_tp1, take_profit_2=_tp2,
                        risk_reward=round(abs(current_price - _tp1) / _risk if _risk > 0 else 2.0, 2),
                        reasoning=f"Bearish candle reversal: {', '.join(_cp_names)}. First-bar entry before lagging indicators confirm.",
                    ))
        except Exception:
            pass

        # ── Late-entry / exhaustion penalty ──────────────────────────────────
        # Reduce confidence of patterns whose direction matches an extended move.
        # Problem: agents enter shorts after 8 consecutive bearish candles — the move
        # is already 80% done but the signal still fires at full confidence.
        #
        # Two checks:
        #   1. Consecutive candle streak: >5 candles in one direction → −0.05/extra candle
        #   2. Momentum deceleration: if the last 3 candle bodies are shrinking vs the
        #      previous 3, momentum is exhausting → additional −0.08 penalty
        #
        # Penalties are capped so a valid pattern can't go below 0.40 confidence —
        # we don't want to silence a signal entirely, just make it compete fairly.
        if patterns and len(closes) >= 10:
            try:
                _recent = closes.iloc[-10:].values
                # Count consecutive candles in each direction from the most recent bar back
                _bearish_streak, _bullish_streak = 0, 0
                for _i in range(len(_recent) - 1, 0, -1):
                    if _recent[_i] < _recent[_i - 1]:
                        if _bearish_streak == _i - (len(_recent) - 1 - _bearish_streak):
                            _bearish_streak += 1
                        else:
                            break
                    else:
                        break
                for _i in range(len(_recent) - 1, 0, -1):
                    if _recent[_i] > _recent[_i - 1]:
                        if _bullish_streak == _i - (len(_recent) - 1 - _bullish_streak):
                            _bullish_streak += 1
                        else:
                            break
                    else:
                        break

                # Simpler, more reliable streak count: walk backwards from tip
                _bearish_streak = 0
                _bullish_streak = 0
                _c = closes.values
                for _k in range(len(_c) - 1, 0, -1):
                    if _c[_k] < _c[_k - 1]:
                        _bearish_streak += 1
                    else:
                        break
                for _k in range(len(_c) - 1, 0, -1):
                    if _c[_k] > _c[_k - 1]:
                        _bullish_streak += 1
                    else:
                        break

                # Candle body sizes for momentum deceleration check
                _opens = df['open'].values
                _bodies_last3  = [abs(_c[-i] - _opens[-i]) for i in range(1, 4)]
                _bodies_prev3  = [abs(_c[-i] - _opens[-i]) for i in range(4, 7)]
                _avg_last3 = sum(_bodies_last3) / 3
                _avg_prev3 = sum(_bodies_prev3) / 3
                _decelerating = _avg_last3 < _avg_prev3 * 0.75  # last 3 bodies <75% of prev 3

                penalised = []
                for pat in patterns:
                    _streak = _bearish_streak if pat.direction == "bearish" else _bullish_streak
                    _penalty = 0.0
                    if _streak > 3:
                        _penalty += min((_streak - 3) * 0.05, 0.20)  # cap −0.20; threshold 5→3 (altcoins complete moves in 3-4 candles)
                    if _decelerating:
                        # Only penalise if the deceleration matches the pattern direction
                        _bearish_decel = _bearish_streak >= 3 and _decelerating
                        _bullish_decel = _bullish_streak >= 3 and _decelerating
                        if (pat.direction == "bearish" and _bearish_decel) or \
                           (pat.direction == "bullish" and _bullish_decel):
                            _penalty += 0.08
                    if _penalty > 0:
                        _new_conf = max(pat.confidence - _penalty, 0.40)
                        if _new_conf < pat.confidence:
                            pat.confidence = round(_new_conf, 3)
                            pat.reasoning = pat.reasoning + f" [exhaustion −{_penalty:.2f}: streak={_streak}, decel={_decelerating}]"
                    penalised.append(pat)
                patterns = penalised
            except Exception:
                pass  # exhaustion check is advisory; never block signal generation

        return patterns

    def _analyze_multitimeframe(
        self, 
        df_1h: pd.DataFrame, 
        df_4h: pd.DataFrame, 
        df_1d: pd.DataFrame,
        current_price: float,
        symbol: str = "BTCUSDT",
        tf_primary: str = "1h",
        tf_mid: str = "4h",
        tf_high: str = "1d",
    ) -> Optional[MultiTimeframeAnalysis]:
        if df_4h.empty or df_1d.empty:
            return None

        def get_trend(data: pd.DataFrame) -> str:
            if len(data) < 50:
                return "neutral"
            sma20 = data['close'].rolling(20).mean().iloc[-1]
            sma50 = data['close'].rolling(50).mean().iloc[-1]
            if sma20 > sma50:
                return "bullish"
            elif sma20 < sma50:
                return "bearish"
            return "neutral"

        tf_1h = get_trend(df_1h)
        tf_4h = get_trend(df_4h)
        tf_1d = get_trend(df_1d)

        trends = [tf_1h, tf_4h, tf_1d]
        bullish_count = trends.count("bullish")
        bearish_count = trends.count("bearish")

        if bullish_count >= 2:
            alignment = "bullish"
            confirmation = True
            confidence = 0.8
        elif bearish_count >= 2:
            alignment = "bearish"
            confirmation = True
            confidence = 0.8
        else:
            alignment = "mixed"
            confirmation = False
            confidence = 0.4

        return MultiTimeframeAnalysis(
            symbol=symbol,
            timeframe_1h={"trend": tf_1h},
            timeframe_4h={"trend": tf_4h},
            timeframe_1d={"trend": tf_1d},
            alignment=alignment,
            trend_confirmation=confirmation,
            confluence_score=confidence,
            trade_setup=None,
            tf_primary=tf_primary,
            tf_mid=tf_mid,
            tf_high=tf_high,
        )

    def _generate_overall_signal(
        self,
        patterns: List[PatternSignal],
        multi_tf: Optional[MultiTimeframeAnalysis],
        levels: PriceLevels,
        current_price: float
    ) -> tuple:
        if not patterns:
            return "hold", 0.3

        # ── Weighted consensus vote ───────────────────────────────────────────
        # Previously this picked the single highest-confidence pattern, meaning
        # one bearish signal at 0.75 would win over three bullish signals at 0.60
        # each. Now we sum confidence weights by direction — a clear majority
        # (>55% of total weight) is required for a directional signal.
        bull_weight = sum(p.confidence for p in patterns if p.direction == "bullish")
        bear_weight = sum(p.confidence for p in patterns if p.direction == "bearish")
        total_weight = bull_weight + bear_weight

        if total_weight == 0:
            return "hold", 0.3

        bull_share = bull_weight / total_weight
        bear_share = bear_weight / total_weight

        if bull_share > 0.55:
            raw_signal = "bullish"
            _bull_patterns = [p for p in patterns if p.direction == "bullish"]
            raw_conf = bull_weight / len(_bull_patterns)
        elif bear_share > 0.55:
            raw_signal = "bearish"
            _bear_patterns = [p for p in patterns if p.direction == "bearish"]
            raw_conf = bear_weight / len(_bear_patterns)
        else:
            # No clear consensus — conflicting signals, stay flat.
            return "hold", 0.3

        # ── Multi-TF alignment check (unchanged logic) ────────────────────────
        if multi_tf and multi_tf.trend_confirmation:
            if multi_tf.alignment == raw_signal:
                raw_conf = min(raw_conf + 0.15, 0.95)
            else:
                return "hold", 0.2

        return raw_signal, raw_conf

    def _generate_observations(
        self,
        levels: PriceLevels,
        patterns: List[PatternSignal],
        multi_tf: Optional[MultiTimeframeAnalysis],
        current_price: float
    ) -> List[str]:
        obs = []

        if levels.support:
            nearest_support = min(levels.support, key=lambda x: abs(x - current_price))
            dist_pct = ((current_price - nearest_support) / current_price) * 100
            obs.append(f"Nearest support: {fmt_price(nearest_support)} ({dist_pct:.1f}% below)")

        if levels.resistance:
            nearest_res = min(levels.resistance, key=lambda x: abs(x - current_price))
            dist_pct = ((nearest_res - current_price) / current_price) * 100
            obs.append(f"Nearest resistance: {fmt_price(nearest_res)} ({dist_pct:.1f}% above)")

        if levels.fibonacci_retracements:
            fib_618 = levels.fibonacci_retracements.get("62%")
            if fib_618:
                obs.append(f"61.8% Fibonacci retracement: {fmt_price(fib_618)}")

        if patterns:
            best = max(patterns, key=lambda p: p.confidence)
            obs.append(f"Best pattern: {best.pattern_type} ({best.confidence:.0%} confidence)")

        if multi_tf:
            obs.append(f"Multi-TF alignment: {multi_tf.alignment} (confluence: {multi_tf.confluence_score:.0%})")

        return obs

    # =========================================================================
    # CHART PATTERN DETECTION METHODS (investingoal.com patterns)
    # =========================================================================
    
    def _detect_flag_pattern(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 20:
            return patterns
            
        closes = df['close']
        highs = df['high']
        lows = df['low']
        volumes = df['volume']
        
        # Bull Flag: strong uptrend (pole) + tight consolidation (flag) + breakout up
        # Bear Flag: strong downtrend (pole) + tight consolidation (flag) + breakout down
        
        lookback = min(40, len(df) - 1)
        recent = df.tail(lookback + 1).copy()
        
        # Find the pole: largest move in one direction
        pole_start_idx = None
        pole_end_idx = None
        pole_size = 0.0
        pole_direction = None
        
        for i in range(lookback - 5):
            window = recent.iloc[i:i+10]
            if len(window) < 5:
                continue
            move = window['close'].iloc[-1] - window['close'].iloc[0]
            move_pct = abs(move) / window['close'].iloc[0]
            
            if move_pct > pole_size and move_pct > 0.03:  # min 3% move for pole
                pole_size = move_pct
                pole_direction = "bullish" if move > 0 else "bearish"
                pole_start_idx = i
                pole_end_idx = i + 9
        
        if pole_direction is None or pole_end_idx is None:
            return patterns
            
        pole_start = recent['close'].iloc[pole_start_idx]
        pole_end = recent['close'].iloc[pole_end_idx]
        
        # Flag: consolidation after pole (tight range, declining volume)
        flag_start = pole_end_idx
        flag_end = lookback
        
        if flag_end - flag_start < 3:  # need at least 3 candles in flag
            return patterns
            
        flag_df = recent.iloc[flag_start:flag_end+1]
        if len(flag_df) < 3:
            return patterns
            
        flag_high = flag_df['high'].max()
        flag_low = flag_df['low'].min()
        flag_range_pct = (flag_high - flag_low) / flag_low
        
        # Flag should be tight: < 50% of pole size
        valid_flag = flag_range_pct < pole_size * 0.5 and flag_range_pct < 0.02
        
        if not valid_flag:
            return patterns
            
        # Volume should decline during flag
        pole_vol = volumes.iloc[pole_start_idx:pole_end_idx+1].mean()
        flag_vol = volumes.iloc[flag_start:flag_end+1].mean()
        volume_declining = flag_vol < pole_vol * 0.8
        
        # Flag breakout: entry at breakout level, not current price
        if pole_direction == "bullish":
            at_breakout = current_price >= flag_high * 0.998
            if at_breakout:
                target = current_price + (pole_end - pole_start)
                stop_loss = flag_low
                conf = 0.78 if volume_declining else 0.68
                patterns.append(PatternSignal(
                    pattern_type="bull_flag",
                    direction="bullish",
                    confidence=conf,
                    entry_price=round(flag_high * 0.9985, 8),  # Enter just below breakout
                    stop_loss=round(stop_loss * 0.998, 8),
                    take_profit_1=round(target * 0.998, 8),
                    take_profit_2=round(target * 1.01, 8),
                    risk_reward=round((target - flag_high) / (flag_high - stop_loss), 2),
                    reasoning="Bull flag breakout: entry at resistance breakout. Target = pole length.",
                    timeframe=timeframe,
                ))
        else:
            at_breakout = current_price <= flag_low * 1.002
            if at_breakout:
                target = current_price - (pole_start - pole_end)
                stop_loss = flag_high
                conf = 0.78 if volume_declining else 0.68
                patterns.append(PatternSignal(
                    pattern_type="bear_flag",
                    direction="bearish",
                    confidence=conf,
                    entry_price=round(flag_low * 1.0015, 8),  # Enter just above breakdown
                    stop_loss=round(stop_loss * 1.002, 8),
                    take_profit_1=round(target * 1.002, 8),
                    take_profit_2=round(target * 0.99, 8),
                    risk_reward=round((flag_low - target) / (stop_loss - flag_low), 2),
                    reasoning="Bear flag breakdown: entry at support breakdown. Target = pole length.",
                    timeframe=timeframe,
                ))
        
        return patterns
    
    def _detect_triangle_patterns(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 40:
            return patterns
            
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close']
        
        # Get trendlines using linear regression on highs and lows
        lookback = min(30, len(df) - 1)
        recent = df.tail(lookback + 1)
        
        upper_lines = []
        lower_lines = []
        
        # Find local extrema for trendline fitting
        for i in range(3, lookback - 3):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                upper_lines.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                lower_lines.append((i, lows[i]))
        
        if len(upper_lines) < 3 or len(lower_lines) < 3:
            return patterns
        
        # Simple linear trendlines
        import numpy as np
        
        upper_x = np.array([p[0] for p in upper_lines])
        upper_y = np.array([p[1] for p in upper_lines])
        lower_x = np.array([p[0] for p in lower_lines])
        lower_y = np.array([p[1] for p in lower_lines])
        
        upper_slope = np.polyfit(upper_x, upper_y, 1)[0]
        lower_slope = np.polyfit(lower_x, lower_y, 1)[0]
        
        # Ascending Triangle: flat top, rising bottom -> bullish breakout
        if abs(upper_slope) < 0.001 and lower_slope > 0.001:
            flat_top = max(highs[-5:])
            _asc_low = min(lows[-5:])
            if current_price > flat_top * 0.998:
                target = flat_top + (flat_top - _asc_low)
                patterns.append(PatternSignal(
                    pattern_type="ascending_triangle",
                    direction="bullish",
                    confidence=0.70,
                    entry_price=round(flat_top * 0.9985, 8),
                    stop_loss=round(_asc_low * 0.97, 8),
                    take_profit_1=round(target * 0.998, 8),
                    take_profit_2=round(target * 1.01, 8),
                    risk_reward=round((target - flat_top) / (flat_top - _asc_low), 2),
                    reasoning="Ascending triangle breakout: entry at resistance. Target = triangle height.",
                    timeframe=timeframe,
                ))
        
        # Descending Triangle: falling top, flat bottom -> bearish breakdown
        elif upper_slope < -0.001 and abs(lower_slope) < 0.001:
            flat_bottom = min(lows[-5:])
            _desc_high = max(highs[-5:])
            if current_price < flat_bottom * 1.002:
                target = flat_bottom - (_desc_high - flat_bottom)
                patterns.append(PatternSignal(
                    pattern_type="descending_triangle",
                    direction="bearish",
                    confidence=0.70,
                    entry_price=round(flat_bottom * 1.0015, 8),
                    stop_loss=round(_desc_high * 1.03, 8),
                    take_profit_1=round(target * 1.002, 8),
                    take_profit_2=round(target * 0.99, 8),
                    risk_reward=round((flat_bottom - target) / (_desc_high - flat_bottom), 2),
                    reasoning="Descending triangle breakdown: entry at support. Target = triangle height.",
                    timeframe=timeframe,
                ))
        
        # Descending Triangle: falling top, flat bottom -> bearish breakdown
        elif upper_slope < -0.001 and abs(lower_slope) < 0.001:
            flat_bottom = min(lows[-5:])
            if current_price < flat_bottom * 1.002:
                target = flat_bottom - (current_high - flat_bottom)
                patterns.append(PatternSignal(
                    pattern_type="descending_triangle",
                    direction="bearish",
                    confidence=0.70,
                    entry_price=round(flat_bottom * 1.0015, 8),  # Entry at breakdown level
                    stop_loss=round(current_high * 1.03, 8),
                    take_profit_1=round(target * 1.002, 8),
                    take_profit_2=round(target * 0.99, 8),
                    risk_reward=round((flat_bottom - target) / (current_high - flat_bottom), 2),
                    reasoning="Descending triangle breakdown: entry at support. Target = triangle height.",
                    timeframe=timeframe,
                ))
        
        # Symmetrical Triangle: converging lines -> breakout either way
        elif abs(upper_slope) < 0.002 and abs(lower_slope) < 0.002 and upper_slope * lower_slope < 0:
            triangle_high = max(highs[-5:])
            triangle_low = min(lows[-5:])
            range_pct = (triangle_high - triangle_low) / current_price
            
            if range_pct < 0.03:
                recent_move = closes.iloc[-1] - closes.iloc[-5]
                if recent_move > 0:
                    target = triangle_high + (triangle_high - triangle_low)
                    patterns.append(PatternSignal(
                        pattern_type="symmetrical_triangle",
                        direction="bullish",
                        confidence=0.60,
                        entry_price=round(triangle_high * 0.9985, 8),
                        stop_loss=round(triangle_low * 0.98, 8),
                        take_profit_1=round(target * 0.998, 8),
                        take_profit_2=round(target * 1.01, 8),
                        risk_reward=round((target - triangle_high) / (triangle_high - triangle_low), 2),
                        reasoning="Symmetrical triangle breakout up: entry at resistance. Target = range height.",
                        timeframe=timeframe,
                    ))
                else:
                    target = triangle_low - (triangle_high - triangle_low)
                    patterns.append(PatternSignal(
                        pattern_type="symmetrical_triangle",
                        direction="bearish",
                        confidence=0.60,
                        entry_price=round(triangle_low * 1.0015, 8),
                        stop_loss=round(triangle_high * 1.02, 8),
                        take_profit_1=round(target * 1.002, 8),
                        take_profit_2=round(target * 0.99, 8),
                        risk_reward=round((triangle_low - target) / (triangle_high - triangle_low), 2),
                        reasoning="Symmetrical triangle breakdown: entry at support. Target = range height.",
                        timeframe=timeframe,
                    ))
        
        # Symmetrical Triangle: converging lines -> breakout either way
        elif abs(upper_slope) < 0.002 and abs(lower_slope) < 0.002 and upper_slope * lower_slope < 0:
            triangle_high = max(highs[-5:])
            triangle_low = min(lows[-5:])
            range_pct = (triangle_high - triangle_low) / current_price
            
            if range_pct < 0.03:  # Tight triangle
                # Determine breakout direction based on recent momentum
                recent_move = closes.iloc[-1] - closes.iloc[-5]
                if recent_move > 0:
                    patterns.append(PatternSignal(
                        pattern_type="symmetrical_triangle",
                        direction="bullish",
                        confidence=0.60,
                        entry_price=current_price,
                        stop_loss=round(triangle_low * 0.98, 8),
                        take_profit_1=round(triangle_high + range_pct * current_price, 8),
                        take_profit_2=round(triangle_high + range_pct * 1.5 * current_price, 8),
                        risk_reward=1.2,
                        reasoning="Symmetrical triangle converging. Recent momentum up, breakout expected.",
                    ))
                else:
                    patterns.append(PatternSignal(
                        pattern_type="symmetrical_triangle",
                        direction="bearish",
                        confidence=0.60,
                        entry_price=current_price,
                        stop_loss=round(triangle_high * 1.02, 8),
                        take_profit_1=round(triangle_low - range_pct * current_price, 8),
                        take_profit_2=round(triangle_low - range_pct * 1.5 * current_price, 8),
                        risk_reward=1.2,
                        reasoning="Symmetrical triangle converging. Recent momentum down, breakdown expected.",
                    ))
        
        return patterns
    
    def _detect_head_shoulders(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 40:
            return patterns
            
        highs = df['high'].values
        lows = df['low'].values
        
        lookback = min(30, len(df) - 1)
        recent = df.tail(lookback + 1)
        
        # Find local maxima for H&S
        local_maxima = []
        for i in range(2, lookback - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
                local_maxima.append((i, highs[i]))
        
        # Find local minima for inverse H&S
        local_minima = []
        for i in range(2, lookback - 2):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
                local_minima.append((i, lows[i]))
        
        # Head and Shoulders: 3 peaks, middle highest
        if len(local_maxima) >= 3:
            # Get last 3 peaks
            peaks = local_maxima[-3:]
            left_shoulder = peaks[0][1]
            head = peaks[1][1]
            right_shoulder = peaks[2][1]
            
            # Head should be higher than both shoulders
            if head > left_shoulder * 1.02 and head > right_shoulder * 1.02:
                # Shoulders should be roughly equal (within 3%)
                if abs(left_shoulder - right_shoulder) / left_shoulder < 0.03:
                    neckline = min(left_shoulder, right_shoulder)
                    if current_price < neckline * 1.01:
                        patterns.append(PatternSignal(
                            pattern_type="head_shoulders",
                            direction="bearish",
                            confidence=0.75,
                            entry_price=current_price,
                            stop_loss=round(head * 1.02, 8),
                            take_profit_1=round(neckline - (head - neckline) * 0.5, 8),
                            take_profit_2=round(neckline - (head - neckline), 8),
                            risk_reward=1.5,
                            reasoning="Head and Shoulders: distribution complete. Neckline break targets lower.",
                            timeframe=timeframe,
                        ))
        
        if len(local_minima) >= 3:
            troughs = local_minima[-3:]
            left_shoulder = troughs[0][1]
            head = troughs[1][1]
            right_shoulder = troughs[2][1]
            
            if head < left_shoulder * 0.98 and head < right_shoulder * 0.98:
                if abs(left_shoulder - right_shoulder) / left_shoulder < 0.03:
                    neckline = max(left_shoulder, right_shoulder)
                    if current_price > neckline * 0.99:
                        patterns.append(PatternSignal(
                            pattern_type="inverse_head_shoulders",
                            direction="bullish",
                            confidence=0.75,
                            entry_price=current_price,
                            stop_loss=round(head * 0.98, 8),
                            take_profit_1=round(neckline + (neckline - head) * 0.5, 8),
                            take_profit_2=round(neckline + (neckline - head), 8),
                            risk_reward=1.5,
                            reasoning="Inverse Head and Shoulders: accumulation complete. Neckline break targets higher.",
                            timeframe=timeframe,
                        ))
        
        return patterns
    
    def _detect_double_triple(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 30:
            return patterns
            
        highs = df['high'].values
        lows = df['low'].values
        
        lookback = min(25, len(df) - 1)
        
        # Find local maxima
        local_maxima = []
        for i in range(2, lookback - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                local_maxima.append(highs[i])
        
        # Find local minima
        local_minima = []
        for i in range(2, lookback - 2):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                local_minima.append(lows[i])
        
        # Double Top: two peaks at same level
        if len(local_maxima) >= 2:
            peak1 = local_maxima[-2]
            peak2 = local_maxima[-1]
            if abs(peak1 - peak2) / peak1 < 0.02:
                resistance = (peak1 + peak2) / 2
                if current_price > resistance * 0.99:
                    patterns.append(PatternSignal(
                        pattern_type="double_top",
                        direction="bearish",
                        confidence=0.72,
                        entry_price=current_price,
                        stop_loss=round(peak2 * 1.02, 8),
                        take_profit_1=round(resistance - (resistance * 0.05), 8),
                        take_profit_2=round(resistance - (resistance * 0.10), 8),
                        risk_reward=2.0,
                        reasoning="Double Top: two peaks at resistance. Strong rejection expected.",
                        timeframe=timeframe,
                    ))
        
        if len(local_minima) >= 2:
            trough1 = local_minima[-2]
            trough2 = local_minima[-1]
            if abs(trough1 - trough2) / trough1 < 0.02:
                support = (trough1 + trough2) / 2
                if current_price < support * 1.01:
                    patterns.append(PatternSignal(
                        pattern_type="double_bottom",
                        direction="bullish",
                        confidence=0.72,
                        entry_price=current_price,
                        stop_loss=round(trough2 * 0.98, 8),
                        take_profit_1=round(support + (support * 0.05), 8),
                        take_profit_2=round(support + (support * 0.10), 8),
                        risk_reward=2.0,
                        reasoning="Double Bottom: two troughs at support. Strong bounce expected.",
                        timeframe=timeframe,
                    ))
        
        if len(local_maxima) >= 3:
            peak1 = local_maxima[-3]
            peak2 = local_maxima[-2]
            peak3 = local_maxima[-1]
            if abs(peak1 - peak2) / peak1 < 0.03 and abs(peak2 - peak3) / peak2 < 0.03:
                resistance = (peak1 + peak2 + peak3) / 3
                if current_price > resistance * 0.99:
                    patterns.append(PatternSignal(
                        pattern_type="triple_top",
                        direction="bearish",
                        confidence=0.80,
                        entry_price=current_price,
                        stop_loss=round(peak3 * 1.02, 8),
                        take_profit_1=round(resistance - (resistance * 0.08), 8),
                        take_profit_2=round(resistance - (resistance * 0.15), 8),
                        risk_reward=2.5,
                        reasoning="Triple Top: three peaks at resistance. Strong rejection = high conviction.",
                        timeframe=timeframe,
                    ))
        
        if len(local_minima) >= 3:
            trough1 = local_minima[-3]
            trough2 = local_minima[-2]
            trough3 = local_minima[-1]
            if abs(trough1 - trough2) / trough1 < 0.03 and abs(trough2 - trough3) / trough2 < 0.03:
                support = (trough1 + trough2 + trough3) / 3
                if current_price < support * 1.01:
                    patterns.append(PatternSignal(
                        pattern_type="triple_bottom",
                        direction="bullish",
                        confidence=0.80,
                        entry_price=current_price,
                        stop_loss=round(trough3 * 0.98, 8),
                        take_profit_1=round(support + (support * 0.08), 8),
                        take_profit_2=round(support + (support * 0.15), 8),
                        risk_reward=2.5,
                        reasoning="Triple Bottom: three troughs at support. Strong bounce = high conviction.",
                        timeframe=timeframe,
                    ))
        
        return patterns
    
    def _detect_cup_handle(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 50:
            return patterns
            
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        volumes = df['volume'].values
        
        lookback = min(40, len(df) - 1)
        
        # Cup and Handle: rounded bottom (U-shape) + pullback + breakout higher
        # Need to find a U-shaped formation
        
        # Find the lowest point in recent data
        min_idx = np.argmin(closes[:lookback])
        min_price = closes[min_idx]
        
        # Check for U-shape: prices on both sides of min should be higher
        if min_idx < 10 or min_idx > lookback - 10:
            return patterns
        
        # Cup: price rises on both sides of minimum
        left_rise = closes[min_idx] - closes[min_idx - 10]
        right_rise = closes[min_idx + 10] - closes[min_idx]
        if left_rise > 0 and right_rise > 0:
            cup_bottom = min_price
            cup_left_edge = closes[min_idx - 10]
            cup_right_edge = closes[min_idx + 10]
            if abs(cup_left_edge - cup_right_edge) / cup_left_edge < 0.05:
                handle_start = min_idx + 10
                handle_end = lookback
                if handle_end - handle_start >= 5:
                    handle_df = closes[handle_start:handle_end+1]
                    handle_high = np.max(handle_df)
                    handle_low = np.min(handle_df)
                    cup_depth = (cup_left_edge + cup_right_edge) / 2 - cup_bottom
                    handle_range = handle_high - handle_low
                    if handle_range < cup_depth * 0.4:
                        cup_edge = (cup_left_edge + cup_right_edge) / 2
                        if current_price > cup_edge * 1.01:
                            patterns.append(PatternSignal(
                                pattern_type="cup_handle",
                                direction="bullish",
                                confidence=0.75,
                                entry_price=current_price,
                                stop_loss=round(cup_bottom * 0.97, 8),
                                take_profit_1=round(cup_edge + cup_depth * 1.0, 8),
                                take_profit_2=round(cup_edge + cup_depth * 1.5, 8),
                                risk_reward=2.0,
                                reasoning="Cup and Handle: U-shape completes, handle pulls back. Breakout targets higher.",
                                timeframe=timeframe,
                            ))
        
        max_idx = np.argmax(closes[:lookback])
        
        if max_idx < 10 or max_idx > lookback - 10:
            return patterns
        
        left_drop = closes[max_idx] - closes[max_idx - 10]
        right_drop = closes[max_idx] - closes[max_idx + 10]
        
        if left_drop > 0 and right_drop > 0:
            cup_top = closes[max_idx]
            cup_left_edge = closes[max_idx - 10]
            cup_right_edge = closes[max_idx + 10]
            
            if abs(cup_left_edge - cup_right_edge) / cup_left_edge < 0.05:
                handle_start = max_idx + 10
                handle_end = lookback
                
                if handle_end - handle_start >= 5:
                    handle_df = closes[handle_start:handle_end+1]
                    handle_high = np.max(handle_df)
                    handle_low = np.min(handle_df)
                    
                    cup_drop = cup_top - (cup_left_edge + cup_right_edge) / 2
                    handle_range = handle_high - handle_low
                    
                    if handle_range < cup_drop * 0.4:
                        cup_edge = (cup_left_edge + cup_right_edge) / 2
                        if current_price < cup_edge * 0.99:
                            patterns.append(PatternSignal(
                                pattern_type="inverse_cup_handle",
                                direction="bearish",
                                confidence=0.75,
                                entry_price=current_price,
                                stop_loss=round(cup_top * 1.03, 8),
                                take_profit_1=round(cup_edge - cup_drop * 1.0, 8),
                                take_profit_2=round(cup_edge - cup_drop * 1.5, 8),
                                risk_reward=2.0,
                                reasoning="Inverse Cup and Handle: ∩-shape completes, handle pulls back. Breakdown targets lower.",
                                timeframe=timeframe,
                            ))
        
        return patterns
    
    def _detect_wedge(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 30:
            return patterns
            
        highs = df['high'].values
        lows = df['low'].values
        
        lookback = min(25, len(df) - 1)
        
        # Find trendlines
        upper_lines = []
        lower_lines = []
        
        for i in range(3, lookback - 3):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                upper_lines.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                lower_lines.append((i, lows[i]))
        
        if len(upper_lines) < 3 or len(lower_lines) < 3:
            return patterns
        
        import numpy as np
        
        upper_x = np.array([p[0] for p in upper_lines])
        upper_y = np.array([p[1] for p in upper_lines])
        lower_x = np.array([p[0] for p in lower_lines])
        lower_y = np.array([p[1] for p in lower_lines])
        
        upper_slope = np.polyfit(upper_x, upper_y, 1)[0]
        lower_slope = np.polyfit(lower_x, lower_y, 1)[0]
        
        # Rising Wedge: both lines sloping up, but upper steeper -> bearish
        if upper_slope > 0 and lower_slope > 0 and upper_slope > lower_slope:
            wedge_high = highs[-1]
            wedge_low = lows[-1]
            recent_range = wedge_high - wedge_low
            
            if current_price < wedge_low * 1.005:
                patterns.append(PatternSignal(
                    pattern_type="rising_wedge",
                    direction="bearish",
                    confidence=0.68,
                    entry_price=current_price,
                    stop_loss=round(wedge_high * 1.02, 8),
                    take_profit_1=round(current_price - recent_range * 0.5, 8),
                    take_profit_2=round(current_price - recent_range, 8),
                    risk_reward=1.5,
                    reasoning="Rising wedge: converging up. Bearish reversal typical.",
                    timeframe=timeframe,
                ))
        
        elif upper_slope < 0 and lower_slope < 0 and lower_slope < upper_slope:
            wedge_high = highs[-1]
            wedge_low = lows[-1]
            recent_range = wedge_high - wedge_low
            
            if current_price > wedge_high * 0.995:
                patterns.append(PatternSignal(
                    pattern_type="falling_wedge",
                    direction="bullish",
                    confidence=0.68,
                    entry_price=current_price,
                    stop_loss=round(wedge_low * 0.98, 8),
                    take_profit_1=round(current_price + recent_range * 0.5, 8),
                    take_profit_2=round(current_price + recent_range, 8),
                    risk_reward=1.5,
                    reasoning="Falling wedge: converging down. Bullish reversal typical.",
                    timeframe=timeframe,
                ))
        
        return patterns
    
    def _detect_rectangle(self, df: pd.DataFrame, current_price: float, timeframe: str = "1h") -> List[PatternSignal]:
        patterns = []
        if len(df) < 20:
            return patterns
            
        highs = df['high'].values
        lows = df['low'].values
        
        lookback = min(20, len(df) - 1)
        
        # Rectangle: price moving sideways between parallel support/resistance
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        
        channel_high = np.max(recent_highs)
        channel_low = np.min(recent_lows)
        range_pct = (channel_high - channel_low) / channel_low
        
        # Should be a tight range (not trending)
        if range_pct < 0.03:
            # Determine breakout direction by recent momentum
            closes = df['close'].values
            recent_move = closes[-1] - closes[-5]
            
            if recent_move > 0:
                patterns.append(PatternSignal(
                    pattern_type="rectangle",
                    direction="bullish",
                    confidence=0.55,
                    entry_price=current_price,
                    stop_loss=round(channel_low * 0.98, 8),
                    take_profit_1=round(channel_high + range_pct * current_price, 8),
                    take_profit_2=round(channel_high + range_pct * 1.5 * current_price, 8),
                    risk_reward=1.2,
                    reasoning="Rectangle: sideways channel. Breakout up likely.",
                    timeframe=timeframe,
                ))
            else:
                patterns.append(PatternSignal(
                    pattern_type="rectangle",
                    direction="bearish",
                    confidence=0.55,
                    entry_price=current_price,
                    stop_loss=round(channel_high * 1.02, 8),
                    take_profit_1=round(channel_low - range_pct * current_price, 8),
                    take_profit_2=round(channel_low - range_pct * 1.5 * current_price, 8),
                    risk_reward=1.2,
                    reasoning="Rectangle: sideways channel. Breakdown likely.",
                    timeframe=timeframe,
                ))
        
        return patterns

    async def get_confluence_scores(self, symbols: List[str], timeframe: str = "1h") -> Dict[str, Dict[str, Any]]:
        """
        For each symbol, compute a confluence score combining:
        - Multi-timeframe alignment (bullish/bearish/mixed)
        - Pattern count and average confidence
        - Overall signal strength

        Uses the three analysis frames derived from *timeframe* so scores are
        relevant to the strategies that will consume them.

        Returns: {symbol: {score: float, signal: str, patterns: int, alignment: str, details: str}}
        """
        results = {}
        for symbol in symbols:
            try:
                report = await self.analyze(symbol, timeframe=timeframe)

                # Base: multi-timeframe confluence (0-1)
                mtf_score = 0.4
                alignment = "mixed"
                if report.multi_timeframe:
                    mtf_score = report.multi_timeframe.confluence_score
                    alignment = report.multi_timeframe.alignment

                # Pattern bonus: more patterns with higher confidence = higher score
                pattern_count = len(report.patterns)
                avg_pattern_conf = 0.0
                if report.patterns:
                    avg_pattern_conf = sum(p.confidence for p in report.patterns) / pattern_count
                pattern_score = min(pattern_count * 0.1 + avg_pattern_conf * 0.3, 0.4)

                # Signal strength (0-0.2)
                signal_score = report.confidence * 0.2

                total = mtf_score * 0.5 + pattern_score + signal_score
                total = round(min(total, 1.0), 3)

                results[symbol] = {
                    "score": total,
                    "signal": report.overall_signal,
                    "confidence": report.confidence,
                    "patterns": pattern_count,
                    "alignment": alignment,
                    "details": "; ".join(report.key_observations[:3]),
                }
            except Exception as e:
                logger.warning(f"Confluence score failed for {symbol}: {e}")
                results[symbol] = {
                    "score": 0.3,
                    "signal": "hold",
                    "confidence": 0.0,
                    "patterns": 0,
                    "alignment": "unknown",
                    "details": f"Analysis failed: {str(e)[:60]}",
                }
        return results

    def evaluate_strategy_fit(
        self,
        strategy_type: str,
        report: TechnicalAnalystReport,
    ) -> Dict[str, Any]:
        """
        Evaluate how well a strategy type fits current technical conditions.
        Returns: {fit_score: float, reasoning: str, recommended_action: str}
        """
        signal = report.overall_signal
        confidence = report.confidence
        alignment = report.multi_timeframe.alignment if report.multi_timeframe else "mixed"
        patterns = report.patterns

        fit_score = 0.5  # neutral default

        if strategy_type == "momentum":
            if alignment in ["bullish", "bearish"] and confidence > 0.5:
                fit_score = 0.8
                reasoning = f"Strong {alignment} trend with {confidence:.0%} confidence — momentum suits this"
            elif alignment == "mixed":
                fit_score = 0.3
                reasoning = "Mixed timeframe alignment — momentum may whipsaw"
            else:
                fit_score = 0.5
                reasoning = "Neutral conditions for momentum"

        elif strategy_type == "mean_reversion":
            oversold = any(p.pattern_type == "oversold_bounce" for p in patterns)
            overbought = any(p.pattern_type == "overbought_reversal" for p in patterns)
            if oversold or overbought:
                fit_score = 0.85
                reasoning = f"{'Oversold bounce' if oversold else 'Overbought reversal'} detected — ideal for mean reversion"
            elif alignment == "mixed" and confidence < 0.5:
                fit_score = 0.7
                reasoning = "Range-bound market — good for mean reversion"
            else:
                fit_score = 0.35
                reasoning = f"Trending {alignment} market — risky for mean reversion"

        elif strategy_type == "breakout":
            if len(patterns) >= 2 and confidence > 0.6:
                fit_score = 0.8
                reasoning = f"{len(patterns)} patterns with high confidence — breakout conditions"
            elif alignment in ["bullish", "bearish"]:
                fit_score = 0.6
                reasoning = f"{alignment.title()} trend may support breakout continuation"
            else:
                fit_score = 0.4
                reasoning = "No clear breakout setup detected"
        else:
            fit_score = 0.5
            reasoning = f"Unknown strategy '{strategy_type}' — neutral fit"

        if fit_score >= 0.7:
            action = "increase_allocation"
        elif fit_score <= 0.3:
            action = "decrease_allocation"
        else:
            action = "maintain"

        return {
            "fit_score": round(fit_score, 2),
            "reasoning": reasoning,
            "recommended_action": action,
        }


technical_analyst = TechnicalAnalyst()