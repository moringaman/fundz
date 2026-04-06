from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
import logging

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService

logger = logging.getLogger(__name__)


@dataclass
class PriceLevels:
    support: List[float]
    resistance: List[float]
    pivot_points: Dict[str, float]
    fibonacci_retracements: Dict[str, float]
    fibonacci_extensions: Dict[str, float]


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
    
    async def analyze(self, symbol: str = "BTCUSDT") -> TechnicalAnalystReport:
        try:
            klines_1h = await self.phemex.get_klines(symbol, "1h", 200)
            klines_4h = await self.phemex.get_klines(symbol, "4h", 200)
            klines_1d = await self.phemex.get_klines(symbol, "1d", 100)

            data_1h = self._parse_klines(klines_1h)
            data_4h = self._parse_klines(klines_4h)
            data_1d = self._parse_klines(klines_1d)

            if not data_1h or len(data_1h) < 50:
                return self._empty_report(symbol)

            current_price = data_1h['close'].iloc[-1]

            price_levels = self._calculate_price_levels(data_1h)
            patterns = self._identify_patterns(data_1h, current_price)
            multi_tf = self._analyze_multitimeframe(data_1h, data_4h, data_1d, current_price)

            signal, confidence = self._generate_overall_signal(
                patterns, multi_tf, price_levels, current_price
            )

            observations = self._generate_observations(
                price_levels, patterns, multi_tf, current_price
            )

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
        
        return PriceLevels(
            support=support_levels,
            resistance=resistance_levels,
            pivot_points=pivot_data,
            fibonacci_retracements=fib_retracements,
            fibonacci_extensions=fib_extensions
        )

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

    def _identify_patterns(self, df: pd.DataFrame, current_price: float) -> List[PatternSignal]:
        patterns = []
        
        closes = df['close']
        highs = df['high']
        lows = df['low']
        
        rsi = self.indicator_service.calculate_rsi(closes).iloc[-1]
        macd_data = self.indicator_service.calculate_macd(closes)
        macd = macd_data['macd'].iloc[-1]
        macd_signal = macd_data['signal'].iloc[-1]
        bb = self.indicator_service.calculate_bollinger_bands(closes)
        
        bb_upper = bb['upper'].iloc[-1]
        bb_middle = bb['middle'].iloc[-1]
        bb_lower = bb['lower'].iloc[-1]

        if rsi < 35 and current_price <= bb_lower:
            rr = 3.0
            patterns.append(PatternSignal(
                pattern_type="oversold_bounce",
                direction="bullish",
                confidence=0.75,
                entry_price=current_price,
                stop_loss=bb_lower * 0.98,
                take_profit_1=bb_middle,
                take_profit_2=bb_upper,
                risk_reward=rr,
                reasoning=f"RSI oversold ({rsi:.1f}) + price at lower BB. Classic reversal setup."
            ))

        if rsi > 65 and current_price >= bb_upper:
            rr = 3.0
            patterns.append(PatternSignal(
                pattern_type="overbought_reversal",
                direction="bearish",
                confidence=0.75,
                entry_price=current_price,
                stop_loss=bb_upper * 1.02,
                take_profit_1=bb_middle,
                take_profit_2=bb_lower,
                risk_reward=rr,
                reasoning=f"RSI overbought ({rsi:.1f}) + price at upper BB. Expect rejection."
            ))

        if macd > macd_signal and macd > 0:
            rr = 2.0
            patterns.append(PatternSignal(
                pattern_type="macd_bullish_cross",
                direction="bullish",
                confidence=0.6,
                entry_price=current_price,
                stop_loss=current_price * 0.97,
                take_profit_1=current_price * 1.05,
                take_profit_2=current_price * 1.08,
                risk_reward=rr,
                reasoning="MACD bullish crossover above zero line."
            ))

        if macd < macd_signal and macd < 0:
            rr = 2.0
            patterns.append(PatternSignal(
                pattern_type="macd_bearish_cross",
                direction="bearish",
                confidence=0.6,
                entry_price=current_price,
                stop_loss=current_price * 1.03,
                take_profit_1=current_price * 0.95,
                take_profit_2=current_price * 0.92,
                risk_reward=rr,
                reasoning="MACD bearish crossover below zero line."
            ))

        return patterns

    def _analyze_multitimeframe(
        self, 
        df_1h: pd.DataFrame, 
        df_4h: pd.DataFrame, 
        df_1d: pd.DataFrame,
        current_price: float
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
            symbol="BTCUSDT",
            timeframe_1h={"trend": tf_1h},
            timeframe_4h={"trend": tf_4h},
            timeframe_1d={"trend": tf_1d},
            alignment=alignment,
            trend_confirmation=confirmation,
            confluence_score=confidence,
            trade_setup=None
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

        best_pattern = max(patterns, key=lambda p: p.confidence)
        
        signal = best_pattern.direction
        confidence = best_pattern.confidence

        if multi_tf and multi_tf.trend_confirmation:
            if multi_tf.alignment == best_pattern.direction:
                confidence = min(confidence + 0.15, 0.95)
            else:
                signal = "hold"
                confidence = 0.2

        return signal, confidence

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
            obs.append(f"Nearest support: ${nearest_support:,.0f} ({dist_pct:.1f}% below)")

        if levels.resistance:
            nearest_res = min(levels.resistance, key=lambda x: abs(x - current_price))
            dist_pct = ((nearest_res - current_price) / current_price) * 100
            obs.append(f"Nearest resistance: ${nearest_res:,.0f} ({dist_pct:.1f}% above)")

        if levels.fibonacci_retracements:
            fib_618 = levels.fibonacci_retracements.get("62%")
            if fib_618:
                obs.append(f"61.8% Fibonacci retracement: ${fib_618:,.0f}")

        if patterns:
            best = max(patterns, key=lambda p: p.confidence)
            obs.append(f"Best pattern: {best.pattern_type} ({best.confidence:.0%} confidence)")

        if multi_tf:
            obs.append(f"Multi-TF alignment: {multi_tf.alignment} (confluence: {multi_tf.confluence_score:.0%})")

        return obs

    def _empty_report(self, symbol: str) -> TechnicalAnalystReport:
        return TechnicalAnalystReport(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            current_price=0,
            price_levels=PriceLevels(
                support=[],
                resistance=[],
                pivot_points={},
                fibonacci_retracements={},
                fibonacci_extensions={}
            ),
            patterns=[],
            multi_timeframe=None,
            overall_signal="hold",
            confidence=0,
            key_observations=[]
        )


technical_analyst = TechnicalAnalyst()