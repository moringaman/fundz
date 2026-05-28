"""
Pattern-driven entry routing (A-007).

Chart patterns (bull_flag, head_shoulders, wedge, etc.) carry a natural entry
level dictated by their geometry — the flag-high for a breakout, the IB low
for a Wyckoff Spring, the neckline for an H&S. Market orders at signal-time
ignore that geometry and fill at whatever the current candle close happens
to be, which can be halfway up the pole.

This module translates a `PatternSignal` (from technical_analyst) into an
`OrderPlan` describing whether the order should be routed as Limit, Stop,
or Market, and at what trigger price.

Routing rules (Phemex API vocabulary):
  - BUY,  entry_price > current_price → "Stop"   (breakout — buy on break above)
  - BUY,  entry_price ≤ current_price → "Limit"  (pullback / retest — buy lower)
  - SELL, entry_price < current_price → "Stop"   (breakdown — sell on break below)
  - SELL, entry_price ≥ current_price → "Limit"  (rally / retest — sell higher)

If no usable pattern is supplied, returns a plain Market plan at current_price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class OrderPlan:
    order_type: str
    entry_price: float
    pattern_type: Optional[str] = None
    pattern_confidence: Optional[float] = None
    rationale: str = ""
    timeframe: str = "1h"


def select_pattern_entry(
    side: str,
    current_price: float,
    patterns: Iterable,
    min_confidence: float,
    enabled: bool = True,
) -> Optional[OrderPlan]:
    if not enabled or not patterns or current_price <= 0:
        return None

    side_norm = side.lower()
    target_direction = (
        "bullish" if side_norm in ("buy", "long") else
        "bearish" if side_norm in ("sell", "short") else
        None
    )
    if target_direction is None:
        return None

    aligned = [
        p for p in patterns
        if getattr(p, "direction", None) == target_direction
        and getattr(p, "confidence", 0.0) >= min_confidence
        and getattr(p, "entry_price", None)
        and float(getattr(p, "entry_price", 0.0)) > 0
    ]
    if not aligned:
        return None

    best = max(aligned, key=lambda p: float(getattr(p, "confidence", 0.0)))
    entry_price = float(best.entry_price)

    if side_norm in ("buy", "long"):
        order_type = "Stop" if entry_price > current_price else "Limit"
    else:
        order_type = "Stop" if entry_price < current_price else "Limit"

    return OrderPlan(
        order_type=order_type,
        entry_price=entry_price,
        pattern_type=getattr(best, "pattern_type", None),
        pattern_confidence=float(getattr(best, "confidence", 0.0)),
        timeframe=getattr(best, "timeframe", "1h"),
        rationale=(
            f"{best.pattern_type} ({best.confidence:.0%}) → {order_type} "
            f"@ {entry_price:.6g} vs current {current_price:.6g}"
        ),
    )


def can_fill_now(
    plan: OrderPlan,
    side: str,
    current_price: float,
    tolerance_pct: float,
) -> bool:
    if plan.order_type == "Market" or current_price <= 0:
        return True

    side_norm = side.lower()
    band = abs(plan.entry_price) * (max(tolerance_pct, 0.0) / 100.0)

    if plan.order_type == "Limit":
        if side_norm in ("buy", "long"):
            return current_price <= plan.entry_price + band
        return current_price >= plan.entry_price - band

    if plan.order_type == "Stop":
        if side_norm in ("buy", "long"):
            return current_price >= plan.entry_price - band
        return current_price <= plan.entry_price + band

    return False
