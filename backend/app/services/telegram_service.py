"""
Telegram Alert Service — sends important trading events to a Telegram chat
via the Bot API (POST https://api.telegram.org/bot<token>/sendMessage).

All alerts are gated by per-event toggles stored in the DB via the settings
module.  Calling code simply awaits the relevant method; if the alert type is
disabled or credentials are missing the call is a no-op.

Inbound polling:
  When polling_enabled=True the service runs a long-poll loop (getUpdates with
  timeout=30) as an asyncio task.  Commands are restricted to the configured
  chat_id so random Telegram users cannot interact with the bot.

  Supported commands:
    /help          — list all commands
    /status        — portfolio summary (balance + open position count + daily P&L)
    /positions     — list all open positions with entry, current price, P&L, SL/TP
    /close <id>    — close a position by its short ID (first 8 chars of UUID)
    /sl <id> <px>  — update stop-loss price on a position
    /tp <id> <px>  — update take-profit price on a position
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False
    polling_enabled: bool = False   # Inbound command polling
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
        # API error cooldown state — prevents alert spam
        self._api_error_last_sent: Optional[datetime] = None
        self._api_error_acknowledged: bool = False
        self._API_ERROR_COOLDOWN = timedelta(hours=6)

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

    async def alert_api_error(self, context: str, error: str, status_code: Optional[int] = None) -> None:
        """Send a Telegram alert for Phemex API errors (500, 401, etc.).

        Respects a 6-hour cooldown: once an alert is sent, no further alerts
        fire until either 6 hours elapse OR the user sends /ack via the bot.
        """
        if not self._config.api_error:
            return

        now = datetime.now()

        # Check cooldown — don't spam if already alerted recently
        if self._api_error_last_sent and not self._api_error_acknowledged:
            elapsed = now - self._api_error_last_sent
            if elapsed < self._API_ERROR_COOLDOWN:
                logger.debug(
                    f"API error alert suppressed — cooldown active "
                    f"({elapsed.total_seconds() / 3600:.1f}h / 6h, /ack not received)"
                )
                return

        # Reset acknowledged flag on new send
        self._api_error_acknowledged = False
        self._api_error_last_sent = now

        code_str = f" `{status_code}`" if status_code else ""
        text = (
            f"🚨 *Phemex API Error{code_str}*\n\n"
            f"Context: {context}\n"
            f"Error: `{error[:300]}`\n\n"
            f"_This alert will repeat in 6 hours unless acknowledged._\n"
            f"Reply /ack to acknowledge."
        )
        await self.send(text)

    def acknowledge_api_error(self) -> str:
        """Acknowledge the API error alert, resetting the cooldown.

        Returns a confirmation message.
        """
        if self._api_error_last_sent is None:
            return "No active API error alerts to acknowledge."
        self._api_error_acknowledged = True
        return (
            f"✅ API error alert acknowledged.\n"
            f"You won't be alerted again until a *new* API error occurs."
        )

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

    # ── Inbound polling ───────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Start the long-poll loop as a background coroutine.

        Call once from the app lifespan after settings have been loaded.
        Safe to call even when polling_enabled=False — it will exit immediately.
        The loop restarts itself on error; it only stops when cancelled.
        """
        if not (self._config.polling_enabled and self.is_enabled()):
            logger.debug("Telegram polling disabled or not configured — skipping")
            return
        logger.info("Telegram polling started")
        offset = 0
        while True:
            try:
                offset = await self._poll_once(offset)
            except asyncio.CancelledError:
                logger.info("Telegram polling cancelled")
                return
            except Exception as exc:
                logger.warning(f"Telegram poll error: {exc} — retrying in 10s")
                await asyncio.sleep(10)

    async def _poll_once(self, offset: int) -> int:
        """Call getUpdates with long-poll timeout=30, process any messages, return new offset."""
        url = f"{TELEGRAM_API}/bot{self._config.bot_token}/getUpdates"
        params = {"timeout": 30, "offset": offset, "allowed_updates": ["message"]}
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            logger.warning(f"Telegram getUpdates failed: {resp.status_code}")
            await asyncio.sleep(5)
            return offset
        data = resp.json()
        for update in data.get("result", []):
            offset = max(offset, update["update_id"] + 1)
            await self._handle_update(update)
        return offset

    async def _handle_update(self, update: dict) -> None:
        """Route an incoming Telegram update to the right command handler."""
        msg = update.get("message", {})
        if not msg:
            return

        # ── Auth gate — only respond to the configured chat_id ────────────
        sender_chat = str(msg.get("chat", {}).get("id", ""))
        if sender_chat != str(self._config.chat_id):
            logger.warning(
                f"Telegram: ignoring message from unauthorized chat_id={sender_chat} "
                f"(expected {self._config.chat_id})"
            )
            return

        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            return  # ignore non-commands

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]   # strip @botname suffix if present
        args  = parts[1:]

        handlers = {
            "/help":      self._cmd_help,
            "/status":    self._cmd_status,
            "/positions": self._cmd_positions,
            "/close":     self._cmd_close,
            "/sl":        self._cmd_sl,
            "/tp":        self._cmd_tp,
            "/ack":       self._cmd_ack,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                await handler(args)
            except Exception as exc:
                logger.error(f"Telegram command {cmd} error: {exc}", exc_info=True)
                await self.send(f"⚠️ Command failed: `{exc}`")
        else:
            await self.send(f"Unknown command `{cmd}`. Use /help for the list.")

    # ── Command handlers ──────────────────────────────────────────────────

    async def _cmd_help(self, _args: list[str]) -> None:
        text = (
            "🤖 *Phemex AI Trader — Bot Commands*\n\n"
            "/status — portfolio summary\n"
            "/positions — list all open positions\n"
            "/close `<id>` — close position by short ID\n"
            "/sl `<id> <price>` — update stop-loss\n"
            "/tp `<id> <price>` — update take-profit\n"
            "/ack — acknowledge API error alert\n"
            "/help — this message"
        )
        await self.send(text)

    async def _cmd_status(self, _args: list[str]) -> None:
        try:
            from app.services.paper_trading import paper_trading
            positions = await paper_trading.get_positions_live()
            balances  = await paper_trading.get_all_balances()
            usdt = next(
                (float(b.available) for b in balances if b.asset == "USDT"),
                0.0,
            )

            total_unrealised  = sum(p.get("unrealized_pnl", 0.0) or 0.0 for p in positions)
            positions_value   = sum(
                (p.get("quantity") or 0) * (p.get("current_price") or 0)
                for p in positions
            )
            total_equity      = usdt + positions_value
            exposure_pct      = (positions_value / total_equity * 100) if total_equity > 0 else 0.0

            unr_sign = "+" if total_unrealised >= 0 else ""
            lines = [
                "📊 *Portfolio Status*\n",
                f"Available USDT:   `${usdt:,.2f}`",
                f"Positions Value:  `${positions_value:,.2f}`",
                f"Total Equity:     `${total_equity:,.2f}`",
                f"Exposure:         `{exposure_pct:.1f}%`",
                f"Open positions:   `{len(positions)}`",
                f"Unrealised P&L:   `{unr_sign}${total_unrealised:,.2f}`",
            ]
            await self.send("\n".join(lines))
        except Exception as exc:
            await self.send(f"⚠️ Could not fetch status: `{exc}`")

    async def _cmd_positions(self, _args: list[str]) -> None:
        try:
            from app.services.paper_trading import paper_trading
            positions = await paper_trading.get_positions_live()
            if not positions:
                await self.send("📭 No open positions.")
                return

            lines = ["📋 *Open Positions*\n"]
            for p in positions:
                pid   = str(p.get("id", ""))[:8]
                sym   = p.get("symbol", "?")
                side  = p.get("side", "?").upper()
                entry = p.get("entry_price", 0.0)
                curr  = p.get("current_price", 0.0)
                pnl   = p.get("unrealized_pnl", 0.0) or 0.0
                sl    = p.get("stop_loss_price")
                tp    = p.get("take_profit_price")
                pnl_e = "🟢" if pnl >= 0 else "🔴"
                sl_s  = f"`${sl:,.4f}`" if sl else "—"
                tp_s  = f"`${tp:,.4f}`" if tp else "—"
                lines.append(
                    f"{pnl_e} `{pid}` *{sym}* {side}\n"
                    f"  Entry `${entry:,.4f}` → Now `${curr:,.4f}`\n"
                    f"  P&L `{'+'if pnl>=0 else ''}${pnl:,.2f}` | SL {sl_s} TP {tp_s}"
                )
            await self.send("\n\n".join(lines))
        except Exception as exc:
            await self.send(f"⚠️ Could not fetch positions: `{exc}`")

    async def _cmd_close(self, args: list[str]) -> None:
        if not args:
            await self.send("Usage: /close `<position-id>`\nGet the ID from /positions")
            return
        short_id = args[0].strip()
        try:
            from app.services.paper_trading import paper_trading
            positions = await paper_trading.get_positions()
            match = next(
                (p for p in positions if str(p.id).startswith(short_id) or str(p.id) == short_id),
                None,
            )
            if not match:
                await self.send(f"❌ No position found matching ID `{short_id}`")
                return
            result = await paper_trading.close_position(str(match.id))
            if result:
                pnl = result.get("pnl", 0.0)
                e   = "🟢" if pnl >= 0 else "🔴"
                await self.send(
                    f"{e} *Position Closed*\n\n"
                    f"`{short_id}` {match.symbol} {match.side.upper()}\n"
                    f"P&L: `{'+'if pnl>=0 else ''}${pnl:,.2f}`"
                )
            else:
                await self.send(f"⚠️ Close returned no result for `{short_id}`")
        except Exception as exc:
            await self.send(f"⚠️ Close failed: `{exc}`")

    async def _cmd_sl(self, args: list[str]) -> None:
        if len(args) < 2:
            await self.send("Usage: /sl `<position-id> <price>`")
            return
        short_id, price_str = args[0], args[1]
        try:
            price = float(price_str)
        except ValueError:
            await self.send(f"❌ Invalid price: `{price_str}`")
            return
        try:
            from app.services.paper_trading import paper_trading
            positions = await paper_trading.get_positions()
            match = next(
                (p for p in positions if str(p.id).startswith(short_id) or str(p.id) == short_id),
                None,
            )
            if not match:
                await self.send(f"❌ No position found matching ID `{short_id}`")
                return
            await paper_trading.update_position_sl_tp(str(match.id), stop_loss_price=price)
            await self.send(
                f"✅ Stop-loss updated\n\n"
                f"`{short_id}` {match.symbol} → SL `${price:,.4f}`"
            )
        except Exception as exc:
            await self.send(f"⚠️ SL update failed: `{exc}`")

    async def _cmd_tp(self, args: list[str]) -> None:
        if len(args) < 2:
            await self.send("Usage: /tp `<position-id> <price>`")
            return
        short_id, price_str = args[0], args[1]
        try:
            price = float(price_str)
        except ValueError:
            await self.send(f"❌ Invalid price: `{price_str}`")
            return
        try:
            from app.services.paper_trading import paper_trading
            positions = await paper_trading.get_positions()
            match = next(
                (p for p in positions if str(p.id).startswith(short_id) or str(p.id) == short_id),
                None,
            )
            if not match:
                await self.send(f"❌ No position found matching ID `{short_id}`")
                return
            await paper_trading.update_position_sl_tp(str(match.id), take_profit_price=price)
            await self.send(
                f"✅ Take-profit updated\n\n"
                f"`{short_id}` {match.symbol} → TP `${price:,.4f}`"
            )
        except Exception as exc:
            await self.send(f"⚠️ TP update failed: `{exc}`")

    async def _cmd_ack(self, _args: list[str]) -> None:
        """Acknowledge active API error alert, silencing further repeats."""
        reply = self.acknowledge_api_error()
        await self.send(reply)


# Singleton
telegram_service = TelegramService()
