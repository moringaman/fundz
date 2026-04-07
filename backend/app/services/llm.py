from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import json
import logging
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    reasoning: str
    confidence: float
    action: str


class LLMRegistry:
    """
    Role-based LLM model assignment for cost optimization.
    Different agent roles use different models based on complexity and frequency.
    Each agent has a name and avatar for frontend display.

    Philosophy: Like a company paying different salaries by role:
    - Research Analyst (deep reasoning, infrequent) → Opus (senior analyst)
    - Portfolio Manager, Risk Manager, CIO (moderate reasoning, regular) → Sonnet (mid-level)
    - Execution Coordinator, Trading Agents (low complexity, frequent) → Haiku (junior/operators)
    """

    AGENTS = {
        'research_analyst': {
            'name': 'Dr. Marina Chen',
            'title': 'Chief Research Analyst',
            'model': 'meta-llama/llama-3.1-8b-instruct',
            'temperature': 0.7,
            'max_tokens': 2000,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Dr.%20Marina%20Chen&gender=female',
            'bio': 'Senior analyst with PhD in quantitative finance. Specializes in macro market analysis and opportunity identification.'
        },
        'technical_analyst': {
            'name': 'Marcus Webb',
            'title': 'Technical Analyst',
            'model': 'google/gemma-2-9b-it',
            'temperature': 0.4,
            'max_tokens': 1500,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Marcus%20Webb&gender=male',
            'bio': 'Chart specialist with 15 years experience. Expert in price patterns, support/resistance, and multi-timeframe analysis.'
        },
        'portfolio_manager': {
            'name': 'James Sterling',
            'title': 'Portfolio Manager',
            'model': 'meta-llama/llama-3.1-8b-instruct',
            'temperature': 0.5,
            'max_tokens': 1200,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=James%20Sterling&gender=male',
            'bio': 'Experienced manager responsible for capital allocation and rebalancing across all agents.'
        },
        'risk_manager': {
            'name': 'Elena Vasquez',
            'title': 'Chief Risk Officer',
            'model': 'google/gemma-2-9b-it',
            'temperature': 0.3,
            'max_tokens': 800,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Elena%20Vasquez&gender=female',
            'bio': 'Risk management expert ensuring portfolio stays within risk parameters and preventing drawdown.'
        },
        'execution_coordinator': {
            'name': 'Alex Liu',
            'title': 'Execution Specialist',
            'model': 'mistralai/mixtral-8x7b-instruct',
            'temperature': 0.2,
            'max_tokens': 400,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Alex%20Liu&gender=male',
            'bio': 'High-speed execution expert optimizing order timing and slippage management.'
        },
        'cio_agent': {
            'name': 'Victoria Montgomery',
            'title': 'Chief Investment Officer',
            'model': 'meta-llama/llama-3.1-8b-instruct',
            'temperature': 0.6,
            'max_tokens': 3000,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Victoria%20Montgomery&gender=female',
            'bio': 'Fund head overseeing all operations, generating reports, and making strategic decisions.'
        },
        'trading_agent': {
            'name': 'Automated Trader',
            'title': 'Trading Agent',
            'model': 'mistralai/mixtral-8x7b-instruct',
            'temperature': 0.5,
            'max_tokens': 800,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Trader&gender=male',
            'bio': 'Individual trading agent executing strategies and generating signals.'
        }
    }

    @staticmethod
    def get_agent_info(role: str) -> dict:
        """Get full agent info (name, title, avatar, bio, model settings)"""
        return LLMRegistry.AGENTS.get(role, LLMRegistry.AGENTS['trading_agent'])

    @staticmethod
    def get_model(role: str) -> str:
        """Get model for role, with fallback to Sonnet"""
        return LLMRegistry.AGENTS.get(role, {}).get('model', 'claude-sonnet-4-6')

    @staticmethod
    def get_config(role: str) -> dict:
        """Get full config (model, temp, max_tokens) for role"""
        agent = LLMRegistry.AGENTS.get(role, {})
        return {
            'model': agent.get('model', 'claude-sonnet-4-6'),
            'temperature': agent.get('temperature', 0.5),
            'max_tokens': agent.get('max_tokens', 1200)
        }

    @staticmethod
    def get_model_and_settings(role: str) -> tuple:
        """Get (model, temperature, max_tokens) for role"""
        config = LLMRegistry.get_config(role)
        return (
            config.get('model', 'claude-sonnet-4-6'),
            config.get('temperature', 0.5),
            config.get('max_tokens', 1200)
        )

    @staticmethod
    def get_name(role: str) -> str:
        """Get agent name"""
        return LLMRegistry.AGENTS.get(role, {}).get('name', 'Unnamed Agent')

    @staticmethod
    def get_avatar(role: str) -> str:
        """Get agent avatar (emoji)"""
        return LLMRegistry.AGENTS.get(role, {}).get('avatar', '🤖')


