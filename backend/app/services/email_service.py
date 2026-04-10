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
        """Use the LLM (as Victoria, the CIO) to write a human-friendly HTML email."""
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

        lb_text = "\n".join(
            f"  #{a.get('rank','-')} {a.get('name', a.get('agent_id','?'))}: "
            f"P&L ${(a.get('total_pnl',0) or 0):+,.2f}, Win Rate {(a.get('win_rate',0) or 0):.0%}"
            for a in leaderboard[:8]
        ) or "  No agent data yet."

        prompt = f"""You are Victoria Montgomery, Chief Investment Officer of a sophisticated AI-powered crypto trading fund.

Write the daily fund summary email for {report_date}.

PERSONA: Authoritative but warm. Analytical. Occasionally dry wit. You care deeply about performance and risk management.

OUTPUT FORMAT: Return ONLY valid JSON with exactly two keys:
  "subject": a concise subject line (e.g. "Daily Fund Report — {report_date} | P&L ${total_pnl:+,.0f}")
  "html": a complete, well-formatted HTML email body. Requirements:
    - Start with a greeting paragraph to the trading team
    - Use a clean HTML table for portfolio metrics (4 columns: Metric, Value)
    - Use a separate HTML table for the agent leaderboard
    - Use <h2> section headers: Portfolio Overview, Trading Activity, Agent Performance, Market & Risk, Victoria's Notes
    - Use inline styles only (no <style> tags) — background #0d1117, text #e6edf3, accent #58a6ff
    - Bold positive P&L green (#3fb950), negative red (#f85149)
    - End with a personal paragraph from Victoria with her assessment and 1-2 forward-looking observations
    - Professional email sign-off from Victoria
    - Keep it readable — paragraphs, not walls of data

FUND DATA ({report_date}):
Portfolio Value: ${portfolio_value:,.2f}
Total P&L: ${total_pnl:+,.2f} ({daily_return:+.2f}%)
Realized P&L: ${realized_pnl:+,.2f} | Unrealized P&L: ${unrealized_pnl:+,.2f}
Trades Opened: {trades_opened} | Closed: {trades_closed} | Open Positions: {open_positions}
Buy Volume: ${buy_vol:,.0f} | Sell Volume: ${sell_vol:,.0f}
Risk Level: {risk_level.upper()} | CIO Sentiment: {cio_sentiment}
CIO Note: {cio_summary[:400]}
Market Conditions: {json.dumps(market)[:400]}

Agent Leaderboard:
{lb_text}

Team Discussion Highlight:
{team_summary[:500] or 'No significant team discussions today.'}
"""

        try:
            resp = await llm_service._call_llm(prompt)
            data = json.loads(resp.content)
            subject = data.get("subject", f"Daily Fund Report — {report_date}")
            html_body = data.get("html", "")
            if html_body:
                return subject, html_body
        except Exception as e:
            logger.warning(f"LLM email composition failed, using fallback: {e}")

        # ── Fallback: hand-crafted HTML email ──────────────────────────
        pnl_color = "#3fb950" if total_pnl >= 0 else "#f85149"
        pnl_arrow = "📈" if total_pnl >= 0 else "📉"
        subject = f"Daily Fund Report — {report_date} | P&L ${total_pnl:+,.0f}"

        lb_rows = ""
        for a in leaderboard[:8]:
            name = a.get("name", a.get("agent_id", "?"))
            pnl = a.get("total_pnl", 0) or 0
            wr = a.get("win_rate", 0) or 0
            c = "#3fb950" if pnl >= 0 else "#f85149"
            lb_rows += (
                f'<tr><td style="padding:6px 12px;border-bottom:1px solid #21262d">{a.get("rank","-")}</td>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #21262d">{name}</td>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #21262d;color:{c};font-weight:600">${pnl:+,.2f}</td>'
                f'<td style="padding:6px 12px;border-bottom:1px solid #21262d">{wr:.0%}</td></tr>'
            )

        html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;color:#e6edf3">
