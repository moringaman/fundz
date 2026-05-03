"""
Accumulation Fund Service — spot asset accumulation for long-term holding.

Manages DCA buys, value averaging, dip-limit orders, and scale-out transfers
to the trading fund. Operates independently of the trading engine.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, delete

from app.database import get_async_session
from app.models import (
    AccumulationConfig,
    Balance as DBBalance,
)
from app.clients.phemex import PhemexClient
from app.config import settings

logger = logging.getLogger(__name__)

FUND_TYPE = "accumulation"


class AccumulationService:

    def __init__(self):
        self._phemex = PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet,
        )
        self._user_id = "default-user"

    # ── Config ─────────────────────────────────────────────────────────

    async def get_configs(self) -> List[Dict[str, Any]]:
        async with get_async_session() as db:
            rows = await db.execute(
                select(AccumulationConfig).where(
                    AccumulationConfig.user_id == self._user_id
                ).order_by(AccumulationConfig.asset)
            )
            return [
                {**{c.name: getattr(r, c.name) for c in AccumulationConfig.__table__.columns},
                 "id": r.id}
                for r in rows.scalars().all()
            ]

    async def upsert_config(self, asset: str, data: Dict[str, Any]) -> Dict[str, Any]:
        async with get_async_session() as db:
            existing = await db.execute(
                select(AccumulationConfig).where(
                    AccumulationConfig.user_id == self._user_id,
                    AccumulationConfig.asset == asset,
                )
            )
            cfg = existing.scalar_one_or_none()
            if cfg is None:
                cfg = AccumulationConfig(
                    id=str(uuid.uuid4()),
                    user_id=self._user_id,
                    asset=asset,
                )
                db.add(cfg)
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            await db.commit()
            await db.refresh(cfg)
            return {c.name: getattr(cfg, c.name) for c in AccumulationConfig.__table__.columns}

    async def delete_config(self, asset: str) -> bool:
        async with get_async_session() as db:
            existing = await db.execute(
                select(AccumulationConfig).where(
                    AccumulationConfig.user_id == self._user_id,
                    AccumulationConfig.asset == asset,
                )
            )
            cfg = existing.scalar_one_or_none()
            if cfg is None:
                return False
            await db.delete(cfg)
            await db.commit()
            return True

    # ── Live check ──────────────────────────────────────────────────────

    def _is_live(self) -> bool:
        try:
            from app.api.routes.settings import get_trading_prefs
            return get_trading_prefs().accumulation_live_enabled
        except Exception:
            return False

    # ── Balances ───────────────────────────────────────────────────────

    async def get_balances(self) -> List[Dict[str, Any]]:
        if self._is_live():
            try:
                account = await self._phemex.get_account_balance()
                wallets = account.get("data", [])
                balances = []
                for w in wallets:
                    currency = w.get("currency", "")
                    ev = float(w.get("balanceEv", 0)) / 100_000_000
                    balances.append({"asset": currency, "available": ev, "locked": 0.0})
                return balances
            except Exception as e:
                logger.warning(f"Failed to fetch live Phemex balance: {e}")
        async with get_async_session() as db:
            rows = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == self._user_id,
                    DBBalance.fund_type == FUND_TYPE,
                )
            )
            return [
                {"asset": r.asset, "available": r.available, "locked": r.locked}
                for r in rows.scalars().all()
            ]

    async def get_balance(self, asset: str) -> float:
        balances = await self.get_balances()
        for b in balances:
            if b["asset"] == asset:
                return b["available"]
        return 0.0

    async def _ensure_balance(self, asset: str, db) -> DBBalance:
        existing = await db.execute(
            select(DBBalance).where(
                DBBalance.user_id == self._user_id,
                DBBalance.asset == asset,
                DBBalance.fund_type == FUND_TYPE,
            )
        )
        bal = existing.scalar_one_or_none()
        if bal is None:
            bal = DBBalance(
                id=str(uuid.uuid4()),
                user_id=self._user_id,
                asset=asset,
                available=0.0,
                locked=0.0,
                fund_type=FUND_TYPE,
            )
            db.add(bal)
            await db.flush()
        return bal

    async def deposit_usdt(self, amount: float) -> Dict[str, Any]:
        """Deposit USDT from external source into the accumulation fund."""
        async with get_async_session() as db:
            bal = await self._ensure_balance("USDT", db)
            bal.available += amount
            await db.commit()
            logger.info(f"Accumulation fund: deposited ${amount:.2f} USDT")
            return {"asset": "USDT", "available": bal.available, "deposited": amount}

    async def transfer_to_trading(self, usd_amount: float) -> Dict[str, Any]:
        """Transfer USDT from accumulation fund to the trading fund."""
        async with get_async_session() as db:
            bal = await self._ensure_balance("USDT", db)
            if bal.available < usd_amount:
                raise ValueError(
                    f"Insufficient accumulation USDT: have ${bal.available:.2f}, need ${usd_amount:.2f}"
                )
            bal.available -= usd_amount
            # Credit the trading fund
            trade_bal = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == self._user_id,
                    DBBalance.asset == "USDT",
                    DBBalance.fund_type == "trading",
                )
            )
            tb = trade_bal.scalar_one_or_none()
            if tb is None:
                tb = DBBalance(
                    id=str(uuid.uuid4()),
                    user_id=self._user_id,
                    asset="USDT",
                    available=0.0,
                    locked=0.0,
                    fund_type="trading",
                )
                db.add(tb)
                await db.flush()
            tb.available += usd_amount
            await db.commit()
            logger.info(
                f"Accumulation → Trading: transferred ${usd_amount:.2f} USDT "
                f"(accumulation remaining: ${bal.available:.2f})"
            )
            return {"transferred": usd_amount, "accumulation_usdt": bal.available}

    # ── Positions (in-memory tracking, not exchange positions) ─────────
    # Stored as a JSON blob in a simple model so we don't need full Position
    # table integration with all its trading-specific fields.

    async def _get_positions_raw(self) -> List[Dict[str, Any]]:
        """Load accumulation positions from DB balance metadata."""
        async with get_async_session() as db:
            rows = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == self._user_id,
                    DBBalance.fund_type == FUND_TYPE,
                    DBBalance.asset != "USDT",
                )
            )
            positions = []
            for r in rows.scalars().all():
                meta = r.locked or 0.0  # abuse locked field to store cost basis
                # Actually let's use a better approach
                positions.append({
                    "asset": r.asset,
                    "quantity": r.available,
                    "avg_cost": meta,
                })
            return positions

    async def get_portfolio(self) -> Dict[str, Any]:
        """Return full portfolio snapshot with current prices."""
        import asyncio
        positions = await self._get_positions_raw()
        usdt_balance = await self.get_balance("USDT")
        total_invested = usdt_balance  # cash is part of invested capital
        current_value = usdt_balance
        enriched = []

        async def _price_for(sym: str) -> float:
            try:
                ticker = await self._phemex.get_ticker(sym)
                return float(ticker.get("result", {}).get("closeRp", 0))
            except Exception:
                return 0.0

        for p in positions:
            sym = p["asset"] if p["asset"].endswith("USDT") else f"{p['asset']}USDT"
            price = await _price_for(sym)
            value = p["quantity"] * price
            cost = p["quantity"] * p["avg_cost"]
            total_invested += cost
            current_value += value
            enriched.append({
                "asset": p["asset"],
                "quantity": p["quantity"],
                "avg_cost": p["avg_cost"],
                "current_price": price,
                "value": round(value, 2),
                "cost_basis": round(cost, 2),
                "unrealized_pnl": round(value - cost, 2),
                "unrealized_pnl_pct": round((value / cost - 1) * 100, 2) if cost else 0,
            })

        return {
            "positions": enriched,
            "usdt_balance": usdt_balance,
            "total_invested": round(total_invested, 2),
            "current_value": round(current_value, 2),
            "total_pnl": round(current_value - total_invested, 2),
            "total_pnl_pct": round((current_value / total_invested - 1) * 100, 2) if total_invested else 0,
        }

    async def buy_spot(self, asset: str, usd_amount: float, live: Optional[bool] = None) -> Dict[str, Any]:
        """Execute a spot market buy and record the position.

        If *live* is True, executes on the real exchange. If False, simulates
        (paper). Defaults to the global accumulation_live_enabled setting.
        """
        if live is None:
            try:
                from app.api.routes.settings import get_trading_prefs
                live = get_trading_prefs().accumulation_live_enabled
            except Exception:
                live = False

        usdt_bal = await self.get_balance("USDT")
        if usdt_bal < usd_amount:
            raise ValueError(
                f"Accumulation USDT insufficient: have ${usdt_bal:.2f}, need ${usd_amount:.2f}"
            )

        sym = asset if asset.endswith("USDT") else f"{asset}USDT"

        if live:
            ticker = await self._phemex.get_ticker(sym)
            price = float(ticker.get("result", {}).get("closeRp", 0))
            if price <= 0:
                raise ValueError(f"Failed to fetch price for {sym}")
            qty = (usd_amount / price) * 0.999
            from app.clients.phemex import OrderSide as PhemexSide
            order = await self._phemex.place_order(symbol=sym, side=PhemexSide.BUY, order_qty=qty)
            if not order:
                raise ValueError(f"Order failed for {sym}")
        else:
            # Paper: simulate price from the exchange for accurate records
            try:
                ticker = await self._phemex.get_ticker(sym)
                price = float(ticker.get("result", {}).get("closeRp", 0))
            except Exception:
                price = 0.0
            if price <= 0:
                price = usd_amount / 100  # fallback
            qty = (usd_amount / price) * 0.999

        # Record the position
        async with get_async_session() as db:
            usdt = await self._ensure_balance("USDT", db)
            usdt.available -= usd_amount

            bal = await self._ensure_balance(asset, db)
            old_cost = bal.available * (bal.locked or 0.0) if bal.locked else 0.0
            new_cost = qty * price
            total_qty = bal.available + qty
            bal.available = total_qty
            bal.locked = (old_cost + new_cost) / total_qty if total_qty > 0 else price

            await db.commit()

        mode = "LIVE" if live else "PAPER"
        logger.info(
            f"Accumulation [{mode}]: bought {qty:.6f} {asset} @ ${price:.4f} (${usd_amount:.2f} USD)"
        )
        return {
            "asset": asset,
            "quantity": qty,
            "price": price,
            "usd_spent": usd_amount,
            "mode": mode,
        }

    # ── DCA Execution ──────────────────────────────────────────────────

    async def run_dca(self) -> List[Dict[str, Any]]:
        """Execute all due DCA buys. Returns list of executed buys."""
        results = []
        configs = await self.get_configs()
        now = datetime.now(timezone.utc)

        for cfg in configs:
            if not cfg.get("dca_enabled") or not cfg.get("enabled"):
                continue
            next_at = cfg.get("dca_next_at")
            if next_at and now < next_at:
                continue

            amount = cfg.get("dca_amount_usd", 50.0)
            try:
                result = await self.buy_spot(cfg["asset"], amount)
                result["strategy"] = "dca"
                results.append(result)
                # Schedule next
                interval = cfg.get("dca_interval_hours", 168)
                new_next = now + timedelta(hours=interval)
                await self.upsert_config(cfg["asset"], {"dca_next_at": new_next})
            except Exception as e:
                logger.error(f"DCA buy failed for {cfg['asset']}: {e}")

        return results

    async def run_value_averaging(self) -> List[Dict[str, Any]]:
        results = []
        configs = await self.get_configs()
        now = datetime.now(timezone.utc)
        portfolio = await self.get_portfolio()
        current_value = portfolio["current_value"]
        total_invested = portfolio["total_invested"]

        for cfg in configs:
            if not cfg.get("va_enabled") or not cfg.get("enabled"):
                continue
            next_at = cfg.get("va_next_at")
            if next_at and now < next_at:
                continue

            target_growth = cfg.get("va_target_growth_rate", 1.0) / 100
            expected_value = total_invested * (1 + target_growth)
            gap = expected_value - current_value

            if gap > 10:  # minimum $10 buy
                try:
                    result = await self.buy_spot(cfg["asset"], min(gap, cfg.get("dca_amount_usd", 50) * 3))
                    result["strategy"] = "value_averaging"
                    result["gap"] = round(gap, 2)
                    results.append(result)
                except Exception as e:
                    logger.error(f"VA buy failed for {cfg['asset']}: {e}")

            period = cfg.get("va_period_hours", 168)
            await self.upsert_config(cfg["asset"], {"va_next_at": now + timedelta(hours=period)})

        return results

    async def run_dip_checks(self) -> List[Dict[str, Any]]:
        results = []
        configs = await self.get_configs()
        portfolio = await self.get_portfolio()

        for cfg in configs:
            if not cfg.get("dip_enabled") or not cfg.get("enabled"):
                continue
            levels = cfg.get("dip_levels") or []
            if not levels:
                continue

            pos = next((p for p in portfolio["positions"] if p["asset"] == cfg["asset"]), None)
            if not pos or pos["current_price"] <= 0:
                continue

            for level in levels:
                pct_drop = level.get("pct", 5)
                amount = level.get("amount", 50)
                threshold = pos["avg_cost"] * (1 - pct_drop / 100)
                if pos["current_price"] <= threshold:
                    try:
                        result = await self.buy_spot(cfg["asset"], amount)
                        result["strategy"] = "dip_buy"
                        result["trigger"] = f"-{pct_drop}% from avg cost"
                        results.append(result)
                    except Exception as e:
                        logger.error(f"Dip buy failed for {cfg['asset']}: {e}")

        return results

    # ── Scale-out (profit-taking → trading fund) ───────────────────────

    async def check_scale_outs(self) -> List[Dict[str, Any]]:
        """Check if any position has reached scale-out targets and transfer
        profits to the trading fund. Returns list of transfers executed."""
        results = []
        configs = await self.get_configs()
        portfolio = await self.get_portfolio()

        for cfg in configs:
            if not cfg.get("scale_out_enabled") or not cfg.get("enabled"):
                continue
            if cfg.get("scale_out_count", 0) >= cfg.get("scale_out_max_transfers", 4):
                continue

            pos = next((p for p in portfolio["positions"] if p["asset"] == cfg["asset"]), None)
            if not pos or pos["avg_cost"] <= 0:
                continue

            gain_pct = pos["unrealized_pnl_pct"]
            target = cfg.get("scale_out_target_pct", 30.0)
            if gain_pct < target:
                continue

            tranche_pct = cfg.get("scale_out_tranche_pct", 10.0) / 100
            sell_qty = pos["quantity"] * tranche_pct
            sym = cfg["asset"] if cfg["asset"].endswith("USDT") else f"{cfg['asset']}USDT"

            try:
                ticker = await self._phemex.get_ticker(sym)
                price = float(ticker.get("result", {}).get("closeRp", 0))
                if price <= 0:
                    continue

                from app.clients.phemex import OrderSide as PhemexSide
                order = await self._phemex.place_order(
                    symbol=sym,
                    side=PhemexSide.SELL,
                    order_qty=sell_qty,
                )
                if not order:
                    continue

                proceeds = sell_qty * price
                fee = proceeds * 0.001  # spot taker fee
                net_proceeds = proceeds - fee

                # Transfer to trading fund
                await self.transfer_to_trading(net_proceeds)

                # Update position
                async with get_async_session() as db:
                    bal = await self._ensure_balance(cfg["asset"], db)
                    bal.available -= sell_qty
                    if bal.available < 0.0001:
                        bal.available = 0.0
                    await db.commit()

                count = cfg.get("scale_out_count", 0) + 1
                await self.upsert_config(cfg["asset"], {"scale_out_count": count})

                results.append({
                    "asset": cfg["asset"],
                    "quantity": sell_qty,
                    "price": price,
                    "proceeds": round(proceeds, 2),
                    "transferred": round(net_proceeds, 2),
                    "gain_pct": round(gain_pct, 1),
                })
                logger.info(
                    f"Scale-out: sold {sell_qty:.4f} {cfg['asset']} @ ${price:.2f} "
                    f"(+{gain_pct:.1f}%), transferred ${net_proceeds:.2f} to trading fund"
                )
            except Exception as e:
                logger.error(f"Scale-out failed for {cfg['asset']}: {e}")

        return results

    # ── Low balance check for Telegram alert ───────────────────────────

    async def check_low_balance(self, min_usdt: float = 200.0,
                                cooldown_hours: float = 24) -> Optional[float]:
        """Return USDT balance if below *min_usdt* and not acknowledged within *cooldown_hours*."""
        bal = await self.get_balance("USDT")
        if bal >= min_usdt:
            return None
        ack_ts = getattr(self, '_low_bal_acknowledged_at', None)
        if ack_ts is not None:
            elapsed = (datetime.now(timezone.utc) - ack_ts).total_seconds()
            if elapsed < cooldown_hours * 3600:
                return None
        return bal

    def ack_low_balance(self) -> None:
        """Acknowledge the low balance alert — suppresses notifications for cooldown period."""
        self._low_bal_acknowledged_at = datetime.now(timezone.utc)
        logger.info("Accumulation low balance acknowledged — alerts suppressed for 24h")


accumulation_service = AccumulationService()
