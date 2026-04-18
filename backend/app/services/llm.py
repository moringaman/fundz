from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import json
import logging
from app.config import settings
from app.utils import fmt_price

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
            'temperature': 0.2,
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
            'name': 'Jordan Blake',
            'title': 'Quantitative Trader',
            'model': 'mistralai/mixtral-8x7b-instruct',
            'temperature': 0.15,
            'max_tokens': 800,
            'avatar': 'https://api.dicebear.com/7.x/avataaars/svg?seed=Jordan%20Blake&gender=male',
            'bio': 'Quantitative trader executing algorithmic strategies and generating entry/exit signals across all pairs.'
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

    async def generate_signal(
        self,
        indicators: Dict[str, Any],
        price_data: Dict[str, Any],
        team_context: Optional[Dict[str, Any]] = None,
        agent_context: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        prompt = self._build_signal_prompt(indicators, price_data, team_context)
        system_prompt = (
            self._build_trader_system_prompt(agent_context)
            if agent_context
            else self._build_generic_system_prompt()
        )
        return await self._call_llm(prompt, system_prompt=system_prompt)

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

    async def _call_llm(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        if self._client is None:
            await self.initialize()
        
        try:
            if self.provider in ("openai", "openrouter", "azure"):
                return await self._call_openai(prompt, system_prompt=system_prompt)
            elif self.provider == "anthropic":
                return await self._call_anthropic(prompt, system_prompt=system_prompt)
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

    async def _call_openai(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
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

    async def _call_anthropic(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt or "You are an expert cryptocurrency trading analyst. Always respond in JSON format.",
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

    async def _call_azure(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        return await self._call_openai(prompt, system_prompt=system_prompt)

    def _build_generic_system_prompt(self) -> str:
        return (
            "You are a highly selective professional cryptocurrency trading analyst. "
            "Your PRIMARY objective is profitable trades — measured on P&L, not trade count. "
            "A missed trade costs nothing; a losing trade costs real money. "
            "Always respond with valid JSON only."
        )

    def _build_trader_system_prompt(self, agent_context: Dict[str, Any]) -> str:
        trader_name = agent_context.get("trader_name", "Anonymous Trader")
        trader_style = agent_context.get("trader_style", "Balanced and disciplined.")
        trader_bio = agent_context.get("trader_bio", "")
        risk_tolerance = agent_context.get("risk_tolerance", "moderate")
        preferred_strategies = agent_context.get("preferred_strategies", [])
        agent_name = agent_context.get("agent_name", "Unnamed Strategy")
        strategy_type = agent_context.get("strategy_type", "momentum")

        strat_str = ", ".join(preferred_strategies) if preferred_strategies else strategy_type
        risk_desc = {
            "high":   "Willing to take larger positions on high-conviction setups and hold through volatility.",
            "low":    "Capital preservation above all. Small positions, tight stops, consistent compounding over big swings.",
            "moderate": "Balanced sizing relative to conviction. Respect stops, let winners run within reason.",
        }.get(risk_tolerance, "Moderate risk approach.")

        return f"""You are {trader_name}, a competing portfolio trader at a professional crypto hedge fund.

IDENTITY: {trader_bio}

TRADING PHILOSOPHY: {trader_style}

RISK TOLERANCE — {risk_tolerance.upper()}: {risk_desc}

CURRENT MANDATE: You are running "{agent_name}", a {strategy_type} strategy. Your preferred approaches: {strat_str}.

CORE RULES (these define your edge — follow them without exception):
1. DEFAULT TO HOLD — signal buy/sell only with HIGH conviction
2. Require 3+ confluent signals before acting
3. NEVER trade against the dominant trend; if unclear, HOLD
4. Validate risk:reward first — require at least 2× reward vs risk (identify concrete S/R levels)
5. RSI 35–65 + MACD histogram near zero = NO SIGNAL → HOLD
6. Choppy/ranging markets = HOLD; only trade clear trends or extreme reversals
7. Fee round-trip = 0.12% — your edge must exceed this
8. A missed trade costs nothing; a losing trade costs real money

SCALE-OUT POLICY (this fund uses staged profit-taking, not single-target exits):
- When you enter a position, the system automatically scales out in up to 3 tranches.
- Tranche 1 (~33% of TP range): 25% of position closes → SL moves to breakeven.
- Tranche 2 (~60% of TP range): another 35% closes → remaining ~40% rides to full TP or trailing stop.
- This means your ENTRY CRITERIA should be higher quality — you will capture meaningful profit
  on winners even if they don't reach full TP, so be selective and let trades breathe.
- Set TP targets at NATURAL RESISTANCE/SUPPORT, not just percentage offsets.
- SL should be below/above a key level — partial closes mean the risk on the runner is free.

CONFIDENCE CALIBRATION:
- 0.8–1.0 → Strong trend + 3+ aligned indicators + clear levels + team agrees → BUY or SELL
- 0.6–0.8 → Clear trend + 2 aligned indicators → BUY or SELL (cautious size)
- < 0.6   → Insufficient evidence → HOLD, regardless of individual indicators

RESPONSE: Valid JSON only — no markdown, no preamble.
{{"action":"buy|sell|hold","confidence":0.0-1.0,"reasoning":"concise technical analysis","key_levels":{{"resistance":0.0,"support":0.0}},"risk_level":"low|medium|high"}}"""

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
                _ta_support = ta.get('support', 0) or 0
                _ta_resist  = ta.get('resistance', 0) or 0
                parts.append(f"""TECHNICAL ANALYST (Marcus Webb) REPORT:
- Overall Signal: {ta.get('signal', 'N/A')} (confidence: {ta.get('confidence', 0):.0%})
- Multi-Timeframe Alignment: {ta.get('alignment', 'N/A')} (confluence: {ta.get('confluence_score', 0):.0%})
- Patterns Detected: {ta.get('patterns_count', 0)} — {ta.get('patterns_summary', 'none')}
- Key Support: {fmt_price(_ta_support)} | Resistance: {fmt_price(_ta_resist)}
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

            # Trade retrospective insights — learnings from past trades
            patterns = team_context.get("trade_patterns")
            if patterns and patterns.get("total_trades", 0) >= 2:
                retro_rules = []

                # ── Actionable rules derived from historical pattern data ──────────
                best = patterns.get("best_pattern")
                worst = patterns.get("worst_pattern")
                exit_eff = patterns.get("avg_exit_efficiency")
                avg_win_pct = patterns.get("avg_win_pct", 0) or 0
                avg_loss_pct = patterns.get("avg_loss_pct", 0) or 0
                win_rate = patterns.get("win_rate", 0) or 0
                hold_win = patterns.get("avg_holding_win_hours", 0) or 0
                hold_loss = patterns.get("avg_holding_loss_hours", 0) or 0
                weaknesses = patterns.get("weaknesses", [])
                strengths = patterns.get("strengths", [])

                if best:
                    retro_rules.append(f"✅ FAVOUR: '{best}' setups have been your most profitable — increase confidence when this pattern is present")
                if worst:
                    retro_rules.append(f"🚫 AVOID: '{worst}' setups have been your worst — reduce confidence or skip when this pattern is present")
                if exit_eff is not None and exit_eff < 0.35:
                    retro_rules.append(f"⚠️ EXIT EARLY: Your exit efficiency is only {exit_eff:.0%} — you are leaving significant profit on the table. Prefer HOLD to let winners run further before exiting")
                elif exit_eff is not None and exit_eff > 0.70:
                    retro_rules.append(f"✅ EXITS: Your exit timing is good ({exit_eff:.0%} efficiency) — maintain current exit discipline")
                if avg_loss_pct and avg_win_pct and abs(avg_loss_pct) > avg_win_pct * 1.5:
                    retro_rules.append(f"🚨 LOSS SIZE: Your average loss ({avg_loss_pct:.2f}%) is {abs(avg_loss_pct)/avg_win_pct:.1f}x your average win ({avg_win_pct:.2f}%) — be MORE selective, only trade your highest-conviction setups")
                if hold_loss > 0 and hold_win > 0 and hold_loss > hold_win * 1.8:
                    retro_rules.append(f"⚠️ HOLDING BIAS: You hold losers {hold_loss/hold_win:.1f}x longer than winners ({hold_loss:.1f}h vs {hold_win:.1f}h) — cut losing trades faster")
                if win_rate < 0.35:
                    retro_rules.append(f"⚠️ LOW WIN RATE: Only {win_rate:.0%} of your recent trades were profitable — require STRONGER confluence before entering. Raise your internal confidence threshold")
                for w in weaknesses[:2]:
                    retro_rules.append(f"⚠️ {w}")
                for s in strengths[:1]:
                    retro_rules.append(f"✅ {s}")

                if retro_rules:
                    parts.append(
                        "YOUR PERSONAL TRADING RULES (derived from your last "
                        f"{patterns['total_trades']} trades — MANDATORY to follow):\n"
                        + "\n".join(f"  {r}" for r in retro_rules)
                    )

            # Whale intelligence — Hyperliquid on-chain smart-money positioning
            whale = team_context.get("whale")
            if whale and (whale.get("long_notional", 0) + whale.get("short_notional", 0)) >= 10_000:
                def _wfmt(v: float) -> str:
                    if v >= 1_000_000:
                        return f"${v / 1_000_000:.1f}M"
                    if v >= 1_000:
                        return f"${v / 1_000:.0f}K"
                    return f"${v:.0f}"
                _wdirection = "NET LONG" if whale.get("net_notional", 0) > 0 else "NET SHORT"
                _wbias = whale.get("bias", "neutral").upper()
                parts.append(
                    f"HYPERLIQUID WHALE POSITIONING (on-chain smart-money):\n"
                    f"- {whale.get('coin', '?')} bias: {_wbias} | {_wdirection} | "
                    f"{_wfmt(whale.get('long_notional', 0))} long vs "
                    f"{_wfmt(whale.get('short_notional', 0))} short\n"
                    f"- {whale.get('whale_count', 0)} tracked wallet(s) | "
                    f"avg {whale.get('avg_leverage', 1):.0f}x leverage\n"
                    f"- Smart-money alignment: if their bias AGREES with your signal, "
                    f"increase confidence; if it OPPOSES, reduce confidence or hold"
                )

            if parts:
                team_section = "\n\nTEAM INTELLIGENCE (factor these into your decision):\n" + "\n\n".join(parts) + """

IMPORTANT: Weight your decision using team intelligence:
- If Technical Analyst's signal OPPOSES yours with high confidence, reduce your confidence or flip
- If market regime is trending, favour momentum/breakout; if ranging, favour mean reversion
- If Risk Manager says "danger" or "caution", prefer HOLD or reduce confidence
- If your recent win rate is below 40%, reduce confidence by 20%
- Look for CONFLUENCE across team signals — 3+ team members agreeing = high conviction
"""

            # Market session warning (US open blackout/confirmation)
            _ms = team_context.get("market_session") if team_context else None
            if _ms and _ms.get("note"):
                team_section += f"\n{_ms['note']}\n"

            # Phase 9.2 — Trader drawdown / Pink Slip pressure
            _trs = team_context.get("trader_risk_status") if team_context else None
            if _trs and _trs.get("note"):
                team_section += f"\n{_trs['note']}\n"

            # Re-entry context — inject AFTER team_section is built so it always surfaces
            recent_stopout = team_context.get("recent_stopout") if team_context else None
            if recent_stopout:
                team_section += (
                    f"\n⚠️  RE-ENTRY CONTEXT: Your last trade on {recent_stopout['symbol']} was stopped out "
                    f"{recent_stopout['minutes_ago']} minutes ago (P&L: {recent_stopout['pnl']:+.2f}). "
                    f"The stop-loss may have been too tight relative to current volatility. "
                    f"If the underlying thesis (trend, mean-reversion setup, key level) is STILL INTACT, "
                    f"this is a re-entry opportunity — not a reason to stand down. "
                    f"Re-entries after tight stop-outs are a normal part of mean-reversion and trend strategies. "
                    f"Evaluate the current setup on its own merits.\n"
                )

        return f"""PRICE ACTION:
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
SCALE-OUT REMINDER: This fund scales out of positions in tranches (25% at 33% of TP, 35% at 60% of TP, remainder at full TP). Factor this into your conviction — a trade only needs to reach 33% of its TP range to be profitable. Prefer setups with clear intermediate resistance/support levels where partial profit-taking makes sense.
NOTE: "sell" means SHORT when no long position exists — you can profit from downtrends.
Analyse the data above and return your trading decision as JSON."""

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
- NOTE: This fund uses scale-out exits (partial closes at 33% and 60% of TP range). Win rate reflects FULL TP hits only — a "loss" may still have returned profit via scale-out. Focus on profit_factor and expectancy over raw win_rate.

CONFIDENCE FACTORS:
- Adequate sample size: {total_trades >= 20}
- Positive expectancy: {total_pnl > 0}
- Win rate sustainable: {win_rate > 0.45 and profit_factor > 1.0}

Return JSON: {{"action": "continue|optimize|pause|stop", "confidence": 0.0-1.0, "reasoning": "evaluation based on profit factor, win rate, and sample size", "recommended_adjustments": ["list of specific optimization suggestions if needed"], "next_focus": "primary metric to improve (win_rate|position_size|entry_filters|stop_losses)"}}
"""


llm_service = LLMService()
