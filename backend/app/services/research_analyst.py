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
        lookback_candles: int = 200
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

                    if not data or len(data) < 50:
                        logger.warning(f"Insufficient data for {symbol}, skipping")
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

            report = ResearchReport(
                timestamp=timestamp,
                market_regime=market_regime,
                opportunities=opportunities,
                symbols_analyzed=[s for s in symbols if s in market_data],
                sector_leadership=sector_leadership,
                top_opportunity=top_opportunity,
                top_risk=top_risk,
                analyst_recommendation=analyst_recommendation,
                reasoning=reasoning
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

                # Detect oversold bounce
                if rsi < 30 and current_price > (bb_lower or 0):
                    opportunities.append(MarketOpportunity(
                        symbol=symbol,
                        opportunity_type="oversold_bounce",
                        confidence=min((30 - rsi) / 30 * 0.8 + 0.2, 1.0),
                        recommended_action="buy",
                        entry_level=current_price,
                        target_level=sma_20,
                        stop_level=bb_lower,
                        reasoning=f"RSI deeply oversold at {rsi:.1f}, price near lower Bollinger Band"
                    ))

                # Detect overbought reversal
                if rsi > 70 and current_price < (bb_upper or float('inf')):
                    opportunities.append(MarketOpportunity(
                        symbol=symbol,
                        opportunity_type="trend_reversal",
                        confidence=min((rsi - 70) / 30 * 0.7 + 0.2, 1.0),
                        recommended_action="sell",
                        entry_level=current_price,
                        target_level=sma_20,
                        stop_level=bb_upper,
                        reasoning=f"RSI overbought at {rsi:.1f}, price near upper Bollinger Band"
                    ))

                # Detect bullish crossover
                if sma_20 and sma_50 and macd and macd_signal:
                    if current_price > sma_20 > sma_50 and macd > macd_signal:
                        opportunities.append(MarketOpportunity(
                            symbol=symbol,
                            opportunity_type="breakout",
                            confidence=0.65,
                            recommended_action="buy",
                            entry_level=current_price,
                            target_level=current_price * 1.05,
                            stop_level=sma_50,
                            reasoning=f"Bullish alignment: Price > SMA20 > SMA50, MACD bullish crossover"
                        ))

                    # Detect bearish crossover
                    if current_price < sma_20 < sma_50 and macd < macd_signal:
                        opportunities.append(MarketOpportunity(
                            symbol=symbol,
                            opportunity_type="breakout",
                            confidence=0.65,
                            recommended_action="sell",
                            entry_level=current_price,
                            target_level=current_price * 0.95,
                            stop_level=sma_50,
                            reasoning=f"Bearish alignment: Price < SMA20 < SMA50, MACD bearish crossover"
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

        for symbol, data in market_data.items():
            indicators = data['indicators']
            price_change = data.get('price_change_24h', 0)

            context_lines.append(f"\n{symbol}:")
            context_lines.append(f"  Price: ${(data.get('current_price', 0) or 0):.2f}")
            context_lines.append(f"  24h Change: {(price_change or 0):.2f}%")
            context_lines.append(f"  RSI(14): {(indicators.get('rsi', 50) or 50):.1f}")
            context_lines.append(f"  SMA20: ${(indicators.get('sma_20', 0) or 0):.2f}")
            context_lines.append(f"  SMA50: ${(indicators.get('sma_50', 0) or 0):.2f}")

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
