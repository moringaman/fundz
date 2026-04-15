import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum


class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradingSignal:
    signal: Signal
    confidence: float
    price: float
    indicators: Dict[str, float]
    reasoning: str
    symbol: Optional[str] = None


class IndicatorService:
    def __init__(self):
        pass

    def calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        # Wilder's EWM (alpha = 1/period) — the standard RSI formula
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def calculate_bollinger_bands(self, prices: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return pd.DataFrame({
            "upper": upper,
            "middle": sma,
            "lower": lower
        })

    def calculate_sma(self, prices: pd.Series, period: int) -> pd.Series:
        return prices.rolling(window=period).mean()

    def calculate_ema(self, prices: pd.Series, period: int) -> pd.Series:
        return prices.ewm(span=period, adjust=False).mean()

    def calculate_macd(self, prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - signal_line
        
        return pd.DataFrame({
            "macd": macd,
            "signal": signal_line,
            "histogram": histogram
        })

    def calculate_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        # Wilder's EWM smoothing (same alpha as RSI)
        atr = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        return atr

    def calculate_volume_sma(self, volume: pd.Series, period: int = 20) -> pd.Series:
        return volume.rolling(window=period).mean()

    def detect_divergence(
        self, close: pd.Series, lookback: int = 20
    ) -> Dict[str, Any]:
        """Detect RSI price-indicator divergence over the last `lookback` candles.

        Bullish divergence: price makes a lower low BUT RSI makes a higher low.
        → Indicates weakening selling pressure; reversal probability high. Weight: 0.35

        Bearish divergence: price makes a higher high BUT RSI makes a lower high.
        → Indicates weakening buying pressure; reversal probability high. Weight: 0.35

        Regular divergence only (hidden divergence is intentionally excluded —
        it requires trend structure context that is already handled by SMA gates).

        Returns:
            {
                "bullish_divergence": bool,
                "bearish_divergence": bool,
                "divergence_weight": float,   # 0.35 bullish, -0.35 bearish, 0.0 none
                "divergence_reason": str,
            }
        """
        result = {"bullish_divergence": False, "bearish_divergence": False,
                  "divergence_weight": 0.0, "divergence_reason": ""}

        if len(close) < lookback + 2:
            return result

        # Compute RSI over the full series, then slice the lookback window
        rsi_series = self.calculate_rsi(close)
        if rsi_series.isna().all():
            return result

        price_window = close.iloc[-lookback:].reset_index(drop=True)
        rsi_window   = rsi_series.iloc[-lookback:].reset_index(drop=True)

        if rsi_window.isna().any():
            return result

        # Find local minima (lows) and maxima (highs) in price and RSI
        def _local_extrema(series: pd.Series, kind: str) -> list[tuple[int, float]]:
            """Return (index, value) pairs for local mins ('low') or maxes ('high')."""
            pts = []
            for i in range(1, len(series) - 1):
                if kind == "low"  and series.iloc[i] < series.iloc[i-1] and series.iloc[i] < series.iloc[i+1]:
                    pts.append((i, float(series.iloc[i])))
                elif kind == "high" and series.iloc[i] > series.iloc[i-1] and series.iloc[i] > series.iloc[i+1]:
                    pts.append((i, float(series.iloc[i])))
            return pts

        price_lows  = _local_extrema(price_window, "low")
        price_highs = _local_extrema(price_window, "high")
        rsi_lows    = _local_extrema(rsi_window,   "low")
        rsi_highs   = _local_extrema(rsi_window,   "high")

        # Need at least 2 pivot points to compare
        if len(price_lows) >= 2 and len(rsi_lows) >= 2:
            # Most recent two price lows
            p_low1_i, p_low1 = price_lows[-2]
            p_low2_i, p_low2 = price_lows[-1]
            # RSI lows closest in time to those price lows
            r_low1_candidates = [r for r in rsi_lows if abs(r[0] - p_low1_i) <= 3]
            r_low2_candidates = [r for r in rsi_lows if abs(r[0] - p_low2_i) <= 3]
            if r_low1_candidates and r_low2_candidates:
                r_low1 = min(r_low1_candidates, key=lambda x: abs(x[0] - p_low1_i))[1]
                r_low2 = min(r_low2_candidates, key=lambda x: abs(x[0] - p_low2_i))[1]
                if p_low2 < p_low1 and r_low2 > r_low1:
                    result["bullish_divergence"] = True
                    result["divergence_weight"]  = 0.35
                    result["divergence_reason"]  = (
                        f"Bullish RSI divergence: price lower low ({p_low1:.4f}→{p_low2:.4f}) "
                        f"but RSI higher low ({r_low1:.1f}→{r_low2:.1f})"
                    )

        if len(price_highs) >= 2 and len(rsi_highs) >= 2 and not result["bullish_divergence"]:
            p_hi1_i, p_hi1 = price_highs[-2]
            p_hi2_i, p_hi2 = price_highs[-1]
            r_hi1_candidates = [r for r in rsi_highs if abs(r[0] - p_hi1_i) <= 3]
            r_hi2_candidates = [r for r in rsi_highs if abs(r[0] - p_hi2_i) <= 3]
            if r_hi1_candidates and r_hi2_candidates:
                r_hi1 = min(r_hi1_candidates, key=lambda x: abs(x[0] - p_hi1_i))[1]
                r_hi2 = min(r_hi2_candidates, key=lambda x: abs(x[0] - p_hi2_i))[1]
                if p_hi2 > p_hi1 and r_hi2 < r_hi1:
                    result["bearish_divergence"] = True
                    result["divergence_weight"]  = -0.35
                    result["divergence_reason"]  = (
                        f"Bearish RSI divergence: price higher high ({p_hi1:.4f}→{p_hi2:.4f}) "
                        f"but RSI lower high ({r_hi1:.1f}→{r_hi2:.1f})"
                    )

        return result

    def calculate_candle_patterns(        self, open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> Dict[str, Any]:
        """Detect candlestick reversal and continuation patterns on the last 3 candles.

        Patterns detected:
          Bullish: hammer, bullish_engulfing, morning_star, bullish_doji
          Bearish: shooting_star, bearish_engulfing, evening_star, bearish_doji

        Returns:
            {
                "bullish_patterns": list[str],  # names of bullish patterns detected
                "bearish_patterns": list[str],
                "pattern_weight":   float,      # net signal weight (positive=bullish)
                "pattern_signal":   "buy"|"sell"|"neutral",
            }
        """
        if len(close) < 3:
            return {"bullish_patterns": [], "bearish_patterns": [],
                    "pattern_weight": 0.0, "pattern_signal": "neutral"}

        o1, h1, l1, c1 = float(open_.iloc[-3]), float(high.iloc[-3]), float(low.iloc[-3]), float(close.iloc[-3])
        o2, h2, l2, c2 = float(open_.iloc[-2]), float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
        o3, h3, l3, c3 = float(open_.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])

        body3  = abs(c3 - o3)
        range3 = max(h3 - l3, 1e-10)
        upper_wick3 = h3 - max(c3, o3)
        lower_wick3 = min(c3, o3) - l3
        is_bullish3 = c3 > o3
        is_bearish3 = c3 < o3

        body2  = abs(c2 - o2)
        range2 = max(h2 - l2, 1e-10)

        bullish: list[str] = []
        bearish: list[str] = []

        # ── Doji (indecision — confirms the PRIOR trend reversal, not by itself) ─
        if body3 / range3 < 0.10:
            if c2 < o2:
                bullish.append("bullish_doji")  # doji after bearish candle
            elif c2 > o2:
                bearish.append("bearish_doji")  # doji after bullish candle

        # ── Hammer (bullish reversal) ─────────────────────────────────────────
        # Small body at top, long lower wick (>= 2× body), little upper wick
        if (body3 / range3 > 0.10 and
                lower_wick3 >= 2 * max(body3, 1e-10) and
                upper_wick3 <= body3 * 0.5 and
                c2 < o2):  # prior candle must be bearish (context)
            bullish.append("hammer")

        # ── Shooting Star (bearish reversal) ─────────────────────────────────
        # Small body at bottom, long upper wick (>= 2× body), little lower wick
        if (body3 / range3 > 0.10 and
                upper_wick3 >= 2 * max(body3, 1e-10) and
                lower_wick3 <= body3 * 0.5 and
                c2 > o2):  # prior candle bullish
            bearish.append("shooting_star")

        # ── Bullish Engulfing ─────────────────────────────────────────────────
        # Current bullish candle body fully engulfs previous bearish candle body
        if (is_bullish3 and c2 < o2 and
                o3 <= c2 and c3 >= o2 and body3 > body2):
            bullish.append("bullish_engulfing")

        # ── Bearish Engulfing ─────────────────────────────────────────────────
        if (is_bearish3 and c2 > o2 and
                o3 >= c2 and c3 <= o2 and body3 > body2):
            bearish.append("bearish_engulfing")

        # ── Morning Star (3-candle bullish reversal) ──────────────────────────
        # C1: large bearish, C2: small body (indecision), C3: large bullish closes above C1 midpoint
        c1_midpoint = (o1 + c1) / 2
        if (c1 < o1 and body2 / max(range2, 1e-10) < 0.35 and
                is_bullish3 and c3 > c1_midpoint and body3 > body2):
            bullish.append("morning_star")

        # ── Evening Star (3-candle bearish reversal) ──────────────────────────
        if (c1 > o1 and body2 / max(range2, 1e-10) < 0.35 and
                is_bearish3 and c3 < c1_midpoint and body3 > body2):
            bearish.append("evening_star")

        # ── Net weight ────────────────────────────────────────────────────────
        # Engulfing / morning_star / evening_star are stronger patterns (0.15)
        # Hammer / shooting_star are moderate (0.12)
        # Doji is weak confirmation only (0.08)
        _weights = {
            "bullish_engulfing": 0.15, "morning_star": 0.15,
            "hammer": 0.12,
            "bullish_doji": 0.08,
            "bearish_engulfing": 0.15, "evening_star": 0.15,
            "shooting_star": 0.12,
            "bearish_doji": 0.08,
        }
        bull_w = sum(_weights.get(p, 0.10) for p in bullish)
        bear_w = sum(_weights.get(p, 0.10) for p in bearish)
        net    = bull_w - bear_w

        return {
            "bullish_patterns": bullish,
            "bearish_patterns": bearish,
            "pattern_weight":   round(net, 3),
            "pattern_signal":   "buy" if net > 0 else ("sell" if net < 0 else "neutral"),
        }

    def calculate_support_resistance(
        self, high: pd.Series, low: pd.Series, close: pd.Series, lookback: int = 50, proximity_pct: float = 0.005
    ) -> Dict[str, Any]:
        """Identify support and resistance levels from recent swing highs/lows.

        Uses a simple but effective approach: find local extrema (swing points) over
        the last `lookback` candles, then cluster nearby levels (within `proximity_pct`)
        to avoid noise. Returns the nearest support below and resistance above the
        current price, and whether the current price is near either level.

        Returns:
            {
                "nearest_support": float | None,
                "nearest_resistance": float | None,
                "at_support": bool,   # price within proximity_pct of support
                "at_resistance": bool,
                "support_strength": int,  # how many swing points cluster here
                "resistance_strength": int,
            }
        """
        if len(close) < 10:
            return {"nearest_support": None, "nearest_resistance": None,
                    "at_support": False, "at_resistance": False,
                    "support_strength": 0, "resistance_strength": 0}

        n = min(lookback, len(high))
        h = high.iloc[-n:].reset_index(drop=True)
        l = low.iloc[-n:].reset_index(drop=True)
        current_price = float(close.iloc[-1])

        # Find swing highs (local maxima) and swing lows (local minima) with a 2-bar window
        swing_highs: list[float] = []
        swing_lows:  list[float] = []
        for i in range(2, len(h) - 2):
            if h.iloc[i] >= h.iloc[i-1] and h.iloc[i] >= h.iloc[i-2] and \
               h.iloc[i] >= h.iloc[i+1] and h.iloc[i] >= h.iloc[i+2]:
                swing_highs.append(float(h.iloc[i]))
            if l.iloc[i] <= l.iloc[i-1] and l.iloc[i] <= l.iloc[i-2] and \
               l.iloc[i] <= l.iloc[i+1] and l.iloc[i] <= l.iloc[i+2]:
                swing_lows.append(float(l.iloc[i]))

        # Cluster levels within proximity_pct of each other
        def _cluster(levels: list[float]) -> list[tuple[float, int]]:
            """Returns (centroid, count) pairs for clustered levels."""
            if not levels:
                return []
            levels = sorted(levels)
            clusters: list[tuple[float, int]] = []
            grp = [levels[0]]
            for v in levels[1:]:
                if abs(v - grp[0]) / max(grp[0], 1e-10) <= proximity_pct * 3:
                    grp.append(v)
                else:
                    clusters.append((sum(grp) / len(grp), len(grp)))
                    grp = [v]
            clusters.append((sum(grp) / len(grp), len(grp)))
            return clusters

        support_clusters    = _cluster(swing_lows)
        resistance_clusters = _cluster(swing_highs)

        # Find nearest support below and resistance above current price
        supports_below    = [(lvl, cnt) for lvl, cnt in support_clusters    if lvl < current_price]
        resistances_above = [(lvl, cnt) for lvl, cnt in resistance_clusters if lvl > current_price]

        nearest_support    = max(supports_below,    key=lambda x: x[0])[0] if supports_below    else None
        nearest_resistance = min(resistances_above, key=lambda x: x[0])[0] if resistances_above else None
        support_strength    = max(supports_below,    key=lambda x: x[0])[1] if supports_below    else 0
        resistance_strength = min(resistances_above, key=lambda x: x[0])[1] if resistances_above else 0

        at_support    = nearest_support    is not None and \
                        abs(current_price - nearest_support)    / max(nearest_support,    1e-10) <= proximity_pct
        at_resistance = nearest_resistance is not None and \
                        abs(current_price - nearest_resistance) / max(nearest_resistance, 1e-10) <= proximity_pct

        return {
            "nearest_support":    nearest_support,
            "nearest_resistance": nearest_resistance,
            "at_support":         at_support,
            "at_resistance":      at_resistance,
            "support_strength":   support_strength,
            "resistance_strength": resistance_strength,
        }

    def calculate_adx(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average Directional Index (ADX) — measures trend strength, not direction.

        ADX < 20: weak / ranging market
        ADX 20–25: developing trend
        ADX > 25: confirmed trend
        ADX > 40: strong trend

        Uses Wilder's smoothing (same as ATR/RSI) for consistency.
        Returns the ADX series. Does NOT indicate trend direction.
        """
        high = high.reset_index(drop=True)
        low  = low.reset_index(drop=True)
        close = close.reset_index(drop=True)

        # Directional movement
        up_move   = high.diff()
        down_move = -low.diff()

        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,  0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True Range (reuse ATR formula)
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)

        # Wilder's smoothed series
        alpha = 1.0 / period
        atr_s    = pd.Series(tr).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        plus_di  = pd.Series(plus_dm).ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s * 100
        minus_di = pd.Series(minus_dm).ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s * 100

        dx_denom = (plus_di + minus_di).replace(0, np.nan)
        dx  = ((plus_di - minus_di).abs() / dx_denom * 100).fillna(0)
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        return adx

    def calculate_all(self, df: pd.DataFrame) -> Dict[str, Any]:
        close = df["close"]
        high = df.get("high", close)
        low = df.get("low", close)
        volume = df.get("volume", pd.Series([0] * len(df)))

        rsi = self.calculate_rsi(close)
        bb = self.calculate_bollinger_bands(close)
        sma_20 = self.calculate_sma(close, 20)
        sma_50 = self.calculate_sma(close, 50)
        sma_200 = self.calculate_sma(close, 200)
        macd = self.calculate_macd(close)
        atr = self.calculate_atr(high, low, close)
        volume_sma = self.calculate_volume_sma(volume)
        adx = self.calculate_adx(high, low, close) if len(df) >= 28 else None
        sr  = self.calculate_support_resistance(high, low, close) if len(df) >= 10 else None
        _open = df.get("open", close)
        cp  = self.calculate_candle_patterns(_open, high, low, close) if len(df) >= 3 else None

        latest = df.iloc[-1] if len(df) > 0 else None
        
        return {
            "rsi": float(rsi.iloc[-1]) if len(rsi) > 0 and not pd.isna(rsi.iloc[-1]) else None,
            "bb_upper": float(bb["upper"].iloc[-1]) if len(bb) > 0 and not pd.isna(bb["upper"].iloc[-1]) else None,
            "bb_middle": float(bb["middle"].iloc[-1]) if len(bb) > 0 and not pd.isna(bb["middle"].iloc[-1]) else None,
            "bb_lower": float(bb["lower"].iloc[-1]) if len(bb) > 0 and not pd.isna(bb["lower"].iloc[-1]) else None,
            "sma_20": float(sma_20.iloc[-1]) if len(sma_20) > 0 and not pd.isna(sma_20.iloc[-1]) else None,
            "sma_50": float(sma_50.iloc[-1]) if len(sma_50) > 0 and not pd.isna(sma_50.iloc[-1]) else None,
            "sma_200": float(sma_200.iloc[-1]) if len(sma_200) > 0 and not pd.isna(sma_200.iloc[-1]) else None,
            "macd": float(macd["macd"].iloc[-1]) if len(macd) > 0 and not pd.isna(macd["macd"].iloc[-1]) else None,
            "macd_signal": float(macd["signal"].iloc[-1]) if len(macd) > 0 and not pd.isna(macd["signal"].iloc[-1]) else None,
            "macd_histogram": float(macd["histogram"].iloc[-1]) if len(macd) > 0 and not pd.isna(macd["histogram"].iloc[-1]) else None,
            "atr": float(atr.iloc[-1]) if len(atr) > 0 and not pd.isna(atr.iloc[-1]) else None,
            "volume_sma": float(volume_sma.iloc[-1]) if len(volume_sma) > 0 and not pd.isna(volume_sma.iloc[-1]) else None,
            "adx": float(adx.iloc[-1]) if adx is not None and len(adx) > 0 and not pd.isna(adx.iloc[-1]) else None,
            "at_support":         sr["at_support"]         if sr else False,
            "at_resistance":      sr["at_resistance"]      if sr else False,
            "nearest_support":    sr["nearest_support"]    if sr else None,
            "nearest_resistance": sr["nearest_resistance"] if sr else None,
            "support_strength":   sr["support_strength"]   if sr else 0,
            "resistance_strength": sr["resistance_strength"] if sr else 0,
            "bullish_patterns":   cp["bullish_patterns"]  if cp else [],
            "bearish_patterns":   cp["bearish_patterns"]  if cp else [],
            "pattern_weight":     cp["pattern_weight"]    if cp else 0.0,
            "pattern_signal":     cp["pattern_signal"]    if cp else "neutral",
        }

    def generate_signal(self, df: pd.DataFrame, config: Dict[str, Any], market_context: Optional[Dict[str, Any]] = None) -> TradingSignal:
        indicators = self.calculate_all(df)
        
        if not indicators.get("rsi"):
            return TradingSignal(
                signal=Signal.HOLD,
                confidence=0.0,
                price=df["close"].iloc[-1] if len(df) > 0 else 0,
                indicators={},
                reasoning="Insufficient data for indicators"
            )

        strategy = config.get('strategy', 'momentum')
        rsi = indicators["rsi"]
        price = df["close"].iloc[-1]
        bb_lower = indicators.get("bb_lower")
        bb_upper = indicators.get("bb_upper")
        bb_middle = indicators.get("bb_middle")
        sma_20 = indicators.get("sma_20")
        sma_50 = indicators.get("sma_50")
        sma_200 = indicators.get("sma_200")
        macd = indicators.get("macd")
        macd_signal_val = indicators.get("macd_signal")
        macd_histogram = indicators.get("macd_histogram")
        atr = indicators.get("atr")

        # ADX — trend strength (not direction). Gated by candle count (needs 28+ candles)
        adx = indicators.get("adx")

        # Volume context — current candle vs 20-period average
        volume_sma = indicators.get("volume_sma")
        current_volume = float(df["volume"].iloc[-1]) if "volume" in df.columns else None
        volume_ratio = (current_volume / volume_sma) if (current_volume and volume_sma and volume_sma > 0) else None

        # Previous candle values for true crossover detection
        prev_sma_20 = prev_sma_50 = None
        prev_ema_fast = prev_ema_slow = None
        ema_fast_val = ema_slow_val = None

        if len(df) >= 2 and strategy in ("breakout", "ema_crossover"):
            _close = df["close"]
            if strategy == "breakout":
                _sma20_ser = self.calculate_sma(_close, 20)
                _sma50_ser = self.calculate_sma(_close, 50)
                if len(_sma20_ser) >= 2 and not pd.isna(_sma20_ser.iloc[-2]):
                    prev_sma_20 = float(_sma20_ser.iloc[-2])
                if len(_sma50_ser) >= 2 and not pd.isna(_sma50_ser.iloc[-2]):
                    prev_sma_50 = float(_sma50_ser.iloc[-2])
            elif strategy == "ema_crossover":
                cfg = config.get("indicators_config", {})
                fast_p = int(cfg.get("ema_fast", 9))
                slow_p = int(cfg.get("ema_slow", 21))
                _ema_f = self.calculate_ema(_close, fast_p)
                _ema_s = self.calculate_ema(_close, slow_p)
                if len(_ema_f) >= 2 and not pd.isna(_ema_f.iloc[-1]):
                    ema_fast_val = float(_ema_f.iloc[-1])
                    prev_ema_fast = float(_ema_f.iloc[-2]) if not pd.isna(_ema_f.iloc[-2]) else None
                if len(_ema_s) >= 2 and not pd.isna(_ema_s.iloc[-1]):
                    ema_slow_val = float(_ema_s.iloc[-1])
                    prev_ema_slow = float(_ema_s.iloc[-2]) if not pd.isna(_ema_s.iloc[-2]) else None

        signals: List[tuple] = []  # (Signal, weight, reasoning)

        # Compute divergence before signal functions — used as a modifier after
        _divergence = self.detect_divergence(df["close"]) if len(df) >= 22 else \
                      {"bullish_divergence": False, "bearish_divergence": False,
                       "divergence_weight": 0.0, "divergence_reason": ""}
        # Add to indicators dict so it's visible in team context / logging
        indicators["bullish_divergence"] = _divergence["bullish_divergence"]
        indicators["bearish_divergence"] = _divergence["bearish_divergence"]
        indicators["divergence_weight"]  = _divergence["divergence_weight"]

        if strategy == "momentum":
            signals = self._momentum_signals(
                rsi, price, sma_20, sma_50, sma_200, macd, macd_signal_val, atr, volume_ratio
            )
        elif strategy == "mean_reversion":
            signals = self._mean_reversion_signals(
                rsi, price, bb_lower, bb_upper, bb_middle, sma_20, volume_ratio, sma_50
            )
        elif strategy == "breakout":
            signals = self._breakout_signals(
                rsi, price, bb_lower, bb_upper, sma_20, sma_50, atr, macd, macd_signal_val,
                prev_sma_20, prev_sma_50, volume_ratio
            )
        elif strategy == "grid":
            signals = self._grid_signals(
                rsi, price, bb_lower, bb_upper, bb_middle, sma_20, atr
            )
        elif strategy == "ema_crossover":
            signals = self._ema_crossover_signals(
                rsi, price, ema_fast_val, ema_slow_val, prev_ema_fast, prev_ema_slow,
                macd, macd_signal_val, atr,
                config.get("indicators_config", {}),
                volume_ratio,
            )
        else:
            signals = self._default_signals(
                rsi, price, bb_lower, bb_upper, sma_20, sma_50, macd, macd_signal_val, volume_ratio
            )

        if not signals:
            return TradingSignal(
                signal=Signal.HOLD,
                confidence=0.0,
                price=price,
                indicators=indicators,
                reasoning=f"No clear {strategy} signal"
            )

        buy_weight = sum(w for s, w, _ in signals if s == Signal.BUY)
        sell_weight = sum(w for s, w, _ in signals if s == Signal.SELL)
        total_weight = buy_weight + sell_weight

        if total_weight == 0:
            final_signal = Signal.HOLD
            confidence = 0.0
            reasoning = "No weighted signals"
        elif buy_weight > sell_weight:
            final_signal = Signal.BUY
            confidence = min(buy_weight / max(total_weight, 1), 1.0)
            reasoning = "; ".join(r for s, _, r in signals if s == Signal.BUY)
        elif sell_weight > buy_weight:
            final_signal = Signal.SELL
            confidence = min(sell_weight / max(total_weight, 1), 1.0)
            reasoning = "; ".join(r for s, _, r in signals if s == Signal.SELL)
        else:
            # Signals tied — no clear edge, stay out
            final_signal = Signal.HOLD
            confidence = 0.0
            reasoning = "Buy/sell signals balanced — no clear edge"

        # ── ADX trend-strength filter ─────────────────────────────────────────
        # ADX measures trend strength (not direction). Applied before market-context
        # so the base confidence reflects actual trend conviction.
        #
        # Affected strategies: momentum, breakout, ema_crossover (trend-following).
        # Not applied to: mean_reversion, grid (thrive in ranging/low-ADX regimes).
        if adx is not None and final_signal != Signal.HOLD:
            _adx_trending_strategies = ("momentum", "breakout", "ema_crossover")
            _adx_ranging_strategies  = ("mean_reversion", "grid")
            if strategy in _adx_trending_strategies:
                if adx < 15:
                    # Only dampen when trend is truly absent (ADX < 15).
                    # ADX 15–20 = early trend forming — don't suppress it.
                    confidence *= 0.70
                    reasoning += f" | ADX {adx:.1f} — no trend yet, dampening confidence -30%"
                elif adx > 40:
                    confidence = min(confidence * 1.25, 1.0)
                    reasoning += f" | ADX {adx:.1f} — strong trend, boosting confidence +25%"
                elif adx > 25:
                    confidence = min(confidence * 1.15, 1.0)
                    reasoning += f" | ADX {adx:.1f} — confirmed trend, boosting confidence +15%"
            elif strategy in _adx_ranging_strategies:
                if adx < 20:
                    confidence = min(confidence * 1.15, 1.0)
                    reasoning += f" | ADX {adx:.1f} — ranging market, boosting {strategy} +15%"
                elif adx > 30:
                    confidence *= 0.75
                    reasoning += f" | ADX {adx:.1f} — trending market, dampening {strategy} -25%"

        # ── Support / Resistance proximity filter ─────────────────────────────
        # Boost BUY signals near support (favourable risk/reward entry).
        # Reduce BUY signals near resistance (buying into a wall is low R/R).
        # Vice versa for SELL signals.
        # Stronger S/R clusters (more swing-point touches) carry more weight.
        _at_support    = indicators.get("at_support",    False)
        _at_resistance = indicators.get("at_resistance", False)
        _s_strength    = indicators.get("support_strength",    0)
        _r_strength    = indicators.get("resistance_strength", 0)
        if final_signal != Signal.HOLD:
            _sr_boost = min(0.10 + (_s_strength - 1) * 0.05, 0.20)   # 10–20% based on cluster strength
            _sr_pen   = min(0.15 + (_r_strength - 1) * 0.05, 0.25)   # 15–25% based on cluster strength
            if final_signal == Signal.BUY and _at_support:
                confidence = min(confidence * (1.0 + _sr_boost), 1.0)
                reasoning += f" | Near support (strength {_s_strength}) +{_sr_boost:.0%}"
            elif final_signal == Signal.BUY and _at_resistance:
                confidence *= (1.0 - _sr_pen)
                reasoning += f" | Near resistance (strength {_r_strength}) -{_sr_pen:.0%} — low R/R buy"
            elif final_signal == Signal.SELL and _at_resistance:
                confidence = min(confidence * (1.0 + _sr_boost), 1.0)
                reasoning += f" | Near resistance (strength {_r_strength}) +{_sr_boost:.0%}"
            elif final_signal == Signal.SELL and _at_support:
                confidence *= (1.0 - _sr_pen)
                reasoning += f" | Near support (strength {_s_strength}) -{_sr_pen:.0%} — low R/R sell"

        # ── Candlestick pattern confirmation ──────────────────────────────────
        # Patterns are a secondary confirmation layer — they don't generate new signals
        # but they can boost confidence when aligned, or reduce it when opposed.
        # Only applies to momentum, mean_reversion, and ema_crossover strategies
        # (breakout already has strong directional conviction; grid ignores candles).
        _pattern_weight  = indicators.get("pattern_weight", 0.0)
        _bullish_pats    = indicators.get("bullish_patterns", [])
        _bearish_pats    = indicators.get("bearish_patterns", [])
        _candle_strategies = ("momentum", "mean_reversion", "ema_crossover", "default")
        if _pattern_weight != 0.0 and strategy in _candle_strategies and final_signal != Signal.HOLD:
            if final_signal == Signal.BUY and _pattern_weight > 0:
                confidence = min(confidence + _pattern_weight, 1.0)
                reasoning += f" | Candle pattern confirms: {', '.join(_bullish_pats)} (+{_pattern_weight:.2f})"
            elif final_signal == Signal.SELL and _pattern_weight < 0:
                confidence = min(confidence + abs(_pattern_weight), 1.0)
                reasoning += f" | Candle pattern confirms: {', '.join(_bearish_pats)} (+{abs(_pattern_weight):.2f})"
            elif final_signal == Signal.BUY and _pattern_weight < 0:
                confidence = max(confidence + _pattern_weight, 0.01)
                reasoning += f" | Candle pattern opposes: {', '.join(_bearish_pats)} ({_pattern_weight:.2f})"
            elif final_signal == Signal.SELL and _pattern_weight > 0:
                confidence = max(confidence - _pattern_weight, 0.01)
                reasoning += f" | Candle pattern opposes: {', '.join(_bullish_pats)} (-{_pattern_weight:.2f})"

        # ── RSI divergence ────────────────────────────────────────────────────
        # Divergence is a high-conviction reversal signal (weight 0.35).
        # When aligned with final_signal it adds confidence; when opposed it reduces.
        # Applied to mean_reversion, momentum, ema_crossover only.
        _div_weight = _divergence["divergence_weight"]
        _div_reason = _divergence["divergence_reason"]
        _div_strategies = ("momentum", "mean_reversion", "ema_crossover", "default")
        if _div_weight != 0.0 and strategy in _div_strategies and final_signal != Signal.HOLD:
            if final_signal == Signal.BUY and _div_weight > 0:
                confidence = min(confidence + _div_weight, 1.0)
                reasoning += f" | {_div_reason} (+{_div_weight:.2f})"
            elif final_signal == Signal.SELL and _div_weight < 0:
                confidence = min(confidence + abs(_div_weight), 1.0)
                reasoning += f" | {_div_reason} (+{abs(_div_weight):.2f})"
            elif final_signal == Signal.BUY and _div_weight < 0:
                confidence = max(confidence + _div_weight, 0.01)
                reasoning += f" | {_div_reason} ({_div_weight:.2f})"
            elif final_signal == Signal.SELL and _div_weight > 0:
                confidence = max(confidence - _div_weight, 0.01)
                reasoning += f" | {_div_reason} (-{_div_weight:.2f})"

        # Apply market context adjustments (team intelligence for non-AI strategies)
        if market_context and final_signal != Signal.HOLD:
            regime = market_context.get("regime", "").lower()
            ta_signal = market_context.get("ta_signal", "").lower()
            ta_confidence = market_context.get("ta_confidence", 0)
            ta_alignment = market_context.get("ta_alignment", "unknown").lower()
            ta_confluence_score = market_context.get("ta_confluence_score", 0.0)
            risk_level = market_context.get("risk_level", "safe").lower()
            win_rate = market_context.get("win_rate")
            htf_trend = market_context.get("htf_trend", "").lower()

            adjustments = []

            # Load configurable thresholds (fall back to safe defaults)
            try:
                from app.api.routes.settings import get_trading_gates as _get_ind_gates
                _ind_gates = _get_ind_gates()
                _htf_boost   = _ind_gates.htf_aligned_boost
                _htf_penalty = _ind_gates.htf_opposed_penalty
                _mtf_strong  = _ind_gates.mtf_strong_alignment_score
                _mtf_boost   = _ind_gates.mtf_aligned_boost
                _mtf_penalty = _ind_gates.mtf_opposed_penalty
                _mtf_mixed_threshold = 1.0 - _ind_gates.mtf_mixed_penalty  # complement for score calc
                _mtf_mixed_pen = _ind_gates.mtf_mixed_penalty
                _ta_boost    = _ind_gates.ta_boost_multiplier
                _ta_penalty  = _ind_gates.ta_penalty_multiplier
                _ta_min_conf = _ind_gates.ta_min_confidence
            except Exception:
                _htf_boost = 0.15; _htf_penalty = 0.30
                _mtf_strong = 0.55; _mtf_boost = 0.10; _mtf_penalty = 0.25
                _mtf_mixed_threshold = 0.55; _mtf_mixed_pen = 0.20
                _ta_boost = 0.20; _ta_penalty = 0.40; _ta_min_conf = 0.60

            # ── Multi-timeframe alignment ──────────────────────────────────
            if htf_trend and htf_trend != "neutral":
                aligned = (
                    (final_signal == Signal.BUY  and htf_trend == "bullish") or
                    (final_signal == Signal.SELL and htf_trend == "bearish")
                )
                opposed = (
                    (final_signal == Signal.BUY  and htf_trend == "bearish") or
                    (final_signal == Signal.SELL and htf_trend == "bullish")
                )
                if aligned:
                    confidence = min(confidence * (1.0 + _htf_boost), 1.0)
                    adjustments.append(f"HTF aligned ({htf_trend}) +{_htf_boost:.0%}")
                elif opposed:
                    confidence *= (1.0 - _htf_penalty)
                    adjustments.append(f"HTF opposes signal ({htf_trend} vs {final_signal.value}) -{_htf_penalty:.0%}")

            # ── TA confluence score adjustment ─────────────────────────────
            # Strong alignment across TFs boosts; mixed with low score penalises
            if ta_alignment and ta_confluence_score > 0:
                if ta_alignment in ("bullish", "bearish"):
                    signal_matches = (
                        (ta_alignment == "bullish" and final_signal == Signal.BUY) or
                        (ta_alignment == "bearish" and final_signal == Signal.SELL)
                    )
                    if signal_matches and ta_confluence_score >= _mtf_strong:
                        confidence = min(confidence * (1.0 + _mtf_boost), 1.0)
                        adjustments.append(f"MTF aligned ({ta_alignment}, score={ta_confluence_score:.2f}) +{_mtf_boost:.0%}")
                    elif not signal_matches and ta_confluence_score >= _mtf_strong:
                        confidence *= (1.0 - _mtf_penalty)
                        adjustments.append(f"MTF opposes signal ({ta_alignment} vs {final_signal.value}) -{_mtf_penalty:.0%}")
                elif ta_alignment == "mixed" and ta_confluence_score < (1.0 - _mtf_mixed_pen):
                    confidence *= (1.0 - _mtf_mixed_pen)
                    adjustments.append(f"MTF mixed/weak (score={ta_confluence_score:.2f}) -{_mtf_mixed_pen:.0%}")

            # Regime–strategy alignment
            if strategy in ("momentum", "breakout") and regime in ("ranging",):
                confidence *= 0.7
                adjustments.append(f"reduced confidence (ranging market vs {strategy})")
            elif strategy == "mean_reversion" and regime in ("trending_up", "trending_down"):
                confidence *= 0.7
                adjustments.append(f"reduced confidence (trending market vs mean_reversion)")
            elif strategy == "grid" and regime in ("ranging",):
                confidence = min(confidence * 1.2, 1.0)
                adjustments.append("boosted confidence (grid strategy thrives in ranging market)")
            elif strategy == "grid" and regime in ("trending_up", "trending_down"):
                confidence *= 0.6
                adjustments.append("reduced confidence (grid strategy in trending market — caution)")
            elif strategy == "ema_crossover" and regime in ("trending_up", "trending_down"):
                confidence = min(confidence * 1.2, 1.0)
                adjustments.append(f"boosted confidence (EMA crossover suits trending market)")
            elif strategy == "ema_crossover" and regime in ("ranging",):
                confidence *= 0.7
                adjustments.append("reduced confidence (EMA crossover generates false signals in ranging market)")
            elif strategy in ("momentum",) and regime in ("trending_up",) and final_signal == Signal.BUY:
                confidence = min(confidence * 1.15, 1.0)
                adjustments.append("boosted confidence (momentum + trending_up + buy)")
            elif strategy in ("momentum",) and regime in ("trending_down",) and final_signal == Signal.SELL:
                confidence = min(confidence * 1.15, 1.0)
                adjustments.append("boosted confidence (momentum + trending_down + sell)")

            # TA confluence boost/penalty
            if ta_signal and ta_confidence > _ta_min_conf:
                if (ta_signal == "bullish" and final_signal == Signal.BUY) or \
                   (ta_signal == "bearish" and final_signal == Signal.SELL):
                    confidence = min(confidence * (1.0 + _ta_boost), 1.0)
                    adjustments.append(f"boosted by TA confluence ({ta_signal}) +{_ta_boost:.0%}")
                elif (ta_signal == "bullish" and final_signal == Signal.SELL) or \
                     (ta_signal == "bearish" and final_signal == Signal.BUY):
                    confidence *= (1.0 - _ta_penalty)
                    adjustments.append(f"penalised — TA opposes ({ta_signal} vs {final_signal.value}) -{_ta_penalty:.0%}")

            # Risk level dampening
            if risk_level == "danger":
                confidence *= 0.5
                adjustments.append("halved — risk level danger")
            elif risk_level == "caution" and final_signal == Signal.BUY:
                confidence *= 0.8
                adjustments.append("reduced — risk level caution")

            # Agent performance dampening
            if win_rate is not None and win_rate < 0.4:
                confidence *= 0.8
                adjustments.append(f"reduced — low win rate ({win_rate:.0%})")

            if adjustments:
                reasoning += " | Context: " + ", ".join(adjustments)

        return TradingSignal(
            signal=final_signal,
            confidence=round(confidence, 3),
            price=price,
            indicators=indicators,
            reasoning=reasoning
        )

    def _momentum_signals(self, rsi, price, sma_20, sma_50, sma_200, macd, macd_signal_val, atr, volume_ratio=None):
        """Momentum: follow the trend — only trade clear trends, not noise."""
        signals = []

        # Trend direction via SMAs.
        # Full confirmation: price > SMA20 > SMA50 (established trend, weight 0.5).
        # Early trend: price > SMA20, SMA20 rising but not yet > SMA50 (weight 0.25).
        # This catches moves earlier — the SMA50 cross is a lagging confirmation,
        # not the only entry gate.
        _trend_down = sma_20 and sma_50 and price < sma_20 and sma_20 < sma_50
        _trend_up   = sma_20 and sma_50 and price > sma_20 and sma_20 > sma_50

        # Early trend: SMA20 above SMA50 not yet required, but price must be above SMA20
        # and SMA20 must be above SMA50 * 0.995 (within 0.5% — approaching crossover).
        _early_up   = (sma_20 and sma_50 and not _trend_up and not _trend_down
                       and price > sma_20 and sma_20 > sma_50 * 0.995)
        _early_down = (sma_20 and sma_50 and not _trend_up and not _trend_down
                       and price < sma_20 and sma_20 < sma_50 * 1.005)

        if sma_20 and sma_50:
            if _trend_up:
                signals.append((Signal.BUY, 0.5, "Strong uptrend: price > SMA20 > SMA50"))
            elif _trend_down:
                signals.append((Signal.SELL, 0.5, "Strong downtrend: price < SMA20 < SMA50"))
            elif _early_up:
                signals.append((Signal.BUY, 0.25, "Early uptrend: price > SMA20, SMA50 cross imminent"))
            elif _early_down:
                signals.append((Signal.SELL, 0.25, "Early downtrend: price < SMA20, SMA50 cross imminent"))

        # RSI — only fire with genuine extremes, not weak signals.
        # Momentum should only ADD to signals when RSI is EXTREME (< 25 or > 75).
        # Moderate RSI (30-70) is noise and should not trigger momentum entries.
        # RSI < 30 in a confirmed downtrend is a falling knife — skip.
        if rsi is not None:
            if rsi < 25 and not _trend_down:
                signals.append((Signal.BUY, 0.5, f"RSI deeply oversold ({rsi:.1f}) — strong reversion"))
            elif rsi > 75 and not _trend_up:
                signals.append((Signal.SELL, 0.5, f"RSI deeply overbought ({rsi:.1f}) — strong reversion"))
            # Suppress moderate RSI (30-70): momentum thrives on trend structure, not mid-range RSI

        # MACD — require meaningful divergence
        if macd is not None and macd_signal_val is not None:
            diff = macd - macd_signal_val
            if atr and atr > 0 and abs(diff) / atr > 0.01:
                if diff > 0:
                    signals.append((Signal.BUY, 0.25, "MACD bullish crossover"))
                else:
                    signals.append((Signal.SELL, 0.25, "MACD bearish crossover"))

        # Volume confirmation — momentum on high volume is more reliable
        if volume_ratio is not None:
            if volume_ratio > 1.5:
                # High volume: boost the dominant signal direction
                buy_w = sum(w for s, w, _ in signals if s == Signal.BUY)
                sell_w = sum(w for s, w, _ in signals if s == Signal.SELL)
                if buy_w > sell_w:
                    signals.append((Signal.BUY, 0.15, f"Volume spike confirms momentum ({volume_ratio:.1f}× avg)"))
                elif sell_w > buy_w:
                    signals.append((Signal.SELL, 0.15, f"Volume spike confirms momentum ({volume_ratio:.1f}× avg)"))
            elif volume_ratio < 0.5:
                # Very low volume — dampen both sides (weak conviction)
                signals = [(s, w * 0.7, r + " [low-volume caution]") for s, w, r in signals]

        return signals

    def _mean_reversion_signals(self, rsi, price, bb_lower, bb_upper, bb_middle, sma_20, volume_ratio=None, sma_50=None):
        """Mean reversion: only trade genuine extremes, not mild deviations.

        Mean reversion assumes price will revert toward a stable mean (SMA20).
        In a trending market the mean itself is moving — buying "oversold" in a
        downtrend is catching a falling knife, not a reversion opportunity.
        Defense-in-depth gate: suppress counter-trend signals when SMA20/SMA50
        confirm a trend structure (regime gate handles the common case, but can
        lag by up to 5 minutes after a regime shift).
        """
        signals = []

        # Trend structure gate: suppress BUY signals in confirmed downtrends
        # and SELL signals in confirmed uptrends. Allow neutral (SMA20 ≈ SMA50).
        _in_downtrend = sma_20 and sma_50 and sma_20 < sma_50 * 0.99
        _in_uptrend   = sma_20 and sma_50 and sma_20 > sma_50 * 1.01

        # RSI mean reversion — only genuine extremes, aligned with trend structure
        if rsi is not None:
            if rsi < 25 and not _in_downtrend:
                signals.append((Signal.BUY, 0.5, f"RSI deeply oversold for reversion ({rsi:.1f})"))
            elif rsi < 30 and not _in_downtrend:
                signals.append((Signal.BUY, 0.35, f"RSI oversold for reversion ({rsi:.1f})"))
            elif rsi > 75 and not _in_uptrend:
                signals.append((Signal.SELL, 0.5, f"RSI deeply overbought for reversion ({rsi:.1f})"))
            elif rsi > 70 and not _in_uptrend:
                signals.append((Signal.SELL, 0.35, f"RSI overbought for reversion ({rsi:.1f})"))

        # Bollinger Band reversion — only at the extremes, and only when not in a trend
        if bb_lower and bb_upper and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                position_in_band = (price - bb_lower) / bb_range
                if position_in_band < 0.10 and not _in_downtrend:
                    signals.append((Signal.BUY, 0.45, f"Price at lower BB extreme ({position_in_band:.0%} of range)"))
                elif position_in_band > 0.90 and not _in_uptrend:
                    signals.append((Signal.SELL, 0.45, f"Price at upper BB extreme ({position_in_band:.0%} of range)"))

        # Price vs SMA20 deviation — require larger deviation, respect trend
        if sma_20 and sma_20 > 0:
            deviation = (price - sma_20) / sma_20
            if deviation < -0.03 and not _in_downtrend:
                signals.append((Signal.BUY, 0.25, f"Price {abs(deviation):.1%} below SMA20"))
            elif deviation > 0.03 and not _in_uptrend:
                signals.append((Signal.SELL, 0.25, f"Price {deviation:.1%} above SMA20"))

        # Volume: low volume at extremes = exhaustion (good reversal); high volume = possible breakout
        if volume_ratio is not None and signals:
            if volume_ratio > 2.0:
                signals = [(s, w * 0.75, r + f" [high-vol caution: {volume_ratio:.1f}×]") for s, w, r in signals]
            elif volume_ratio < 0.8:
                buy_w = sum(w for s, w, _ in signals if s == Signal.BUY)
                sell_w = sum(w for s, w, _ in signals if s == Signal.SELL)
                dom = Signal.BUY if buy_w >= sell_w else Signal.SELL
                signals.append((dom, 0.10, f"Low volume ({volume_ratio:.1f}×) at extreme — exhaustion reversal"))

        return signals

    def _breakout_signals(self, rsi, price, bb_lower, bb_upper, sma_20, sma_50, atr, macd, macd_signal_val,
                          prev_sma_20=None, prev_sma_50=None, volume_ratio=None):
        """Breakout: detect range breaks — volume confirmation is critical here.
        
        Breakouts at the exact moment of band touch are often exhaustion, not strength.
        Reject overbought/oversold RSI (>75 or <25) to avoid entering at the top/bottom.
        """
        signals = []

        # Bollinger Band breakout (price outside bands = breakout)
        # CAUTION: At RSI >75 (overbought), a BB break is often exhaustion — reduce confidence
        # At RSI <25 (oversold), a BB break is often panic — reduce confidence
        if bb_upper and price > bb_upper:
            rsi_overbought = rsi and rsi > 75
            if not rsi_overbought:
                signals.append((Signal.BUY, 0.25, "Price broke above upper BB — bullish breakout"))
            else:
                signals.append((Signal.BUY, 0.08, f"Price broke above BB but RSI overbought {rsi:.0f} — weak entry risk"))
        elif bb_lower and price < bb_lower:
            rsi_oversold = rsi and rsi < 25
            if not rsi_oversold:
                signals.append((Signal.SELL, 0.25, "Price broke below lower BB — bearish breakout"))
            else:
                signals.append((Signal.SELL, 0.08, f"Price broke below BB but RSI oversold {rsi:.0f} — weak entry risk"))

        # SMA crossover — require an actual crossover event, not just current state
        if sma_20 and sma_50 and prev_sma_20 and prev_sma_50:
            just_crossed_above = (sma_20 > sma_50) and (prev_sma_20 <= prev_sma_50)
            just_crossed_below = (sma_20 < sma_50) and (prev_sma_20 >= prev_sma_50)
            if just_crossed_above:
                signals.append((Signal.BUY, 0.4, "SMA20 just crossed above SMA50 — fresh bullish crossover"))
            elif just_crossed_below:
                signals.append((Signal.SELL, 0.4, "SMA20 just crossed below SMA50 — fresh bearish crossover"))
        elif sma_20 and sma_50 and (prev_sma_20 is None or prev_sma_50 is None):
            ratio = sma_20 / sma_50 if sma_50 else 1
            if ratio > 1.02:
                signals.append((Signal.BUY, 0.2, "SMA20 well above SMA50 — bullish trend (no crossover data)"))
            elif ratio < 0.98:
                signals.append((Signal.SELL, 0.2, "SMA20 well below SMA50 — bearish trend (no crossover data)"))

        # RSI momentum confirmation
        if rsi is not None:
            if rsi > 65:
                signals.append((Signal.BUY, 0.2, f"RSI confirms bullish momentum ({rsi:.1f})"))
            elif rsi < 35:
                signals.append((Signal.SELL, 0.2, f"RSI confirms bearish momentum ({rsi:.1f})"))

        # MACD momentum
        if macd is not None and macd_signal_val is not None:
            if macd > macd_signal_val and macd > 0:
                signals.append((Signal.BUY, 0.2, "MACD positive and above signal"))
            elif macd < macd_signal_val and macd < 0:
                signals.append((Signal.SELL, 0.2, "MACD negative and below signal"))

        # Volume — breakouts on high volume are genuine; low-volume breakouts are traps
        if volume_ratio is not None:
            if volume_ratio >= 2.0:  # Raised threshold from 1.5 to 2.0 for stronger conviction
                buy_w = sum(w for s, w, _ in signals if s == Signal.BUY)
                sell_w = sum(w for s, w, _ in signals if s == Signal.SELL)
                if buy_w > sell_w:
                    signals.append((Signal.BUY, 0.30, f"STRONG volume {volume_ratio:.1f}× confirms breakout — genuine move"))
                elif sell_w > buy_w:
                    signals.append((Signal.SELL, 0.30, f"STRONG volume {volume_ratio:.1f}× confirms breakdown — genuine move"))
            elif 1.2 <= volume_ratio < 2.0:  # Moderate volume — mildly helpful but not enough alone
                # Don't add extra weight; rely on other confirmations
                pass
            elif volume_ratio < 0.8:
                # Low-volume breakout = high false-positive risk — aggressively dampen
                signals = [(s, w * 0.3, r + f" [WEAK-VOL trap risk: {volume_ratio:.1f}× only]") for s, w, r in signals]

        return signals

    def _grid_signals(self, rsi, price, bb_lower, bb_upper, bb_middle, sma_20, atr):
        """Grid trading: buy at lower grid lines, sell at upper grid lines.

        Divides the Bollinger Band range into a grid. Generates BUY signals
        near the lower third of the range and SELL signals near the upper third.
        Strongest signals at the band extremes. Designed for ranging markets —
        the scheduler automatically reduces confidence in trending regimes.
        """
        signals = []

        if bb_lower and bb_upper and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                # Position within the band: 0 = at lower band, 1 = at upper band
                pos = (price - bb_lower) / bb_range

                # Lower grid zone: buy when price dips below midpoint
                if pos <= 0.20:
                    signals.append((Signal.BUY, 0.55, f"Grid: price at bottom of range ({pos:.0%}) — buy level"))
                elif pos <= 0.35:
                    signals.append((Signal.BUY, 0.40, f"Grid: price in lower zone ({pos:.0%}) — buy level"))
                # Upper grid zone: sell when price rises above midpoint
                elif pos >= 0.80:
                    signals.append((Signal.SELL, 0.55, f"Grid: price at top of range ({pos:.0%}) — sell level"))
                elif pos >= 0.65:
                    signals.append((Signal.SELL, 0.40, f"Grid: price in upper zone ({pos:.0%}) — sell level"))
                # Near midpoint: hold — wait for clearer grid level
                else:
                    signals.append((Signal.HOLD, 0.0, f"Grid: price near midpoint ({pos:.0%}) — waiting for grid level"))

        # RSI confirmation — grid buys need RSI not overbought, sells not oversold
        if rsi is not None:
            has_buy = any(s[0] == Signal.BUY for s in signals)
            has_sell = any(s[0] == Signal.SELL for s in signals)
            if has_buy and rsi < 45:
                signals.append((Signal.BUY, 0.20, f"RSI confirms grid buy ({rsi:.1f} — not overbought)"))
            elif has_sell and rsi > 55:
                signals.append((Signal.SELL, 0.20, f"RSI confirms grid sell ({rsi:.1f} — not oversold)"))

        # ATR range filter: only trade when market is not too volatile (grid needs tight range)
        if atr and bb_upper and bb_lower:
            bb_range = bb_upper - bb_lower
            # If ATR > 60% of BB range the market is too volatile for grid
            if atr > bb_range * 0.6:
                signals = [(s, w * 0.5, r + " [ATR high — volatility caution]") for s, w, r in signals]

        return signals

    def _ema_crossover_signals(
        self, rsi, price,
        ema_fast, ema_slow, prev_ema_fast, prev_ema_slow,
        macd, macd_signal_val, atr,
        cfg: dict,
        volume_ratio=None,
    ):
        """
        EMA Crossover — primary signal is the fast EMA crossing above/below the slow EMA.

        Entry conditions:
          BUY  — fast EMA crosses ABOVE slow EMA (golden cross), RSI not overbought,
                 MACD histogram positive (confirmation)
          SELL — fast EMA crosses BELOW slow EMA (death cross), RSI not oversold,
                 MACD histogram negative (confirmation)

        Signal weights:
          Crossover event  → 0.55 (primary, highest weight)
          EMA separation   → 0.20 (trend strength bonus)
          RSI confirmation → 0.15 (filter false signals)
          MACD confluence  → 0.10 (secondary confirmation)

        Total maximum: 1.0 — threshold for execution is handled by confidence_threshold.
        """
        signals = []

        if ema_fast is None or ema_slow is None:
            return signals

        fast_period = int(cfg.get("ema_fast", 9))
        slow_period = int(cfg.get("ema_slow", 21))
        rsi_overbought = int(cfg.get("rsi_overbought", 65))
        rsi_oversold   = int(cfg.get("rsi_oversold", 35))

        # ── Primary: crossover event (candle N-1 → candle N) ──────────────
        golden_cross = (
            prev_ema_fast is not None and prev_ema_slow is not None
            and prev_ema_fast <= prev_ema_slow  # was below or equal
            and ema_fast > ema_slow              # now above
        )
        death_cross = (
            prev_ema_fast is not None and prev_ema_slow is not None
            and prev_ema_fast >= prev_ema_slow
            and ema_fast < ema_slow
        )

        # Fallback: sustained alignment (no fresh cross but EMAs clearly separated)
        ema_gap_pct = abs(ema_fast - ema_slow) / ema_slow if ema_slow else 0
        ema_bullish = ema_fast > ema_slow and ema_gap_pct > 0.002  # 0.2% min separation
        ema_bearish = ema_fast < ema_slow and ema_gap_pct > 0.002

        if golden_cross:
            signals.append((Signal.BUY, 0.55, f"Golden cross: EMA{fast_period} crossed above EMA{slow_period}"))
        elif ema_bullish:
            signals.append((Signal.BUY, 0.30, f"EMA{fast_period} > EMA{slow_period} ({ema_gap_pct:.2%} gap)"))

        if death_cross:
            signals.append((Signal.SELL, 0.55, f"Death cross: EMA{fast_period} crossed below EMA{slow_period}"))
        elif ema_bearish:
            signals.append((Signal.SELL, 0.30, f"EMA{fast_period} < EMA{slow_period} ({ema_gap_pct:.2%} gap)"))

        # ── EMA separation bonus (trend strength) ─────────────────────────
        if ema_gap_pct > 0.005 and ema_bullish:
            signals.append((Signal.BUY, 0.20, f"Strong EMA separation ({ema_gap_pct:.2%}) — momentum building"))
        elif ema_gap_pct > 0.005 and ema_bearish:
            signals.append((Signal.SELL, 0.20, f"Strong EMA separation ({ema_gap_pct:.2%}) — downward momentum"))

        # ── RSI filter — avoid buying overbought / selling oversold ───────
        if rsi is not None:
            has_buy  = any(s[0] == Signal.BUY  for s in signals)
            has_sell = any(s[0] == Signal.SELL for s in signals)
            if has_buy and rsi < rsi_overbought:
                signals.append((Signal.BUY, 0.15, f"RSI confirms buy ({rsi:.1f} < {rsi_overbought} — not overbought)"))
            elif has_buy and rsi >= rsi_overbought:
                # Cancel buy — price is overbought, false cross likely
                signals = [(s, w * 0.4, r + f" [RSI overbought {rsi:.1f} — caution]")
                           for s, w, r in signals if s == Signal.BUY] + \
                          [item for item in signals if item[0] != Signal.BUY]
            if has_sell and rsi > rsi_oversold:
                signals.append((Signal.SELL, 0.15, f"RSI confirms sell ({rsi:.1f} > {rsi_oversold} — not oversold)"))
            elif has_sell and rsi <= rsi_oversold:
                signals = [(s, w * 0.4, r + f" [RSI oversold {rsi:.1f} — caution]")
                           for s, w, r in signals if s == Signal.SELL] + \
                          [item for item in signals if item[0] != Signal.SELL]

        # ── MACD confluence (secondary confirmation) ───────────────────────
        if macd is not None and macd_signal_val is not None:
            macd_hist = macd - macd_signal_val
            has_buy  = any(s[0] == Signal.BUY  for s in signals)
            has_sell = any(s[0] == Signal.SELL for s in signals)
            if has_buy and macd_hist > 0:
                signals.append((Signal.BUY, 0.10, f"MACD histogram positive ({macd_hist:+.4f}) — bullish momentum"))
            elif has_sell and macd_hist < 0:
                signals.append((Signal.SELL, 0.10, f"MACD histogram negative ({macd_hist:+.4f}) — bearish momentum"))

        # ── Volume confirmation ─────────────────────────────────────────────
        if volume_ratio is not None and signals:
            if volume_ratio >= 1.3:
                buy_w = sum(w for s, w, _ in signals if s == Signal.BUY)
                sell_w = sum(w for s, w, _ in signals if s == Signal.SELL)
                if buy_w > sell_w:
                    signals.append((Signal.BUY, 0.10, f"Volume {volume_ratio:.1f}× confirms EMA cross"))
                elif sell_w > buy_w:
                    signals.append((Signal.SELL, 0.10, f"Volume {volume_ratio:.1f}× confirms EMA cross"))
            elif volume_ratio < 0.6:
                signals = [(s, w * 0.75, r + f" [low-vol EMA cross caution]") for s, w, r in signals]

        return signals

    def _default_signals(self, rsi, price, bb_lower, bb_upper, sma_20, sma_50, macd, macd_signal_val, volume_ratio=None):
        """Balanced default strategy — require strong confluence."""
        signals = []

        # Trend structure gate: same defense-in-depth as mean_reversion —
        # don't buy RSI oversold or lower BB in a confirmed downtrend.
        _in_downtrend = sma_20 and sma_50 and sma_20 < sma_50 * 0.99
        _in_uptrend   = sma_20 and sma_50 and sma_20 > sma_50 * 1.01

        if rsi is not None:
            if rsi < 30 and not _in_downtrend:
                signals.append((Signal.BUY, 0.4, f"RSI oversold ({rsi:.1f})"))
            elif rsi > 70 and not _in_uptrend:
                signals.append((Signal.SELL, 0.4, f"RSI overbought ({rsi:.1f})"))

        if bb_lower and price <= bb_lower and not _in_downtrend:
            signals.append((Signal.BUY, 0.3, "Price at lower Bollinger Band"))
        elif bb_upper and price >= bb_upper and not _in_uptrend:
            signals.append((Signal.SELL, 0.3, "Price at upper Bollinger Band"))

        if sma_20 and sma_50:
            if sma_20 > sma_50 * 1.01:
                signals.append((Signal.BUY, 0.25, "SMA20 > SMA50 (bullish cross)"))
            elif sma_20 < sma_50 * 0.99:
                signals.append((Signal.SELL, 0.25, "SMA20 < SMA50 (bearish cross)"))

        if macd is not None and macd_signal_val is not None:
            diff = macd - macd_signal_val
            if diff > 0 and macd > 0:
                signals.append((Signal.BUY, 0.2, "MACD positive and above signal"))
            elif diff < 0 and macd < 0:
                signals.append((Signal.SELL, 0.2, "MACD negative and below signal"))

        # Volume confirmation
        if volume_ratio is not None and signals:
            if volume_ratio >= 1.5:
                buy_w = sum(w for s, w, _ in signals if s == Signal.BUY)
                sell_w = sum(w for s, w, _ in signals if s == Signal.SELL)
                dom = Signal.BUY if buy_w >= sell_w else Signal.SELL
                signals.append((dom, 0.15, f"Volume {volume_ratio:.1f}× confirms signal"))
            elif volume_ratio < 0.5:
                signals = [(s, w * 0.7, r + " [low-volume caution]") for s, w, r in signals]

        return signals
