from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
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
            # Use enough candles for reliable indicators; higher frames need fewer
            bars_primary = 200
            bars_mid = 200
            bars_high = 100

            klines_primary = await self.phemex.get_klines(symbol, tf_primary, bars_primary)
            klines_mid     = await self.phemex.get_klines(symbol, tf_mid,     bars_mid)
            klines_high    = await self.phemex.get_klines(symbol, tf_high,    bars_high)

            data_primary = self._parse_klines(klines_primary)
            data_mid     = self._parse_klines(klines_mid)
            data_high    = self._parse_klines(klines_high)

            if data_primary is None or (isinstance(data_primary, pd.DataFrame) and data_primary.empty) or (not isinstance(data_primary, pd.DataFrame) and not data_primary):
                return self._empty_report(symbol)
            if len(data_primary) < 50:
                return self._empty_report(symbol)

            current_price = data_primary['close'].iloc[-1]

            price_levels = self._calculate_price_levels(data_primary)
            patterns = self._identify_patterns(data_primary, current_price, price_levels)
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

    def _identify_patterns(self, df: pd.DataFrame, current_price: float, price_levels: Optional[PriceLevels] = None) -> List[PatternSignal]:
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

        # Pre-compute nearest structural targets for MACD patterns
        # so TP1/TP2 sit at real chart levels, not arbitrary % offsets.
        _levels_above: List[float] = price_levels.all_levels_above(current_price) if price_levels else []
        _levels_below: List[float] = price_levels.all_levels_below(current_price) if price_levels else []

        if rsi < 35 and current_price <= bb_lower:
            # Oversold bounce — bullish
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