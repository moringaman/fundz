from typing import List, Dict, Optional
from datetime import datetime
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
import logging

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService, Signal, TradingSignal

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    net_pnl: float  # after fees
    total_fees: float
    max_drawdown: float
    sharpe_ratio: float
    avg_trade_pnl: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    trades: List[Dict]
    equity_curve: List[float] = field(default_factory=list)
    drawdown_curve: List[float] = field(default_factory=list)


@dataclass
class BacktestConfig:
    symbol: str
    interval: str = "1h"
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    initial_balance: float = 10000.0
    position_size_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    strategy: str = "momentum"
    # Valid strategies: momentum | mean_reversion | breakout | grid | ema_crossover | wyckoff | fractal
    # Fee modeling — defaults to spot USDT rates (most agents trade USDT pairs)
    # Phemex perpetual contract taker: 0.06% | spot taker: 0.10%
    maker_fee_pct: float = 0.06   # Phemex perpetual contract maker fee
    taker_fee_pct: float = 0.06   # Phemex perpetual contract taker fee
    slippage_pct: float = 0.02    # 0.02% estimated slippage
    # Trailing stop
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 0.03  # 3% trailing stop
    # Data window — EXPANDED for better signal quality
    # 5000 candles: ~208 days on 1h, ~35 days on 15m, ~833 days on 4h, ~13.7 years on 1d
    # Minimum 500 candles for robust indicator calculation
    candle_limit: int = 5000      # EXPANDED from 2000; fetched in paginated 1000-candle batches


