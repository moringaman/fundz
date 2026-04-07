"""
Email Service — Composes an LLM-written daily summary and dispatches it
through the Webnostix microservice email API.
"""

from __future__ import annotations
import json
import logging
from datetime import date, datetime
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Sends emails via the wx-microservice-email REST API."""

    def __init__(self):
        self._domain = settings.mail_server_domain
        self._api_key = settings.mail_server_api_key
        self._to = settings.mail_to_address
        self._from = settings.mail_from_address

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def send_daily_summary(self, report: dict, force: bool = False) -> bool:
        """Compose a daily summary via LLM and send it by email.

        Args:
            report: The daily report dict produced by DailyReportService.
            force: If True, bypass the "already sent today" guard.

        Returns:
            True if the email was accepted by the mail server.
        """
        subject, body = await self._compose_summary(report)
        return await self._send(subject=subject, body=body)

    async def send_test_email(self) -> bool:
        """Send a short test email to verify configuration."""
        from app.services.daily_report import daily_report_service

        report = await daily_report_service.generate_daily_report(force=True)
        if not report:
            return await self._send(
                subject="Phemex AI Trader — Test Email",
                body=(
                    "✅ TEST EMAIL\n\n"
                    "Your email configuration is working correctly.\n\n"
                    "No daily report data is available yet — "
                    "this message confirms the pipeline is connected.\n\n"
                    "— Phemex AI Trader"
                ),
            )
        subject, body = await self._compose_summary(report)
        subject = f"[TEST] {subject}"
        return await self._send(subject=subject, body=body)

    # ------------------------------------------------------------------
    # LLM composition
    # ------------------------------------------------------------------

    async def _compose_summary(self, report: dict) -> tuple[str, str]:
        """Use the LLM (as Victoria, the CIO) to write a human-friendly email."""
        from app.services.llm import llm_service

        report_date = report.get("report_date", date.today().isoformat())

        total_pnl = report.get("total_pnl", 0) or 0
        realized_pnl = report.get("realized_pnl", 0) or 0
        unrealized_pnl = report.get("unrealized_pnl", 0) or 0
        daily_return = report.get("daily_return_pct", 0) or 0
        trades_opened = report.get("trades_opened", 0) or 0
        trades_closed = report.get("trades_closed", 0) or 0
        open_positions = report.get("open_positions_count", 0) or 0
        portfolio_value = report.get("portfolio_value", 0) or 0
        buy_vol = report.get("total_buy_volume", 0) or 0
        sell_vol = report.get("total_sell_volume", 0) or 0
        cio_sentiment = report.get("cio_sentiment", "neutral")
        cio_summary = report.get("cio_summary", "")
        risk = report.get("risk_summary", {}) or {}
        risk_level = risk.get("risk_level", "unknown")
        leaderboard = report.get("agent_leaderboard", []) or []
        team_summary = report.get("team_discussion_summary", "")
        market = report.get("market_conditions", {}) or {}

        lb_text = ""
        for a in leaderboard[:5]:
            name = a.get("name", a.get("agent_id", "?"))
            pnl = a.get("total_pnl", 0) or 0
            wr = a.get("win_rate", 0) or 0
            lb_text += f"  #{a.get('rank','-')} {name}: P&L ${pnl:+,.2f}, Win Rate {wr:.0%}\n"

        prompt = f"""You are Victoria Montgomery, Chief Investment Officer of our AI-powered crypto trading fund.
Write a daily summary EMAIL for {report_date} addressed to the trading team.

STYLE: Professional but warm. Include personality — brief quips, emojis sparingly.
FORMAT: Return ONLY valid JSON with two keys:
  "subject": "email subject line",
  "body": "the full email body in PLAIN TEXT (no HTML tags). Use line breaks, dashes, and unicode symbols (✅ ❌ 📊 📈 📉 ⚠️) for formatting. Use sections with headers in CAPS or with emoji prefixes."

DATA FOR TODAY ({report_date}):
Portfolio Value: ${portfolio_value:,.2f}
Total P&L: ${total_pnl:+,.2f} ({daily_return:+.2f}%)
Realized P&L: ${realized_pnl:+,.2f}
Unrealized P&L: ${unrealized_pnl:+,.2f}
Trades Opened: {trades_opened} | Closed: {trades_closed}
Buy Volume: ${buy_vol:,.0f} | Sell Volume: ${sell_vol:,.0f}
Open Positions: {open_positions}
Risk Level: {risk_level}
CIO Sentiment: {cio_sentiment}
CIO Note: {cio_summary[:300]}
Market: {json.dumps(market)[:400]}

Agent Leaderboard:
{lb_text or '  No agent data yet.'}

Team Discussion Highlight:
{team_summary[:400] or 'No team messages today.'}
"""

        try:
            resp = await llm_service._call_llm(prompt)
            data = json.loads(resp.content)
            subject = data.get("subject", f"Daily Trading Summary — {report_date}")
            body = data.get("body", "")
            if body:
                return subject, body
        except Exception as e:
            logger.warning(f"LLM email composition failed, using fallback: {e}")

        # Fallback — clean plain-text email
        pnl_arrow = "📈" if total_pnl >= 0 else "📉"
        subject = f"Daily Trading Summary — {report_date}"

        lb_lines = ""
        for a in leaderboard[:5]:
            name = a.get("name", a.get("agent_id", "?"))
            pnl = a.get("total_pnl", 0) or 0
            wr = a.get("win_rate", 0) or 0
            icon = "✅" if pnl >= 0 else "❌"
            lb_lines += f"  {icon} {name}: ${pnl:+,.2f} P&L, {wr:.0%} win rate\n"

        body = f"""📊 DAILY TRADING SUMMARY — {report_date}
{'=' * 45}

{pnl_arrow} PORTFOLIO OVERVIEW
  Portfolio Value:  ${portfolio_value:,.2f}
  Total P&L:       ${total_pnl:+,.2f} ({daily_return:+.2f}%)
  Realized P&L:    ${realized_pnl:+,.2f}
  Unrealized P&L:  ${unrealized_pnl:+,.2f}

📋 TRADING ACTIVITY
  Trades Opened: {trades_opened}  |  Closed: {trades_closed}
  Buy Volume:  ${buy_vol:,.0f}
  Sell Volume: ${sell_vol:,.0f}
  Open Positions: {open_positions}

⚠️ RISK & SENTIMENT
  Risk Level: {risk_level.upper()}
  CIO Sentiment: {cio_sentiment}

🏆 AGENT LEADERBOARD
{lb_lines or '  No agent data yet.'}
{f'💬 CIO NOTE: {cio_summary[:300]}' if cio_summary else ''}
{'─' * 45}
Phemex AI Trader — Automated daily report
"""
        return subject, body

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _send(self, subject: str, body: str) -> bool:
        if not self._api_key:
            logger.error("MAIL_SERVER_API_KEY not configured — cannot send email")
            return False

        url = f"https://{self._domain}/api/v1/send-email"
        payload = {
            "email_type": "generic",
            "sender_org": "phemex-ai-trader",
            "to_address": self._to,
            "from_address": self._from,
            "message_subject": subject,
            "message_sent": False,
            "message_body": {
                "generic_body": body,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": self._api_key,
                    },
                )
            if resp.status_code < 300:
                logger.info(f"Daily summary email sent to {self._to}")
                return True
            else:
                logger.error(f"Email API returned {resp.status_code}: {resp.text[:300]}")
                return False
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False


email_service = EmailService()
