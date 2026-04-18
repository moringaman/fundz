from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
import logging

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService
from app.services.llm import LLMService

logger = logging.getLogger(__name__)

# Default symbols to analyze
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]


@dataclass
class MarketOpportunity:
    """Identified trading opportunity"""
    symbol: str
    opportunity_type: str  # "oversold_bounce", "breakout", "trend_reversal", "mean_reversion"
    confidence: float  # 0.0-1.0
    recommended_action: str  # "buy", "sell", "wait"
    entry_level: Optional[float] = None
    target_level: Optional[float] = None
    stop_level: Optional[float] = None
    reasoning: str = ""


@dataclass
class MarketRegime:
    """Overall market regime assessment"""
    regime: str  # "trending_up", "trending_down", "ranging", "volatility_expansion"
    regime_confidence: float  # 0.0-1.0
    sentiment: str  # "very_bullish", "bullish", "neutral", "bearish", "very_bearish"
    correlation_status: str  # "all_up", "all_down", "diverging", "mixed"
    volatility_regime: str  # "low", "medium", "high", "extreme"
    macro_context: str  # Brief macro interpretation


@dataclass
class StrategyRecommendation:
    """Marina's regime-derived strategy proposal for the strategy review service."""
    strategy_type: str          # momentum, mean_reversion, breakout, grid, scalping
    recommended_symbols: List[str]
    timeframe: str
    rationale: str
    priority: float             # 0.0–1.0; used to rank proposals


@dataclass
class ResearchReport:
    """Analyst's complete market research report"""
    timestamp: datetime
    market_regime: MarketRegime
    opportunities: List[MarketOpportunity]
    symbols_analyzed: List[str]
    sector_leadership: Dict[str, str]  # {symbol: "leader"|"laggard"}
    top_opportunity: Optional[MarketOpportunity]
    top_risk: str  # Primary risk identified
    analyst_recommendation: str  # Overall action for portfolio
    reasoning: str
    strategy_recommendations: List['StrategyRecommendation'] = None  # regime-derived proposals

    def __post_init__(self):
        if self.strategy_recommendations is None:
            self.strategy_recommendations = []


