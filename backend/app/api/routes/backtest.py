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
    # Phase 1 — Monte Carlo bootstrap of trade PnL. Default ON; flip off to skip
    # the resampling step (e.g. for tight unit-test loops).
    run_montecarlo: bool = True
    mc_simulations: Optional[int] = None  # None → adaptive
    mc_seed: Optional[int] = None         # for reproducible runs


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
    # Phase 1 — Monte Carlo bootstrap percentiles. None when MC was disabled or
    # the backtest produced zero trades.
    mc_summary: Optional[dict] = None


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
            run_montecarlo=config.run_montecarlo,
            mc_simulations=config.mc_simulations,
            mc_seed=config.mc_seed,
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
            mc_summary=result.mc_summary,
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


# ── Phase 2 — Walk-forward analysis ─────────────────────────────────────────

class WalkForwardRequest(BaseModel):
    symbol: str
    interval: str = "1h"
    initial_balance: float = 10000.0
    position_size_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.05
    strategy: str = "momentum"
    candle_limit: int = 5000           # need more candles for multiple windows
    n_windows: int = 6
    mode: str = "anchored"             # "anchored" | "rolling"
    agent_id: Optional[str] = None


@router.post("/walk-forward")
async def run_walk_forward_route(req: WalkForwardRequest):
    """Run walk-forward analysis and persist as a BacktestRecord.

    The persisted row carries the walk-forward summary plus the OOS-stitched
    equity curve in `equity_curve` so the existing history view doesn't get
    confused. Source is `walkforward` for filtering.
    """
    try:
        from app.services.backtest_walkforward import run_walk_forward as _run_wf

        if req.mode not in ("anchored", "rolling"):
            raise ValueError(f"Invalid mode '{req.mode}', expected 'anchored' or 'rolling'")

        cfg = BacktestConfig(
            symbol=req.symbol,
            interval=req.interval,
            initial_balance=req.initial_balance,
            position_size_pct=req.position_size_pct,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
            strategy=req.strategy,
            candle_limit=req.candle_limit,
            run_montecarlo=False,  # MC per window adds noise; full-run MC is the way
        )

        wf_result = await _run_wf(cfg, n_windows=req.n_windows, mode=req.mode)
        wf_dict = wf_result.to_dict()

        # Persist as BacktestRecord with source=walkforward. Pull headline metrics
        # from the OOS aggregate so the standard history view shows OOS perf.
        record_id = await _persist_walk_forward(req, cfg, wf_dict)

        return {
            "id": record_id,
            "walk_forward": wf_dict,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Walk-forward error: {str(e)}")


async def _persist_walk_forward(
    req: "WalkForwardRequest",
    cfg: BacktestConfig,
    wf_dict: dict,
) -> Optional[str]:
    """Save walk-forward run as a BacktestRecord. Returns record id."""
    try:
        async with get_async_session() as session:
            # Aggregate OOS metrics across windows for the headline columns
            windows = wf_dict.get("windows", [])
            n_oos_trades = sum(w.get("oos_total_trades", 0) for w in windows)
            sum_oos_pnl = sum(w.get("oos_net_pnl", 0.0) for w in windows)
            # Win rate weighted by trade count — simple approximation
            wr_num = sum(
                w.get("oos_win_rate", 0.0) * w.get("oos_total_trades", 0) for w in windows
            )
            agg_win_rate = (wr_num / n_oos_trades) if n_oos_trades > 0 else 0.0

            record = BacktestRecord(
                agent_id=req.agent_id,
                symbol=req.symbol,
                strategy=req.strategy,
                interval=req.interval,
                config_params={
                    "initial_balance": cfg.initial_balance,
                    "position_size_pct": cfg.position_size_pct,
                    "stop_loss_pct": cfg.stop_loss_pct,
                    "take_profit_pct": cfg.take_profit_pct,
                    "n_windows": req.n_windows,
                    "mode": req.mode,
                },
                total_trades=n_oos_trades,
                winning_trades=0,  # not tracked per-window; kept zero for now
                losing_trades=0,
                win_rate=agg_win_rate,
                total_pnl=sum_oos_pnl,
                net_pnl=sum_oos_pnl,
                total_fees=0.0,
                max_drawdown=wf_dict.get("oos_max_drawdown", 0.0),
                sharpe_ratio=wf_dict.get("mean_oos_sharpe", 0.0) or 0.0,
                avg_trade_pnl=(sum_oos_pnl / n_oos_trades) if n_oos_trades > 0 else 0.0,
                profit_factor=0.0,  # not aggregated cross-window
                equity_curve=wf_dict.get("oos_equity_curve", [])[-200:],
                trades_data=[],  # individual trades not retained across windows
                walk_forward_summary=wf_dict,
                source="walkforward",
                candle_count=wf_dict.get("total_candles", 0),
            )
            session.add(record)
            await session.commit()
            return record.id
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to persist walk-forward: {e}")
        return None


# ── Phase 3 — Strategy sensitivity sweep ─────────────────────────────────────

class SensitivityRequest(BaseModel):
    symbol: str
    interval: str = "1h"
    strategy: str = "momentum"
    axis_x: str                          # "stop_loss" | "take_profit" | "position_size"
    axis_y: str
    values_x: List[float]
    values_y: List[float]
    chosen_x: float                      # the agent's current value on axis_x
    chosen_y: float                      # ditto axis_y
    candle_limit: int = 5000
    agent_id: Optional[str] = None


@router.post("/sensitivity")
async def run_sensitivity_route(req: SensitivityRequest):
    """Run a 2D sensitivity sweep and persist as a SensitivityRecord."""
    try:
        from app.services.backtest_sensitivity import run_sensitivity_analysis

        result = await run_sensitivity_analysis(
            symbol=req.symbol,
            interval=req.interval,
            strategy=req.strategy,
            axis_x=req.axis_x,
            axis_y=req.axis_y,
            values_x=req.values_x,
            values_y=req.values_y,
            chosen_x=req.chosen_x,
            chosen_y=req.chosen_y,
            candle_limit=req.candle_limit,
        )
        record_id = await _persist_sensitivity(req, result)
        out = result.to_dict()
        out['id'] = record_id
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sensitivity error: {str(e)}")


async def _persist_sensitivity(req: "SensitivityRequest", result) -> Optional[str]:
    """Save sweep result. Returns record id."""
    try:
        from app.models import SensitivityRecord
        async with get_async_session() as session:
            # Sanitise stability_score for JSON / numeric column — NaN is invalid
            # in JSON and Postgres will reject it for a Float column.
            import math
            score = result.stability_score
            score_db = None if (score is None or math.isnan(score)) else float(score)

            record = SensitivityRecord(
                agent_id=req.agent_id,
                symbol=req.symbol,
                strategy=req.strategy,
                interval=req.interval,
                axis_x=req.axis_x,
                axis_y=req.axis_y,
                chosen_x_value=result.chosen_x_value,
                chosen_y_value=result.chosen_y_value,
                chosen_sharpe=result.chosen_sharpe,
                chosen_net_pnl=result.chosen_net_pnl,
                chosen_max_dd=result.chosen_max_dd,
                stability_score=score_db,
                stability_tier=result.stability_tier,
                n_cells_total=result.n_cells_total,
                n_cells_valid=result.n_cells_valid,
                surface={
                    'axis_x': result.axis_x,
                    'axis_y': result.axis_y,
                    'values_x': result.values_x,
                    'values_y': result.values_y,
                    'cells': result.cells,
                },
                source="manual",
            )
            session.add(record)
            await session.commit()
            return record.id
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to persist sensitivity: {e}")
        return None


@router.get("/sensitivity/latest")
async def get_latest_sensitivity(agent_id: Optional[str] = None, limit: int = 10):
    """Recent sensitivity sweeps. Headline columns only — caller fetches the
    full surface by id when ready to render the heatmap."""
    try:
        from sqlalchemy import select, desc
        from app.models import SensitivityRecord
        async with get_async_session() as session:
            q = select(SensitivityRecord).order_by(desc(SensitivityRecord.created_at)).limit(limit)
            if agent_id:
                q = q.where(SensitivityRecord.agent_id == agent_id)
            res = await session.execute(q)
            rows = res.scalars().all()
            return [
                {
                    'id': r.id,
                    'agent_id': r.agent_id,
                    'symbol': r.symbol,
                    'strategy': r.strategy,
                    'axis_x': r.axis_x,
                    'axis_y': r.axis_y,
                    'chosen_sharpe': r.chosen_sharpe,
                    'stability_score': r.stability_score,
                    'stability_tier': r.stability_tier,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sensitivity history: {str(e)}")


@router.get("/sensitivity/{record_id}")
async def get_sensitivity_detail(record_id: str):
    """Full sweep including the surface grid."""
    try:
        from sqlalchemy import select
        from app.models import SensitivityRecord
        async with get_async_session() as session:
            res = await session.execute(select(SensitivityRecord).where(SensitivityRecord.id == record_id))
            r = res.scalar_one_or_none()
            if not r:
                raise HTTPException(status_code=404, detail="Sensitivity record not found")
            return {
                'id': r.id,
                'agent_id': r.agent_id,
                'symbol': r.symbol,
                'strategy': r.strategy,
                'interval': r.interval,
                'axis_x': r.axis_x,
                'axis_y': r.axis_y,
                'chosen_x_value': r.chosen_x_value,
                'chosen_y_value': r.chosen_y_value,
                'chosen_sharpe': r.chosen_sharpe,
                'chosen_net_pnl': r.chosen_net_pnl,
                'chosen_max_dd': r.chosen_max_dd,
                'stability_score': r.stability_score,
                'stability_tier': r.stability_tier,
                'n_cells_total': r.n_cells_total,
                'n_cells_valid': r.n_cells_valid,
                'surface': r.surface,
                'source': r.source,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sensitivity detail: {str(e)}")


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
                mc_summary=result.mc_summary,
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
