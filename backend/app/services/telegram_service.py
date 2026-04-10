"""
Telegram Alert Service — sends important trading events to a Telegram chat
via the Bot API (POST https://api.telegram.org/bot<token>/sendMessage).

All alerts are gated by per-event toggles stored in the DB via the settings
module.  Calling code simply awaits the relevant method; if the alert type is
disabled or credentials are missing the call is a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False
    # Per-event toggles
    trade_executed: bool = True
    trade_rejected: bool = True
    ta_veto: bool = True
    daily_loss_limit: bool = True
    position_closed: bool = True
    take_profit_hit: bool = True
    automation_start_stop: bool = True
    agent_error: bool = True
    api_error: bool = True
    daily_report: bool = True
    rebalance: bool = False   # Can be noisy — off by default


class TelegramService:
    """Sends formatted Markdown messages to a Telegram chat."""

    def __init__(self) -> None:
        self._config = TelegramConfig()

    # ── Config management ──────────────────────────────────────────────────

    def configure(self, config: TelegramConfig) -> None:
        self._config = config

    def get_config(self) -> TelegramConfig:
        return self._config

    def is_enabled(self) -> bool:
        return bool(
            self._config.enabled
            and self._config.bot_token
            and self._config.chat_id
        )

    # ── Core send ─────────────────────────────────────────────────────────

    async def send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a raw message. Returns True on success."""
        if not self.is_enabled():
            logger.debug(
                "Telegram send skipped — not enabled "
                f"(enabled={self._config.enabled}, "
                f"has_token={bool(self._config.bot_token)}, "
                f"has_chat={bool(self._config.chat_id)})"
            )
            return False
        try:
            url = f"{TELEGRAM_API}/bot{self._config.bot_token}/sendMessage"
            payload = {
                "chat_id": self._config.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
                    return False
            return True
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    async def test_connection(self, bot_token: str, chat_id: str) -> dict:
        """Send a test message with provided credentials (before saving)."""
        try:
            url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": "✅ *Phemex AI Trader*\n\nTelegram alerts connected successfully\\! You\\'ll receive important trading notifications here\\.",
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    return {"ok": True, "message": "Test message sent successfully"}
                return {"ok": False, "message": data.get("description", "Unknown error")}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ── Alert methods ──────────────────────────────────────────────────────

    async def alert_trade_executed(
        self,
        trader_name: str,
        agent_name: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        sl_price: Optional[float],
        tp_price: Optional[float],
        is_paper: bool = True,
    ) -> None:
        if not self._config.trade_executed:
            return
        emoji = "📈" if side == "buy" else "📉"
        direction = "LONG" if side == "buy" else "SHORT"
        mode = "📝 Paper" if is_paper else "🔴 LIVE"
        sl_str = f"${sl_price:,.2f}" if sl_price else "N/A"
        tp_str = f"${tp_price:,.2f}" if tp_price else "N/A"
        value = quantity * price
        text = (
            f"{emoji} *Trade Executed* — {mode}\n\n"
            f"*{direction} {symbol}*\n"
            f"Price: `${price:,.4f}` | Value: `${value:,.2f}`\n"
            f"SL: `{sl_str}` | TP: `{tp_str}`\n\n"
            f"Trader: {trader_name} via *{agent_name}*"
        )
        await self.send(text)

    async def alert_trade_rejected(
        self,
        agent_name: str,
        symbol: str,
        side: str,
        reason: str,
        rejected_by: str = "Risk Manager",
    ) -> None:
        if not self._config.trade_rejected:
            return
        direction = "LONG" if side == "buy" else "SHORT"
        text = (
            f"🚫 *Trade Rejected*\n\n"
            f"*{direction} {symbol}* blocked by {rejected_by}\n"
            f"Agent: {agent_name}\n"
            f"Reason: _{reason}_"
        )
        await self.send(text)

    async def alert_ta_veto(
        self,
        agent_name: str,
        symbol: str,
        intended_side: str,
        ta_signal: str,
        ta_confidence: float,
    ) -> None:
        if not self._config.ta_veto:
            return
        direction = "LONG" if intended_side == "buy" else "SHORT"
        text = (
            f"⚠️ *TA Veto*\n\n"
            f"{agent_name} wanted *{direction} {symbol}*\n"
            f"Marcus flagged opposing signal: *{ta_signal.upper()}* at {ta_confidence:.0%} confidence"
        )
        await self.send(text)

    async def alert_daily_loss_limit(
        self,
        daily_loss_pct: float,
        limit_pct: float,
    ) -> None:
        if not self._config.daily_loss_limit:
            return
        text = (
            f"🚨 *Daily Loss Limit Hit*\n\n"
            f"Loss: `{daily_loss_pct:.2f}%` | Limit: `{limit_pct:.2f}%`\n"
            f"All new trades are blocked for today. Elena has halted execution."
        )
        await self.send(text)

    async def alert_position_closed(
        self,
        symbol: str,
        side: str,
        pnl: float,
        close_reason: str = "SL triggered",
    ) -> None:
        if not self._config.position_closed:
            return
        emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
        text = (
            f"{emoji} *Position Closed* — {close_reason}\n\n"
            f"*{symbol}* {side.upper()}\n"
            f"P&L: `{pnl_str}`"
        )
        await self.send(text)

    async def alert_take_profit_hit(
        self,
        symbol: str,
        side: str,
        pnl: float,
    ) -> None:
        if not self._config.take_profit_hit:
            return
        pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"${pnl:,.2f}"
        text = (
            f"💰 *Take Profit Hit*\n\n"
            f"*{symbol}* {side.upper()} closed at target\n"
            f"P&L: `{pnl_str}` 🎯"
        )
        await self.send(text)

    async def alert_automation_started(self) -> None:
        if not self._config.automation_start_stop:
            return
        await self.send("🟢 *Automation Started*\n\nThe trading engine is now running.")

    async def alert_automation_stopped(self) -> None:
        if not self._config.automation_start_stop:
            return
        await self.send("🔴 *Automation Stopped*\n\nThe trading engine has been paused.")

    async def alert_agent_error(self, agent_name: str, error: str) -> None:
        if not self._config.agent_error:
            return
        text = (
            f"❌ *Agent Error*\n\n"
            f"*{agent_name}* encountered an error:\n"
            f"`{error[:300]}`"
        )
        await self.send(text)

    async def alert_api_error(self, context: str, error: str) -> None:
        if not self._config.api_error:
            return
        text = (
            f"🔌 *API Error*\n\n"
            f"Context: {context}\n"
            f"Error: `{error[:300]}`"
        )
        await self.send(text)

    async def alert_daily_report(
        self,
        date_str: str,
        total_pnl: float,
        daily_return_pct: float,
        trades_opened: int,
        trades_closed: int,
        best_agent: Optional[str],
        portfolio_value: float,
    ) -> None:
        if not self._config.daily_report:
            return
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        pnl_str = f"+${total_pnl:,.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.2f}"
        ret_str = f"+{daily_return_pct:.2f}%" if daily_return_pct >= 0 else f"{daily_return_pct:.2f}%"
        best_str = f"\nTop strategy: *{best_agent}*" if best_agent else ""
        text = (
            f"📊 *Daily Report — {date_str}*\n\n"
            f"{pnl_emoji} P&L: `{pnl_str}` ({ret_str})\n"
            f"Portfolio: `${portfolio_value:,.2f}`\n"
            f"Trades: {trades_opened} opened / {trades_closed} closed"
            f"{best_str}"
        )
        await self.send(text)

    async def alert_rebalance(
        self,
        summary: str,
    ) -> None:
        if not self._config.rebalance:
            return
        text = f"⚖️ *Portfolio Rebalanced*\n\n{summary}"
        await self.send(text)


# Singleton
telegram_service = TelegramService()
