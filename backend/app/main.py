from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import json
import asyncio
import logging

from app.config import settings
from app.api.routes import market, trading, agents, backtest, paper_trading, automation, llm, fund
from app.api.routes import settings as settings_routes
from app.api.routes import traders as traders_routes
from app.api.routes import whale as whale_routes
from app.api.routes import grid as grid_routes
from app.api.routes import live_trading as live_trading_routes
from app.api.routes import strategies as strategies_routes

# Configure root logger so app.* loggers are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Noise reduction ───────────────────────────────────────────────────────────
# Only show WARNING+ for high-volume modules that produce per-cycle INFO spam.
# Errors, circuit breaker events, trade executions and blocks all use WARNING
# or higher so they remain visible.

# SQLAlchemy — suppress per-query echo
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

# uvicorn — suppress 200 OK access lines; errors surface naturally
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

# httpx / httpcore — suppress connection pool chatter
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Agent scheduler — fires every 5 min per agent; per-cycle INFO is noise.
# WARNING+ covers: trade blocked, circuit breaker, gate blocks, errors.
logging.getLogger("app.services.agent_scheduler").setLevel(logging.WARNING)

# Team chat — persists every message to DB; INFO confirmation is noise
logging.getLogger("app.services.team_chat").setLevel(logging.WARNING)

# Whale intelligence — 60s broadcast loop
logging.getLogger("app.services.whale_intelligence").setLevel(logging.WARNING)

# Position sync — runs continuously in background
logging.getLogger("app.services.position_sync").setLevel(logging.WARNING)

