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

        signals = []
        confidences = []

        rsi = indicators["rsi"]
        price = df["close"].iloc[-1]
        
        if rsi < 30:
            signals.append(Signal.BUY)
            confidences.append(0.8)
            reasoning = f"RSI oversold at {rsi:.2f}"
        elif rsi > 70:
            signals.append(Signal.SELL)
            confidences.append(0.8)
            reasoning = f"RSI overbought at {rsi:.2f}"

        bb_lower = indicators["bb_lower"]
        bb_upper = indicators["bb_upper"]
        
        if bb_lower and price <= bb_lower:
            signals.append(Signal.BUY)
            confidences.append(0.7)
            reasoning = f"Price at lower Bollinger Band"
        elif bb_upper and price >= bb_upper:
            signals.append(Signal.SELL)
            confidences.append(0.7)
            reasoning = f"Price at upper Bollinger Band"

        sma_20 = indicators["sma_20"]
        sma_50 = indicators["sma_50"]
        sma_200 = indicators["sma_200"]
        
        if sma_20 and sma_50:
            if sma_20 > sma_50 and (not sma_200 or sma_50 > sma_200):
                signals.append(Signal.BUY)
                confidences.append(0.6)
                reasoning = "Golden cross (SMA 20 > SMA 50)"
            elif sma_20 < sma_50 and (not sma_200 or sma_50 < sma_200):
                signals.append(Signal.SELL)
                confidences.append(0.6)
                reasoning = "Death cross (SMA 20 < SMA 50)"

        macd = indicators["macd"]
        macd_signal = indicators["macd_signal"]
        
        if macd and macd_signal:
            if macd > macd_signal:
                signals.append(Signal.BUY)
                confidences.append(0.5)
                reasoning = "MACD above signal line"
            elif macd < macd_signal:
                signals.append(Signal.SELL)
                confidences.append(0.5)
                reasoning = "MACD below signal line"

        buy_count = signals.count(Signal.BUY)
        sell_count = signals.count(Signal.SELL)
        
        if buy_count > sell_count:
            final_signal = Signal.BUY
        elif sell_count > buy_count:
            final_signal = Signal.SELL
        else:
            final_signal = Signal.HOLD

        confidence = max(confidences) if confidences else 0.0

        return TradingSignal(
            signal=final_signal,
            confidence=confidence,
            price=price,
            indicators=indicators,
            reasoning=reasoning
        )
