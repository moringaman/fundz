from fastapi import APIRouter, HTTPException
from typing import Optional, List
from pydantic import BaseModel

from app.services.backtest import BacktestEngine, BacktestConfig, backtest_engine
from app.database import get_async_session
from app.models import BacktestRecord

router = APIRouter(prefix="/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    symbol: str
    interval: str = "1h"
    initial_balance: float = 10000.0
    position_size_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    strategy: str = "momentum"
    maker_fee_pct: float = 0.01
    taker_fee_pct: float = 0.06
    slippage_pct: float = 0.02
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 0.03
    # EXPANDED candle limit from 500 to 3000 for API requests (users can still request more)
    # 3000 candles: ~125 days on 1h, ~20 days on 15m, ~500 days on 4h
    candle_limit: int = 3000
    agent_id: Optional[str] = None


class BacktestResponse(BaseModel):
    id: Optional[str] = None
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    net_pnl: float
    total_fees: float
    max_drawdown: float
    sharpe_ratio: float
    avg_trade_pnl: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    trades: List[dict]
    equity_curve: List[float]
    drawdown_curve: List[float]


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
            maker_fee_pct=config.maker_fee_pct,
            taker_fee_pct=config.taker_fee_pct,
            slippage_pct=config.slippage_pct,
            use_trailing_stop=config.use_trailing_stop,
            trailing_stop_pct=config.trailing_stop_pct,
            candle_limit=config.candle_limit,
        )

        result = await backtest_engine.run_backtest(backtest_config)

        # Persist to DB
        record_id = await _persist_backtest(config, result, source="manual")

        return BacktestResponse(
            id=record_id,
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            win_rate=result.win_rate,
            total_pnl=result.total_pnl,
            net_pnl=result.net_pnl,
            total_fees=result.total_fees,
            max_drawdown=result.max_drawdown,
            sharpe_ratio=result.sharpe_ratio,
            avg_trade_pnl=result.avg_trade_pnl,
            profit_factor=result.profit_factor,
            avg_win=result.avg_win,
            avg_loss=result.avg_loss,
            max_consecutive_wins=result.max_consecutive_wins,
            max_consecutive_losses=result.max_consecutive_losses,
            trades=result.trades[-20:],  # last 20 trades for response
            equity_curve=result.equity_curve,
            drawdown_curve=result.drawdown_curve,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest error: {str(e)}")


@router.post("/optimize")
async def optimize_parameters(
    symbol: str,
    interval: str = "1h",
    strategy: str = "momentum",
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

        result = await backtest_engine.optimize_parameters(symbol, interval, ranges, strategy=strategy)
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


@router.get("/history")
async def get_backtest_history(
    agent_id: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 20,
):
    """Retrieve historical backtest results from the database."""
    try:
        from sqlalchemy import select, desc
        async with get_async_session() as session:
            query = select(BacktestRecord).order_by(desc(BacktestRecord.created_at)).limit(limit)
            if agent_id:
                query = query.where(BacktestRecord.agent_id == agent_id)
            if strategy:
                query = query.where(BacktestRecord.strategy == strategy)

            result = await session.execute(query)
            records = result.scalars().all()

            return [
                {
                    "id": r.id,
                    "agent_id": r.agent_id,
                    "symbol": r.symbol,
                    "strategy": r.strategy,
                    "interval": r.interval,
                    "total_trades": r.total_trades,
                    "win_rate": r.win_rate,
                    "total_pnl": r.total_pnl,
                    "net_pnl": r.net_pnl,
                    "total_fees": r.total_fees,
                    "max_drawdown": r.max_drawdown,
                    "sharpe_ratio": r.sharpe_ratio,
                    "profit_factor": r.profit_factor,
                    "source": r.source,
                    "candle_count": r.candle_count,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in records
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")


async def _persist_backtest(config: BacktestRequest, result, source: str = "manual") -> Optional[str]:
    """Save backtest result to database. Returns record id."""
    try:
        from app.models import BacktestRecord
        async with get_async_session() as session:
            record = BacktestRecord(
                agent_id=config.agent_id,
                symbol=config.symbol,
                strategy=config.strategy,
                interval=config.interval,
                config_params={
                    "initial_balance": config.initial_balance,
                    "position_size_pct": config.position_size_pct,
                    "stop_loss_pct": config.stop_loss_pct,
                    "take_profit_pct": config.take_profit_pct,
                    "maker_fee_pct": config.maker_fee_pct,
                    "taker_fee_pct": config.taker_fee_pct,
                    "slippage_pct": config.slippage_pct,
                    "use_trailing_stop": config.use_trailing_stop,
                    "trailing_stop_pct": config.trailing_stop_pct,
                },
                total_trades=result.total_trades,
                winning_trades=result.winning_trades,
                losing_trades=result.losing_trades,
                win_rate=result.win_rate,
                total_pnl=result.total_pnl,
                net_pnl=result.net_pnl,
                total_fees=result.total_fees,
                max_drawdown=result.max_drawdown,
                sharpe_ratio=result.sharpe_ratio,
                avg_trade_pnl=result.avg_trade_pnl,
                profit_factor=result.profit_factor,
                equity_curve=result.equity_curve[-200:],  # keep last 200 points
                trades_data=result.trades[-50:],  # keep last 50 trades
                source=source,
                candle_count=len(result.equity_curve),
            )
            session.add(record)
            await session.commit()
            return record.id
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to persist backtest: {e}")
        return None