<div style="max-width:680px;margin:32px auto;background:#161b22;border-radius:12px;overflow:hidden;border:1px solid #30363d">

  <!-- Header -->
  <div style="background:#1f2937;padding:28px 32px;border-bottom:1px solid #30363d">
    <div style="font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px">Phemex AI Trader</div>
    <h1 style="margin:0;font-size:22px;font-weight:700;color:#f0f6fc">{pnl_arrow} Daily Fund Report</h1>
    <div style="color:#8b949e;font-size:13px;margin-top:4px">{report_date}</div>
  </div>

  <!-- Body -->
  <div style="padding:28px 32px">

    <p style="color:#c9d1d9;line-height:1.7;margin-top:0">
      Good morning, team. Here is your daily summary for <strong>{report_date}</strong>.
      The fund {("performed positively" if total_pnl >= 0 else "faced headwinds")} today
      with a net P&amp;L of <strong style="color:{pnl_color}">${total_pnl:+,.2f}</strong> ({daily_return:+.2f}%).
    </p>

    <!-- Portfolio Overview -->
    <h2 style="font-size:15px;font-weight:700;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px;margin-top:28px">📊 Portfolio Overview</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <tr style="background:#1f2937">
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Portfolio Value</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;font-weight:600">${portfolio_value:,.2f}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Total P&amp;L</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;font-weight:600;color:{pnl_color}">${total_pnl:+,.2f}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Realized P&amp;L</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">${realized_pnl:+,.2f}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Unrealized P&amp;L</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">${unrealized_pnl:+,.2f}</td>
      </tr>
      <tr style="background:#1f2937">
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Daily Return</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:{pnl_color}">{daily_return:+.2f}%</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Risk Level</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">{risk_level.upper()}</td>
      </tr>
    </table>

    <!-- Trading Activity -->
    <h2 style="font-size:15px;font-weight:700;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px;margin-top:28px">📋 Trading Activity</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <tr style="background:#1f2937">
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Trades Opened</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">{trades_opened}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Trades Closed</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">{trades_closed}</td>
      </tr>
      <tr>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Open Positions</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">{open_positions}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">CIO Sentiment</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">{cio_sentiment.title()}</td>
      </tr>
      <tr style="background:#1f2937">
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Buy Volume</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">${buy_vol:,.0f}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">Sell Volume</td>
        <td style="padding:8px 12px;border-bottom:1px solid #21262d">${sell_vol:,.0f}</td>
      </tr>
    </table>

    <!-- Agent Leaderboard -->
    <h2 style="font-size:15px;font-weight:700;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px;margin-top:28px">🏆 Agent Performance</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <tr style="background:#1f2937;color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.05em">
        <td style="padding:6px 12px">#</td>
        <td style="padding:6px 12px">Agent</td>
        <td style="padding:6px 12px">P&amp;L</td>
        <td style="padding:6px 12px">Win Rate</td>
      </tr>
      {lb_rows or '<tr><td colspan="4" style="padding:12px;color:#8b949e;text-align:center">No agent data yet</td></tr>'}
    </table>

    <!-- Victoria's Notes -->
    <h2 style="font-size:15px;font-weight:700;color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:8px;margin-top:28px">💼 Victoria's Notes</h2>
    <p style="color:#c9d1d9;line-height:1.7">
      {cio_summary[:500] if cio_summary else "No specific notes for today. The fund continues to operate within normal parameters."}
    </p>
    {(f'<p style="color:#c9d1d9;line-height:1.7"><strong>Team Discussion:</strong> {team_summary[:400]}</p>') if team_summary else ''}

  </div>

  <!-- Footer -->
  <div style="padding:20px 32px;background:#1f2937;border-top:1px solid #30363d;font-size:11px;color:#8b949e">
    <div>Victoria Montgomery &mdash; Chief Investment Officer, Phemex AI Trader</div>
    <div style="margin-top:4px">Automated daily report &middot; {report_date}</div>
  </div>

</div>
</body>
</html>"""
        return subject, html_body

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _send(self, subject: str, body: str) -> bool:
        if not self._api_key:
            logger.error("MAIL_SERVER_API_KEY not configured — cannot send email")
            return False

        url = f"https://{self._domain}/api/v1/send-email"
        # body is now HTML; also send a stripped plain-text fallback
        import re as _re
        plain_fallback = _re.sub(r'<[^>]+>', '', body)
        plain_fallback = _re.sub(r'&amp;', '&', _re.sub(r'&lt;', '<', _re.sub(r'&gt;', '>', _re.sub(r'&nbsp;', ' ', _re.sub(r'&mdash;', '—', _re.sub(r'&middot;', '·', plain_fallback))))))
        payload = {
            "email_type": "generic",
            "sender_org": "phemex-ai-trader",
            "to_address": self._to,
            "from_address": self._from,
            "message_subject": subject,
            "message_sent": False,
            "message_body": {
                "generic_body": plain_fallback,
                "html_body": body,
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
