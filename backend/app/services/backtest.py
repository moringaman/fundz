from typing import List, Dict, Optional
from datetime import datetime
import pandas as pd
from dataclasses import dataclass

from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService, Signal, TradingSignal


@dataclass
class BacktestResult:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    avg_trade_pnl: float
    trades: List[Dict]


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
        
        if len(klines) < 100:
            raise ValueError(f"Not enough data. Need at least 100 candles, got {len(klines)}")
        
        df = pd.DataFrame(klines)
        df = df.sort_values('time')
        
        all_indicators = self.indicator_service.calculate_all(df)
        
        trades = []
        balance = config.initial_balance
        position = None
        entry_price = 0
        entry_time = 0
        
        equity_curve = []
        peak_balance = balance
        
        for i in range(50, len(df)):
            current_time = df.iloc[i]['time']
            current_price = df.iloc[i]['close']
            
            current_indicators = {
                'rsi': all_indicators.get('rsi'),
                'bb_upper': all_indicators.get('bb_upper'),
                'bb_middle': all_indicators.get('bb_middle'),
                'bb_lower': all_indicators.get('bb_lower'),
                'sma_20': all_indicators.get('sma_20'),
                'sma_50': all_indicators.get('sma_50'),
                'sma_200': all_indicators.get('sma_200'),
                'macd': all_indicators.get('macd'),
                'macd_signal': all_indicators.get('macd_signal'),
                'atr': all_indicators.get('atr'),
            }
            
            signal = self.indicator_service.generate_signal(df.iloc[:i+1], {'strategy': config.strategy})
            signal_action = signal.signal.value if signal.signal else 'hold'
            
            if position is None and signal_action in ['buy', 'sell']:
                position_size = balance * config.position_size_pct
                quantity = position_size / current_price
                
                position = {
                    'side': signal_action,
                    'entry_price': current_price,
                    'quantity': quantity,
                    'entry_time': current_time,
                }
                entry_price = current_price
                entry_time = current_time
                
                trades.append({
                    'time': current_time,
                    'type': 'ENTRY',
                    'side': signal_action,
                    'price': current_price,
                    'quantity': quantity,
                    'balance': balance,
                })
            
            elif position:
                pnl_pct = 0
                if position['side'] == 'buy':
                    pnl_pct = (current_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - current_price) / entry_price
                
                should_exit = (
                    pnl_pct <= -config.stop_loss_pct or
                    pnl_pct >= config.take_profit_pct or
                    signal_action == 'sell' if position['side'] == 'buy' else signal_action == 'buy'
                )
                
                if should_exit:
                    pnl = position['quantity'] * (current_price - entry_price) if position['side'] == 'buy' else position['quantity'] * (entry_price - current_price)
                    balance += pnl
                    
                    trades.append({
                        'time': current_time,
                        'type': 'EXIT',
                        'side': position['side'],
                        'price': current_price,
                        'quantity': position['quantity'],
                        'pnl': pnl,
                        'balance': balance,
                        'pnl_pct': pnl_pct * 100,
                    })
                    
                    position = None
            
            equity_curve.append(balance)
            if balance > peak_balance:
                peak_balance = balance
        
        return self._calculate_metrics(trades, equity_curve, config.initial_balance)
    
    async def _fetch_historical_data(self, config: BacktestConfig) -> List[Dict]:
        try:
            response = await self.phemex.get_klines(
                symbol=config.symbol,
                interval=config.interval,
                limit=500
            )
            
            data = response.get('data', response) if isinstance(response, dict) else response
            
            klines = []
            for k in data:
                klines.append({
                    'time': k[0] / 1000,
                    'open': float(k[2]),
                    'high': float(k[3]),
                    'low': float(k[4]),
                    'close': float(k[5]),
                    'volume': float(k[7]),
                })
            
            return klines
        except Exception as e:
            raise ValueError(f"Failed to fetch historical data: {str(e)}")
    
    def _calculate_metrics(self, trades: List[Dict], equity_curve: List[float], initial_balance: float) -> BacktestResult:
        if not trades:
            return BacktestResult(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                total_pnl=0.0,
                max_drawdown=0.0,
                sharpe_ratio=0.0,
                avg_trade_pnl=0.0,
                trades=[]
            )
        
        exit_trades = [t for t in trades if t['type'] == 'EXIT']
        
        winning_trades = [t for t in exit_trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in exit_trades if t.get('pnl', 0) <= 0]
        
        total_pnl = sum(t.get('pnl', 0) for t in exit_trades)
        
        win_rate = len(winning_trades) / len(exit_trades) if exit_trades else 0.0
        
        avg_trade_pnl = total_pnl / len(exit_trades) if exit_trades else 0.0
        
        peak = equity_curve[0]
        max_dd = 0
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        
        returns = []
        for i in range(1, len(equity_curve)):
            ret = (equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1] if equity_curve[i-1] > 0 else 0
            returns.append(ret)
        
        avg_return = sum(returns) / len(returns) if returns else 0
        std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1
        sharpe = (avg_return / std_return * (24 * 365) ** 0.5) if std_return > 0 else 0
        
        return BacktestResult(
            total_trades=len(exit_trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            avg_trade_pnl=avg_trade_pnl,
            trades=trades
        )
    
    async def optimize_parameters(
        self,
        symbol: str,
        interval: str,
        parameter_ranges: Dict[str, List]
    ) -> Dict:
        best_result = None
        best_params = {}
        best_score = float('-inf')
        
        param_combinations = self._generate_param_combinations(parameter_ranges)
        
        for params in param_combinations[:10]:
            config = BacktestConfig(
                symbol=symbol,
                interval=interval,
                position_size_pct=params.get('position_size', 0.1),
                stop_loss_pct=params.get('stop_loss', 0.02),
                take_profit_pct=params.get('take_profit', 0.05),
            )
            
            try:
                result = await self.run_backtest(config)
                score = result.sharpe_ratio * 0.5 + result.win_rate * 0.3 + (result.total_pnl / 10000) * 0.2
                
                if score > best_score:
                    best_score = score
                    best_result = result
                    best_params = params
            except:
                continue
        
        return {
            'best_params': best_params,
            'metrics': {
                'total_trades': best_result.total_trades if best_result else 0,
                'win_rate': best_result.win_rate if best_result else 0,
                'total_pnl': best_result.total_pnl if best_result else 0,
                'sharpe_ratio': best_result.sharpe_ratio if best_result else 0,
            }
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
