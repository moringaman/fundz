from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
import logging

from app.config import settings
from app.api.routes import market, trading, agents, backtest, paper_trading, automation, llm, fund
from app.api.routes import settings as settings_routes

logger = logging.getLogger(__name__)

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
                    await manager.broadcast(
                        {
                            "type": "ticker",
                            "symbol": symbol,
                            "data": {
                                "lastPrice": float(ticker_data.get("closeRp", 0)) / 100000,
                                "priceChange": (
                                    float(ticker_data.get("closeRp", 0))
                                    - float(ticker_data.get("openRp", 0))
                                ) / 100000,
                                "priceChangePercent": (
                                    (
                                        float(ticker_data.get("closeRp", 0))
                                        - float(ticker_data.get("openRp", 0))
                                    )
                                    / max(float(ticker_data.get("openRp", 1)), 1)
                                ) * 100,
                                "high": float(ticker_data.get("highRp", 0)) / 100000,
                                "low": float(ticker_data.get("lowRp", 0)) / 100000,
                                "volume": float(ticker_data.get("turnoverRv", 0)) / 100000,
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
    from app.models import AgentRunRecord, AgentMetricRecord, TeamChatMessageRecord, DailyReport  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.services.llm import llm_service
    try:
        await llm_service.initialize()
    except Exception as e:
        logger.warning(f"LLM service not initialized: {e}")

    # Wire team chat broadcasts to the WS connection manager
    from app.services.team_chat import team_chat
    team_chat.set_broadcast(lambda msg: manager.broadcast(msg))

    broadcast_task = asyncio.create_task(_market_broadcast_loop())

    yield

    broadcast_task.cancel()
    try:
        await broadcast_task
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

app.include_router(api_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": settings.app_version}


@app.get("/")
async def root():
    return {"message": "Phemex AI Trader API", "version": settings.app_version}
