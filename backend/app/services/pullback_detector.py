"""
Pullback / Re-entry Detector — prevents chasing price moves.

When a signal fires but price has already moved significantly, this module
computes a better entry level (EMA touch, Fibonacci retracement, or fixed %
pullback) and returns a PullbackPlan.  The scheduler can then route the order
as Limit at that level instead of Market at the current (chased) price.

Usage
-----
    plan = detect_pullback(
        side="buy", current_price=45100,
        close_series=df["close"], high_series=df["high"], low_series=df["low"],
        technical_report=ta_report,  # carries fib levels
        settings=pullback_config_dict,
    )
    if plan:
        order_type, entry_price = plan.order_type, plan.entry_price
    else:
        order_type, entry_price = "Market", None   # proceed normally
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PullbackPlan:
    """Describes a pullback entry — replace Market with Limit at *entry_price*."""
    order_type: str = "Limit"
    entry_price: float = 0.0
    pullback_type: str = ""         # ema | fib | range | fixed
    pullback_depth_pct: float = 0.0  # how far from market
    rationale: str = ""


def _ema_pullback(
    side: str,
    current_price: float,
    close: pd.Series,
    ema_period: int,
    max_chase_pct: float,
) -> Optional[PullbackPlan]:
    """Check if price is stretched from EMA and offer a reversion level."""
    if len(close) < ema_period + 5:
        return None
    ema = close.ewm(span=ema_period, adjust=False).mean().iloc[-1]
    if ema <= 0:
        return None

    pct_from_ema = (current_price - ema) / ema * 100  # signed %

    is_buy = side.lower() == "buy"
    is_sell = side.lower() == "sell"

    # For a buy: price should be above EMA (uptrend), but not too far
    if is_buy:
        if pct_from_ema < 0:
            # Price below EMA — already pulled back, no need to wait
            return None
        if pct_from_ema < max_chase_pct:
            # Within tolerance, proceed at market
            return None
        entry_price = ema
        depth = pct_from_ema
        rationale = (
            f"Price {pct_from_ema:.2f}% above EMA{ema_period} — "
            f"setting limit at EMA ${ema:.2f} ({depth:.2f}% pullback)"
        )
    elif is_sell:
        if pct_from_ema > 0:
            # Price above EMA — already pulled back for sell
            return None
        if abs(pct_from_ema) < max_chase_pct:
            return None
        entry_price = ema
        depth = abs(pct_from_ema)
        rationale = (
            f"Price {abs(pct_from_ema):.2f}% below EMA{ema_period} — "
            f"setting limit at EMA ${ema:.2f} ({depth:.2f}% pullback)"
        )
    else:
        return None

    return PullbackPlan(
        order_type="Limit",
        entry_price=round(float(entry_price), 4),
        pullback_type="ema",
        pullback_depth_pct=round(depth, 2),
        rationale=rationale,
    )


def _fib_pullback(
    side: str,
    current_price: float,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    fib_level: float,
    max_chase_pct: float,
    lookback: int = 50,
) -> Optional[PullbackPlan]:
    """Compute Fibonacci retracement of the most recent swing and offer entry at *fib_level*."""
    if len(close) < lookback:
        return None

    recent_high = float(high.iloc[-lookback:].max())
    recent_low = float(low.iloc[-lookback:].min())
    swing_range = recent_high - recent_low
    if swing_range <= 0 or recent_low <= 0:
        return None

    is_buy = side.lower() == "buy"
    is_sell = side.lower() == "sell"

    if is_buy:
        # For buys: swing high is the recent peak, swing low is the origin
        # Price should be near the high — we wait for retrace to fib_level of the range
        swing_origin = recent_low
        swing_extreme = recent_high
        pct_of_range = (current_price - swing_origin) / swing_range * 100
        if pct_of_range < (100 - max_chase_pct * 2):
            # Price is not near the top of the range; no chasing risk
            return None
        retrace_price = swing_extreme - (swing_range * fib_level)
        entry_price = max(retrace_price, swing_origin)
        depth_pct = (current_price - entry_price) / current_price * 100
        if depth_pct < 0.1:
            return None  # too close to current price
        rationale = (
            f"Price at {pct_of_range:.0f}% of recent range high — "
            f"waiting for {fib_level:.0%} fib retrace to ${entry_price:.2f} "
            f"({depth_pct:.2f}% pullback)"
        )
    elif is_sell:
        swing_origin = recent_high
        swing_extreme = recent_low
        pct_of_range = (swing_origin - current_price) / swing_range * 100
        if pct_of_range < (100 - max_chase_pct * 2):
            return None
        retrace_price = swing_extreme + (swing_range * fib_level)
        entry_price = min(retrace_price, swing_origin)
        depth_pct = (entry_price - current_price) / current_price * 100
        if depth_pct < 0.1:
            return None
        rationale = (
            f"Price at {pct_of_range:.0f}% of recent range low — "
            f"waiting for {fib_level:.0%} fib retrace to ${entry_price:.2f} "
            f"({depth_pct:.2f}% pullback)"
        )
    else:
        return None

    return PullbackPlan(
        order_type="Limit",
        entry_price=round(float(entry_price), 4),
        pullback_type="fib",
        pullback_depth_pct=round(abs(depth_pct), 2),
        rationale=rationale,
    )


def _fixed_offset_pullback(
    side: str,
    current_price: float,
    offset_bps: float,
    max_chase_pct: float,
    atr: Optional[float] = None,
    atr_mult: float = 0.5,
) -> Optional[PullbackPlan]:
    """Simple fixed-offset pullback: wait for price to retrace *offset_bps* from current."""
    is_buy = side.lower() == "buy"
    is_sell = side.lower() == "sell"

    if atr and atr > 0:
        offset_pct = (atr * atr_mult) / current_price * 100
    else:
        offset_pct = offset_bps / 100

    if offset_pct < max_chase_pct:
        return None  # offset is smaller than chase threshold — no need to wait

    if is_buy:
        entry_price = current_price * (1 - offset_pct / 100)
    elif is_sell:
        entry_price = current_price * (1 + offset_pct / 100)
    else:
        return None

    rationale = (
        f"Fixed pullback: setting limit {offset_pct:.2f}% {'below' if is_buy else 'above'} "
        f"market (${entry_price:.2f})"
    )

    return PullbackPlan(
        order_type="Limit",
        entry_price=round(float(entry_price), 4),
        pullback_type="fixed",
        pullback_depth_pct=round(offset_pct, 2),
        rationale=rationale,
    )


def detect_pullback(
    side: str,
    current_price: float,
    close: Optional[pd.Series] = None,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    technical_report: Any = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Optional[PullbackPlan]:
    """Main entry point — detect if price has chased and compute pullback entry.

    Parameters
    ----------
    side : str
        "buy" or "sell"
    current_price : float
        Current market price (last candle close)
    close, high, low : pd.Series, optional
        Price series for EMA / fib / range calculations
    technical_report : TechnicalAnalystReport, optional
        Carries PriceLevels with fibonacci_retracements
    settings : dict, optional
        Pullback configuration with keys:
        - pullback_entries_enabled (bool)
        - pullback_strategy (str): "ema" | "fib" | "fixed"
        - pullback_max_chase_pct (float): min % move before triggering
        - pullback_ema_period (int)
        - pullback_fib_level (float)
        - pullback_fixed_offset_bps (float)
        - pullback_atr_mult (float)

    Returns
    -------
    PullbackPlan or None
        None means no pullback needed — proceed at market.
    """
    if not settings:
        return None
    if not settings.get("pullback_entries_enabled", False):
        return None

    max_chase = float(settings.get("pullback_max_chase_pct", 0.5) or 0.5)
    strategy = settings.get("pullback_strategy", "ema")

    plan: Optional[PullbackPlan] = None

    # Try the configured strategy first; fall back through other strategies
    # so that if e.g. EMA fails (not enough data) we still try fib.

    if strategy == "ema" or strategy == "best":
        if close is not None and len(close) > 0:
            ema_period = int(settings.get("pullback_ema_period", 20) or 20)
            plan = _ema_pullback(side, current_price, close, ema_period, max_chase)
            if plan is not None:
                return plan

    if strategy == "fib" or strategy == "best":
        if high is not None and low is not None and close is not None:
            fib_level = float(settings.get("pullback_fib_level", 0.382) or 0.382)
            plan = _fib_pullback(side, current_price, high, low, close, fib_level, max_chase)
            if plan is not None:
                return plan

    if strategy == "fixed" or strategy == "best":
        offset_bps = float(settings.get("pullback_fixed_offset_bps", 30) or 30)
        atr_mult = float(settings.get("pullback_atr_mult", 0.5) or 0.5)
        atr = None
        if close is not None and high is not None and low is not None and len(close) > 14:
            from app.services.indicators import IndicatorService
            atr_ser = IndicatorService().calculate_atr(high, low, close)
            if atr_ser is not None and len(atr_ser) > 0:
                atr = float(atr_ser.iloc[-1])
        plan = _fixed_offset_pullback(side, current_price, offset_bps, max_chase, atr=atr, atr_mult=atr_mult)
        if plan is not None:
            return plan

    # Try support/resistance level pullback before TA fib fallback
    if plan is None and technical_report is not None:
        _pl = getattr(technical_report, "price_levels", None)
        if _pl is not None:
            is_buy = side.lower() == "buy"
            if is_buy and _pl.support:
                _nearest_support = max(s for s in _pl.support if s < current_price)
                _dist_pct = (current_price - _nearest_support) / current_price * 100
                if _dist_pct >= max_chase:
                    _entry = min(_nearest_support * 1.001, current_price)  # 0.1% above support
                    _depth = (current_price - _entry) / current_price * 100
                    if _depth >= 0.1:
                        plan = PullbackPlan(
                            order_type="Limit",
                            entry_price=round(float(_entry), 8),
                            pullback_type="support",
                            pullback_depth_pct=round(_depth, 2),
                            rationale=(
                                f"Price {_dist_pct:.2f}% above nearest support ${_nearest_support:.6f} — "
                                f"setting limit at ${_entry:.6f} ({_depth:.2f}% pullback)"
                            ),
                        )
                        return plan
            elif not is_buy and _pl.resistance:
                _nearest_res = min(r for r in _pl.resistance if r > current_price)
                _dist_pct = (_nearest_res - current_price) / current_price * 100
                if _dist_pct >= max_chase:
                    _entry = max(_nearest_res * 0.999, current_price)  # 0.1% below resistance
                    _depth = (_entry - current_price) / current_price * 100
                    if _depth >= 0.1:
                        plan = PullbackPlan(
                            order_type="Limit",
                            entry_price=round(float(_entry), 8),
                            pullback_type="resistance",
                            pullback_depth_pct=round(_depth, 2),
                            rationale=(
                                f"Price {_dist_pct:.2f}% below nearest resistance ${_nearest_res:.6f} — "
                                f"setting limit at ${_entry:.6f} ({_depth:.2f}% pullback)"
                            ),
                        )
                        return plan

    # Try fibonacci levels from the technical analyst report as a last resort
    if plan is None and technical_report is not None:
        fibs = getattr(getattr(technical_report, "price_levels", None), "fibonacci_retracements", None)
        if fibs and isinstance(fibs, dict):
            is_buy = side.lower() == "buy"
            for _label, _level in sorted(fibs.items(), key=lambda x: float(x[0].rstrip("%")) if x[0].rstrip("%").replace(".", "").isdigit() else 0):
                try:
                    _price = float(_level)
                except (ValueError, TypeError):
                    continue
                if _price <= 0:
                    continue
                pct_diff = abs(current_price - _price) / current_price * 100
                if pct_diff < 0.1 or pct_diff > max_chase * 3:
                    continue
                direction_ok = (_price < current_price and is_buy) or (_price > current_price and not is_buy)
                if not direction_ok:
                    continue
                plan = PullbackPlan(
                    order_type="Limit",
                    entry_price=round(_price, 4),
                    pullback_type="fib_ta",
                    pullback_depth_pct=round(pct_diff, 2),
                    rationale=(
                        f"TA fib retracement {_label} @ ${_price:.2f} "
                        f"({pct_diff:.2f}% {'below' if is_buy else 'above'} market)"
                    ),
                )
                break

    return plan
