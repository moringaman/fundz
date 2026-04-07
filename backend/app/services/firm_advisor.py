"""
Firm Advisor Chatbot Service

Allows users to ask the fund management team hypothetical questions about
market conditions, strategy, and risk. Responses reflect the firm's actual
state: live positions, risk limits, agent configs, TA signals, and regime.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from app.services.llm import LLMService, LLMRegistry
from app.services.risk_manager import risk_manager
from app.services.paper_trading import paper_trading

logger = logging.getLogger(__name__)


class FirmAdvisorService:
    def __init__(self):
        self._llm = LLMService()
        self._conversation: List[Dict[str, str]] = []  # in-memory conversation log
        self._max_history = 50

    async def ask(self, question: str) -> Dict[str, Any]:
        """Process a user question and return the firm's response."""
        context = await self._gather_firm_context()
        system_prompt = self._build_system_prompt(context)

        # Include recent conversation for continuity
        recent = self._conversation[-6:]  # last 3 exchanges
        history_block = ""
        if recent:
            history_block = "\n\nRECENT CONVERSATION:\n"
            for msg in recent:
                role = "CLIENT" if msg["role"] == "user" else "FIRM"
                history_block += f"{role}: {msg['content']}\n"

        user_prompt = f"{history_block}\nCLIENT QUESTION: {question}"

        response_text = await self._llm._call_llm_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.7,
            max_tokens=1500,
        )

        # Store conversation
        ts = datetime.now().isoformat()
        self._conversation.append({"role": "user", "content": question, "timestamp": ts})
        self._conversation.append({"role": "advisor", "content": response_text, "timestamp": ts})
        if len(self._conversation) > self._max_history:
            self._conversation = self._conversation[-self._max_history:]

        return {
            "response": response_text,
            "timestamp": ts,
            "context_summary": {
                "positions_count": context.get("positions_count", 0),
                "risk_level": context.get("risk_level", "unknown"),
                "daily_pnl": context.get("daily_pnl", 0),
            },
        }

    def get_history(self, limit: int = 50) -> List[Dict[str, str]]:
        """Return conversation history."""
        return self._conversation[-limit:]

    def clear_history(self):
        """Clear conversation history."""
        self._conversation.clear()

    async def _gather_firm_context(self) -> Dict[str, Any]:
        """Gather live firm state for the advisor prompt."""
        ctx: Dict[str, Any] = {}

        # Positions
        try:
            positions = await paper_trading.get_positions_live()
            ctx["positions"] = [
                {
                    "symbol": p["symbol"],
                    "side": p.get("side", "buy"),
                    "qty": p.get("quantity", 0),
                    "entry": p.get("entry_price", 0),
                    "current": p.get("current_price", 0),
                    "pnl": p.get("unrealized_pnl", 0),
                    "pnl_pct": p.get("unrealized_pnl_pct", 0),
                    "agent": p.get("agent_id"),
                }
                for p in positions
            ]
            ctx["positions_count"] = len(positions)
        except Exception as e:
            logger.debug(f"Advisor: failed to get positions: {e}")
            ctx["positions"] = []
            ctx["positions_count"] = 0

        # Capital
        try:
            balances = await paper_trading.get_all_balances()
            usdt = next((b.available for b in balances if b.asset == "USDT"), 0)
            pos_value = sum(
                p.get("qty", 0) * p.get("current", 0) for p in ctx["positions"]
            )
            ctx["total_capital"] = round(usdt + pos_value, 2)
            ctx["available_usdt"] = round(usdt, 2)
        except Exception:
            ctx["total_capital"] = 0
            ctx["available_usdt"] = 0

        # Risk assessment
        try:
            from app.services.agent_scheduler import agent_scheduler
            cached_risk = agent_scheduler.get_current_risk_assessment()
            if cached_risk:
                ctx["risk_level"] = cached_risk.risk_level
                ctx["daily_pnl"] = cached_risk.daily_pnl
                ctx["exposure_pct"] = cached_risk.exposure_pct_of_capital
                ctx["concentration"] = cached_risk.concentration_risk
                ctx["risk_recommendations"] = cached_risk.recommendations or []
            else:
                ctx["risk_level"] = "unknown"
                ctx["daily_pnl"] = risk_manager.get_daily_pnl()
        except Exception:
            ctx["risk_level"] = "unknown"
            ctx["daily_pnl"] = 0

        # Agent configs
        try:
            from app.services.agent_scheduler import agent_scheduler
            agents_info = []
            for aid, cfg in agent_scheduler._enabled_agents.items():
                agents_info.append({
                    "name": cfg.get("name", aid[:8]),
                    "strategy": cfg.get("strategy_type", "unknown"),
                    "pairs": cfg.get("trading_pairs", []),
                    "sl_pct": cfg.get("stop_loss_pct"),
                    "tp_pct": cfg.get("take_profit_pct"),
                })
            ctx["agents"] = agents_info
        except Exception:
            ctx["agents"] = []

        # TA confluence
        try:
            from app.services.agent_scheduler import agent_scheduler
            scores = getattr(agent_scheduler, "_current_confluence_scores", None)
            if scores:
                ctx["ta_confluence"] = {
                    sym: {
                        "signal": s.get("overall_signal", "neutral"),
                        "confidence": s.get("confluence_score", 0),
                    }
                    for sym, s in scores.items()
                }
        except Exception:
            pass

        # Research regime
        try:
            from app.services.agent_scheduler import agent_scheduler
            report = getattr(agent_scheduler, "_current_analyst_report", None)
            if report and hasattr(report, "market_regime"):
                ctx["market_regime"] = report.market_regime.regime
                ctx["market_sentiment"] = report.market_regime.sentiment
        except Exception:
            pass

        # Team member identities
        ctx["team_members"] = {
            role: {"name": info["name"], "title": info["title"]}
            for role, info in LLMRegistry.AGENTS.items()
        }

        return ctx

    def _build_system_prompt(self, ctx: Dict[str, Any]) -> str:
        """Build the system prompt that makes the LLM respond as the firm."""
        team = ctx.get("team_members", {})
        team_lines = "\n".join(
            f"  - {v['name']} ({v['title']})" for v in team.values()
        )

        positions_lines = "None currently."
        if ctx.get("positions"):
            positions_lines = "\n".join(
                f"  - {p['symbol']}: {p['qty']:.6f} @ ${p['entry']:.2f} → ${p['current']:.2f} "
                f"(P&L: ${p['pnl']:.2f} / {p['pnl_pct']:.2f}%)"
                for p in ctx["positions"]
            )

        agents_lines = "No agents registered."
        if ctx.get("agents"):
            agents_lines = "\n".join(
                f"  - {a['name']}: {a['strategy']} strategy, trading {', '.join(a['pairs'][:3])}"
                for a in ctx["agents"]
            )

        ta_lines = ""
        if ctx.get("ta_confluence"):
            ta_lines = "\nTECHNICAL ANALYSIS SIGNALS:\n" + "\n".join(
                f"  - {sym}: {s['signal']} (confidence: {s['confidence']:.2f})"
                for sym, s in ctx["ta_confluence"].items()
            )

        regime = ctx.get("market_regime", "unknown")
        sentiment = ctx.get("market_sentiment", "unknown")

        return f"""You are the collective voice of a professional AI-powered crypto fund management team.
When a client asks a question, you respond as the firm — drawing on the expertise of all team members.

YOUR TEAM:
{team_lines}

CURRENT PORTFOLIO STATE:
  Total Capital: ${ctx.get('total_capital', 0):,.2f}
  Available USDT: ${ctx.get('available_usdt', 0):,.2f}
  Risk Level: {ctx.get('risk_level', 'unknown').upper()}
  Daily P&L: ${ctx.get('daily_pnl', 0):,.2f}
  Exposure: {ctx.get('exposure_pct', 0):.1f}%
  Concentration Risk: {ctx.get('concentration', 'unknown')}

OPEN POSITIONS:
{positions_lines}

ACTIVE AGENTS:
{agents_lines}

MARKET REGIME: {regime} | Sentiment: {sentiment}
{ta_lines}

RESPONSE GUIDELINES:
- Respond conversationally but professionally, as a fund management team speaking to a client
- Reference specific team members by name when their expertise is relevant
  (e.g., "Elena would flag this as high risk", "Marcus's TA shows bearish divergence")
- Ground your answers in the ACTUAL portfolio state above — don't make up positions or data
- For hypothetical scenarios, explain what each relevant team member would do:
  risk limits Elena would enforce, signals Marcus would look for, rebalancing James would execute
- Be honest about uncertainties — crypto is volatile, acknowledge risks
- If asked about adjustments, explain what config changes (SL/TP, allocation, strategy) could achieve the goal
- Keep responses focused and actionable, typically 150-300 words
- Use markdown formatting for clarity (bold for emphasis, bullet points for lists)"""


firm_advisor = FirmAdvisorService()
