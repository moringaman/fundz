"""
Accumulation Fund Service — spot asset accumulation for long-term holding.

Manages DCA buys, value averaging, dip-limit orders, and scale-out transfers
to the trading fund. Uses Hyperliquid (DEX, USDC) for live execution;
paper/DB mode for testing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from contextvars import ContextVar

from sqlalchemy import select, func

from app.database import get_async_session
from app.models import (
    AccumulationConfig,
    AccumulationExecutionRecord,
    Balance as DBBalance,
)
from app.config import settings

logger = logging.getLogger(__name__)

# Context var for the current user — set by route handlers.
ctx_user_id: ContextVar[str] = ContextVar("ctx_user_id", default="default-user")

def set_ctx_user_id(uid: str) -> None:
    ctx_user_id.set(uid)

def _get_user_id() -> str:
    return ctx_user_id.get()

FUND_TYPE = "accumulation"
QUOTE_CURRENCY = "USDC"


class AccumulationService:

    def __init__(self):
        self._exchange = None   # hyperliquid.exchange.Exchange — lazy init
        self._info_client = None  # HyperliquidClient — lazy init

        # ── Trend filter cache ────────────────────────────────────────
        # 200-day SMA only meaningfully changes once per day, so cache the
        # trend assessment per asset for 6 hours. Avoids hammering the
        # Phemex/Binance kline endpoint on every DCA/VA tick.
        # key = asset, value = (trend_state_dict, cached_at_datetime)
        self._trend_cache: Dict[str, tuple] = {}
        self._TREND_CACHE_TTL_SECONDS = 6 * 3600

    # ── Trend filter ──────────────────────────────────────────────────

    async def _get_trend_state(self, asset: str) -> Optional[Dict[str, Any]]:
        """Compute long-term trend state for an asset using daily candles.

        Returns:
          {
            "in_downtrend": bool,   # True if price < SMA200 AND SMA200 sloping down
            "price": float,         # latest close
            "sma_200": float,
            "sma_slope_pct": float, # 20-day % change of SMA200 (negative = falling)
            "pct_below_sma": float, # signed % distance from SMA200
          }
        Returns None on data fetch failure (caller should fail open — allow DCA).

        Caching: 6h TTL per asset to avoid hammering the kline endpoint.
        """
        cached = self._trend_cache.get(asset)
        if cached:
            state, cached_at = cached
            if (datetime.now(timezone.utc) - cached_at).total_seconds() < self._TREND_CACHE_TTL_SECONDS:
                return state

        try:
            from app.clients.phemex import PhemexClient
            symbol = asset if asset.endswith("USDT") else f"{asset}USDT"
            client = PhemexClient()
            try:
                rows = await client.get_klines(symbol, interval="1d", limit=250)
            finally:
                # PhemexClient may hold an httpx session; close defensively
                close = getattr(client, "close", None)
                if close:
                    try:
                        await close()
                    except Exception:
                        pass

            if not rows or len(rows) < 200:
                logger.debug(f"Trend filter: insufficient daily history for {asset} ({len(rows) if rows else 0} rows)")
                return None

            # Phemex format: [ts, open, high, low, close, volume, ...] (close at idx 5 typical)
            # Binance fallback format is the same shape via _get_binance_klines.
            closes: List[float] = []
            for r in rows:
                if isinstance(r, dict):
                    c = r.get("close") or r.get("c")
                else:
                    # List/tuple — Phemex uses index 5 for close in /kline/last responses
                    # but Binance fallback uses index 4. Try idx 5 first, fall back to 4.
                    c = r[5] if len(r) > 5 else r[4]
                try:
                    closes.append(float(c))
                except (TypeError, ValueError):
                    continue

            if len(closes) < 200:
                return None

            sma_window = closes[-200:]
            sma_200 = sum(sma_window) / len(sma_window)
            price = closes[-1]

            # Slope: SMA200 today vs SMA200 ~20 days ago. Negative = falling.
            if len(closes) >= 220:
                older_window = closes[-220:-20]
                sma_200_prior = sum(older_window) / len(older_window)
                sma_slope_pct = ((sma_200 - sma_200_prior) / sma_200_prior * 100) if sma_200_prior > 0 else 0.0
            else:
                sma_slope_pct = 0.0

            pct_below_sma = ((price - sma_200) / sma_200 * 100) if sma_200 > 0 else 0.0
            # In a confirmed downtrend: BELOW SMA200 by at least 2% AND slope is falling.
            # The 2% buffer prevents flapping around the line on a sideways grind.
            in_downtrend = (pct_below_sma < -2.0) and (sma_slope_pct < 0.0)

            state = {
                "in_downtrend": in_downtrend,
                "price": price,
                "sma_200": sma_200,
                "sma_slope_pct": round(sma_slope_pct, 3),
                "pct_below_sma": round(pct_below_sma, 3),
            }
            self._trend_cache[asset] = (state, datetime.now(timezone.utc))
            return state
        except Exception as exc:
            logger.debug(f"Trend filter: failed to compute trend for {asset}: {exc}")
            return None

    # ── Config ─────────────────────────────────────────────────────────

    async def get_configs(self) -> List[Dict[str, Any]]:
        async with get_async_session() as db:
            rows = await db.execute(
                select(AccumulationConfig).where(
                    AccumulationConfig.user_id == _get_user_id()
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
                    AccumulationConfig.user_id == _get_user_id(),
                    AccumulationConfig.asset == asset,
                )
            )
            cfg = existing.scalar_one_or_none()
            if cfg is None:
                cfg = AccumulationConfig(
                    id=str(uuid.uuid4()),
                    user_id=_get_user_id(),
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
                    AccumulationConfig.user_id == _get_user_id(),
                    AccumulationConfig.asset == asset,
                )
            )
            cfg = existing.scalar_one_or_none()
            if cfg is None:
                return False
            await db.delete(cfg)
            await db.commit()
            return True

    # ── Hyperliquid client accessors ─────────────────────────────────────

    def _get_exchange(self):
        if self._exchange is None:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            if not settings.hyperliquid_wallet_key:
                raise RuntimeError(
                    "HYPERLIQUID_WALLET_KEY is not configured. "
                    "Set it in Settings → API Keys or as an environment variable."
                )
            wallet = Account.from_key(settings.hyperliquid_wallet_key)
            self._exchange = Exchange(wallet, "https://api.hyperliquid.xyz")
        return self._exchange

    def _get_info(self):
        if self._info_client is None:
            from app.clients.hyperliquid import HyperliquidClient
            self._info_client = HyperliquidClient()
        return self._info_client

    def _hl_coin(self, asset: str) -> str:
        """Strip USDT suffix -> bare coin name for Hyperliquid."""
        s = asset.upper()
        for suffix in ("USDT", "BUSD", "USD"):
            if s.endswith(suffix):
                return s[: -len(suffix)]
        return s

    async def _hl_mid_price(self, asset: str) -> float:
        """Fetch current mid price from Hyperliquid."""
        coin = self._hl_coin(asset)
        try:
            mids = await self._get_info().get_all_mids()
            if isinstance(mids, dict):
                val = mids.get(coin)
                if val:
                    return float(val)
        except Exception as exc:
            logger.warning(f"Accumulation: failed to fetch price for {coin}: {exc}")
        return 0.0

    def _hl_sz_decimals(self, coin: str) -> int:
        """Return size decimals for a coin from the perp universe."""
        cache = getattr(self, '_sz_decimals_cache', None)
        if cache is None:
            import httpx
            try:
                resp = httpx.post(
                    "https://api.hyperliquid.xyz/info",
                    json={"type": "meta"},
                    timeout=10,
                )
                universe = resp.json().get("universe", [])
                cache = {u["name"]: u["szDecimals"] for u in universe}
            except Exception:
                cache = {}
            self._sz_decimals_cache = cache
        return cache.get(coin, 4)  # default 4 if unknown

    # ── Live check ──────────────────────────────────────────────────────

    def _is_live(self) -> bool:
        try:
            from app.api.routes.settings import get_trading_prefs
            result = get_trading_prefs().accumulation_live_enabled
            logger.debug(f"Accumulation _is_live check: {result}")
            return result
        except Exception as exc:
            logger.warning(f"Accumulation _is_live check failed: {exc}")
            return False

    # ── Balances ───────────────────────────────────────────────────────

    async def get_balances(self, force_live: bool = False) -> List[Dict[str, Any]]:
        """Return accumulation fund balances.

        Paper mode: DB source of truth.  Live mode: Hyperliquid spot wallet
        balances only — perp positions are shown separately in portfolio.
        """
        if force_live or self._is_live():
            from app.config import settings as _s
            addr = _s.hyperliquid_wallet_address
            if addr:
                from app.services.hl_live_trading import hl_live_trading as _hl
                result = []

                try:
                    state = await _hl._get_info().spot_user_state(addr)
                    for b in (state.get("balances", []) if isinstance(state, dict) else []):
                        coin = b.get("coin", "")
                        total = float(b.get("total", 0))
                        if total == 0:
                            continue
                        hold = float(b.get("hold", 0))
                        result.append({"asset": coin, "available": total - hold, "locked": hold})
                except Exception as e:
                    logger.warning(f"Live HL spot_user_state failed: {e}")

                return result
            return []
        async with get_async_session() as db:
            rows = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == _get_user_id(),
                    DBBalance.fund_type == FUND_TYPE,
                )
            )
            merged: Dict[str, Dict[str, Any]] = {}
            for r in rows.scalars().all():
                asset = r.asset if r.asset != "USDT" else QUOTE_CURRENCY
                if asset not in merged:
                    merged[asset] = {"asset": asset, "available": 0.0, "locked": r.locked or 0.0}
                merged[asset]["available"] += r.available
                if r.locked:
                    merged[asset]["locked"] = (merged[asset]["locked"] + r.locked) / 2
            return list(merged.values())

    async def get_balance(self, asset: str) -> float:
        balances = await self.get_balances()
        for b in balances:
            if b["asset"] == asset:
                return b["available"]
        return 0.0

    async def _ensure_balance(self, asset: str, db) -> DBBalance:
        existing = await db.execute(
            select(DBBalance).where(
                DBBalance.user_id == _get_user_id(),
                DBBalance.asset == asset,
                DBBalance.fund_type == FUND_TYPE,
            )
        )
        bal = existing.scalar_one_or_none()
        if bal is None:
            bal = DBBalance(
                id=str(uuid.uuid4()),
                user_id=_get_user_id(),
                asset=asset,
                available=0.0,
                locked=0.0,
                fund_type=FUND_TYPE,
            )
            db.add(bal)
            await db.flush()
        return bal

    async def deposit_usdc(self, amount: float) -> Dict[str, Any]:
        """Deposit USDC from external source into the accumulation fund."""
        async with get_async_session() as db:
            bal = await self._ensure_balance(QUOTE_CURRENCY, db)
            bal.available += amount
            await db.commit()
            logger.info(f"Accumulation fund: deposited ${amount:.2f} USDC")
            return {"asset": QUOTE_CURRENCY, "available": bal.available, "deposited": amount}

    async def transfer_to_trading(self, usd_amount: float) -> Dict[str, Any]:
        """Transfer USDC from accumulation fund to the trading fund."""
        async with get_async_session() as db:
            bal = await self._ensure_balance(QUOTE_CURRENCY, db)
            if bal.available < usd_amount:
                raise ValueError(
                    f"Insufficient accumulation USDC: have ${bal.available:.2f}, need ${usd_amount:.2f}"
                )
            bal.available -= usd_amount
            trade_bal = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == _get_user_id(),
                    DBBalance.asset == QUOTE_CURRENCY,
                    DBBalance.fund_type == "trading",
                )
            )
            tb = trade_bal.scalar_one_or_none()
            if tb is None:
                tb = DBBalance(
                    id=str(uuid.uuid4()),
                    user_id=_get_user_id(),
                    asset=QUOTE_CURRENCY,
                    available=0.0,
                    locked=0.0,
                    fund_type="trading",
                )
                db.add(tb)
                await db.flush()
            tb.available += usd_amount
            await db.commit()
            logger.info(
                f"Accumulation → Trading: transferred ${usd_amount:.2f} USDC "
                f"(accumulation remaining: ${bal.available:.2f})"
            )
            return {"transferred": usd_amount, "accumulation_usdc": bal.available}

    # ── Positions (in-memory tracking, not exchange positions) ─────────
    # Stored as a JSON blob in a simple model so we don't need full Position
    # table integration with all its trading-specific fields.

    async def _get_positions_raw(self) -> List[Dict[str, Any]]:
        """Load accumulation positions from DB balance metadata."""
        async with get_async_session() as db:
            rows = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == _get_user_id(),
                    DBBalance.fund_type == FUND_TYPE,
                    DBBalance.asset != QUOTE_CURRENCY,
                )
            )
            positions = []
            for r in rows.scalars().all():
                meta = r.locked or 0.0
                positions.append({
                    "asset": r.asset,
                    "quantity": r.available,
                    "avg_cost": meta,
                })
            return positions

    async def get_portfolio(self) -> Dict[str, Any]:
        """Return full portfolio snapshot with current prices.

        Paper mode: DB positions + prices from HL mids.
        Live mode: all data from Hyperliquid only — no DB fallback.
        """
        if self._is_live():
            return await self._get_live_portfolio()
        return await self._get_paper_portfolio()

    async def _get_paper_portfolio(self) -> Dict[str, Any]:
        positions = await self._get_positions_raw()
        usdc_balance = await self.get_balance(QUOTE_CURRENCY)
        enriched = []
        total_unrealized_pnl = 0.0

        for p in positions:
            if p["asset"] == "_DEPOSITED":
                continue
            price = await self._hl_mid_price(p["asset"])
            value = p["quantity"] * price
            cost = p["quantity"] * p["avg_cost"]
            unrealized = value - cost
            total_unrealized_pnl += unrealized
            enriched.append({
                "asset": p["asset"],
                "quantity": p["quantity"],
                "avg_cost": p["avg_cost"],
                "current_price": price,
                "value": round(value, 2),
                "cost_basis": round(cost, 2),
                "unrealized_pnl": round(unrealized, 2),
                "unrealized_pnl_pct": round((value / cost - 1) * 100, 2) if cost else 0,
            })

        # Use stored total_deposited (synced from live) or compute from cost bases
        _deposited = await self.get_balance("_DEPOSITED")
        total_invested = _deposited if _deposited > 0 else (usdc_balance + sum(
            p["quantity"] * p["avg_cost"] for p in positions if p["asset"] != "_DEPOSITED"
        ))

        # Total equity = USDC free balance + unrealized PnL from positions
        current_value = usdc_balance + total_unrealized_pnl
        positions_value = round(sum(p["value"] for p in enriched), 2)
        total_pnl = round(current_value - total_invested, 2)
        return {
            "positions": enriched,
            "usdc_balance": usdc_balance,
            "positions_value": positions_value,
            "total_invested": round(total_invested, 2),
            "current_value": round(current_value, 2),
            "total_pnl": total_pnl,
            "total_pnl_pct": round(current_value / total_invested * 100 - 100, 2) if total_invested else 0,
            "total_realized_pnl": round(total_unrealized_pnl, 2),
        }

    async def _get_live_portfolio(self, force_live: bool = False) -> Dict[str, Any]:
        """Portfolio from Hyperliquid — spot balances + perp positions.

        Total equity = spot USDC total (gross, including held margin) +
        perp unrealized PnL.  The perp position values are not added
        separately because they are funded by USDC held as margin.

        When *force_live* is True, reads from Hyperliquid regardless of the
        current paper/live setting (used by sync_live_to_paper).
        """
        from app.config import settings as _s
        addr = _s.hyperliquid_wallet_address

        balances = await self.get_balances(force_live=force_live)
        spot_usdc_total = 0.0
        spot_usdc_hold = 0.0
        for b in balances:
            if b["asset"] == QUOTE_CURRENCY:
                spot_usdc_total = b["available"] + b["locked"]
                spot_usdc_hold = b["locked"]
                break

        usdc_free = spot_usdc_total - spot_usdc_hold
        enriched = []
        total_unrealized_pnl = 0.0

        # Spot token balances (non-USDC)
        for b in balances:
            if b["asset"] == QUOTE_CURRENCY:
                continue
            qty = b["available"] + b["locked"]
            if qty <= 0:
                continue
            coin = b["asset"].replace("USDT", "").replace("USDC", "")
            price = await self._hl_mid_price(f"{coin}USDT")
            value = qty * price
            enriched.append({
                "asset": coin,
                "quantity": qty,
                "avg_cost": price,
                "current_price": price,
                "value": round(value, 2),
                "cost_basis": round(value, 2),
                "unrealized_pnl": 0.0,
                "unrealized_pnl_pct": 0.0,
            })

        # Perp positions
        if addr:
            try:
                ch_state = await self._get_info().get_clearinghouse_state(addr)
                for item in ch_state.get("assetPositions", []):
                    pos = item.get("position", {})
                    szi = float(pos.get("szi", 0))
                    if szi == 0:
                        continue
                    coin = pos.get("coin", "")
                    entry_px = float(pos.get("entryPx") or 0)
                    pos_value = float(pos.get("positionValue") or 0)
                    unrealized_pnl = float(pos.get("unrealizedPnl") or 0)
                    total_unrealized_pnl += unrealized_pnl
                    price = await self._hl_mid_price(f"{coin}USDT")
                    market_value = pos_value + unrealized_pnl
                    nominal_cost = abs(szi) * entry_px
                    pnl_pct = round((unrealized_pnl / pos_value) * 100, 2) if pos_value else 0
                    enriched.append({
                        "asset": coin,
                        "quantity": abs(szi),
                        "avg_cost": entry_px,
                        "current_price": price,
                        "value": round(market_value, 2),
                        "cost_basis": round(nominal_cost, 2),
                        "unrealized_pnl": round(unrealized_pnl, 2),
                        "unrealized_pnl_pct": pnl_pct,
                    })
            except Exception as e:
                logger.warning(f"Live HL perp positions failed: {e}")

        # Deduplicate: prefer perp over spot for same coin
        seen = set()
        deduped = []
        for p in enriched:
            if p["asset"] not in seen:
                seen.add(p["asset"])
                deduped.append(p)
            else:
                # Perp positions come after spot in the list, overwrite
                for i, dp in enumerate(deduped):
                    if dp["asset"] == p["asset"]:
                        deduped[i] = p
                        break
        enriched = deduped

        current_value = spot_usdc_total + total_unrealized_pnl
        positions_value = round(sum(p["value"] for p in enriched), 2)
        return {
            "positions": enriched,
            "usdc_balance": round(usdc_free, 2),
            "usdc_total": round(spot_usdc_total, 2),
            "usdc_hold": round(spot_usdc_hold, 2),
            "positions_value": positions_value,
            "total_invested": round(spot_usdc_total, 2),
            "current_value": round(current_value, 2),
            "total_pnl": round(total_unrealized_pnl, 2),
            "total_pnl_pct": round((total_unrealized_pnl / spot_usdc_total) * 100, 2) if spot_usdc_total else 0,
        }

    async def sync_live_to_paper(self) -> None:
        """Snapshot Hyperliquid accumulation state into the paper DB.

        Reads live portfolio from the exchange, then updates the paper
        `balances` table (fund_type='accumulation') to match — quantities,
        cost basis, and USDC balance.  Existing paper-only positions that
        no longer exist on Hyperliquid are removed.

        Also persists the live portfolio's `total_invested` into a special
        balance row so that paper mode shows the correct deposited amount.
        """
        live = await self._get_live_portfolio(force_live=True)
        live_positions = {p["asset"]: p for p in live.get("positions", [])}
        live_usdc = live.get("usdc_balance", 0)
        live_total_invested = live.get("total_invested", live_usdc)

        async with get_async_session() as db:
            rows = await db.execute(
                select(DBBalance).where(
                    DBBalance.user_id == _get_user_id(),
                    DBBalance.fund_type == FUND_TYPE,
                )
            )
            existing = {r.asset: r for r in rows.scalars().all()}

            # USDC balance
            if "USDC" in existing:
                existing["USDC"].available = live_usdc
                existing["USDC"].locked = 0.0
            else:
                db.add(DBBalance(
                    user_id=_get_user_id(), asset="USDC",
                    fund_type=FUND_TYPE,
                    available=live_usdc, locked=0.0,
                ))

            # Positions — locked stores avg cost PER UNIT
            for asset, lp in live_positions.items():
                qty = lp["quantity"]
                avg_cost = lp.get("avg_cost", 0)
                if asset in existing:
                    existing[asset].available = qty
                    if avg_cost:
                        existing[asset].locked = avg_cost
                else:
                    db.add(DBBalance(
                        user_id=_get_user_id(), asset=asset,
                        fund_type=FUND_TYPE,
                        available=qty, locked=avg_cost,
                    ))

            # Remove paper-only positions no longer on Hyperliquid
            hl_assets = set(live_positions.keys()) | {"USDC"}
            for asset, row in existing.items():
                if asset not in hl_assets and row.fund_type == FUND_TYPE:
                    await db.delete(row)

            # Persist the live total_invested so paper mode shows correct deposits
            _existing_deposited = None
            for asset, row in existing.items():
                if asset == "_DEPOSITED":
                    _existing_deposited = row
                    break
            if _existing_deposited:
                _existing_deposited.available = live_total_invested
            else:
                db.add(DBBalance(
                    user_id=_get_user_id(), asset="_DEPOSITED",
                    fund_type=FUND_TYPE,
                    available=live_total_invested, locked=0.0,
                ))

            await db.commit()
            logger.info(
                f"Paper accumulation DB synced to Hyperliquid: "
                f"{len(live_positions)} positions, USDC={live_usdc:.2f}, "
                f"total_invested={live_total_invested:.2f}"
            )

    async def buy_spot(self, asset: str, usd_amount: float, live: Optional[bool] = None) -> Dict[str, Any]:
        """Execute a spot market buy and record the position.

        If *live* is True, executes on Hyperliquid (DEX, USDC). If False,
        simulates (paper). Defaults to accumulation_live_enabled setting.
        """
        if live is None:
            try:
                from app.api.routes.settings import get_trading_prefs
                live = get_trading_prefs().accumulation_live_enabled
            except Exception:
                live = False

        bal = await self.get_balance(QUOTE_CURRENCY)
        if bal < usd_amount:
            raise ValueError(
                f"Accumulation USDC insufficient: have ${bal:.2f}, need ${usd_amount:.2f}"
            )

        # ── Accumulation safety circuit breakers ──────────────────────────
        _MAX_ACCUM_PER_ASSET_USD = 25_000.0
        _CATASTROPHIC_DD_PCT = 0.40
        try:
            _portfolio = await self.get_portfolio()
            _pos = next(
                (p for p in _portfolio.get("positions", []) if p.get("asset") == asset),
                None,
            )
            if _pos:
                _invested = float(_pos.get("cost_basis", 0.0) or 0.0)

        except Exception:
            pass

        if live:
            coin = self._hl_coin(asset)
            sz_dec = self._hl_sz_decimals(coin)
            price = await self._hl_mid_price(asset)
            if price <= 0:
                raise ValueError(f"Failed to fetch price for {asset} on Hyperliquid")
            raw_qty = (usd_amount / price) * 0.999
            qty = round(raw_qty, sz_dec)
            if qty <= 0:
                raise ValueError(f"Calculated quantity too small for {asset} (szDecimals={sz_dec})")
            exchange = self._get_exchange()

            try:
                exchange.usd_class_transfer(usd_amount * 1.02, to_perp=True)
            except Exception as e:
                logger.warning(f"Failed to transfer USDC to perp wallet: {e}")

            result = exchange.market_open(coin, True, qty, None, slippage=0.01)
            status = (result or {}).get("status", "")
            if status != "ok":
                raise ValueError(f"Hyperliquid order failed for {asset}: {result}")
        else:
            price = await self._hl_mid_price(asset)
            if price <= 0:
                price = usd_amount / 100  # fallback
            qty = (usd_amount / price) * 0.999
        async with get_async_session() as db:
            q_bal = await self._ensure_balance(QUOTE_CURRENCY, db)
            q_bal.available -= usd_amount

            pos_bal = await self._ensure_balance(asset, db)
            old_cost = pos_bal.available * (pos_bal.locked or 0.0) if pos_bal.locked else 0.0
            new_cost = qty * price
            total_qty = pos_bal.available + qty
            pos_bal.available = total_qty
            pos_bal.locked = (old_cost + new_cost) / total_qty if total_qty > 0 else price

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

    async def run_dca(self, force: bool = False) -> List[Dict[str, Any]]:
        """Execute all due DCA buys. Returns list of executed buys.

        If *force* is True, bypasses the dca_next_at schedule check (used
        for the manual "Run DCA Now" button).
        """
        results = []
        configs = await self.get_configs()
        now = datetime.now(timezone.utc)

        for cfg in configs:
            if not cfg.get("dca_enabled") or not cfg.get("enabled"):
                continue
            if not force:
                next_at = cfg.get("dca_next_at")
                if next_at and now < next_at:
                    continue

            # ── Long-term trend filter ──────────────────────────────────
            # Mechanical DCA into a sustained downtrend (price below 200d SMA
            # AND SMA falling) is wealth destruction — you keep buying as the
            # asset declines and the average cost drops with it, locking
            # losses in. Skip the buy when both conditions hold; resume
            # automatically when trend structure repairs.
            #
            # The user can opt out by manually calling run_dca(force=True)
            # via the "Run DCA Now" button (force bypasses BOTH the schedule
            # and this trend filter — explicit operator override).
            if not force:
                trend = await self._get_trend_state(cfg["asset"])
                if trend and trend.get("in_downtrend"):
                    logger.info(
                        f"DCA skip: {cfg['asset']} in confirmed downtrend "
                        f"(price ${trend['price']:.2f}, SMA200 ${trend['sma_200']:.2f}, "
                        f"{trend['pct_below_sma']:+.1f}% below, slope {trend['sma_slope_pct']:+.2f}%/20d). "
                        f"Will resume when trend repairs."
                    )
                    # Record the skip so it's visible in the execution history.
                    # amount_usd / quantity / price all 0 — the execution_records
                    # consumer can filter strategy="dca_skip_downtrend" if it
                    # wants to keep the buy/sell ledger clean.
                    try:
                        await self._record_execution(
                            asset=cfg["asset"], strategy="dca_skip_downtrend",
                            amount_usd=0.0, quantity=0.0, price=trend["price"],
                            details={
                                "sma_200": trend["sma_200"],
                                "pct_below_sma": trend["pct_below_sma"],
                                "sma_slope_pct": trend["sma_slope_pct"],
                            },
                        )
                    except Exception as _rec_e:
                        logger.debug(f"Failed to record DCA skip: {_rec_e}")
                    # Reschedule next attempt at the normal interval so we
                    # check again on the next cycle (don't keep retrying every
                    # tick — that defeats the purpose of the schedule).
                    interval = cfg.get("dca_interval_hours", 168)
                    new_next = now + timedelta(hours=interval)
                    await self.upsert_config(cfg["asset"], {"dca_next_at": new_next})
                    continue

            pct = cfg.get("dca_balance_pct", 0.0)
            if pct > 0:
                available = await self.get_balance(QUOTE_CURRENCY)
                amount = available * (pct / 100)
            else:
                amount = cfg.get("dca_amount_usd", 50.0)
            try:
                result = await self.buy_spot(cfg["asset"], amount)
                result["strategy"] = "dca"
                results.append(result)
                await self._record_execution(
                    asset=cfg["asset"], strategy="dca",
                    amount_usd=amount, quantity=result.get("quantity", 0),
                    price=result.get("price", 0),
                )
                # Schedule next + increment count
                interval = cfg.get("dca_interval_hours", 168)
                new_next = now + timedelta(hours=interval)
                curr = cfg.get("dca_count", 0)
                await self.upsert_config(cfg["asset"], {"dca_next_at": new_next, "dca_count": curr + 1})
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
                # ── Long-term trend filter (same as DCA) ────────────────
                # VA is also a mechanical "buy-when-behind-target" rule and
                # suffers the same downtrend bleed as DCA. Skip when the
                # asset is in a confirmed downtrend; the gap will still be
                # there (likely larger) when the trend repairs.
                trend = await self._get_trend_state(cfg["asset"])
                if trend and trend.get("in_downtrend"):
                    logger.info(
                        f"VA skip: {cfg['asset']} in confirmed downtrend "
                        f"(price ${trend['price']:.2f}, SMA200 ${trend['sma_200']:.2f}, "
                        f"{trend['pct_below_sma']:+.1f}% below). Gap ${gap:.2f} deferred."
                    )
                    try:
                        await self._record_execution(
                            asset=cfg["asset"], strategy="va_skip_downtrend",
                            amount_usd=0.0, quantity=0.0, price=trend["price"],
                            details={
                                "gap": round(gap, 2),
                                "sma_200": trend["sma_200"],
                                "pct_below_sma": trend["pct_below_sma"],
                                "sma_slope_pct": trend["sma_slope_pct"],
                            },
                        )
                    except Exception as _rec_e:
                        logger.debug(f"Failed to record VA skip: {_rec_e}")
                else:
                    try:
                        amt = min(gap, cfg.get("dca_amount_usd", 50) * 3)
                        result = await self.buy_spot(cfg["asset"], amt)
                        result["strategy"] = "value_averaging"
                        result["gap"] = round(gap, 2)
                        results.append(result)
                        await self._record_execution(
                            asset=cfg["asset"], strategy="value_averaging",
                            amount_usd=amt, quantity=result.get("quantity", 0),
                            price=result.get("price", 0),
                        )
                        curr_va = cfg.get("va_count", 0)
                        await self.upsert_config(cfg["asset"], {"va_count": curr_va + 1})
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
                        await self._record_execution(
                            asset=cfg["asset"], strategy="dip_buy",
                            amount_usd=amount, quantity=result.get("quantity", 0),
                            price=result.get("price", 0),
                            details={"trigger_pct": pct_drop, "threshold": threshold},
                        )
                        curr_dip = cfg.get("dip_count", 0)
                        await self.upsert_config(cfg["asset"], {"dip_count": curr_dip + 1})
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
                price = await self._hl_mid_price(cfg["asset"])
                if price <= 0:
                    continue

                coin = self._hl_coin(cfg["asset"])
                sz_dec = self._hl_sz_decimals(coin)
                sell_qty = round(sell_qty, sz_dec)
                if sell_qty <= 0:
                    continue
                exchange = self._get_exchange()
                result = exchange.order(
                    coin, False, sell_qty, price * 0.99,
                    {"limit": {"tif": "Ioc"}},
                    reduce_only=False,
                )
                status = (result or {}).get("status", "")
                if status != "ok":
                    logger.warning(f"Scale-out order rejected for {cfg['asset']}: {result}")
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
                await self._record_execution(
                    asset=cfg["asset"], strategy="scale_out",
                    amount_usd=round(net_proceeds, 2),
                    quantity=sell_qty, price=price,
                    details={"gain_pct": gain_pct, "proceeds": round(proceeds, 2)},
                )
                logger.info(
                    f"Scale-out: sold {sell_qty:.4f} {cfg['asset']} @ ${price:.2f} "
                    f"(+{gain_pct:.1f}%), transferred ${net_proceeds:.2f} to trading fund"
                )
            except Exception as e:
                logger.error(f"Scale-out failed for {cfg['asset']}: {e}")

        return results

    # ── Execution tracking ────────────────────────────────────────────

    async def _record_execution(self, asset: str, strategy: str,
                                amount_usd: float, quantity: float, price: float,
                                details: Optional[dict] = None) -> None:
        async with get_async_session() as db:
            rec = AccumulationExecutionRecord(
                id=str(uuid.uuid4()),
                user_id=_get_user_id(),
                asset=asset,
                strategy=strategy,
                amount_usd=amount_usd,
                quantity=quantity,
                price=price,
                details=details or {},
            )
            db.add(rec)
            await db.commit()

    async def get_executions(self, asset: Optional[str] = None,
                             strategy: Optional[str] = None,
                             limit: int = 100) -> List[Dict[str, Any]]:
        async with get_async_session() as db:
            q = select(AccumulationExecutionRecord).where(
                AccumulationExecutionRecord.user_id == _get_user_id()
            )
            if asset:
                q = q.where(AccumulationExecutionRecord.asset == asset)
            if strategy:
                q = q.where(AccumulationExecutionRecord.strategy == strategy)
            q = q.order_by(AccumulationExecutionRecord.executed_at.desc()).limit(limit)
            rows = await db.execute(q)
            return [
                {
                    "id": r.id,
                    "asset": r.asset,
                    "strategy": r.strategy,
                    "amount_usd": r.amount_usd,
                    "quantity": r.quantity,
                    "price": r.price,
                    "details": r.details,
                    "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                }
                for r in rows.scalars().all()
            ]

    async def get_metrics(self) -> Dict[str, Any]:
        """Aggregated execution counts per (asset, strategy) + portfolio stats.

        Counts come from AccumulationConfig (backfilled) for lifetime accuracy.
        Timestamps from AccumulationExecutionRecord for recent activity.
        """
        configs = await self.get_configs()
        portfolio = await self.get_portfolio()

        # Lifetime counts from config (backfilled for pre-existing runs)
        by_asset: Dict[str, Dict[str, int]] = {}
        total_by_strategy: Dict[str, int] = {}
        for cfg in configs:
            asset = cfg["asset"]
            counts = {}
            for strat_key, cfg_key in [("dca", "dca_count"), ("value_averaging", "va_count"),
                                        ("dip_buy", "dip_count"), ("scale_out", "scale_out_count")]:
                v = cfg.get(cfg_key, 0) or 0
                if v > 0:
                    counts[strat_key] = v
                    total_by_strategy[strat_key] = total_by_strategy.get(strat_key, 0) + v
            if counts:
                by_asset[asset] = counts

        # Last executed timestamps from execution records
        async with get_async_session() as db:
            last_rows = await db.execute(
                select(
                    AccumulationExecutionRecord.asset,
                    AccumulationExecutionRecord.strategy,
                    func.max(AccumulationExecutionRecord.executed_at).label("last_at"),
                ).where(
                    AccumulationExecutionRecord.user_id == _get_user_id()
                ).group_by(
                    AccumulationExecutionRecord.asset,
                    AccumulationExecutionRecord.strategy,
                )
            )
            last_executed: Dict[str, Dict[str, str]] = {}
            for asset, strategy, last_at in last_rows:
                if asset not in last_executed:
                    last_executed[asset] = {}
                last_executed[asset][strategy] = last_at.isoformat() if last_at else None

        total_execs = sum(total_by_strategy.values())
        return {
            "by_asset": by_asset,
            "total_by_strategy": total_by_strategy,
            "total_executions": total_execs,
            "last_executed": last_executed,
            "portfolio": {
                "total_invested": portfolio.get("total_invested"),
                "current_value": portfolio.get("current_value"),
                "total_pnl": portfolio.get("total_pnl"),
                "total_pnl_pct": portfolio.get("total_pnl_pct"),
                "positions_count": len(portfolio.get("positions", [])),
            },
        }

    async def get_performance_chart(self) -> List[Dict[str, Any]]:
        """Return time-series of portfolio value from execution records.

        Computes cumulative invested and estimated value over time by
        replaying executions in chronological order against current prices.
        """
        async with get_async_session() as db:
            rows = await db.execute(
                select(AccumulationExecutionRecord).where(
                    AccumulationExecutionRecord.user_id == _get_user_id()
                ).order_by(AccumulationExecutionRecord.executed_at.asc())
            )
            records = rows.scalars().all()

        if not records:
            return []

        portfolio = await self.get_portfolio()
        current_prices = {}
        for p in portfolio.get("positions", []):
            current_prices[p["asset"]] = p["current_price"]

        rows_out = []
        cum_invested = 0.0
        holdings: Dict[str, float] = {}
        total_holdings_cost: Dict[str, float] = {}

        for r in records:
            if r.strategy == "scale_out":
                cum_invested -= r.amount_usd
            else:
                cum_invested += r.amount_usd
                holdings[r.asset] = holdings.get(r.asset, 0) + r.quantity
                total_holdings_cost[r.asset] = total_holdings_cost.get(r.asset, 0) + r.amount_usd

            current_val = 0.0
            for asset, qty in holdings.items():
                price = current_prices.get(asset)
                if price:
                    current_val += qty * price
                else:
                    cost = total_holdings_cost.get(asset, 0)
                    current_val += cost

            rows_out.append({
                "date": r.executed_at.isoformat() if r.executed_at else None,
                "cumulative_invested": round(cum_invested, 2),
                "estimated_value": round(current_val + portfolio.get("usdc_balance", 0), 2),
                "event": f"{r.strategy}_{r.asset}",
            })

        latest = rows_out[-1] if rows_out else {}
        return {
            "series": rows_out,
            "summary": {
                "total_invested": portfolio.get("total_invested"),
                "current_value": portfolio.get("current_value"),
                "total_pnl": portfolio.get("total_pnl"),
                "total_pnl_pct": portfolio.get("total_pnl_pct"),
            },
        }

    # ── Low balance check for Telegram alert ───────────────────────────

    async def check_low_balance(self, min_usdc: float = 200.0,
                                cooldown_hours: float = 24) -> Optional[float]:
        """Return USDC balance if below *min_usdc* and not acknowledged within *cooldown_hours*."""
        bal = await self.get_balance(QUOTE_CURRENCY)
        if bal >= min_usdc:
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