class ResearchAnalystAgent:
    """
    Market Research Agent: Analyzes market conditions across symbols,
    identifies opportunities, and provides strategic insights to inform
    portfolio management decisions.
    """

    def __init__(self):
        self.phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet
        )
        self.indicator_service = IndicatorService()
        self.llm_service = LLMService()

    async def analyze_markets(
        self,
        symbols: List[str] = None,
        lookback_candles: int = 500  # EXPANDED from 200 for better trend confirmation
    ) -> ResearchReport:
        """
        Comprehensive multi-symbol market analysis
        Returns opportunities, regime assessment, and strategic recommendations
        """
        symbols = symbols or DEFAULT_SYMBOLS
        timestamp = datetime.utcnow()

        try:
            # Fetch market data for all symbols in parallel
            market_data = {}
            for symbol in symbols:
                try:
                    klines = await self.phemex.get_klines(symbol, "1h", lookback_candles)
                    data = klines if isinstance(klines, list) else klines.get('data', [])

                    if not data or len(data) < 200:
                        logger.warning(f"Insufficient data for {symbol} (need 200+ candles), skipping")
                        continue

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

                    df = pd.DataFrame(df_data).sort_values('time')
                    indicators = self.indicator_service.calculate_all(df)
                    current_price = df['close'].iloc[-1]

                    market_data[symbol] = {
                        'df': df,
                        'indicators': indicators,
                        'current_price': current_price,
                        'price_change_24h': (df['close'].iloc[-1] - df['close'].iloc[-24]) / df['close'].iloc[-24] * 100 if len(df) > 24 else 0,
                    }
                except Exception as e:
                    logger.error(f"Failed to fetch data for {symbol}: {e}")
                    continue

            if not market_data:
                logger.error("No market data available for analysis")
                return self._default_report(timestamp, symbols)

            # Use LLM to analyze market data and identify opportunities
            opportunities = await self._identify_opportunities(market_data)
            market_regime = await self._assess_market_regime(market_data, opportunities)
            sector_leadership = self._assess_sector_leadership(market_data)

            top_opportunity = max(opportunities, key=lambda x: x.confidence) if opportunities else None
            top_risk = self._identify_top_risk(market_regime, market_data)

            analyst_recommendation = self._generate_recommendation(
                market_regime, opportunities, sector_leadership
            )

            reasoning = self._build_reasoning(market_regime, opportunities, sector_leadership)

            strategy_recommendations = self._derive_strategy_recommendations(
                market_regime, opportunities, sector_leadership, market_data
            )

            report = ResearchReport(
                timestamp=timestamp,
                market_regime=market_regime,
                opportunities=opportunities,
                symbols_analyzed=[s for s in symbols if s in market_data],
                sector_leadership=sector_leadership,
                top_opportunity=top_opportunity,
                top_risk=top_risk,
                analyst_recommendation=analyst_recommendation,
                reasoning=reasoning,
                strategy_recommendations=strategy_recommendations,
            )

            return report

        except Exception as e:
            logger.error(f"Market analysis failed: {e}")
            return self._default_report(timestamp, symbols)

    async def _identify_opportunities(
        self,
        market_data: Dict[str, Dict]
    ) -> List[MarketOpportunity]:
        """Identify trading opportunities across symbols"""
        opportunities = []

        sr_block_pct = 0.005  # fallback: 0.5%
        try:
            from app.api.routes.settings import get_trading_gates
            sr_block_pct = float(get_trading_gates().sr_proximity_block_pct)
        except Exception:
            pass

        for symbol, data in market_data.items():
            df = data['df']
            indicators = data['indicators']
            current_price = data['current_price']

            try:
                rsi = indicators.get('rsi', 50) or 50
                sma_20 = indicators.get('sma_20')
                sma_50 = indicators.get('sma_50')
                bb_lower = indicators.get('bb_lower')
                bb_upper = indicators.get('bb_upper')
                macd = indicators.get('macd')
                macd_signal = indicators.get('macd_signal')

                sr = self.indicator_service.calculate_support_resistance(
                    df['high'],
                    df['low'],
                    df['close'],
                    lookback=min(80, len(df)),
                    proximity_pct=sr_block_pct,
                )
                nearest_support = sr.get('nearest_support')
                nearest_resistance = sr.get('nearest_resistance')
                at_support = bool(sr.get('at_support', False))
                at_resistance = bool(sr.get('at_resistance', False))

                support_dist_pct = (
                    abs(current_price - nearest_support) / max(nearest_support, 1e-10)
                    if nearest_support is not None
                    else None
                )
                resistance_dist_pct = (
                    abs(nearest_resistance - current_price) / max(nearest_resistance, 1e-10)
                    if nearest_resistance is not None
                    else None
                )

                # Detect oversold bounce
                if rsi < 30 and current_price > (bb_lower or 0) and not at_resistance:
                    opportunities.append(MarketOpportunity(
                        symbol=symbol,
                        opportunity_type="oversold_bounce",
                        confidence=min((30 - rsi) / 30 * 0.8 + 0.2, 1.0),
                        recommended_action="buy",
                        entry_level=current_price,
                        target_level=nearest_resistance or sma_20,
                        stop_level=nearest_support or bb_lower,
                        reasoning=(
                            f"RSI deeply oversold at {rsi:.1f}, price near lower Bollinger Band; "
                            f"nearest resistance {(resistance_dist_pct * 100):.2f}% away"
                            if resistance_dist_pct is not None
                            else f"RSI deeply oversold at {rsi:.1f}, price near lower Bollinger Band"
                        )
                    ))

                # Detect overbought reversal
                if rsi > 70 and current_price < (bb_upper or float('inf')) and not at_support:
                    opportunities.append(MarketOpportunity(
                        symbol=symbol,
                        opportunity_type="trend_reversal",
                        confidence=min((rsi - 70) / 30 * 0.7 + 0.2, 1.0),
                        recommended_action="sell",
                        entry_level=current_price,
                        target_level=nearest_support or sma_20,
                        stop_level=nearest_resistance or bb_upper,
                        reasoning=(
                            f"RSI overbought at {rsi:.1f}, price near upper Bollinger Band; "
                            f"nearest support {(support_dist_pct * 100):.2f}% away"
                            if support_dist_pct is not None
                            else f"RSI overbought at {rsi:.1f}, price near upper Bollinger Band"
                        )
                    ))

                # Detect bullish crossover
                if sma_20 and sma_50 and macd and macd_signal:
                    if current_price > sma_20 > sma_50 and macd > macd_signal and not at_resistance:
                        opportunities.append(MarketOpportunity(
                            symbol=symbol,
                            opportunity_type="breakout",
                            confidence=0.65,
                            recommended_action="buy",
                            entry_level=current_price,
                            target_level=nearest_resistance or (current_price * 1.05),
                            stop_level=nearest_support or sma_50,
                            reasoning=(
                                "Bullish alignment: Price > SMA20 > SMA50, MACD bullish crossover, "
                                "and not pressing resistance"
                            )
                        ))

                    # Detect bearish crossover
                    if current_price < sma_20 < sma_50 and macd < macd_signal and not at_support:
                        opportunities.append(MarketOpportunity(
                            symbol=symbol,
                            opportunity_type="breakout",
                            confidence=0.65,
                            recommended_action="sell",
                            entry_level=current_price,
                            target_level=nearest_support or (current_price * 0.95),
                            stop_level=nearest_resistance or sma_50,
                            reasoning=(
                                "Bearish alignment: Price < SMA20 < SMA50, MACD bearish crossover, "
                                "and not pressing support"
                            )
                        ))

            except Exception as e:
                logger.error(f"Error identifying opportunities for {symbol}: {e}")

        return opportunities

    async def _assess_market_regime(
        self,
        market_data: Dict[str, Dict],
        opportunities: List[MarketOpportunity]
    ) -> MarketRegime:
        """Assess overall market regime and structure"""
        try:
            # Build market context string for LLM
            context = self._build_market_context(market_data)

            # Use LLM to assess regime
            prompt = f"""Analyze the current market regime based on this data:

{context}

Identified opportunities:
{self._format_opportunities(opportunities)}

Determine:
1. Market regime (trending_up, trending_down, ranging, volatility_expansion)
2. Overall sentiment (very_bullish, bullish, neutral, bearish, very_bearish)
3. Correlation status (all_up, all_down, diverging, mixed)
4. Volatility regime (low, medium, high, extreme)
5. Brief macro context

Return JSON: {{"regime": "...", "regime_confidence": 0.0-1.0, "sentiment": "...", "correlation_status": "...", "volatility_regime": "...", "macro_context": "brief explanation"}}
"""

            response = await self.llm_service._call_llm(prompt)

            try:
                import json
                data = json.loads(response.content)
                return MarketRegime(
                    regime=data.get('regime', 'ranging'),
                    regime_confidence=float(data.get('regime_confidence', 0.5)),
                    sentiment=data.get('sentiment', 'neutral'),
                    correlation_status=data.get('correlation_status', 'mixed'),
                    volatility_regime=data.get('volatility_regime', 'medium'),
                    macro_context=data.get('macro_context', 'Analyzing market conditions')
                )
            except (json.JSONDecodeError, ValueError):
                # Fallback to default regime
                return self._default_regime()

        except Exception as e:
            logger.error(f"Market regime assessment failed: {e}")
            return self._default_regime()

    def _assess_sector_leadership(self, market_data: Dict[str, Dict]) -> Dict[str, str]:
        """Assess which symbols are leaders vs laggards"""
        leadership = {}

        try:
            # Simple approach: rank by recent performance
            symbols_performance = []
            for symbol, data in market_data.items():
                price_change = data.get('price_change_24h', 0)
                symbols_performance.append((symbol, price_change))

            symbols_performance.sort(key=lambda x: x[1], reverse=True)

            # Top half are leaders
            mid_point = len(symbols_performance) // 2
            for i, (symbol, _) in enumerate(symbols_performance):
                leadership[symbol] = "leader" if i < mid_point else "laggard"

        except Exception as e:
            logger.warning(f"Sector leadership assessment failed: {e}, defaulting to neutral")
            leadership = {symbol: "neutral" for symbol in market_data.keys()}

        return leadership

    def _identify_top_risk(self, market_regime: MarketRegime, market_data: Dict) -> str:
        """Identify primary risk for the portfolio"""
        if market_regime.volatility_regime == "extreme":
            return "Extreme volatility could trigger stop losses or unexpected reversals"
        elif market_regime.regime == "ranging" and market_regime.regime_confidence < 0.5:
            return "Unclear market direction - risk of sudden breakout in either direction"
        elif market_regime.sentiment in ["very_bearish", "bearish"]:
            return "Bearish sentiment could lead to sharp selloff; watch key support levels"
        elif market_regime.correlation_status == "diverging":
            return "Diverging correlations between symbols - portfolio hedge may be ineffective"
        else:
            return "Market evolution - continuous monitoring required"

    def _generate_recommendation(
        self,
        regime: MarketRegime,
        opportunities: List[MarketOpportunity],
        leadership: Dict[str, str]
    ) -> str:
        """Generate overall portfolio action recommendation"""
        bullish_opps = sum(1 for o in opportunities if o.recommended_action == "buy")
        bearish_opps = sum(1 for o in opportunities if o.recommended_action == "sell")

        if regime.sentiment in ["very_bullish", "bullish"] and bullish_opps > bearish_opps:
            return "increase_allocation"
        elif regime.sentiment in ["very_bearish", "bearish"] and bearish_opps > bullish_opps:
            return "reduce_risk"
        elif regime.regime == "ranging":
            return "maintain_current"
        else:
            return "watch_and_wait"

    def _build_reasoning(
        self,
        regime: MarketRegime,
        opportunities: List[MarketOpportunity],
        leadership: Dict[str, str]
    ) -> str:
        """Build detailed reasoning for analysis"""
        lines = [
            f"Market Regime: {regime.regime} (confidence: {(regime.regime_confidence or 0):.1%})",
            f"Sentiment: {regime.sentiment}",
            f"Volatility: {regime.volatility_regime}",
            f"Correlations: {regime.correlation_status}",
            f"Identified {len(opportunities)} opportunities:",
        ]

        for opp in opportunities[:5]:  # Top 5
            lines.append(f"  - {opp.symbol}: {opp.opportunity_type} ({opp.confidence:.1%} confidence)")

        leaders = [s for s, l in leadership.items() if l == "leader"]
        if leaders:
            lines.append(f"Sector Leaders: {', '.join(leaders)}")

        lines.append(f"Top Risk: {regime.macro_context}")

        return "\n".join(lines)

    def _build_market_context(self, market_data: Dict[str, Dict]) -> str:
        """Build text context of market data for LLM analysis"""
        context_lines = []

        sr_block_pct = 0.005  # fallback: 0.5%
        try:
            from app.api.routes.settings import get_trading_gates
            sr_block_pct = float(get_trading_gates().sr_proximity_block_pct)
        except Exception:
            pass

        for symbol, data in market_data.items():
            indicators = data['indicators']
            price_change = data.get('price_change_24h', 0)
            df = data.get('df')

            nearest_support = None
            nearest_resistance = None
            if isinstance(df, pd.DataFrame) and not df.empty:
                sr = self.indicator_service.calculate_support_resistance(
                    df['high'],
                    df['low'],
                    df['close'],
                    lookback=min(80, len(df)),
                    proximity_pct=sr_block_pct,
                )
                nearest_support = sr.get('nearest_support')
                nearest_resistance = sr.get('nearest_resistance')

            context_lines.append(f"\n{symbol}:")
            context_lines.append(f"  Price: ${(data.get('current_price', 0) or 0):.2f}")
            context_lines.append(f"  24h Change: {(price_change or 0):.2f}%")
            context_lines.append(f"  RSI(14): {(indicators.get('rsi', 50) or 50):.1f}")
            context_lines.append(f"  SMA20: ${(indicators.get('sma_20', 0) or 0):.2f}")
            context_lines.append(f"  SMA50: ${(indicators.get('sma_50', 0) or 0):.2f}")
            context_lines.append(
                f"  Nearest Support: ${(nearest_support or 0):.2f}" if nearest_support is not None else "  Nearest Support: N/A"
            )
            context_lines.append(
                f"  Nearest Resistance: ${(nearest_resistance or 0):.2f}" if nearest_resistance is not None else "  Nearest Resistance: N/A"
            )

        return "\n".join(context_lines)

    def _format_opportunities(self, opportunities: List[MarketOpportunity]) -> str:
        """Format opportunities for LLM"""
        if not opportunities:
            return "No strong opportunities identified yet"

        lines = []
        for opp in opportunities:
            lines.append(f"- {opp.symbol}: {opp.opportunity_type.replace('_', ' ')} "
                        f"({opp.confidence:.1%} confidence) → {opp.recommended_action.upper()}")

        return "\n".join(lines)

    def _derive_strategy_recommendations(
        self,
        regime: MarketRegime,
        opportunities: List[MarketOpportunity],
        leadership: Dict[str, str],
        market_data: Dict[str, Dict],
    ) -> List[StrategyRecommendation]:
        """Derive strategy type recommendations directly from the market regime.

        This converts Marina's regime assessment into concrete strategy proposals
        that the strategy review service and traders can act on — closing the loop
        between research and execution.
        """
        recs: List[StrategyRecommendation] = []
        r = regime.regime
        vol = regime.volatility_regime
        sentiment = regime.sentiment

        # Leader symbols are preferred for directional strategies
        leaders = [s for s, lbl in leadership.items() if lbl == "leader"]
        all_syms = list(market_data.keys())
        best_syms = leaders[:3] if leaders else all_syms[:3]

        # 1. Trending market → momentum and breakout strategies shine
        if r in ("trending_up", "trending_down"):
            direction = "bullish" if r == "trending_up" else "bearish"
            recs.append(StrategyRecommendation(
                strategy_type="momentum",
                recommended_symbols=best_syms,
                timeframe="15m" if vol in ("high", "extreme") else "1h",
                rationale=f"Market is {r} ({regime.regime_confidence:.0%} confidence, {direction} bias). "
                          f"Momentum strategies perform best in clear trends. "
                          f"Leaders: {', '.join(leaders[:2]) if leaders else 'BTC/ETH'}.",
                priority=min(0.9, regime.regime_confidence + 0.1),
            ))
            recs.append(StrategyRecommendation(
                strategy_type="breakout",
                recommended_symbols=best_syms,
                timeframe="1h",
                rationale=f"{r.replace('_', ' ').title()} regime creates breakout opportunities "
                          f"at key resistance/support levels.",
                priority=min(0.8, regime.regime_confidence),
            ))

        # 2. Ranging market → mean reversion and grid strategies
        if r in ("ranging",):
            recs.append(StrategyRecommendation(
                strategy_type="mean_reversion",
                recommended_symbols=best_syms,
                timeframe="1h",
                rationale=f"Ranging market ({regime.regime_confidence:.0%} confidence) is ideal for "
                          f"mean reversion — price oscillates around equilibrium. "
                          f"Avoid momentum strategies until a breakout confirms.",
                priority=min(0.9, regime.regime_confidence + 0.1),
            ))
            if vol in ("low", "medium"):
                recs.append(StrategyRecommendation(
                    strategy_type="grid",
                    recommended_symbols=best_syms[:2],
                    timeframe="15m",
                    rationale=f"Low-medium volatility ranging market suits grid trading — "
                              f"systematic buy/sell at fixed intervals within the BB range.",
                    priority=min(0.75, regime.regime_confidence),
                ))

        # 3. Volatility expansion → scalping and tight mean reversion
        if r == "volatility_expansion" or vol in ("high", "extreme"):
            recs.append(StrategyRecommendation(
                strategy_type="scalping",
                recommended_symbols=best_syms[:2],
                timeframe="5m",
                rationale=f"Volatility expansion ({vol}) creates frequent short-term swings. "
                          f"Scalping with tight stops captures these without overnight risk.",
                priority=0.65 if vol == "high" else 0.55,
            ))

        # 4. Trending up with bullish sentiment → trend following for larger moves
        if r == "trending_up" and sentiment in ("bullish", "very_bullish"):
            recs.append(StrategyRecommendation(
                strategy_type="trend_following",
                recommended_symbols=best_syms,
                timeframe="4h",
                rationale=f"Strong {sentiment} sentiment in uptrend. "
                          f"Trend following on 4h captures the macro move beyond intraday noise.",
                priority=0.70,
            ))

        # Sort by priority descending
        recs.sort(key=lambda x: x.priority, reverse=True)
        return recs[:4]  # cap at 4 proposals per cycle

    def _default_regime(self) -> MarketRegime:
        """Return safe default regime assessment"""
        return MarketRegime(
            regime="ranging",
            regime_confidence=0.5,
            sentiment="neutral",
            correlation_status="mixed",
            volatility_regime="medium",
            macro_context="Insufficient data for detailed regime assessment"
        )

    def _default_report(self, timestamp: datetime, symbols: List[str]) -> ResearchReport:
        """Return safe default report when analysis fails"""
        return ResearchReport(
            timestamp=timestamp,
            market_regime=self._default_regime(),
            opportunities=[],
            symbols_analyzed=symbols,
            sector_leadership={s: "neutral" for s in symbols},
            top_opportunity=None,
            top_risk="Data availability issue - cannot assess risk",
            analyst_recommendation="watch_and_wait",
            reasoning="Insufficient market data for comprehensive analysis. Recommend checking data source."
        )


# Global singleton
research_analyst = ResearchAnalystAgent()
