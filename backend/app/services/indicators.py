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
        
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        
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
        atr = true_range.rolling(window=period).mean()
        return atr

    def calculate_volume_sma(self, volume: pd.Series, period: int = 20) -> pd.Series:
        return volume.rolling(window=period).mean()

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
        }

    def generate_signal(self, df: pd.DataFrame, config: Dict[str, Any]) -> TradingSignal:
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
        atr = indicators.get("atr")

        signals: List[tuple] = []  # (Signal, weight, reasoning)

        if strategy == "momentum":
            signals = self._momentum_signals(
                rsi, price, sma_20, sma_50, sma_200, macd, macd_signal_val, atr
            )
        elif strategy == "mean_reversion":
            signals = self._mean_reversion_signals(
                rsi, price, bb_lower, bb_upper, bb_middle, sma_20
            )
        elif strategy == "breakout":
            signals = self._breakout_signals(
                rsi, price, bb_lower, bb_upper, sma_20, sma_50, atr, macd, macd_signal_val
            )
        else:
            signals = self._default_signals(
                rsi, price, bb_lower, bb_upper, sma_20, sma_50, macd, macd_signal_val
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
            # Tie-break: use RSI direction
            if rsi and rsi < 50:
                final_signal = Signal.BUY
                confidence = 0.35
                reasoning = "Tie-broken by RSI < 50 (slight bullish lean)"
            elif rsi and rsi > 50:
                final_signal = Signal.SELL
                confidence = 0.35
                reasoning = "Tie-broken by RSI > 50 (slight bearish lean)"
            else:
                final_signal = Signal.HOLD
                confidence = 0.0
                reasoning = "Perfect signal tie, staying neutral"

        return TradingSignal(
            signal=final_signal,
            confidence=round(confidence, 3),
            price=price,
            indicators=indicators,
            reasoning=reasoning
        )

    def _momentum_signals(self, rsi, price, sma_20, sma_50, sma_200, macd, macd_signal_val, atr):
        """Momentum: follow the trend, wider RSI bands, heavier SMA weighting."""
        signals = []

        # Trend direction via SMAs (primary momentum indicator)
        if sma_20 and sma_50:
            if price > sma_20 > sma_50:
                signals.append((Signal.BUY, 0.4, "Strong uptrend: price > SMA20 > SMA50"))
            elif price > sma_20 and sma_20 > sma_50 * 0.99:
                signals.append((Signal.BUY, 0.25, "Uptrend forming: price above SMA20"))
            elif price < sma_20 < sma_50:
                signals.append((Signal.SELL, 0.4, "Strong downtrend: price < SMA20 < SMA50"))
            elif price < sma_20 and sma_20 < sma_50 * 1.01:
                signals.append((Signal.SELL, 0.25, "Downtrend forming: price below SMA20"))

        # RSI momentum (not just extremes)
        if rsi is not None:
            if rsi < 40:
                signals.append((Signal.BUY, 0.3 if rsi < 30 else 0.15, f"RSI momentum buy ({rsi:.1f})"))
            elif rsi > 60:
                signals.append((Signal.SELL, 0.3 if rsi > 70 else 0.15, f"RSI momentum sell ({rsi:.1f})"))

        # MACD crossover
        if macd is not None and macd_signal_val is not None:
            diff = macd - macd_signal_val
            if diff > 0:
                signals.append((Signal.BUY, 0.2, "MACD bullish"))
            elif diff < 0:
                signals.append((Signal.SELL, 0.2, "MACD bearish"))

        return signals

    def _mean_reversion_signals(self, rsi, price, bb_lower, bb_upper, bb_middle, sma_20):
        """Mean reversion: buy oversold, sell overbought, wider trigger zones."""
        signals = []

        # RSI mean reversion (wider bands than momentum)
        if rsi is not None:
            if rsi < 35:
                signals.append((Signal.BUY, 0.4 if rsi < 25 else 0.3, f"RSI oversold for reversion ({rsi:.1f})"))
            elif rsi > 65:
                signals.append((Signal.SELL, 0.4 if rsi > 75 else 0.3, f"RSI overbought for reversion ({rsi:.1f})"))

        # Bollinger Band reversion (proximity, not just touching)
        if bb_lower and bb_upper and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                position_in_band = (price - bb_lower) / bb_range
                if position_in_band < 0.15:
                    signals.append((Signal.BUY, 0.35, f"Price near lower BB ({position_in_band:.0%} of range)"))
                elif position_in_band < 0.30:
                    signals.append((Signal.BUY, 0.15, f"Price in lower BB zone ({position_in_band:.0%})"))
                elif position_in_band > 0.85:
                    signals.append((Signal.SELL, 0.35, f"Price near upper BB ({position_in_band:.0%} of range)"))
                elif position_in_band > 0.70:
                    signals.append((Signal.SELL, 0.15, f"Price in upper BB zone ({position_in_band:.0%})"))

        # Price vs SMA20 deviation
        if sma_20 and sma_20 > 0:
            deviation = (price - sma_20) / sma_20
            if deviation < -0.02:
                signals.append((Signal.BUY, 0.2, f"Price {abs(deviation):.1%} below SMA20"))
            elif deviation > 0.02:
                signals.append((Signal.SELL, 0.2, f"Price {deviation:.1%} above SMA20"))

        return signals

    def _breakout_signals(self, rsi, price, bb_lower, bb_upper, sma_20, sma_50, atr, macd, macd_signal_val):
        """Breakout: detect range breaks with volume/volatility confirmation."""
        signals = []

        # Bollinger Band breakout (price outside bands = breakout)
        if bb_upper and price > bb_upper:
            signals.append((Signal.BUY, 0.35, "Price broke above upper BB — bullish breakout"))
        elif bb_lower and price < bb_lower:
            signals.append((Signal.SELL, 0.35, "Price broke below lower BB — bearish breakout"))

        # SMA crossover breakout
        if sma_20 and sma_50:
            ratio = sma_20 / sma_50 if sma_50 else 1
            if ratio > 1.01:
                signals.append((Signal.BUY, 0.3, "SMA20 crossed above SMA50 — bullish breakout"))
            elif ratio < 0.99:
                signals.append((Signal.SELL, 0.3, "SMA20 crossed below SMA50 — bearish breakout"))

        # RSI momentum confirmation
        if rsi is not None:
            if rsi > 55:
                signals.append((Signal.BUY, 0.15, f"RSI confirms bullish momentum ({rsi:.1f})"))
            elif rsi < 45:
                signals.append((Signal.SELL, 0.15, f"RSI confirms bearish momentum ({rsi:.1f})"))

        # MACD momentum
        if macd is not None and macd_signal_val is not None:
            if macd > macd_signal_val and macd > 0:
                signals.append((Signal.BUY, 0.2, "MACD positive and above signal"))
            elif macd < macd_signal_val and macd < 0:
                signals.append((Signal.SELL, 0.2, "MACD negative and below signal"))

        return signals

    def _default_signals(self, rsi, price, bb_lower, bb_upper, sma_20, sma_50, macd, macd_signal_val):
        """Balanced default strategy combining multiple signals."""
        signals = []

        if rsi is not None:
            if rsi < 35:
                signals.append((Signal.BUY, 0.3, f"RSI oversold ({rsi:.1f})"))
            elif rsi > 65:
                signals.append((Signal.SELL, 0.3, f"RSI overbought ({rsi:.1f})"))

        if bb_lower and price <= bb_lower:
            signals.append((Signal.BUY, 0.25, "Price at lower Bollinger Band"))
        elif bb_upper and price >= bb_upper:
            signals.append((Signal.SELL, 0.25, "Price at upper Bollinger Band"))

        if sma_20 and sma_50:
            if sma_20 > sma_50:
                signals.append((Signal.BUY, 0.2, "SMA20 > SMA50 (bullish)"))
            else:
                signals.append((Signal.SELL, 0.2, "SMA20 < SMA50 (bearish)"))

        if macd is not None and macd_signal_val is not None:
            if macd > macd_signal_val:
                signals.append((Signal.BUY, 0.15, "MACD bullish"))
            else:
                signals.append((Signal.SELL, 0.15, "MACD bearish"))

        return signals