class LLMService:
    def __init__(self):
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self._client = None
    
    async def initialize(self):
        if self.provider == "openrouter":
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=settings.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        elif self.provider == "openai":
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        elif self.provider == "anthropic":
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        elif self.provider == "azure":
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=settings.azure_openai_key,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version="2024-02-01",
            )
            self.model = settings.azure_openai_deployment or self.model
        logger.info(f"LLM Service initialized with provider: {self.provider}, model: {self.model}")

    async def analyze_market(self, market_data: Dict[str, Any]) -> LLMResponse:
        prompt = self._build_market_analysis_prompt(market_data)
        return await self._call_llm(prompt)

    async def generate_signal(self, indicators: Dict[str, Any], price_data: Dict[str, Any], team_context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        prompt = self._build_signal_prompt(indicators, price_data, team_context)
        return await self._call_llm(prompt)

    async def evaluate_strategy(self, strategy_config: Dict[str, Any], performance: Dict[str, Any]) -> LLMResponse:
        prompt = self._build_strategy_evaluation_prompt(strategy_config, performance)
        return await self._call_llm(prompt)

    def _build_market_prompt(self, market_data: Dict[str, Any]) -> str:
        return f"""You are an expert cryptocurrency trading analyst. Analyze the current market conditions and provide insights.

Current Market Data:
- Symbol: {market_data.get('symbol', 'N/A')}
- Current Price: ${market_data.get('price', 0):,.2f}
- 24h Change: {market_data.get('price_change_percent', 0):.2f}%
- 24h High: ${market_data.get('high', 0):,.2f}
- 24h Low: ${market_data.get('low', 0):,.2f}
- 24h Volume: {market_data.get('volume', 0):,.0f}

Technical Indicators:
- RSI (14): {market_data.get('rsi', 'N/A')}
- MACD: {market_data.get('macd', 'N/A')}
- MACD Signal: {market_data.get('macd_signal', 'N/A')}
- Bollinger Bands: Upper ${market_data.get('bb_upper', 0):,.2f}, Middle ${market_data.get('bb_middle', 0):,.2f}, Lower ${market_data.get('bb_lower', 0):,.2f}
- SMA 20: ${market_data.get('sma_20', 0):,.2f}
- SMA 50: ${market_data.get('sma_50', 0):,.2f}

Provide your analysis in JSON format:
{{"trend": "bullish/bearish/neutral", "volatility": "high/medium/low", "momentum": "strong/moderate/weak", "recommendation": "buy/sell/hold", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}
"""

    async def _call_llm(self, prompt: str) -> LLMResponse:
        if self._client is None:
            await self.initialize()
        
        try:
            if self.provider in ("openai", "openrouter", "azure"):
                return await self._call_openai(prompt)
            elif self.provider == "anthropic":
                return await self._call_anthropic(prompt)
            else:
                raise ValueError(f"Unknown LLM provider: {self.provider}")
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return LLMResponse(
                content="Error generating response",
                reasoning=str(e),
                confidence=0.0,
                action="hold"
            )

    async def _call_llm_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
        """Call LLM with system+user prompts, returning free-text (no JSON constraint)."""
        if self._client is None:
            await self.initialize()

        try:
            if self.provider in ("openai", "openrouter", "azure"):
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            elif self.provider == "anthropic":
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text
            else:
                raise ValueError(f"Unknown provider: {self.provider}")
        except Exception as e:
            logger.error(f"LLM text call failed: {e}")
            return f"I'm sorry, I couldn't generate a response at this time. Error: {e}"

    async def _call_openai(self, prompt: str) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        return LLMResponse(
            content=content,
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.5)),
            action=data.get("recommendation", data.get("action", "hold"))
        )

    async def _call_anthropic(self, prompt: str) -> LLMResponse:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system="You are an expert cryptocurrency trading analyst. Always respond in JSON format.",
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = response.content[0].text
        data = json.loads(content)
        
        return LLMResponse(
            content=content,
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.5)),
            action=data.get("recommendation", data.get("action", "hold"))
        )

    async def _call_azure(self, prompt: str) -> LLMResponse:
        return await self._call_openai(prompt)

    def _build_market_analysis_prompt(self, market_data: Dict[str, Any]) -> str:
        symbol = market_data.get('symbol', 'N/A')
        price = market_data.get('price', 0) or 0
        change = market_data.get('price_change_percent', 0) or 0
        volume = market_data.get('volume', 0) or 0
        rsi = market_data.get('rsi', 50) or 50
        macd = market_data.get('macd', 0) or 0
        macd_signal = market_data.get('macd_signal', 0) or 0
        sma20 = market_data.get('sma_20', 0) or 0
        sma50 = market_data.get('sma_50', 0) or 0

        return f"""Professional market structure analysis for {symbol}.

PRICE & VOLUME CONTEXT:
- Current Price: ${price:,.2f}
- 24h Change: {change:.2f}% ({"Bullish" if change > 0 else "Bearish"})
- 24h Volume: {volume:,.0f} USDT
- 24h High: ${market_data.get('high', 0):,.2f}
- 24h Low: ${market_data.get('low', 0):,.2f}
- Range: ${market_data.get('high', 0) - market_data.get('low', 0):,.2f} ({(((market_data.get('high', 0) - market_data.get('low', 0)) / price) * 100) if price else 0:.1f}% daily range)

TREND ANALYSIS (Moving Averages):
- SMA 20: ${sma20:,.2f} {"(price above = uptrend signal)" if price and sma20 and price > sma20 else "(price below = downtrend signal)"}
- SMA 50: ${sma50:,.2f} {"(20 > 50 = bullish alignment)" if sma20 and sma50 and sma20 > sma50 else "(20 < 50 = bearish alignment)"}
- Trend Structure: {"Higher Highs/Lows Forming" if price > market_data.get('high', 0) * 0.99 else "Lower Highs/Lows Forming" if price < market_data.get('low', 0) * 1.01 else "Range-bound"}

MOMENTUM INDICATORS:
- RSI (14): {rsi:.1f} (Interpretation: {"Severely oversold, potential reversal" if rsi < 30 else "Oversold, watch for bounce" if rsi < 40 else "Neutral zone" if 40 <= rsi <= 60 else "Overbought, watch for pullback" if rsi <= 70 else "Severely overbought, correction likely"})
- MACD Line: {macd:.4f}
- Signal Line: {macd_signal:.4f}
- Histogram: {(macd - macd_signal) if macd and macd_signal else 0:.4f} ({"Positive = Bullish" if macd and macd_signal and (macd - macd_signal) > 0 else "Negative = Bearish"})

CONFLUENCE ANALYSIS:
Evaluate how many signals align:
- Trend alignment (price > SMA20 > SMA50 for buy, or opposite for sell)
- MACD alignment (bullish/bearish crossover)
- RSI extremes (oversold for buy opportunity, overbought for sell)
- Volume confirmation (volume supporting price direction)

DECISION FRAMEWORK:
- STRONG BUY: Price above SMA20 + MACD bullish + RSI <40 + rising volume
- BUY: 2+ bullish signals aligned
- HOLD: Mixed signals, consolidation, or insufficient confluence
- SELL: 2+ bearish signals aligned
- STRONG SELL: Price below SMA20 + MACD bearish + RSI >60 + rising volume

Return JSON: {{"trend": "strong_bullish|bullish|neutral|bearish|strong_bearish", "volatility": "high|medium|low", "momentum": "strong_bullish|bullish|neutral|bearish|strong_bearish", "recommendation": "buy|sell|hold", "confidence": 0.0-1.0, "reasoning": "structured analysis explaining signal confluence and conviction level"}}
"""

    def _build_signal_prompt(self, indicators: Dict[str, Any], price_data: Dict[str, Any], team_context: Optional[Dict[str, Any]] = None) -> str:
        rsi = indicators.get('rsi', 50) or 50
        macd = indicators.get('macd', 0) or 0
        macd_signal = indicators.get('macd_signal', 0) or 0
        bb_upper = indicators.get('bb_upper', 0) or 0
        bb_middle = indicators.get('bb_middle', 0) or 0
        bb_lower = indicators.get('bb_lower', 0) or 0
        atr = indicators.get('atr', 0) or 0

        # Build team intelligence section
        team_section = ""
        if team_context:
            parts = []

            # Technical Analyst (Marcus) — patterns, S/R, multi-timeframe
            ta = team_context.get("ta")
            if ta:
                parts.append(f"""TECHNICAL ANALYST (Marcus Webb) REPORT:
- Overall Signal: {ta.get('signal', 'N/A')} (confidence: {ta.get('confidence', 0):.0%})
- Multi-Timeframe Alignment: {ta.get('alignment', 'N/A')} (confluence: {ta.get('confluence_score', 0):.0%})
- Patterns Detected: {ta.get('patterns_count', 0)} — {ta.get('patterns_summary', 'none')}
- Key Support: ${ta.get('support', 0):,.2f} | Resistance: ${ta.get('resistance', 0):,.2f}
- Key Observations: {ta.get('observations', 'N/A')}""")

            # Research Analyst (Marina) — regime, sentiment
            research = team_context.get("research")
            if research:
                parts.append(f"""RESEARCH ANALYST (Dr. Marina Chen) REPORT:
- Market Regime: {research.get('regime', 'N/A')}
- Sentiment: {research.get('sentiment', 'N/A')}
- Volatility Regime: {research.get('volatility', 'N/A')}
- Correlation: {research.get('correlation', 'N/A')}
- Top Opportunity: {research.get('top_opportunity', 'N/A')}""")

            # Risk Manager (Elena) — risk level, exposure
            risk = team_context.get("risk")
            if risk:
                parts.append(f"""RISK MANAGER (Elena Vasquez) ASSESSMENT:
- Risk Level: {risk.get('risk_level', 'N/A')}
- Portfolio Exposure: {risk.get('exposure_pct', 0):.1f}% of capital
- Daily P&L: ${risk.get('daily_pnl', 0):+,.2f}
- Concentration Risk: {risk.get('concentration', 'N/A')}
- Recommendations: {risk.get('recommendations', 'None')}""")

            # Agent's own performance
            perf = team_context.get("agent_performance")
            if perf:
                parts.append(f"""YOUR TRACK RECORD:
- Win Rate: {perf.get('win_rate', 0):.0%} ({perf.get('total_runs', 0)} trades)
- Total P&L: ${perf.get('total_pnl', 0):+,.2f}
- Recent Streak: {perf.get('streak', 'N/A')}""")

            if parts:
                team_section = "\n\nTEAM INTELLIGENCE (factor these into your decision):\n" + "\n\n".join(parts) + """

IMPORTANT: Weight your decision using team intelligence:
- If Technical Analyst's signal OPPOSES yours with high confidence, reduce your confidence or flip
- If market regime is trending, favour momentum/breakout; if ranging, favour mean reversion
- If Risk Manager says "danger" or "caution", prefer HOLD or reduce confidence
- If your recent win rate is below 40%, reduce confidence by 20%
- Look for CONFLUENCE across team signals — 3+ team members agreeing = high conviction
"""

        return f"""You are a professional cryptocurrency trading analyst. Generate a precise trading signal using technical analysis and team intelligence.

PRICE ACTION:
- Current Price: ${price_data.get('current', 0)}
- Price Change (24h): {price_data.get('change_pct', 0):.2f}%

MOMENTUM & TREND INDICATORS:
- RSI (14): {rsi:.1f} (oversold <30, neutral 40-60, overbought >70)
- MACD: {macd:.4f} (Signal: {macd_signal:.4f}, Histogram: {(macd - macd_signal) if macd and macd_signal else 0:.4f})
- MACD Status: {"Bullish crossover" if macd and macd_signal and macd > macd_signal else "Bearish crossover" if macd and macd_signal else "Unknown"}

VOLATILITY & SUPPORT/RESISTANCE:
- Bollinger Bands Upper: ${bb_upper:,.2f}
- Bollinger Bands Middle (SMA 20): ${bb_middle:,.2f}
- Bollinger Bands Lower: ${bb_lower:,.2f}
- ATR (Volatility): {atr:.2f}
- Current position vs BB: {"Near resistance (upper band)" if price_data.get('current', 0) > bb_middle else "Near support (lower band)" if price_data.get('current', 0) < bb_middle else "Mid-range"}
{team_section}
ANALYSIS FRAMEWORK:
1. TREND: Identify the dominant trend (uptrend = higher highs/lows, downtrend = lower highs/lows, range = oscillating)
2. MOMENTUM: Assess RSI and MACD for momentum strength (converging/diverging signals)
3. CONFLUENCE: Look for overlapping signals across indicators AND team intelligence
4. REVERSAL RISK: Consider divergences (price making new highs but momentum weakening = potential reversal)
5. VOLATILITY: Factor in ATR (high volatility = wider stops, low volatility = tighter risk)
6. TEAM ALIGNMENT: Weight team signals — if TA, Research, and Risk agree, increase conviction

CONFIDENCE CALIBRATION:
- High (0.7-1.0): 3+ confluent signals, clear trend, MACD + RSI aligned, team agrees
- Medium (0.4-0.7): 2 aligned signals, mixed momentum, some team disagreement
- Low (0.0-0.4): Conflicting signals, choppy action, team signals diverge

Return JSON: {{"action": "buy|sell|hold", "confidence": 0.0-1.0, "reasoning": "brief but specific technical analysis reasoning incorporating team intelligence", "key_levels": {{"resistance": 0.0, "support": 0.0}}, "risk_level": "low|medium|high"}}

IMPORTANT: "sell" means SHORT if no long position exists. You can profit from downtrends by shorting. Use sell signals when bearish indicators are strong.
"""

    def _build_strategy_evaluation_prompt(self, strategy_config: Dict[str, Any], performance: Dict[str, Any]) -> str:
        total_trades = performance.get('total_trades', 0) or 0
        win_rate = performance.get('win_rate', 0) or 0
        total_pnl = performance.get('total_pnl', 0) or 0
        avg_win = performance.get('avg_win', 0) or 0
        avg_loss = performance.get('avg_loss', 0) or 0
        profit_factor = performance.get('profit_factor', 0) or 0

        return f"""Evaluate this trading strategy's performance using professional metrics.

STRATEGY CONFIGURATION:
- Type: {strategy_config.get('strategy_type', 'N/A')}
- Trading Pairs: {', '.join(strategy_config.get('trading_pairs', []))}
- Max Position Size: {strategy_config.get('max_position_size', 0)}%
- Stop Loss: {strategy_config.get('stop_loss_pct', 0)}%
- Take Profit: {strategy_config.get('take_profit_pct', 0)}%

PERFORMANCE METRICS:
- Total Trades: {total_trades}
- Win Rate: {win_rate * 100:.1f}%
- Total P&L: ${total_pnl:,.2f}
- Average Win: ${avg_win:,.2f}
- Average Loss: ${avg_loss:,.2f}
- Profit Factor: {profit_factor:.2f} (Gross Profit / Gross Loss; >1.5 is healthy)
- Expectancy: ${(avg_win * win_rate) - (abs(avg_loss) * (1 - win_rate)):,.2f} per trade (should be positive)

STRATEGY HEALTH ASSESSMENT:
1. SAMPLE SIZE: {"Insufficient data (<10 trades)" if total_trades < 10 else "Small sample (10-30)" if total_trades < 30 else "Moderate sample (30-100)" if total_trades < 100 else "Large sample (100+)"}
2. WIN RATE QUALITY: {"Excellent (>65%)" if win_rate > 0.65 else "Good (55-65%)" if win_rate > 0.55 else "Acceptable (45-55%)" if win_rate > 0.45 else "Below target (<45%)"}
3. PROFIT FACTOR: {"Strong (>2.0)" if profit_factor > 2.0 else "Healthy (>1.5)" if profit_factor > 1.5 else "Acceptable (>1.0)" if profit_factor > 1.0 else "Concerning (<1.0)"}
4. RISK/REWARD: {"Well-balanced" if avg_loss and abs(avg_win) / abs(avg_loss) > 1.5 else "Needs adjustment"}

OPTIMIZATION OPPORTUNITIES:
- If win_rate <50%: Consider tighter entry rules or different timeframe
- If profit_factor <1.5: Increase winners or reduce losers (tighter stops?)
- If win_rate >60% but low PnL: Increase position size or extend profit targets
- If losses exceed wins: Review signal confluence, add filters

CONFIDENCE FACTORS:
- Adequate sample size: {total_trades >= 20}
- Positive expectancy: {total_pnl > 0}
- Win rate sustainable: {win_rate > 0.45 and profit_factor > 1.0}

Return JSON: {{"action": "continue|optimize|pause|stop", "confidence": 0.0-1.0, "reasoning": "evaluation based on profit factor, win rate, and sample size", "recommended_adjustments": ["list of specific optimization suggestions if needed"], "next_focus": "primary metric to improve (win_rate|position_size|entry_filters|stop_losses)"}}
"""


llm_service = LLMService()