# Market data broadcast — runs every 5s
logging.getLogger("app.services.market_data").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a PostgreSQL table."""
    result = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.fetchone() is not None

api_router = APIRouter(prefix="/api")
api_router.include_router(market.router)
api_router.include_router(trading.router)
api_router.include_router(agents.router)
api_router.include_router(backtest.router)
api_router.include_router(paper_trading.router)
api_router.include_router(automation.router)
api_router.include_router(llm.router)
api_router.include_router(fund.router)
api_router.include_router(settings_routes.router)
api_router.include_router(traders_routes.router)
api_router.include_router(whale_routes.router)
api_router.include_router(grid_routes.router)
api_router.include_router(live_trading_routes.router)
api_router.include_router(strategies_routes.router)


class ConnectionManager:
    def __init__(self):
        # Maps WebSocket -> set of subscribed symbols
        self.active_connections: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = set()
        logger.info(f"WS client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)
        logger.info(f"WS client disconnected. Total: {len(self.active_connections)}")

    def subscribe(self, websocket: WebSocket, symbols: list[str]):
        if websocket in self.active_connections:
            self.active_connections[websocket].update(symbols)

    def unsubscribe(self, websocket: WebSocket, symbols: list[str]):
        if websocket in self.active_connections:
            self.active_connections[websocket].difference_update(symbols)

    def get_subscribed_symbols(self) -> set[str]:
        """All unique symbols across all connections."""
        all_symbols: set[str] = set()
        for syms in self.active_connections.values():
            all_symbols.update(syms)
        return all_symbols

    async def send_personal(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: dict, symbol: str | None = None):
        """Broadcast to all clients subscribed to symbol (or all if symbol is None)."""
        dead: list[WebSocket] = []
        for ws, subs in list(self.active_connections.items()):
            if symbol is None or symbol in subs:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _market_broadcast_loop():
    """
    Background task: every 5 seconds, fetch market data for all subscribed
    symbols via REST and broadcast typed WS messages to connected clients.
    """
    from app.clients.phemex import PhemexClient
    from app.services.indicators import IndicatorService

    phemex = PhemexClient(
        api_key=settings.phemex_api_key,
        api_secret=settings.phemex_api_secret,
        testnet=settings.phemex_testnet,
    )
    indicator_svc = IndicatorService()

    while True:
        await asyncio.sleep(5)
        if not manager.active_connections:
            continue

        symbols = manager.get_subscribed_symbols()
        if not symbols:
            continue

        for symbol in symbols:
            try:
                # --- ticker ---
                ticker_resp = await phemex.get_ticker(symbol)
                ticker_data = (ticker_resp or {}).get("result", {})
                if ticker_data:
                    close_price = float(ticker_data.get("closeRp", 0))
                    open_price = float(ticker_data.get("openRp", 0))
                    await manager.broadcast(
                        {
                            "type": "ticker",
                            "symbol": symbol,
                            "data": {
                                "lastPrice": close_price,
                                "priceChange": close_price - open_price,
                                "priceChangePercent": (
                                    (close_price - open_price)
                                    / max(open_price, 1)
                                ) * 100,
                                "high": float(ticker_data.get("highRp", 0)),
                                "low": float(ticker_data.get("lowRp", 0)),
                                "volume": float(ticker_data.get("turnoverRv", 0)),
                            },
                        },
                        symbol=symbol,
                    )
            except Exception as e:
                logger.debug(f"Ticker fetch failed for {symbol}: {e}")

            try:
                # --- klines (last bar only for live tick) ---
                klines_resp = await phemex.get_klines(symbol, "1h", 200)
                raw = klines_resp.get("data", klines_resp) if isinstance(klines_resp, dict) else klines_resp
                if raw and len(raw) >= 2:
                    import pandas as pd
                    df_rows = []
                    for k in raw:
                        df_rows.append({
                            "time": k[0] // 1000,
                            "open": float(k[2]),
                            "high": float(k[3]),
                            "low": float(k[4]),
                            "close": float(k[5]),
                            "volume": float(k[7]),
                        })
                    df = pd.DataFrame(df_rows).sort_values("time")

                    # Broadcast the latest bar for live-tick
                    last = df.iloc[-1]
                    await manager.broadcast(
                        {
                            "type": "kline",
                            "symbol": symbol,
                            "interval": "1h",
                            "data": {
                                "time": int(last["time"]),
                                "open": float(last["open"]),
                                "high": float(last["high"]),
                                "low": float(last["low"]),
                                "close": float(last["close"]),
                                "volume": float(last["volume"]),
                            },
                        },
                        symbol=symbol,
                    )

                    # --- indicators + signal ---
                    try:
                        ind = indicator_svc.calculate_all(df)
                        sig = indicator_svc.generate_signal(df)
                        await manager.broadcast(
                            {
                                "type": "indicators",
                                "symbol": symbol,
                                "data": {
                                    "rsi": ind.get("rsi"),
                                    "bb_upper": ind.get("bb_upper"),
                                    "bb_middle": ind.get("bb_middle"),
                                    "bb_lower": ind.get("bb_lower"),
                                    "sma_20": ind.get("sma_20"),
                                    "sma_50": ind.get("sma_50"),
                                    "sma_200": ind.get("sma_200"),
                                    "macd": ind.get("macd"),
                                    "macd_signal": ind.get("macd_signal"),
                                    "macd_histogram": ind.get("macd_histogram"),
                                    "atr": ind.get("atr"),
                                    "volume_sma": ind.get("volume_sma"),
                                },
                            },
                            symbol=symbol,
                        )
                        signal_val = sig.signal.value if sig and sig.signal else "hold"
                        await manager.broadcast(
                            {
                                "type": "signal",
                                "symbol": symbol,
                                "data": {
                                    "action": signal_val,
                                    "confidence": sig.confidence if sig else 0,
                                    "reasoning": getattr(sig, "reasoning", ""),
                                },
                            },
                            symbol=symbol,
                        )
                    except Exception as e:
                        logger.debug(f"Indicator calc failed for {symbol}: {e}")

            except Exception as e:
                logger.debug(f"Klines fetch failed for {symbol}: {e}")


async def _whale_broadcast_loop():
    """
    Background task: every 60 seconds, fetch whale intelligence and broadcast
    to all connected WS clients. Respects Hyperliquid API rate limits via cache.
    """
    from datetime import timezone
    from app.services.whale_intelligence import whale_intelligence
    from app.services.team_chat import team_chat
    from app.database import get_async_session

    previous_report = None

    _snapshot_cycle = 0

    while True:
        await asyncio.sleep(60)
        if not manager.active_connections:
            continue
        try:
            async with get_async_session() as db:
                report = await whale_intelligence.fetch_whale_report(db)

            if report is None:
                continue

            # Alert team chat on significant position changes
            if previous_report is not None:
                await whale_intelligence.check_and_alert_significant_moves(
                    previous_report, report, team_chat
                )
            previous_report = report

            await manager.broadcast({
                "type": "whale_intelligence",
                "data": {
                    "timestamp": report.timestamp.isoformat(),
                    "coin_biases": {
                        coin: {
                            "bias": bias.bias,
                            "long_notional": bias.long_notional,
                            "short_notional": bias.short_notional,
                            "net_notional": bias.net_notional,
                            "whale_count": bias.whale_count,
                            "avg_leverage": bias.avg_leverage,
                        }
                        for coin, bias in report.coin_biases.items()
                    },
                    "total_whales_tracked": report.total_whales_tracked,
                    "total_whales_with_positions": report.total_whales_with_positions,
                },
            })

            # Persist snapshots every 10 minutes (not every 60s) to avoid
            # constant checkpoint pressure — 25 rows * 60s = 1500 rows/hr otherwise.
            _snapshot_cycle += 1
            if report.all_positions and _snapshot_cycle >= 10:
                _snapshot_cycle = 0
                try:
                    async with get_async_session() as snap_db:
                        await whale_intelligence.persist_snapshots(report, snap_db)
                        # Prune snapshots older than 7 days to keep the table lean
                        from sqlalchemy import text as _text
                        from datetime import timedelta
                        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                        await snap_db.execute(
                            _text("DELETE FROM whale_snapshots WHERE captured_at < :cutoff"),
                            {"cutoff": cutoff},
                        )
                        await snap_db.commit()
                except Exception as e:
                    logger.debug(f"Whale snapshot persist/prune failed: {e}")

        except Exception as e:
            logger.debug(f"Whale broadcast failed: {e}")


@api_router.websocket("/ws/market")
async def websocket_market(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await manager.send_personal({"type": "connected", "status": "ok"}, websocket)

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await manager.send_personal({"type": "pong"}, websocket)

            elif msg_type == "subscribe":
                symbols = msg.get("symbols", [])
                manager.subscribe(websocket, symbols)
                await manager.send_personal(
                    {"type": "subscribed", "symbols": symbols}, websocket
                )

            elif msg_type == "unsubscribe":
                symbols = msg.get("symbols", [])
                manager.unsubscribe(websocket, symbols)
                await manager.send_personal(
                    {"type": "unsubscribed", "symbols": symbols}, websocket
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WS error: {e}")
    finally:
        manager.disconnect(websocket)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine, Base
    from app.models import AgentRunRecord, AgentMetricRecord, TeamChatMessageRecord, DailyReport, Trader  # noqa: F401
    from app.models import GridState, GridLevel  # noqa: F401 — ensure grid tables are created
    from app.models import TraderLegacy  # noqa: F401 — Phase 9.2

    # ── Step 1: create all tables ────────────────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Step 2: incremental column migrations — each in its own transaction ──
    # PostgreSQL aborts the ENTIRE transaction on any failed statement, so we
    # must use separate transactions for each DDL that might already exist.
    _migrations = [
        ("positions", "stop_loss_price",   "ALTER TABLE positions ADD COLUMN stop_loss_price FLOAT"),
        ("positions", "take_profit_price", "ALTER TABLE positions ADD COLUMN take_profit_price FLOAT"),
        ("positions", "highest_price",     "ALTER TABLE positions ADD COLUMN highest_price FLOAT"),
        ("positions", "trailing_stop_pct", "ALTER TABLE positions ADD COLUMN trailing_stop_pct FLOAT"),
        ("positions", "leverage",          "ALTER TABLE positions ADD COLUMN leverage FLOAT DEFAULT 1.0"),
        ("positions", "margin_used",       "ALTER TABLE positions ADD COLUMN margin_used FLOAT DEFAULT 0.0"),
        ("positions", "liquidation_price", "ALTER TABLE positions ADD COLUMN liquidation_price FLOAT"),
        ("positions", "is_paper",          "ALTER TABLE positions ADD COLUMN is_paper BOOLEAN DEFAULT TRUE"),
        ("positions", "scale_out_levels",  "ALTER TABLE positions ADD COLUMN scale_out_levels TEXT"),
        ("trades",    "is_paper",          "ALTER TABLE trades ADD COLUMN is_paper BOOLEAN DEFAULT TRUE"),
        ("trades",    "leverage",          "ALTER TABLE trades ADD COLUMN leverage FLOAT DEFAULT 1.0"),
        ("trades",    "margin_used",       "ALTER TABLE trades ADD COLUMN margin_used FLOAT DEFAULT 0.0"),
        ("agents",    "trader_id",         "ALTER TABLE agents ADD COLUMN trader_id VARCHAR(36)"),
        ("trades",    "trader_id",         "ALTER TABLE trades ADD COLUMN trader_id VARCHAR(36)"),
        ("agent_metric_records", "actual_trades",  "ALTER TABLE agent_metric_records ADD COLUMN actual_trades INTEGER DEFAULT 0"),
        ("agent_metric_records", "winning_trades", "ALTER TABLE agent_metric_records ADD COLUMN winning_trades INTEGER DEFAULT 0"),
        ("positions", "grid_id",       "ALTER TABLE positions ADD COLUMN grid_id VARCHAR(36)"),
        ("positions", "grid_level_id", "ALTER TABLE positions ADD COLUMN grid_level_id VARCHAR(36)"),
        ("positions", "phemex_order_id", "ALTER TABLE positions ADD COLUMN phemex_order_id VARCHAR(100)"),
        ("agent_metric_records", "is_paper", "ALTER TABLE agent_metric_records ADD COLUMN is_paper BOOLEAN DEFAULT TRUE"),
        # Phase 9.2 — Drawdown tracking
        ("traders", "lifetime_peak_balance",  "ALTER TABLE traders ADD COLUMN lifetime_peak_balance FLOAT"),
        ("traders", "lifetime_drawdown_pct",  "ALTER TABLE traders ADD COLUMN lifetime_drawdown_pct FLOAT"),
        ("traders", "drawdown_warning_level", "ALTER TABLE traders ADD COLUMN drawdown_warning_level VARCHAR(20)"),
        ("traders", "successor_of",           "ALTER TABLE traders ADD COLUMN successor_of VARCHAR(36)"),
        # Grid engine — capital tracking
        ("grid_states", "initial_capital",    "ALTER TABLE grid_states ADD COLUMN initial_capital FLOAT"),
    ]
    for table, column, ddl in _migrations:
        try:
            async with engine.begin() as conn:
                exists = await _column_exists(conn, table, column)
                if not exists:
                    await conn.execute(text(ddl))
                    if column == "is_paper":
                        await conn.execute(text(
                            f"UPDATE {table} SET is_paper = TRUE WHERE is_paper IS NULL"
                        ))
        except Exception as e:
            logger.warning(f"Migration '{ddl[:60]}' failed (may already exist): {e}")

    # ── Step 3: unique indexes — own transactions, safe to fail ──────────────
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_position_user_agent_symbol "
                "ON positions (user_id, agent_id, symbol)"
            ))
    except Exception:
        pass  # already exists

    try:
        async with engine.begin() as conn:
            # Drop old single-column unique constraint, add composite (agent_id, is_paper)
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_metric_agent_mode "
                "ON agent_metric_records (agent_id, is_paper)"
            ))
    except Exception:
        pass  # already exists

    # ── Step 3b: deduplicate balances and add unique constraint on (user_id, asset) ─
    try:
        async with engine.begin() as conn:
            # Remove duplicate balance rows, keeping the one with the highest available value
            await conn.execute(text("""
                DELETE FROM balances
                WHERE id NOT IN (
                    SELECT DISTINCT ON (user_id, asset) id
                    FROM balances
                    ORDER BY user_id, asset, available DESC
                )
            """))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_balance_user_asset "
                "ON balances (user_id, asset)"
            ))
    except Exception as e:
        logger.warning(f"Balance dedup/unique index failed (non-fatal): {e}")

    # ── Step 4: seed strategy_overrides with enabled=True for all known types ─
    try:
        import app.strategies as strategy_registry
        from app.database import get_async_session
        async with get_async_session() as db:
            from app.models import StrategyOverride
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            for stype in strategy_registry.all_types():
                stmt = pg_insert(StrategyOverride).values(
                    strategy_type=stype, enabled=True
                ).on_conflict_do_nothing(index_elements=["strategy_type"])
                await db.execute(stmt)
            await db.commit()
    except Exception as e:
        logger.warning(f"strategy_overrides seed failed (non-fatal): {e}")

    from app.services.llm import llm_service
    try:
        await llm_service.initialize()
    except Exception as e:
        logger.warning(f"LLM service not initialized: {e}")

    # Load persisted settings from DB
    from app.api.routes.settings import _load_all_settings
    try:
        await _load_all_settings()
    except Exception as e:
        logger.warning(f"Settings load from DB failed (using defaults): {e}")

    # Wire team chat broadcasts to the WS connection manager
    from app.services.team_chat import team_chat
    team_chat.set_broadcast(lambda msg: manager.broadcast(msg))

    # Seed default whale watchlist in the background so it never blocks server startup.
    # The leaderboard fetch (~33K rows) can take a few seconds; we don't want to hold
    # up request handling while waiting for it.
    from app.services.whale_intelligence import whale_intelligence as _whale_svc
    async def _seed_whale_watchlist():
        from app.database import get_async_session as _get_session
        try:
            async with _get_session() as _db:
                await _whale_svc.seed_default_watchlist(_db)
        except Exception as e:
            logger.warning(f"Whale watchlist seeding failed: {e}")
    asyncio.create_task(_seed_whale_watchlist())

    broadcast_task = asyncio.create_task(_market_broadcast_loop())
    whale_task = asyncio.create_task(_whale_broadcast_loop())

    # Gate autopilot — auto-adjusts TradingGates every 30 min when enabled.
    from app.services.gate_autopilot import gate_autopilot as _gate_autopilot
    asyncio.create_task(_gate_autopilot.start_loop())

    # Telegram inbound polling — starts only if polling_enabled=True in config.
    # Zero overhead when disabled; a single idle long-poll connection when enabled.
    from app.services.telegram_service import telegram_service as _tg_svc
    telegram_poll_task = asyncio.create_task(_tg_svc.start_polling())

    # Auto-start the trading scheduler so it survives backend restarts without
    # requiring a manual click in the UI.
    async def _auto_start_scheduler():
        from app.services.agent_scheduler import agent_scheduler as _sched
        try:
            await _sched.start()
        except Exception as _e:
            logger.warning(f"Auto-start scheduler failed: {_e}")
    asyncio.create_task(_auto_start_scheduler())

    yield

    broadcast_task.cancel()
    whale_task.cancel()
    telegram_poll_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass
    try:
        await whale_task
    except asyncio.CancelledError:
        pass
    try:
        await telegram_poll_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_access_logger = logging.getLogger("api.access")

@app.middleware("http")
async def _log_errors(request, call_next):
    response = await call_next(request)
    if response.status_code >= 400:
        _access_logger.warning(
            "%s %s → %d", request.method, request.url.path, response.status_code
        )
    return response

app.include_router(api_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": settings.app_version}


@app.get("/")
async def root():
    return {"message": "Phemex AI Trader API", "version": settings.app_version}
