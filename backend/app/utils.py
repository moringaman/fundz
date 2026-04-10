"""Shared utility helpers."""
import math


def fmt_price(value: float) -> str:
    """Format a price with appropriate decimal precision for any magnitude.

    Works for BTC ($71,972), mid-range alts ($84.14), and micro-cap tokens
    like LUNC ($0.00003729) that would round to $0 with :,.0f or :,.2f.

    Examples:
        0.00003729  → "$0.00003729"
        0.842       → "$0.8420"
        84.14       → "$84.14"
        71972.59    → "$71,972.59"
    """
    if value == 0:
        return "$0"
    abs_val = abs(value)
    if abs_val >= 1000:
        return f"${value:,.2f}"
    elif abs_val >= 1:
        # 4 sig figs, drop trailing zeros
        return f"${value:.4f}".rstrip('0').rstrip('.')
    else:
        # Sub-dollar: enough decimals to show at least 4 significant digits
        sig_digits = max(4, -int(math.floor(math.log10(abs_val))) + 3)
        return f"${value:.{sig_digits}f}"
