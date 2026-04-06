from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel

from app.services.backtest import BacktestEngine, BacktestConfig, backtest_engine

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbol: str
    interval: str = "1h"
    initial_balance: float = 10000.0
    position_size_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    strategy: str = "momentum"


class BacktestResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    avg_trade_pnl: float
    trades: List[dict]


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(config: BacktestRequest):
    try:
        backtest_config = BacktestConfig(
            symbol=config.symbol,
            interval=config.interval,
            initial_balance=config.initial_balance,
            position_size_pct=config.position_size_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            strategy=config.strategy,
        )
        
        result = await backtest_engine.run_backtest(backtest_config)
        
        return BacktestResponse(
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            win_rate=result.win_rate,
            total_pnl=result.total_pnl,
            max_drawdown=result.max_drawdown,
            sharpe_ratio=result.sharpe_ratio,
            avg_trade_pnl=result.avg_trade_pnl,
            trades=result.trades,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest error: {str(e)}")


@router.post("/optimize")
async def optimize_parameters(
    symbol: str,
    interval: str = "1h",
    position_size_range: Optional[str] = "0.05,0.1,0.2",
    stop_loss_range: Optional[str] = "0.01,0.02,0.05",
    take_profit_range: Optional[str] = "0.03,0.05,0.1",
):
    try:
        ranges = {
            'position_size': [float(x) for x in position_size_range.split(',')],
            'stop_loss': [float(x) for x in stop_loss_range.split(',')],
            'take_profit': [float(x) for x in take_profit_range.split(',')],
        }
        
        result = await backtest_engine.optimize_parameters(symbol, interval, ranges)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimization error: {str(e)}")


@router.get("/strategies")
async def get_strategies():
    return {
        "strategies": [
            {"id": "momentum", "name": "Momentum", "description": "Follows trend strength using RSI and MACD"},
            {"id": "mean_reversion", "name": "Mean Reversion", "description": "Trades around Bollinger Bands"},
            {"id": "breakout", "name": "Breakout", "description": "Trades price breakouts from ranges"},
        ]
    }
