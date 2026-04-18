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

    async def run_backtest(self, config: BacktestConfig) -> BacktestResult:
        klines = await self._fetch_historical_data(config)

        # Require minimum 500 candles for robust indicator calculation (RSI, MACD, Bollinger Bands need warmup)
        if len(klines) < 500:
            raise ValueError(f"Not enough data. Need at least 500 candles for quality indicators, got {len(klines)}")

        df = pd.DataFrame(klines)
        df = df.sort_values('time').reset_index(drop=True)

        all_indicators = self.indicator_service.calculate_all(df)

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

        # Start at candle 150 to allow full warmup for long-period indicators (200-candle SMA, etc.)
        for i in range(150, len(df)):
            current_time = df.iloc[i]['time']
            current_price = float(df.iloc[i]['close'])

            signal = self.indicator_service.generate_signal(df.iloc[:i + 1], {'strategy': config.strategy})
            signal_action = signal.signal.value if signal.signal else 'hold'

            # Mirror the live agent confidence gate — only trade signals with meaningful edge
            if signal.confidence < 0.6:
                signal_action = 'hold'

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

                should_exit = (
                    pnl_pct <= -config.stop_loss_pct
                    or pnl_pct >= config.take_profit_pct
                    or trailing_stop_hit
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
