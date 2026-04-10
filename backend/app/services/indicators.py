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
        elif strategy == "grid":
            signals = self._grid_signals(
                rsi, price, bb_lower, bb_upper, bb_middle, sma_20, atr
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
            # Signals tied — no clear edge, stay out
            final_signal = Signal.HOLD
            confidence = 0.0
            reasoning = "Buy/sell signals balanced — no clear edge"

        # Apply market context adjustments (team intelligence for non-AI strategies)
        if market_context and final_signal != Signal.HOLD:
            regime = market_context.get("regime", "").lower()
            ta_signal = market_context.get("ta_signal", "").lower()
            ta_confidence = market_context.get("ta_confidence", 0)
            risk_level = market_context.get("risk_level", "safe").lower()
            win_rate = market_context.get("win_rate")

            adjustments = []

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
            elif strategy in ("momentum",) and regime in ("trending_up",) and final_signal == Signal.BUY:
                confidence = min(confidence * 1.15, 1.0)
                adjustments.append("boosted confidence (momentum + trending_up + buy)")
            elif strategy in ("momentum",) and regime in ("trending_down",) and final_signal == Signal.SELL:
                confidence = min(confidence * 1.15, 1.0)
                adjustments.append("boosted confidence (momentum + trending_down + sell)")

            # TA confluence boost/penalty
            if ta_signal and ta_confidence > 0.6:
                if (ta_signal == "bullish" and final_signal == Signal.BUY) or \
                   (ta_signal == "bearish" and final_signal == Signal.SELL):
                    confidence = min(confidence * 1.2, 1.0)
                    adjustments.append(f"boosted by TA confluence ({ta_signal})")
                elif (ta_signal == "bullish" and final_signal == Signal.SELL) or \
                     (ta_signal == "bearish" and final_signal == Signal.BUY):
                    confidence *= 0.6
                    adjustments.append(f"penalised — TA opposes ({ta_signal} vs {final_signal.value})")

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

    def _momentum_signals(self, rsi, price, sma_20, sma_50, sma_200, macd, macd_signal_val, atr):
        """Momentum: follow the trend — only trade clear trends, not noise."""
        signals = []

        # Trend direction via SMAs — require clear alignment
        if sma_20 and sma_50:
            if price > sma_20 > sma_50:
                signals.append((Signal.BUY, 0.5, "Strong uptrend: price > SMA20 > SMA50"))
            elif price < sma_20 < sma_50:
                signals.append((Signal.SELL, 0.5, "Strong downtrend: price < SMA20 < SMA50"))

        # RSI — only extremes, not mild readings
        if rsi is not None:
            if rsi < 25:
                signals.append((Signal.BUY, 0.5, f"RSI deeply oversold ({rsi:.1f})"))
            elif rsi < 30:
                signals.append((Signal.BUY, 0.3, f"RSI oversold ({rsi:.1f})"))
            elif rsi > 75:
                signals.append((Signal.SELL, 0.5, f"RSI deeply overbought ({rsi:.1f})"))
            elif rsi > 70:
                signals.append((Signal.SELL, 0.3, f"RSI overbought ({rsi:.1f})"))

        # MACD — require meaningful divergence
        if macd is not None and macd_signal_val is not None:
            diff = macd - macd_signal_val
            if atr and atr > 0 and abs(diff) / atr > 0.01:
                if diff > 0:
                    signals.append((Signal.BUY, 0.25, "MACD bullish crossover"))
                else:
                    signals.append((Signal.SELL, 0.25, "MACD bearish crossover"))

        return signals

    def _mean_reversion_signals(self, rsi, price, bb_lower, bb_upper, bb_middle, sma_20):
        """Mean reversion: only trade genuine extremes, not mild deviations."""
        signals = []

        # RSI mean reversion — only genuine extremes
        if rsi is not None:
            if rsi < 25:
                signals.append((Signal.BUY, 0.5, f"RSI deeply oversold for reversion ({rsi:.1f})"))
            elif rsi < 30:
                signals.append((Signal.BUY, 0.35, f"RSI oversold for reversion ({rsi:.1f})"))
            elif rsi > 75:
                signals.append((Signal.SELL, 0.5, f"RSI deeply overbought for reversion ({rsi:.1f})"))
            elif rsi > 70:
                signals.append((Signal.SELL, 0.35, f"RSI overbought for reversion ({rsi:.1f})"))

        # Bollinger Band reversion — only at the extremes
        if bb_lower and bb_upper and bb_middle:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                position_in_band = (price - bb_lower) / bb_range
                if position_in_band < 0.10:
                    signals.append((Signal.BUY, 0.45, f"Price at lower BB extreme ({position_in_band:.0%} of range)"))
                elif position_in_band > 0.90:
                    signals.append((Signal.SELL, 0.45, f"Price at upper BB extreme ({position_in_band:.0%} of range)"))

        # Price vs SMA20 deviation — require larger deviation
        if sma_20 and sma_20 > 0:
            deviation = (price - sma_20) / sma_20
            if deviation < -0.03:
                signals.append((Signal.BUY, 0.25, f"Price {abs(deviation):.1%} below SMA20"))
            elif deviation > 0.03:
                signals.append((Signal.SELL, 0.25, f"Price {deviation:.1%} above SMA20"))

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

        # RSI momentum confirmation — only clear momentum
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

    def _default_signals(self, rsi, price, bb_lower, bb_upper, sma_20, sma_50, macd, macd_signal_val):
        """Balanced default strategy — require strong confluence."""
        signals = []

        if rsi is not None:
            if rsi < 30:
                signals.append((Signal.BUY, 0.4, f"RSI oversold ({rsi:.1f})"))
            elif rsi > 70:
                signals.append((Signal.SELL, 0.4, f"RSI overbought ({rsi:.1f})"))

        if bb_lower and price <= bb_lower:
            signals.append((Signal.BUY, 0.3, "Price at lower Bollinger Band"))
        elif bb_upper and price >= bb_upper:
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

        return signals
