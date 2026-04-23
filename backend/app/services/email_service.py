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
            f"P&L ${(a.get('total_pnl',0) or 0):+,.2f}, Win Rate {(a.get('win_rate',0) or 0):.0%}, "
            f"{a.get('total_trades', 0) or 0} trades"
            for a in leaderboard[:8]
        ) or "  No agent data yet."

        victoria_system = """You are Victoria Montgomery, Chief Investment Officer of an AI-powered crypto hedge fund.

Your character:
- 20 years in derivatives and crypto. You have seen every market regime imaginable.
- Dry wit, especially when the market misbehaves. You are real, not corporate.
- You treat the AI trading agents like junior analysts on your desk. You name them directly.
- You are honest about losses — you explain them without excuses and without spin.
- You are decisive. You form a view and state it clearly. No hedging into meaninglessness.
- Warm but professional. This letter goes to the trading desk, not to clients.
- Every observation is specific. You reference actual numbers and actual agent names.
- You write in short paragraphs. Maximum 4 sentences per paragraph. Never more."""

        victoria_user = f"""Write your personal daily assessment for {report_date}.

Write 4 to 5 paragraphs of genuine prose. No headers, no bullet points, no JSON. Just the paragraphs, separated by blank lines.

Structure:
1. Opening — what does today's number tell you about the fund's health? Not just the sign of P&L but what's driving it and what it means.
2. Agent performance — who performed well and why do you think so? Who underperformed and is it regime mismatch, bad R:R, or something structural? Name them.
3. Market reading — what is the market telling you today? What regime do you think we're in and how well-positioned is the fund for what's coming?
4. Risk and concern — what are you watching closely? What could go wrong in the next 48 hours? Be specific.
5. Forward look — one sharp, non-obvious observation about tomorrow or the week ahead. End with a brief closing that sounds like you — not boilerplate.

FUND DATA ({report_date}):
Net P&L: ${total_pnl:+,.2f} ({daily_return:+.2f}%)
Realized: ${realized_pnl:+,.2f} | Unrealized: ${unrealized_pnl:+,.2f}
Trades: {trades_opened} opened, {trades_closed} closed | {open_positions} open positions
Risk Level: {risk_level.upper()} | Sentiment: {cio_sentiment}
Market: {json.dumps(market)[:500]}

Internal CIO assessment (use as raw material — rewrite in your own voice, do not quote directly):
{cio_summary[:800] or 'No formal assessment was generated today.'}

Agent leaderboard:
{lb_text}

Team discussion today:
{team_summary[:600] or 'No significant team discussions recorded today.'}"""

        victoria_html_paragraphs = ""
        try:
            raw_prose = await llm_service._call_llm_text(
                system_prompt=victoria_system,
                user_prompt=victoria_user,
                temperature=0.75,
                max_tokens=1200,
            )
            if raw_prose and not raw_prose.startswith("I'm sorry"):
                paragraphs = [p.strip() for p in raw_prose.split("\n\n") if p.strip()]
                victoria_html_paragraphs = "\n".join(
                    f'<p style="color:#c9d1d9;line-height:1.85;font-size:13.5px;margin:0 0 18px 0">{p}</p>'
                    for p in paragraphs
                )
        except Exception as e:
            logger.warning(f"Victoria prose generation failed, using fallback: {e}")

        # ── Build HTML email with Python-controlled structure ──────────
        pnl_color = "#3fb950" if total_pnl >= 0 else "#f85149"
        pnl_arrow = "📈" if total_pnl >= 0 else "📉"
        subject = f"Fund Report — {report_date} | Net P&L ${total_pnl:+,.0f}"

        lb_rows = ""
        for a in leaderboard[:8]:
            name = a.get("name", a.get("agent_id", "?"))
            pnl = a.get("total_pnl", 0) or 0
            wr = a.get("win_rate", 0) or 0
            trades = a.get("total_trades", 0) or 0
            c = "#3fb950" if pnl >= 0 else "#f85149"
            row_bg = "#1f2937" if (a.get("rank", 1) or 1) % 2 == 1 else "#161b22"
            lb_rows += (
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">{a.get("rank","-")}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #21262d;font-weight:600">{name}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #21262d;color:{c};font-weight:700">${pnl:+,.2f}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #21262d">{wr:.0%}</td>'
                f'<td style="padding:8px 12px;border-bottom:1px solid #21262d;color:#8b949e">{trades} trades</td>'
                f'</tr>'
            )

        # Use LLM-generated prose if available, otherwise a data-driven fallback
        if not victoria_html_paragraphs:
            direction_word = "positive territory" if total_pnl >= 0 else "the red"
            victoria_html_paragraphs = (
                f'<p style="color:#c9d1d9;line-height:1.85;font-size:13.5px;margin:0 0 18px 0">'
                f'The fund closed {direction_word} today with a net P&L of '
                f'<strong style="color:{pnl_color}">${total_pnl:+,.2f}</strong> ({daily_return:+.2f}%). '
                f'We executed {trades_opened + trades_closed} trades and are carrying {open_positions} open positions into the next session. '
                f'Risk level is currently <strong>{risk_level.upper()}</strong>.</p>'
                f'<p style="color:#c9d1d9;line-height:1.85;font-size:13.5px;margin:0 0 18px 0">'
                f'{cio_summary[:600] if cio_summary else "No CIO assessment was generated for today. Review the agent leaderboard for any agents showing deteriorating P&L relative to their win rate — that gap is where the fee drag hides."}'
                f'</p>'
                f'<p style="color:#c9d1d9;line-height:1.85;font-size:13.5px;margin:0 0 18px 0">'
                f'As always, I remind the desk: win rate is vanity. P&L after fees is sanity. '
                f'Any agent with a win rate above 60% and a negative cumulative P&L has an R:R problem, not a signal problem. '
                f'Fix the exits before you fix the entries.</p>'
            )

        discussion_block = ""
        if team_summary:
            discussion_block = (
                f'<h2 style="font-size:14px;font-weight:700;color:#58a6ff;text-transform:uppercase;'
                f'letter-spacing:.08em;border-bottom:1px solid #21262d;padding-bottom:10px;margin:32px 0 16px 0">'
                f'Team Discussion</h2>'
                f'<p style="color:#c9d1d9;line-height:1.8;font-size:13px;margin:0 0 24px 0">'
                f'{team_summary[:800].replace(chr(10), "<br><br>")}</p>'
            )

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;color:#e6edf3">
<div style="max-width:660px;margin:32px auto;background:#161b22;border-radius:12px;overflow:hidden;border:1px solid #30363d">

  <!-- ── Header ───────────────────────────────────────────────────────── -->
  <div style="background:linear-gradient(135deg,#1f2937 0%,#161b22 100%);padding:32px;border-bottom:1px solid #30363d">
    <div style="font-size:11px;color:#58a6ff;text-transform:uppercase;letter-spacing:.12em;margin-bottom:8px">
      Phemex AI Trader &mdash; Daily Fund Report
    </div>
    <h1 style="margin:0;font-size:26px;font-weight:700;color:#f0f6fc;line-height:1.2">
      {pnl_arrow}&nbsp; <span style="color:{pnl_color}">${total_pnl:+,.2f}</span>
    </h1>
    <div style="color:#8b949e;font-size:13px;margin-top:8px">
      {report_date} &nbsp;&middot;&nbsp; Portfolio Value: <strong style="color:#e6edf3">${portfolio_value:,.2f}</strong>
      &nbsp;&middot;&nbsp; Daily Return: <strong style="color:{pnl_color}">{daily_return:+.2f}%</strong>
    </div>
  </div>

  <!-- ── Body ─────────────────────────────────────────────────────────── -->
  <div style="padding:32px">

    <!-- Portfolio metrics -->
    <h2 style="font-size:14px;font-weight:700;color:#58a6ff;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #21262d;padding-bottom:10px;margin:0 0 16px 0">
      Portfolio Overview
    </h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:32px">
      <tr style="background:#1f2937">
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e;width:25%">Realized P&amp;L</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;font-weight:600;color:{('#3fb950' if realized_pnl >= 0 else '#f85149')}">${realized_pnl:+,.2f}</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e;width:25%">Unrealized P&amp;L</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;font-weight:600;color:{('#3fb950' if unrealized_pnl >= 0 else '#f85149')}">${unrealized_pnl:+,.2f}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e">Trades Opened</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d">{trades_opened}</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e">Trades Closed</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d">{trades_closed}</td>
      </tr>
      <tr style="background:#1f2937">
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e">Open Positions</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d">{open_positions}</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;color:#8b949e">Risk Level</td>
        <td style="padding:10px 14px;border-bottom:1px solid #21262d;font-weight:600">{risk_level.upper()}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;color:#8b949e">Buy Volume</td>
        <td style="padding:10px 14px">${buy_vol:,.0f}</td>
        <td style="padding:10px 14px;color:#8b949e">Sell Volume</td>
        <td style="padding:10px 14px">${sell_vol:,.0f}</td>
      </tr>
    </table>

    <!-- Agent leaderboard -->
    <h2 style="font-size:14px;font-weight:700;color:#58a6ff;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #21262d;padding-bottom:10px;margin:0 0 16px 0">
      Agent Leaderboard
    </h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:32px">
      <tr style="background:#0d1117;color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.06em">
        <td style="padding:8px 12px">#</td>
        <td style="padding:8px 12px">Agent</td>
        <td style="padding:8px 12px">Cumulative P&amp;L</td>
        <td style="padding:8px 12px">Win Rate</td>
        <td style="padding:8px 12px">Trades</td>
      </tr>
      {lb_rows or '<tr><td colspan="5" style="padding:16px 12px;color:#8b949e;text-align:center;font-style:italic">No agent performance data available yet.</td></tr>'}
    </table>

    <!-- Victoria's notes -->
    <h2 style="font-size:14px;font-weight:700;color:#58a6ff;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #21262d;padding-bottom:10px;margin:0 0 20px 0">
      Victoria's Assessment
    </h2>
    {victoria_html_paragraphs}

    {discussion_block}

  </div>

  <!-- ── Footer ───────────────────────────────────────────────────────── -->
  <div style="padding:20px 32px;background:#0d1117;border-top:1px solid #21262d;font-size:11px;color:#8b949e;display:flex;justify-content:space-between">
    <div>
      <strong style="color:#c9d1d9">Victoria Montgomery</strong><br>
      Chief Investment Officer &mdash; Phemex AI Trader
    </div>
    <div style="text-align:right">
      Automated daily report<br>{report_date}
    </div>
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
        payload = {
            "email_type": "generic",
            "sender_org": "phemex-ai-trader",
            "to_address": self._to,
            "from_address": self._from,
            "message_subject": subject,
            "message_sent": False,
            "message_body": {
                # html_body triggers the {{#if html_body}} branch in generic.hbs,
                # which renders ONLY the full HTML document without any wrapper chrome.
                # generic_body is kept as a plain-text fallback for non-HTML clients.
                "html_body": body,
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