class BacktestEngine:
    def __init__(self, phemex_client: Optional[PhemexClient] = None):
        self.phemex = phemex_client or PhemexClient(
            api_key=settings.phemex_api_key,
            api_secret=settings.phemex_api_secret,
            testnet=settings.phemex_testnet
        )
        self.indicator_service = IndicatorService()

    # ── Pre-computation helpers ───────────────────────────────────────────────
    def _precompute_indicator_series(self, df: pd.DataFrame) -> dict:
        """Compute ALL indicator Series on the full df ONCE.

        Arrr, this be the treasure map that saves us from the O(n²) curse — calling
        calculate_all per bar on a sliding window meant running every pandas rolling op
        ~4850 times. This does each op once in vectorised C-land, then the loop just
        reads scalars via .iloc[i]. Supertrend and fractals had Python inner loops that
        were the worst offenders; both are now computed once here.
        """
        ind = self.indicator_service
        close  = df["close"].astype(float).reset_index(drop=True)
        high   = df["high"].astype(float).reset_index(drop=True)   if "high"   in df.columns else close.copy()
        low    = df["low"].astype(float).reset_index(drop=True)    if "low"    in df.columns else close.copy()
        volume = df["volume"].astype(float).reset_index(drop=True) if "volume" in df.columns else pd.Series([0.0] * len(df))
        open_  = df["open"].astype(float).reset_index(drop=True)   if "open"   in df.columns else close.copy()

        # ── Rolling / EWM series (each computed once instead of 4850×) ────────
        rsi     = ind.calculate_rsi(close)
        bb      = ind.calculate_bollinger_bands(close)
        sma20   = ind.calculate_sma(close, 20)
        sma50   = ind.calculate_sma(close, 50)
        sma200  = ind.calculate_sma(close, 200)
        macd_df = ind.calculate_macd(close)
        atr     = ind.calculate_atr(high, low, close)
        vol_sma = ind.calculate_volume_sma(volume)
        adx     = ind.calculate_adx(high, low, close) if len(df) >= 28 else pd.Series([np.nan] * len(df))
        ema9    = ind.calculate_ema(close, 9)
        ema21   = ind.calculate_ema(close, 21)

        # BB width ratio for breakout strategy (pre-computed avoids a duplicate BB calc)
        _bb_widths = (bb["upper"] - bb["lower"]) / bb["middle"].replace(0, np.nan)
        _bb_roll   = _bb_widths.rolling(40).mean()
        bb_width_ratio = (_bb_widths / _bb_roll.replace(0, np.nan)).round(3)

        # ── Ichimoku (all rolling → compute once) ─────────────────────────────
        def _mid(h: pd.Series, l: pd.Series, p: int) -> pd.Series:
            return (h.rolling(p).max() + l.rolling(p).min()) / 2

        ichi_tenkan = _mid(high, low, 9)
        ichi_kijun  = _mid(high, low, 26)
        ichi_sa     = ((ichi_tenkan + ichi_kijun) / 2).shift(26)
        ichi_sb     = _mid(high, low, 52).shift(26)
        ichi_tk_bull = ((ichi_tenkan.shift(1) <= ichi_kijun.shift(1)) & (ichi_tenkan > ichi_kijun)).fillna(False)
        ichi_tk_bear = ((ichi_tenkan.shift(1) >= ichi_kijun.shift(1)) & (ichi_tenkan < ichi_kijun)).fillna(False)

        # ── Supertrend (Python inner loop — compute ONCE on full df) ──────────
        # The per-bar version looped over ~240 rows each of 4850 iterations = 1.16M
        # Python ops. Here we loop over all 5000 rows exactly once.
        _st_period, _st_mult = 10, 3.0
        _atr_st    = ind.calculate_atr(high, low, close, _st_period)
        _hl2       = (high + low) / 2
        _ub_basic  = _hl2 + _st_mult * _atr_st
        _lb_basic  = _hl2 - _st_mult * _atr_st
        _ub = _ub_basic.copy()
        _lb = _lb_basic.copy()
        st_vals = pd.Series([np.nan] * len(close), dtype=float)
        st_dir  = pd.Series([0]      * len(close), dtype=int)
        _first  = _st_period
        if _first < len(close):
            _ub.iloc[_first] = float(_ub_basic.iloc[_first])
            _lb.iloc[_first] = float(_lb_basic.iloc[_first])
            st_dir.iloc[_first] = 1
            for _si in range(_first + 1, len(close)):
                _ub.iloc[_si] = (float(_ub_basic.iloc[_si])
                    if float(_ub_basic.iloc[_si]) < float(_ub.iloc[_si - 1])
                       or float(close.iloc[_si - 1]) > float(_ub.iloc[_si - 1])
                    else float(_ub.iloc[_si - 1]))
                _lb.iloc[_si] = (float(_lb_basic.iloc[_si])
                    if float(_lb_basic.iloc[_si]) > float(_lb.iloc[_si - 1])
                       or float(close.iloc[_si - 1]) < float(_lb.iloc[_si - 1])
                    else float(_lb.iloc[_si - 1]))
                _pd = int(st_dir.iloc[_si - 1])
                if _pd == -1 and float(close.iloc[_si]) > float(_ub.iloc[_si]):
                    st_dir.iloc[_si] = 1
                elif _pd == 1 and float(close.iloc[_si]) < float(_lb.iloc[_si]):
                    st_dir.iloc[_si] = -1
                else:
                    st_dir.iloc[_si] = _pd
                st_vals.iloc[_si] = (float(_lb.iloc[_si]) if st_dir.iloc[_si] == 1
                                     else float(_ub.iloc[_si]))
        st_flipped = pd.Series(
            [False] + [int(st_dir.iloc[j]) != int(st_dir.iloc[j - 1])
                       if st_dir.iloc[j] != 0 and st_dir.iloc[j - 1] != 0 else False
                       for j in range(1, len(st_dir))],
            dtype=bool,
        )

        # ── Fractals (Python loop over full df — O(n) total instead of O(n²)) ─
        # Arrr, the look-ahead guard: fractal at position p is confirmed only
        # when bars p+1 and p+2 have closed. Forward-fill with a 2-bar delay.
        _n_frac = 2
        h_arr = high.values
        l_arr = low.values
        _up_frac   = np.full(len(df), np.nan)
        _down_frac = np.full(len(df), np.nan)
        for _fi in range(_n_frac, len(df) - _n_frac):
            if (l_arr[_fi] < l_arr[_fi - 1] and l_arr[_fi] < l_arr[_fi - 2] and
                    l_arr[_fi] < l_arr[_fi + 1] and l_arr[_fi] < l_arr[_fi + 2]):
                _up_frac[_fi] = l_arr[_fi]
            if (h_arr[_fi] > h_arr[_fi - 1] and h_arr[_fi] > h_arr[_fi - 2] and
                    h_arr[_fi] > h_arr[_fi + 1] and h_arr[_fi] > h_arr[_fi + 2]):
                _down_frac[_fi] = h_arr[_fi]

        frac_last_up   = np.full(len(df), np.nan)
        frac_last_down = np.full(len(df), np.nan)
        _lu = _ld = np.nan
        for _fi in range(len(df)):
            _ci = _fi - _n_frac  # confirmed at this index
            if _ci >= 0:
                if not np.isnan(_up_frac[_ci]):   _lu = _up_frac[_ci]
                if not np.isnan(_down_frac[_ci]): _ld = _down_frac[_ci]
            frac_last_up[_fi]   = _lu
            frac_last_down[_fi] = _ld

        return {
            "close": close, "high": high, "low": low, "volume": volume, "open": open_,
            "rsi": rsi,
            "bb_upper": bb["upper"], "bb_middle": bb["middle"], "bb_lower": bb["lower"],
            "sma20": sma20, "sma50": sma50, "sma200": sma200,
            "macd": macd_df["macd"], "macd_signal": macd_df["signal"], "macd_histogram": macd_df["histogram"],
            "atr": atr, "vol_sma": vol_sma, "adx": adx,
            "ema9": ema9, "ema21": ema21,
            "bb_width_ratio": bb_width_ratio,
            # Ichimoku
            "ichi_tenkan": ichi_tenkan, "ichi_kijun": ichi_kijun,
            "ichi_sa": ichi_sa, "ichi_sb": ichi_sb,
            "ichi_tk_bull": ichi_tk_bull, "ichi_tk_bear": ichi_tk_bear,
            # Supertrend
            "st_vals": st_vals, "st_dir": st_dir, "st_flipped": st_flipped,
            # Fractals (forward-filled, 2-bar confirmation lag preserved)
            "frac_last_up": frac_last_up, "frac_last_down": frac_last_down,
        }

    def _build_indicators_at(self, i: int, p: dict) -> dict:
        """Build the per-bar indicators dict from precomputed series.

        Only S/R (50 bars), candle patterns (3 bars), divergence (22 bars), and
        pivot/fib (25 bars) are computed fresh — all other indicators are O(1) lookups.
        """
        ind = self.indicator_service

        def _f(series) -> Optional[float]:
            v = series.iloc[i] if hasattr(series, "iloc") else series[i]
            return None if (v != v) else float(v)  # NaN check via inequality

        def _b(series, default: bool = False) -> bool:
            v = series.iloc[i] if hasattr(series, "iloc") else series[i]
            return bool(v) if not (v != v) else default

        def _fp(series) -> Optional[float]:
            if i == 0: return None
            v = series.iloc[i - 1]
            return None if (v != v) else float(v)

        price = _f(p["close"]) or 0.0

        # ── Per-bar small-window calculations ─────────────────────────────────
        sr = ind.calculate_support_resistance(
            p["high"].iloc[max(0, i - 49):i + 1],
            p["low"].iloc[max(0, i - 49):i + 1],
            p["close"].iloc[max(0, i - 49):i + 1],
        ) if i >= 10 else {
            "at_support": False, "at_resistance": False,
            "nearest_support": None, "nearest_resistance": None,
            "support_strength": 0, "resistance_strength": 0,
        }

        cp = ind.calculate_candle_patterns(
            p["open"].iloc[max(0, i - 2):i + 1],
            p["high"].iloc[max(0, i - 2):i + 1],
            p["low"].iloc[max(0, i - 2):i + 1],
            p["close"].iloc[max(0, i - 2):i + 1],
        ) if i >= 2 else {"bullish_patterns": [], "bearish_patterns": [], "pattern_weight": 0.0, "pattern_signal": "neutral"}

        div = ind.detect_divergence(p["close"].iloc[max(0, i - 21):i + 1]) if i >= 22 else {
            "bullish_divergence": False, "bearish_divergence": False,
            "divergence_weight": 0.0, "divergence_reason": "",
        }

        pf = ind.calculate_pivot_fibonacci(
            p["high"].iloc[max(0, i - 24):i + 1],
            p["low"].iloc[max(0, i - 24):i + 1],
            p["close"].iloc[max(0, i - 24):i + 1],
        ) if i >= 2 else {}

        # ── Ichimoku derived values ────────────────────────────────────────────
        sa_i = _f(p["ichi_sa"])
        sb_i = _f(p["ichi_sb"])
        cloud_top    = max(sa_i, sb_i) if (sa_i is not None and sb_i is not None) else None
        cloud_bottom = min(sa_i, sb_i) if (sa_i is not None and sb_i is not None) else None
        cloud_bull = cloud_top    is not None and price > cloud_top
        cloud_bear = cloud_bottom is not None and price < cloud_bottom
        cloud_neut = not cloud_bull and not cloud_bear
        above_pct  = round((price - cloud_top) / cloud_top * 100, 3) if cloud_top and cloud_top > 0 else None
        t_i, k_i   = _f(p["ichi_tenkan"]), _f(p["ichi_kijun"])
        cloud_color = (None if not (sa_i and sb_i) else
                       "green" if sa_i > sb_i else "red" if sa_i < sb_i else None)

        # ── Supertrend derived values ──────────────────────────────────────────
        st_val    = _f(p["st_vals"])
        st_dir_i  = int(p["st_dir"].iloc[i]) if p["st_dir"].iloc[i] != 0 else 0
        st_trend  = "bullish" if st_dir_i == 1 else ("bearish" if st_dir_i == -1 else "neutral")
        st_dist   = round((price - st_val) / st_val * 100, 3) if st_val and st_val > 0 else None

        # ── Fractal lookup ─────────────────────────────────────────────────────
        _lu = p["frac_last_up"][i]
        _ld = p["frac_last_down"][i]

        return {
            "rsi":             _f(p["rsi"]),
            "bb_upper":        _f(p["bb_upper"]),
            "bb_middle":       _f(p["bb_middle"]),
            "bb_lower":        _f(p["bb_lower"]),
            "sma_20":          _f(p["sma20"]),
            "sma_50":          _f(p["sma50"]),
            "sma_200":         _f(p["sma200"]),
            "macd":            _f(p["macd"]),
            "macd_signal":     _f(p["macd_signal"]),
            "macd_histogram":  _f(p["macd_histogram"]),
            "atr":             _f(p["atr"]),
            "volume_sma":      _f(p["vol_sma"]),
            "adx":             _f(p["adx"]),
            # S/R
            "at_support":          sr.get("at_support", False),
            "at_resistance":       sr.get("at_resistance", False),
            "nearest_support":     sr.get("nearest_support"),
            "nearest_resistance":  sr.get("nearest_resistance"),
            "support_strength":    sr.get("support_strength", 0),
            "resistance_strength": sr.get("resistance_strength", 0),
            # Candle patterns
            "bullish_patterns": cp.get("bullish_patterns", []),
            "bearish_patterns": cp.get("bearish_patterns", []),
            "pattern_weight":   cp.get("pattern_weight", 0.0),
            "pattern_signal":   cp.get("pattern_signal", "neutral"),
            # Fractals
            "last_up_fractal":   float(_lu) if not (_lu != _lu) else None,
            "last_down_fractal": float(_ld) if not (_ld != _ld) else None,
            "fractal_up_count":   0,
            "fractal_down_count": 0,
            # Ichimoku
            "ichi_cloud_bullish":   cloud_bull,
            "ichi_cloud_bearish":   cloud_bear,
            "ichi_cloud_neutral":   cloud_neut,
            "ichi_tk_cross_bull":   _b(p["ichi_tk_bull"]),
            "ichi_tk_cross_bear":   _b(p["ichi_tk_bear"]),
            "ichi_above_cloud_pct": above_pct,
            "ichi_cloud_color":     cloud_color,
            "ichi_tenkan":          t_i,
            "ichi_kijun":           k_i,
            # Supertrend
            "supertrend":              st_val,
            "supertrend_trend":        st_trend,
            "supertrend_just_flipped": _b(p["st_flipped"]),
            "supertrend_distance_pct": st_dist,
            # Pivot/Fib
            "pivot":               pf.get("pivot"),
            "pivot_r1":            pf.get("r1"),
            "pivot_r2":            pf.get("r2"),
            "pivot_r3":            pf.get("r3"),
            "pivot_s1":            pf.get("s1"),
            "pivot_s2":            pf.get("s2"),
            "pivot_s3":            pf.get("s3"),
            "pivot_fib_levels":    pf.get("fib_levels", {}),
            "at_pivot_level":      pf.get("at_pivot_level", False),
            "pivot_bias":          pf.get("pivot_bias", "neutral"),
            "nearest_pivot_level": pf.get("nearest_pivot_level"),
            # Divergence (computed on 22-bar window per bar)
            "bullish_divergence": div.get("bullish_divergence", False),
            "bearish_divergence": div.get("bearish_divergence", False),
            "divergence_weight":  div.get("divergence_weight", 0.0),
            # Strategy-specific prev/crossover values (avoid duplicate series calcs in generate_signal)
            "_prev_sma_20":   _fp(p["sma20"]),
            "_prev_sma_50":   _fp(p["sma50"]),
            "_ema_fast_val":  _f(p["ema9"]),
            "_ema_slow_val":  _f(p["ema21"]),
            "_prev_ema_fast": _fp(p["ema9"]),
            "_prev_ema_slow": _fp(p["ema21"]),
            "_bb_width_ratio": _f(p["bb_width_ratio"]),
            "_current_volume": _f(p["volume"]),
        }

    async def run_backtest(self, config: BacktestConfig) -> BacktestResult:
        klines = await self._fetch_historical_data(config)

        # Require minimum 500 candles for robust indicator calculation (RSI, MACD, Bollinger Bands need warmup)
        if len(klines) < 500:
            raise ValueError(f"Not enough data. Need at least 500 candles for quality indicators, got {len(klines)}")

        df = pd.DataFrame(klines)
        df = df.sort_values('time').reset_index(drop=True)

        trades: List[Dict] = []
        balance = config.initial_balance
        position = None
        entry_price = 0.0
        trailing_high = 0.0
        trailing_low = float('inf')
        total_fees = 0.0

        equity_curve: List[float] = []
        drawdown_curve: List[float] = []
        peak_balance = balance

        # Scale-out schedule for backtest — mirrors live _SCALE_PROFILES "default"
        # [(pct_of_tp_range, close_pct), ...]
        _SCALE_LEVELS = [(0.33, 0.25), (0.60, 0.35)]

        # Pre-compute ALL indicator Series on the full df ONCE.
        # The loop reads per-bar scalars via _build_indicators_at(i, _pre) instead of
        # calling calculate_all on a sliding window — O(n) total vs O(n²) before.
        _pre = self._precompute_indicator_series(df)

        # Start at candle 150 to allow full warmup for long-period indicators (200-candle SMA, etc.)
        for i in range(150, len(df)):
            current_time = df.iloc[i]['time']
            current_price = float(df.iloc[i]['close'])

            _indicators_at_i = self._build_indicators_at(i, _pre)
            # Wyckoff and fractal strategies need the raw df window for their signal logic
            _df_slice = df.iloc[max(0, i - 49):i + 1] if config.strategy in ("wyckoff", "fractal") else df.iloc[i:i + 1]
            signal = self.indicator_service.generate_signal(
                _df_slice, {'strategy': config.strategy},
                _precomputed_indicators=_indicators_at_i,
            )
            signal_action = signal.signal.value if signal.signal else 'hold'

            # Mirror the live agent confidence gate — only trade signals with meaningful edge
            if signal.confidence < 0.6:
                signal_action = 'hold'

            # ── Fractal structural stop: derive SL from the opposite fractal level
            # rather than a fixed %-based stop. This mirrors how practitioners use
            # fractals — the most recent bullish fractal low is the "invalidation" for
            # a long, and vice versa for a short. Only active when strategy == "fractal".
            _fractal_sl: Optional[float] = None
            if config.strategy == "fractal" and signal_action in ("buy", "sell"):
                # Read from precomputed fractal arrays — O(1), no loop needed
                _lu = _pre["frac_last_up"][i]
                _ld = _pre["frac_last_down"][i]
                if signal_action == "buy" and not (_lu != _lu):   # NaN check
                    _fractal_sl = float(_lu)
                elif signal_action == "sell" and not (_ld != _ld):
                    _fractal_sl = float(_ld)

                # ── Structural stop width guard ───────────────────────────────
                # If the fractal stop is too far from entry the R:R silently
                # degrades — a 6% structural stop with a 7.5% TP is barely 1.25:1.
                # Cap at 1.5× the configured stop_loss_pct; beyond that the fractal
                # is stale or the market is too wide, so fall back to fixed %.
                if _fractal_sl is not None:
                    _max_sl_dist = current_price * (config.stop_loss_pct / 100) * 1.5
                    if abs(current_price - _fractal_sl) > _max_sl_dist:
                        _fractal_sl = None  # fall back to config.stop_loss_pct

            # --- ENTRY ---
            if position is None and signal_action in ['buy', 'sell']:
                position_size = balance * config.position_size_pct
                # Apply slippage to entry
                slippage = current_price * config.slippage_pct / 100
                fill_price = current_price + slippage if signal_action == 'buy' else current_price - slippage
                quantity = position_size / fill_price

                # Entry fee (taker — market orders)
                entry_fee = position_size * config.taker_fee_pct / 100
                total_fees += entry_fee
                balance -= entry_fee

                position = {
                    'side': signal_action,
                    'entry_price': fill_price,
                    'quantity': quantity,
                    'entry_time': current_time,
                    'scale_triggered': [False] * len(_SCALE_LEVELS),  # track per-level
                    'fractal_sl': _fractal_sl,  # None for non-fractal strategies
                }
                entry_price = fill_price
                trailing_high = fill_price
                trailing_low = fill_price

                trades.append({
                    'time': current_time,
                    'type': 'ENTRY',
                    'side': signal_action,
                    'price': fill_price,
                    'quantity': quantity,
                    'fee': entry_fee,
                    'balance': balance,
                })

            # --- POSITION MANAGEMENT ---
            elif position:
                # Update trailing extremes
                if current_price > trailing_high:
                    trailing_high = current_price
                if current_price < trailing_low:
                    trailing_low = current_price

                pnl_pct = 0.0
                if position['side'] == 'buy':
                    pnl_pct = (current_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - current_price) / entry_price

                tp_pct = config.take_profit_pct  # e.g. 0.06 for 6%

                # ── Scale-out partial closes ──────────────────────────────────
                if pnl_pct > 0:
                    _progress = pnl_pct / tp_pct if tp_pct > 0 else 0
                    for _li, (_threshold, _close_pct) in enumerate(_SCALE_LEVELS):
                        if position['scale_triggered'][_li]:
                            continue
                        if _progress >= _threshold:
                            _close_qty = position['quantity'] * _close_pct
                            if position['side'] == 'buy':
                                _fill = current_price - current_price * config.slippage_pct / 100
                                _slice_pnl = _close_qty * (_fill - entry_price)
                            else:
                                _fill = current_price + current_price * config.slippage_pct / 100
                                _slice_pnl = _close_qty * (entry_price - _fill)
                            _slice_fee = _close_qty * _fill * config.taker_fee_pct / 100
                            _entry_fee_slice = _close_qty * entry_price * config.taker_fee_pct / 100
                            total_fees += _slice_fee  # entry_fee_slice already in total_fees from entry
                            _net_slice = _slice_pnl - _slice_fee - _entry_fee_slice
                            balance += _net_slice
                            position['quantity'] -= _close_qty
                            position['scale_triggered'][_li] = True
                            trades.append({
                                'time': current_time,
                                'type': 'EXIT',
                                'side': position['side'],
                                'price': _fill,
                                'quantity': _close_qty,
                                'pnl': _slice_pnl,
                                'net_pnl': _net_slice,
                                'fee': _slice_fee + _entry_fee_slice,
                                'balance': balance,
                                'pnl_pct': pnl_pct * 100,
                                'exit_reason': f'scale_out_{_threshold:.0%}',
                            })

                # Trailing stop check
                trailing_stop_hit = False
                if config.use_trailing_stop:
                    if position['side'] == 'buy':
                        trail_from_high = (trailing_high - current_price) / trailing_high
                        trailing_stop_hit = trail_from_high >= config.trailing_stop_pct and pnl_pct > 0
                    else:
                        trail_from_low = (current_price - trailing_low) / trailing_low if trailing_low > 0 else 0
                        trailing_stop_hit = trail_from_low >= config.trailing_stop_pct and pnl_pct > 0

                # Fractal structural stop: exit if price closes through the invalidation fractal
                fractal_sl_hit = False
                _fsl = position.get('fractal_sl')
                if _fsl is not None:
                    if position['side'] == 'buy'  and current_price < _fsl:
                        fractal_sl_hit = True
                    elif position['side'] == 'sell' and current_price > _fsl:
                        fractal_sl_hit = True

                should_exit = (
                    pnl_pct <= -config.stop_loss_pct
                    or pnl_pct >= config.take_profit_pct
                    or trailing_stop_hit
                    or fractal_sl_hit
                    or position['quantity'] < 1e-12  # fully scaled out
                )

                if should_exit:
                    # Apply slippage to exit
                    slippage = current_price * config.slippage_pct / 100
                    if position['side'] == 'buy':
                        fill_price = current_price - slippage
                        raw_pnl = position['quantity'] * (fill_price - entry_price)
                    else:
                        fill_price = current_price + slippage
                        raw_pnl = position['quantity'] * (entry_price - fill_price)

                    # Exit fee + proportional entry fee for remaining quantity
                    exit_notional = position['quantity'] * fill_price
                    exit_fee = exit_notional * config.taker_fee_pct / 100
                    entry_fee_remaining = position['quantity'] * entry_price * config.taker_fee_pct / 100
                    total_fees += exit_fee  # entry_fee_remaining already in total_fees from entry

                    net_pnl = raw_pnl - exit_fee - entry_fee_remaining
                    balance += net_pnl

                    exit_reason = "signal"
                    if trailing_stop_hit:
                        exit_reason = "trailing_stop"
                    elif fractal_sl_hit:
                        exit_reason = "fractal_stop"
                    elif pnl_pct <= -config.stop_loss_pct:
                        exit_reason = "stop_loss"
                    elif pnl_pct >= config.take_profit_pct:
                        exit_reason = "take_profit"

                    trades.append({
                        'time': current_time,
                        'type': 'EXIT',
                        'side': position['side'],
                        'price': fill_price,
                        'quantity': position['quantity'],
                        'pnl': raw_pnl,
                        'net_pnl': net_pnl,
                        'fee': exit_fee + entry_fee_remaining,
                        'balance': balance,
                        'pnl_pct': pnl_pct * 100,
                        'exit_reason': exit_reason,
                    })

                    position = None
                    trailing_high = 0.0
                    trailing_low = float('inf')

            equity_curve.append(balance)
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
            drawdown_curve.append(dd)

        return self._calculate_metrics(trades, equity_curve, drawdown_curve, config.initial_balance, total_fees)

    async def _fetch_historical_data(self, config: BacktestConfig) -> List[Dict]:
        """
        Fetch kline data for the backtest window.

        Phemex supports up to ~500 rows per call; Binance supports up to 1000.
        get_klines() automatically falls back to Binance when Phemex returns fewer
        rows than requested.  For limits > 1000, we paginate via the endTime cursor
        so callers simply set candle_limit=2000 (or more) without worrying about batching.
        """
        try:
            BATCH = 1000  # Binance/Phemex API max per request
            remaining = config.candle_limit
            all_klines: list = []
            end_time_ms: int | None = None  # None = "up to now"

            while remaining > 0:
                batch_limit = min(remaining, BATCH)

                if end_time_ms is not None:
                    # Paginate backwards via Binance endTime cursor
                    import httpx
                    async with httpx.AsyncClient() as hc:
                        resp = await hc.get(
                            "https://api.binance.com/api/v3/klines",
                            params={
                                "symbol": config.symbol,
                                "interval": config.interval,
                                "limit": batch_limit,
                                "endTime": end_time_ms,
                            },
                            timeout=15,
                        )
                    raw = resp.json()
                    batch_data = [
                        [int(k[0] / 1000), "60", k[1], k[2], k[3], k[4], k[5], k[5], config.symbol]
                        for k in raw
                    ]
                else:
                    response = await self.phemex.get_klines(
                        symbol=config.symbol,
                        interval=config.interval,
                        limit=batch_limit,
                    )
                    batch_data = response.get('data', response) if isinstance(response, dict) else response

                if not batch_data:
                    break

                all_klines.extend(batch_data)
                remaining -= len(batch_data)

                if len(batch_data) < batch_limit:
                    break  # exchange returned fewer than requested — no older data

                oldest_ts_sec = min(
                    (k[0] / 1000 if k[0] > 1e10 else k[0]) for k in batch_data
                )
                end_time_ms = int(oldest_ts_sec * 1000) - 1

            if not all_klines:
                return []

            klines = []
            for k in all_klines:
                klines.append({
                    'time': k[0] / 1000 if k[0] > 1e10 else k[0],
                    'open':   float(k[2]),
                    'high':   float(k[3]),
                    'low':    float(k[4]),
                    'close':  float(k[5]),
                    'volume': float(k[7]),
                })

            # De-duplicate and sort ascending
            seen: set = set()
            unique = []
            for k in klines:
                if k['time'] not in seen:
                    seen.add(k['time'])
                    unique.append(k)
            unique.sort(key=lambda x: x['time'])
            return unique

        except Exception as e:
            raise ValueError(f"Failed to fetch historical data: {str(e)}")

    def _calculate_metrics(
        self,
        trades: List[Dict],
        equity_curve: List[float],
        drawdown_curve: List[float],
        initial_balance: float,
        total_fees: float,
    ) -> BacktestResult:
        empty = BacktestResult(
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate=0.0, total_pnl=0.0, net_pnl=0.0, total_fees=total_fees,
            max_drawdown=0.0, sharpe_ratio=0.0, avg_trade_pnl=0.0,
            profit_factor=0.0, avg_win=0.0, avg_loss=0.0,
            max_consecutive_wins=0, max_consecutive_losses=0,
            trades=[], equity_curve=equity_curve, drawdown_curve=drawdown_curve,
        )
        if not trades:
            return empty

        exit_trades = [t for t in trades if t['type'] == 'EXIT']
        if not exit_trades:
            empty.trades = trades
            return empty

        wins = [t for t in exit_trades if t.get('net_pnl', t.get('pnl', 0)) > 0]
        losses = [t for t in exit_trades if t.get('net_pnl', t.get('pnl', 0)) <= 0]

        gross_pnl = sum(t.get('pnl', 0) for t in exit_trades)
        net_pnl = sum(t.get('net_pnl', t.get('pnl', 0)) for t in exit_trades)

        win_rate = len(wins) / len(exit_trades)
        avg_trade_pnl = net_pnl / len(exit_trades)

        total_win = sum(t.get('net_pnl', t.get('pnl', 0)) for t in wins)
        total_loss = abs(sum(t.get('net_pnl', t.get('pnl', 0)) for t in losses))
        avg_win = total_win / len(wins) if wins else 0.0
        avg_loss = total_loss / len(losses) if losses else 0.0
        profit_factor = total_win / total_loss if total_loss > 0 else float('inf') if total_win > 0 else 0.0

        # Consecutive wins/losses
        max_cw = max_cl = cw = cl = 0
        for t in exit_trades:
            if t.get('net_pnl', t.get('pnl', 0)) > 0:
                cw += 1
                cl = 0
            else:
                cl += 1
                cw = 0
            max_cw = max(max_cw, cw)
            max_cl = max(max_cl, cl)

        max_dd = max(drawdown_curve) if drawdown_curve else 0.0

        # Sharpe ratio — annualized for hourly data (√8760 hours/year)
        returns = []
        for i in range(1, len(equity_curve)):
            ret = (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1] if equity_curve[i - 1] > 0 else 0
            returns.append(ret)

        if returns:
            avg_return = np.mean(returns)
            std_return = np.std(returns, ddof=1) if len(returns) > 1 else 1.0
            sharpe = float(avg_return / std_return * np.sqrt(8760)) if std_return > 0 else 0.0
        else:
            sharpe = 0.0

        return BacktestResult(
            total_trades=len(exit_trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            total_pnl=gross_pnl,
            net_pnl=net_pnl,
            total_fees=total_fees,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            avg_trade_pnl=avg_trade_pnl,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_consecutive_wins=max_cw,
            max_consecutive_losses=max_cl,
            trades=trades,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
        )

    async def optimize_parameters(
        self,
        symbol: str,
        interval: str,
        parameter_ranges: Dict[str, List],
        strategy: str = "momentum",
    ) -> Dict:
        best_result = None
        best_params = {}
        best_score = float('-inf')
        all_results: List[Dict] = []

        param_combinations = self._generate_param_combinations(parameter_ranges)

        # Test all combinations (capped at 27 = 3^3 for safety)
        for params in param_combinations[:27]:
            config = BacktestConfig(
                symbol=symbol,
                interval=interval,
                position_size_pct=params.get('position_size', 0.1),
                stop_loss_pct=params.get('stop_loss', 0.02),
                take_profit_pct=params.get('take_profit', 0.05),
                strategy=strategy,
            )

            try:
                result = await self.run_backtest(config)
                # Composite score: risk-adjusted returns
                score = (
                    result.sharpe_ratio * 0.4
                    + result.win_rate * 0.2
                    + (result.net_pnl / config.initial_balance) * 0.2
                    + (result.profit_factor / 5.0 if result.profit_factor != float('inf') else 1.0) * 0.1
                    - result.max_drawdown * 0.1
                )

                all_results.append({
                    'params': params,
                    'score': score,
                    'win_rate': result.win_rate,
                    'net_pnl': result.net_pnl,
                    'sharpe': result.sharpe_ratio,
                    'max_dd': result.max_drawdown,
                    'trades': result.total_trades,
                    'profit_factor': result.profit_factor,
                })

                if score > best_score:
                    best_score = score
                    best_result = result
                    best_params = params
            except Exception as e:
                logger.debug(f"Optimization combo failed: {e}")
                continue

        return {
            'best_params': best_params,
            'best_score': best_score,
            'metrics': {
                'total_trades': best_result.total_trades if best_result else 0,
                'win_rate': best_result.win_rate if best_result else 0,
                'total_pnl': best_result.total_pnl if best_result else 0,
                'net_pnl': best_result.net_pnl if best_result else 0,
                'total_fees': best_result.total_fees if best_result else 0,
                'sharpe_ratio': best_result.sharpe_ratio if best_result else 0,
                'max_drawdown': best_result.max_drawdown if best_result else 0,
                'profit_factor': best_result.profit_factor if best_result else 0,
            },
            'all_results': sorted(all_results, key=lambda x: x['score'], reverse=True)[:10],
        }

    def _generate_param_combinations(self, ranges: Dict[str, List]) -> List[Dict]:
        combinations = [{}]

        for key, values in ranges.items():
            new_combos = []
            for combo in combinations:
                for value in values:
                    new_combo = combo.copy()
                    new_combo[key] = value
                    new_combos.append(new_combo)
            combinations = new_combos

        return combinations


backtest_engine = BacktestEngine()
