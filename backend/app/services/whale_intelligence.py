"""
Whale Intelligence Service — tracks Hyperliquid whale accounts and aggregates
positioning data as market research for the TA and Trader LLM prompts.

Cache strategy:
  - 60-second TTL in-memory cache (respects HL rate limits)
  - asyncio.Lock prevents thundering herd on simultaneous callers
  - asyncio.Semaphore(10) caps concurrent HTTP requests as watchlist grows
  - Graceful degradation: individual address failures don't block the report
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
SIGNIFICANT_MOVE_NOTIONAL_USD = 500_000  # threshold for team chat alerts

# No default addresses — users must add real Hyperliquid addresses via the watchlist.
# Find high-value wallets at https://app.hyperliquid.xyz/leaderboard
# Then add via POST /api/whale/watchlist or the Whale Intelligence panel watchlist drawer.
DEFAULT_WHALE_ADDRESSES: List[Dict[str, str]] = []

# Special coin→symbol mappings (Hyperliquid coin names → Phemex-style pair symbols)
_COIN_TO_SYMBOL: Dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "DOGE": "DOGEUSDT",
    "DOT": "DOTUSDT",
    "LINK": "LINKUSDT",
    "MATIC": "MATICUSDT",
    "UNI": "UNIUSDT",
    "ATOM": "ATOMUSDT",
    "LTC": "LTCUSDT",
    "OP": "OPUSDT",
    "ARB": "ARBUSDT",
    "SUI": "SUIUSDT",
    "APT": "APTUSDT",
    "PEPE": "1000PEPEUSDT",
    "WIF": "WIFUSDT",
    "BONK": "1000BONKUSDT",
    "SHIB": "1000SHIBUSDT",
}


@dataclass
class WhalePosition:
    address: str
    label: str
    coin: str
    side: str           # "long" or "short"
    size: float         # size in coin units
    notional_usd: float
    leverage: float
    entry_price: float
    unrealized_pnl: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoinWhaleBias:
    coin: str
    bias: str           # "bullish", "bearish", "neutral"
    long_notional: float
    short_notional: float
    net_notional: float  # positive = net long
    whale_count: int
    avg_leverage: float
    top_positions: List[WhalePosition] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["top_positions"] = [p.to_dict() for p in self.top_positions]
        return d


@dataclass
class WhaleIntelligenceReport:
    timestamp: datetime
    coin_biases: Dict[str, CoinWhaleBias]
    all_positions: List[WhalePosition]
    total_whales_tracked: int
    total_whales_with_positions: int
    fetch_errors: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "coin_biases": {k: v.to_dict() for k, v in self.coin_biases.items()},
            "all_positions": [p.to_dict() for p in self.all_positions],
            "total_whales_tracked": self.total_whales_tracked,
            "total_whales_with_positions": self.total_whales_with_positions,
            "fetch_errors": self.fetch_errors,
        }


class WhaleIntelligenceService:
    """Aggregates Hyperliquid whale positioning data for market research."""

    def __init__(self) -> None:
        from app.clients.hyperliquid import HyperliquidClient
        self._client = HyperliquidClient()
        self._cache: Optional[WhaleIntelligenceReport] = None
        self._cache_ts: Optional[datetime] = None
        self._lock = asyncio.Lock()
        # In-memory watchlist (populated from DB or defaults on first call)
        self._watchlist: Optional[List[Dict[str, str]]] = None

    def _is_cache_fresh(self) -> bool:
        if self._cache is None or self._cache_ts is None:
            return False
        return (datetime.now(timezone.utc) - self._cache_ts).total_seconds() < CACHE_TTL_SECONDS

    def coin_to_symbol(self, coin: str) -> str:
        """Convert Hyperliquid coin name to Phemex-style symbol."""
        return _COIN_TO_SYMBOL.get(coin, f"{coin}USDT")

    def symbol_to_coin(self, symbol: str) -> str:
        """Convert Phemex-style symbol to Hyperliquid coin name."""
        # Reverse lookup
        for coin, sym in _COIN_TO_SYMBOL.items():
            if sym == symbol:
                return coin
        # Fallback: strip USDT
        return symbol.replace("USDT", "").replace("USD", "")

    async def _load_watchlist(self, db=None) -> List[Dict[str, str]]:
        """Load active whale addresses from DB, fall back to defaults.

        If no db session is provided, opens a short-lived one from the
        app's async session factory so background callers (scheduler,
        fund-manager, risk-manager) always read from the DB rather than
        falling through to the empty DEFAULT_WHALE_ADDRESSES list.
        """
        if self._watchlist is not None:
            return self._watchlist

        async def _query_db(session) -> bool:
            try:
                from sqlalchemy import select
                from app.models import WhaleAddress
                result = await session.execute(
                    select(WhaleAddress).where(WhaleAddress.is_active == True)
                )
                rows = result.scalars().all()
                if rows:
                    self._watchlist = [
                        {"address": r.address, "label": r.label or r.address[:10] + "…"}
                        for r in rows
                    ]
                    return True
                return False
            except Exception as e:
                logger.debug(f"Failed to load whale watchlist from DB: {e}")
                return False

        if db is not None:
            if await _query_db(db):
                return self._watchlist

        # No db session provided — open one ourselves
        try:
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                await _query_db(session)
        except Exception as e:
            logger.debug(f"Could not open DB session for whale watchlist: {e}")

        if not self._watchlist:
            self._watchlist = DEFAULT_WHALE_ADDRESSES
        return self._watchlist

    def invalidate_watchlist_cache(self) -> None:
        """Force reload of watchlist from DB on next fetch."""
        self._watchlist = None

    async def fetch_whale_report(self, db=None) -> Optional[WhaleIntelligenceReport]:
        """
        Returns the current whale intelligence report, using cache when fresh.
        Thread-safe: uses asyncio.Lock to prevent thundering herd.
        Returns None on complete failure (callers degrade gracefully).
        """
        if self._is_cache_fresh():
            return self._cache

        async with self._lock:
            # Double-check after acquiring lock
            if self._is_cache_fresh():
                return self._cache

            try:
                addresses = await self._load_watchlist(db)
                report = await self._fetch_fresh_report(addresses)
                self._cache = report
                self._cache_ts = datetime.now(timezone.utc)
                return report
            except Exception as e:
                logger.warning(f"Whale intelligence fetch failed: {e}")
                return self._cache  # return stale cache on failure if available

    async def _fetch_fresh_report(self, addresses: List[Dict[str, str]]) -> WhaleIntelligenceReport:
        """Concurrently fetch clearinghouse state for all addresses."""
        semaphore = asyncio.Semaphore(10)

        async def fetch_one(entry: Dict[str, str]) -> Optional[List[WhalePosition]]:
            async with semaphore:
                try:
                    state = await self._client.get_clearinghouse_state(entry["address"])
                    return self._parse_hl_positions(state, entry["address"], entry.get("label", ""))
                except Exception as e:
                    logger.debug(f"Whale fetch failed for {entry['address'][:10]}…: {e}")
                    return None

        results = await asyncio.gather(*[fetch_one(a) for a in addresses], return_exceptions=True)

        all_positions: List[WhalePosition] = []
        errors = 0
        whales_with_positions = 0

        for r in results:
            if isinstance(r, Exception) or r is None:
                errors += 1
            else:
                positions = [p for p in r if p is not None]
                if positions:
                    whales_with_positions += 1
                    all_positions.extend(positions)

        coin_biases = self._aggregate_biases(all_positions)

        return WhaleIntelligenceReport(
            timestamp=datetime.now(timezone.utc),
            coin_biases=coin_biases,
            all_positions=all_positions,
            total_whales_tracked=len(addresses),
            total_whales_with_positions=whales_with_positions,
            fetch_errors=errors,
        )

    def _parse_hl_positions(
        self, state: Dict[str, Any], address: str, label: str
    ) -> List[WhalePosition]:
        """
        Parse Hyperliquid clearinghouseState into WhalePosition objects.
        HL schema: state["assetPositions"][i]["position"] = {
            coin, szi (negative=short), entryPx, unrealizedPnl,
            leverage: {value, type}, positionValue
        }
        """
        positions: List[WhalePosition] = []
        asset_positions = state.get("assetPositions", [])

        for ap in asset_positions:
            pos = ap.get("position", {})
            try:
                szi = float(pos.get("szi", 0))
                if szi == 0:
                    continue  # no open position

                coin = pos.get("coin", "")
                side = "long" if szi > 0 else "short"
                size = abs(szi)
                entry_px = float(pos.get("entryPx") or 0)
                unrealized_pnl = float(pos.get("unrealizedPnl") or 0)
                position_value = float(pos.get("positionValue") or (size * entry_px))

                leverage_data = pos.get("leverage", {})
                if isinstance(leverage_data, dict):
                    leverage = float(leverage_data.get("value", 1))
                else:
                    leverage = float(leverage_data or 1)

                positions.append(WhalePosition(
                    address=address,
                    label=label or address[:10] + "…",
                    coin=coin,
                    side=side,
                    size=size,
                    notional_usd=abs(position_value),
                    leverage=leverage,
                    entry_price=entry_px,
                    unrealized_pnl=unrealized_pnl,
                ))
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"Failed to parse position for {address[:10]}…: {e}")
                continue

        return positions

    def _aggregate_biases(self, positions: List[WhalePosition]) -> Dict[str, CoinWhaleBias]:
        """Group positions by coin and compute net long/short bias metrics."""
        by_coin: Dict[str, List[WhalePosition]] = {}
        for p in positions:
            by_coin.setdefault(p.coin, []).append(p)

        biases: Dict[str, CoinWhaleBias] = {}
        for coin, coin_positions in by_coin.items():
            long_positions = [p for p in coin_positions if p.side == "long"]
            short_positions = [p for p in coin_positions if p.side == "short"]

            long_notional = sum(p.notional_usd for p in long_positions)
            short_notional = sum(p.notional_usd for p in short_positions)
            net_notional = long_notional - short_notional

            all_notional = long_notional + short_notional
            if all_notional > 0:
                avg_leverage = sum(
                    p.leverage * p.notional_usd for p in coin_positions
                ) / all_notional
            else:
                avg_leverage = 1.0

            # Determine bias: >60% in one direction = directional, else neutral
            if all_notional > 0:
                long_ratio = long_notional / all_notional
                if long_ratio >= 0.6:
                    bias = "bullish"
                elif long_ratio <= 0.4:
                    bias = "bearish"
                else:
                    bias = "neutral"
            else:
                bias = "neutral"

            # Top 3 positions by notional
            top_3 = sorted(coin_positions, key=lambda p: p.notional_usd, reverse=True)[:3]

            biases[coin] = CoinWhaleBias(
                coin=coin,
                bias=bias,
                long_notional=long_notional,
                short_notional=short_notional,
                net_notional=net_notional,
                whale_count=len(coin_positions),
                avg_leverage=round(avg_leverage, 1),
                top_positions=top_3,
            )

        return biases

    def build_ta_observations(
        self, symbol: str, bias: Optional[CoinWhaleBias]
    ) -> List[str]:
        """
        Returns 0-3 observation strings to append to TechnicalAnalystReport.key_observations.
        Returns empty list when no data is available.
        """
        if bias is None:
            return []

        observations: List[str] = []
        total = bias.long_notional + bias.short_notional

        if total < 10_000:  # less than $10K total — not meaningful
            return []

        def fmt_usd(v: float) -> str:
            if v >= 1_000_000:
                return f"${v/1_000_000:.1f}M"
            if v >= 1_000:
                return f"${v/1_000:.0f}K"
            return f"${v:.0f}"

        direction = "NET LONG" if bias.net_notional > 0 else "NET SHORT"
        obs = (
            f"Hyperliquid whale positioning: {bias.whale_count} tracked whale(s) "
            f"{direction} {bias.coin} — {fmt_usd(bias.long_notional)} long vs "
            f"{fmt_usd(bias.short_notional)} short | avg {bias.avg_leverage:.0f}x leverage"
        )

        sentiment_suffix = {
            "bullish": " — smart-money bullish signal",
            "bearish": " — smart-money bearish signal",
            "neutral": " — mixed smart-money positioning",
        }.get(bias.bias, "")
        observations.append(obs + sentiment_suffix)

        # If top whale position is significant, add detail + exit pressure
        if bias.top_positions:
            top = bias.top_positions[0]
            if top.notional_usd >= 100_000:
                pnl_str = (
                    f"+{fmt_usd(top.unrealized_pnl)}" if top.unrealized_pnl >= 0
                    else fmt_usd(top.unrealized_pnl)
                )
                observations.append(
                    f"Largest whale position: {top.side.upper()} {top.coin} "
                    f"{fmt_usd(top.notional_usd)} at {top.entry_price:.4g} "
                    f"({top.leverage:.0f}x, unr. PnL {pnl_str})"
                )
                # Exit pressure: large unrealised gain is potential profit-taking trigger
                if top.notional_usd > 0:
                    pnl_ratio = top.unrealized_pnl / top.notional_usd
                    if pnl_ratio >= 0.25:
                        observations.append(
                            f"⚠️ EXIT PRESSURE: Top whale is up {pnl_ratio:.0%} on this position "
                            f"— significant profit-taking risk. Consider tighter TP targets."
                        )
                    elif pnl_ratio <= -0.20:
                        observations.append(
                            f"📌 TRAPPED WHALE: Top whale is down {abs(pnl_ratio):.0%} "
                            f"— unlikely to flip; acts as demand/supply wall near entry {top.entry_price:.4g}."
                        )

        return observations

    def build_llm_context_block(
        self, report: Optional[WhaleIntelligenceReport]
    ) -> str:
        """
        Returns a formatted block to insert into trader/fund-manager LLM prompts.
        Returns empty string when no data is available (prompt unchanged).
        """
        if report is None or not report.coin_biases:
            return ""

        def fmt_usd(v: float) -> str:
            if v >= 1_000_000:
                return f"${v/1_000_000:.1f}M"
            if v >= 1_000:
                return f"${v/1_000:.0f}K"
            return f"${v:.0f}"

        lines = [
            f"\nWHALE INTELLIGENCE (Hyperliquid on-chain, "
            f"{report.total_whales_tracked} wallets tracked):"
        ]
        sorted_biases = sorted(
            report.coin_biases.values(),
            key=lambda b: b.long_notional + b.short_notional,
            reverse=True,
        )
        for bias in sorted_biases[:8]:  # top 8 coins by activity
            total = bias.long_notional + bias.short_notional
            if total < 10_000:
                continue
            direction = "NET LONG" if bias.net_notional > 0 else "NET SHORT"
            # Add exit pressure flag inline
            pressure_flag = ""
            if bias.top_positions:
                top = bias.top_positions[0]
                if top.notional_usd > 0:
                    pnl_ratio = top.unrealized_pnl / top.notional_usd
                    if pnl_ratio >= 0.25:
                        pressure_flag = " ⚠️ exit-pressure"
                    elif pnl_ratio <= -0.20:
                        pressure_flag = " 📌 trapped"
            lines.append(
                f"  {bias.coin}: {bias.bias.upper()} | {direction} | "
                f"{fmt_usd(bias.long_notional)} long vs {fmt_usd(bias.short_notional)} short | "
                f"{bias.whale_count} whale(s) | avg {bias.avg_leverage:.0f}x{pressure_flag}"
            )

        if len(lines) == 1:
            return ""  # no meaningful data

        return "\n".join(lines)

    def build_exit_pressure_insights(
        self, report: Optional[WhaleIntelligenceReport]
    ) -> str:
        """
        Returns a concise summary of exit-pressure and trapped-whale situations
        for fund manager / portfolio-level prompts. Returns empty string when
        no meaningful data exists.

        Exit pressure: whale sitting on large unrealised gain (≥25% of notional)
          → position may close soon, removing buying/selling pressure.
        Trapped whale: whale deeply underwater (≤-20% of notional)
          → acts as a price wall; position unlikely to flip direction.
        """
        if report is None or not report.all_positions:
            return ""

        exit_pressure: List[str] = []
        trapped: List[str] = []

        def fmt_usd(v: float) -> str:
            if v >= 1_000_000:
                return f"${v/1_000_000:.1f}M"
            if v >= 1_000:
                return f"${v/1_000:.0f}K"
            return f"${v:.0f}"

        # Aggregate per-coin worst-case metrics
        coin_metrics: Dict[str, Dict] = {}
        for p in report.all_positions:
            if p.notional_usd < 50_000:
                continue
            pnl_ratio = p.unrealized_pnl / p.notional_usd if p.notional_usd > 0 else 0
            key = p.coin
            if key not in coin_metrics or abs(pnl_ratio) > abs(coin_metrics[key]["pnl_ratio"]):
                coin_metrics[key] = {
                    "coin": p.coin,
                    "side": p.side,
                    "notional": p.notional_usd,
                    "pnl_ratio": pnl_ratio,
                    "entry": p.entry_price,
                    "label": p.label,
                }

        for coin, m in sorted(coin_metrics.items(), key=lambda x: abs(x[1]["pnl_ratio"]), reverse=True):
            ratio = m["pnl_ratio"]
            if ratio >= 0.25:
                exit_pressure.append(
                    f"  {coin} ({m['side'].upper()}, {fmt_usd(m['notional'])}): "
                    f"+{ratio:.0%} unr. gain — profit-taking likely if price stalls"
                )
            elif ratio <= -0.20:
                trapped.append(
                    f"  {coin} ({m['side'].upper()}, {fmt_usd(m['notional'])}): "
                    f"{ratio:.0%} underwater near {m['entry']:.4g} — acts as price support/resistance"
                )

        if not exit_pressure and not trapped:
            return ""

        parts = ["\nWHALE POSITION MATURITY SIGNALS:"]
        if exit_pressure:
            parts.append("  Exit Pressure (whales in large profit — may close soon):")
            parts.extend(exit_pressure)
        if trapped:
            parts.append("  Trapped Whales (deeply underwater — unlikely to flip):")
            parts.extend(trapped)

        return "\n".join(parts)

    async def check_and_alert_significant_moves(
        self,
        previous_report: WhaleIntelligenceReport,
        current_report: WhaleIntelligenceReport,
        team_chat: Any,
    ) -> None:
        """
        Compare consecutive snapshots to detect significant whale position changes.
        Logs to team chat if a new large position appears or a large position closes.
        """
        try:
            # Build sets of (address, coin, side) → notional for comparison
            prev_map: Dict[tuple, float] = {}
            for p in previous_report.all_positions:
                prev_map[(p.address, p.coin, p.side)] = p.notional_usd

            for p in current_report.all_positions:
                key = (p.address, p.coin, p.side)
                prev_notional = prev_map.get(key, 0)
                change = p.notional_usd - prev_notional

                if change >= SIGNIFICANT_MOVE_NOTIONAL_USD:
                    change_type = "opened" if prev_notional == 0 else "increased"
                    symbol = self.coin_to_symbol(p.coin)
                    await team_chat.log_whale_alert(
                        symbol=symbol,
                        whale_label=p.label,
                        side=p.side,
                        notional_usd=p.notional_usd,
                        leverage=p.leverage,
                        change_type=change_type,
                    )

            # Check for closed large positions
            curr_map: Dict[tuple, float] = {}
            for p in current_report.all_positions:
                curr_map[(p.address, p.coin, p.side)] = p.notional_usd

            for key, prev_notional in prev_map.items():
                if prev_notional >= SIGNIFICANT_MOVE_NOTIONAL_USD and key not in curr_map:
                    address, coin, side = key
                    symbol = self.coin_to_symbol(coin)
                    await team_chat.log_whale_alert(
                        symbol=symbol,
                        whale_label=address[:10] + "…",
                        side=side,
                        notional_usd=prev_notional,
                        leverage=1.0,
                        change_type="closed",
                    )
        except Exception as e:
            logger.debug(f"Whale alert check failed: {e}")

    async def persist_snapshots(
        self, report: WhaleIntelligenceReport, db: Any
    ) -> None:
        """Save WhaleSnapshot records to DB for historical tracking."""
        try:
            from app.models import WhaleAddress, WhaleSnapshot
            from sqlalchemy import select

            result = await db.execute(
                select(WhaleAddress).where(WhaleAddress.is_active == True)
            )
            whale_rows = {r.address: r for r in result.scalars().all()}

            # Group positions by address
            by_address: Dict[str, List[WhalePosition]] = {}
            for p in report.all_positions:
                by_address.setdefault(p.address, []).append(p)

            for address, positions in by_address.items():
                whale = whale_rows.get(address)
                if whale is None:
                    continue
                total_notional = sum(p.notional_usd for p in positions)
                snapshot = WhaleSnapshot(
                    whale_id=whale.id,
                    positions=[p.to_dict() for p in positions],
                    total_notional=total_notional,
                    symbols_active=list({p.coin for p in positions}),
                )
                db.add(snapshot)

            await db.commit()
        except Exception as e:
            logger.debug(f"Failed to persist whale snapshots: {e}")

    # How many top traders to auto-seed from the Hyperliquid leaderboard
    _SEED_TOP_N = 25
    # Minimum account value (USD) to be included — filters out noise
    _SEED_MIN_ACCOUNT_VALUE = 1_000_000
    # Known placeholder addresses that were incorrectly seeded in earlier versions
    _STALE_PLACEHOLDER_ADDRESSES = {
        "0x9b8f6f229ded01e9bca36d3bfabea979843c39d2",
        "0x4f14998e2e8b01b1b73e2e5b2d0e8d4e8b9f6c7e",
        "0x1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
    }

    async def seed_default_watchlist(self, db: Any) -> None:
        """
        On startup:
          1. Remove any stale placeholder addresses from earlier boots.
          2. If the watchlist is empty, auto-seed the top N profitable wallets
             from the Hyperliquid leaderboard (stats-data.hyperliquid.xyz).

        Selection criteria (designed to find serious, large traders):
          - Account value >= $1 M
          - Positive all-time PnL
          - Sorted by all-time PnL descending — take top 25
        """
        try:
            from app.models import WhaleAddress
            from sqlalchemy import select

            # ── Step 1: clean stale placeholders ──────────────────────────────
            result = await db.execute(select(WhaleAddress))
            all_rows = result.scalars().all()
            removed = 0
            for row in all_rows:
                if row.address in self._STALE_PLACEHOLDER_ADDRESSES:
                    await db.delete(row)
                    removed += 1
            if removed:
                await db.commit()
                self.invalidate_watchlist_cache()
                logger.info(f"Removed {removed} stale placeholder whale address(es)")

            # ── Step 2: seed from leaderboard if watchlist is empty ────────────
            remaining = await db.execute(
                select(WhaleAddress).where(WhaleAddress.is_active == True)
            )
            if remaining.scalars().first():
                return  # already populated — don't overwrite user's list

            logger.info("Whale watchlist is empty — seeding from Hyperliquid leaderboard…")
            try:
                rows = await self._client.get_leaderboard()
            except Exception as e:
                logger.warning(f"Leaderboard fetch failed during seed: {e}")
                return

            # Parse and score each row
            candidates = []
            for row in rows:
                try:
                    address = row.get("ethAddress", "").strip().lower()
                    if not address.startswith("0x") or len(address) < 10:
                        continue
                    account_value = float(row.get("accountValue", 0) or 0)
                    if account_value < self._SEED_MIN_ACCOUNT_VALUE:
                        continue
                    perfs = {k: v for k, v in row.get("windowPerformances", [])}
                    alltime_pnl = float(perfs.get("allTime", {}).get("pnl", 0) or 0)
                    if alltime_pnl <= 0:
                        continue
                    display_name = row.get("displayName") or None
                    candidates.append({
                        "address": address,
                        "label": display_name,
                        "alltime_pnl": alltime_pnl,
                        "account_value": account_value,
                    })
                except (ValueError, TypeError, KeyError):
                    continue

            # Sort by all-time PnL desc, take top N
            candidates.sort(key=lambda c: c["alltime_pnl"], reverse=True)
            top = candidates[: self._SEED_TOP_N]

            if not top:
                logger.warning("Leaderboard returned no eligible wallets to seed")
                return

            seeded = 0
            for c in top:
                row = WhaleAddress(
                    address=c["address"],
                    label=c["label"] or f"HL Top Trader ${c['account_value']/1_000_000:.1f}M",
                    notes=f"Auto-seeded from leaderboard. All-time PnL: ${c['alltime_pnl']:,.0f}",
                    is_active=True,
                )
                db.add(row)
                seeded += 1

            await db.commit()
            self.invalidate_watchlist_cache()
            # Also bust the intelligence cache so the next REST/WS call reflects
            # the newly seeded wallets immediately (not after the 60s TTL).
            self._cache = None
            self._cache_ts = None
            logger.info(
                f"Seeded {seeded} whale addresses from Hyperliquid leaderboard "
                f"(top {self._SEED_TOP_N} by all-time PnL, ≥$1M account value)"
            )

        except Exception as e:
            logger.warning(f"Whale watchlist seeding failed: {e}")


# Singleton
whale_intelligence = WhaleIntelligenceService()
