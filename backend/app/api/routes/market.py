from fastapi import APIRouter, Query
from typing import Optional, List
import pandas as pd
from app.clients.phemex import PhemexClient
from app.config import settings
from app.services.indicators import IndicatorService

router = APIRouter(prefix="/market", tags=["market"])

phemex_client = PhemexClient(
    api_key=settings.phemex_api_key,
    api_secret=settings.phemex_api_secret,
    testnet=settings.phemex_testnet
)

indicator_service = IndicatorService()


@router.get("/klines")
async def get_klines(
    symbol: str = Query(..., description="Trading pair symbol"),
    interval: str = Query("1h", description="Kline interval (1m, 5m, 15m, 1h, 4h, 1d)"),
    limit: int = Query(100, ge=1, le=1000)
):
    klines = await phemex_client.get_klines(symbol, interval, limit)
    
    if isinstance(klines, dict) and "rows" in klines:
        return {"symbol": symbol, "interval": interval, "data": klines["rows"]}
    return {"symbol": symbol, "interval": interval, "data": klines}


@router.get("/indicators")
async def get_indicators(
    symbol: str = Query(..., description="Trading pair symbol"),
    interval: str = Query("1h", description="Kline interval"),
    limit: int = Query(200, ge=50, le=500)
):
    klines = await phemex_client.get_klines(symbol, interval, limit)
    
    if isinstance(klines, dict) and "rows" in klines:
        klines = klines["rows"]
    
    if not klines:
        return {"symbol": symbol, "error": "No data available"}
    
    df = pd.DataFrame(klines, columns=["timestamp", "resolution", "open", "high", "low", "close", "turnover", "volume", "symbol"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    
    indicators = indicator_service.calculate_all(df)
    signal = indicator_service.generate_signal(df, {})
    
    return {
        "symbol": symbol,
        "interval": interval,
        "indicators": indicators,
        "signal": {
            "action": signal.signal.value,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning
        }
    }


@router.get("/ticker")
async def get_ticker(symbol: str = Query(..., description="Trading pair symbol")):
    ticker = await phemex_client.get_ticker(symbol)
    return ticker


@router.get("/orderbook")
async def get_orderbook(
    symbol: str = Query(..., description="Trading pair symbol"),
    limit: int = Query(20, ge=1, le=100)
):
    orderbook = await phemex_client.get_orderbook(symbol, limit)
    return orderbook


@router.get("/trades")
async def get_trades(
    symbol: str = Query(..., description="Trading pair symbol"),
    limit: int = Query(50, ge=1, le=100)
):
    trades = await phemex_client.get_trades(symbol, limit)
    return {"symbol": symbol, "data": trades}
