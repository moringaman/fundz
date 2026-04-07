import { useEffect, useRef } from 'react';
import { wsClient } from '../lib/websocket';
import { marketApi } from '../lib/api';
import { useAppSelector, useAppDispatch } from '../store/hooks';
import {
  setKlines,
  upsertKline,
  setTicker,
  setTickerForSymbol,
  setIndicators,
  setSignal,
} from '../store/slices/marketSlice';
import { store } from '../store';

const FALLBACK_POLL_MS = 60_000;   // 60s (was 10s — too aggressive, causes redraws)
const FALLBACK_TIMEOUT_MS = 10_000; // Wait 10s before starting fallback

/**
 * Drives all market data into the Redux store.
 *
 * Strategy:
 *  1. On mount / symbol / timeframe change: fetch REST immediately as baseline.
 *  2. WebSocket messages update state in real-time (kline upsert, ticker, indicators, signal).
 *  3. If WS is not connected within FALLBACK_TIMEOUT_MS, activate 10s REST polling.
 *  4. When WS reconnects the polling stops.
 */
export function useMarketStream(timeframe: string) {
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const wsStatus = useAppSelector((s) => s.ui.wsStatus);
  const dispatch = useAppDispatch();

  const restFetch = async () => {
    try {
      const klinesRes = await marketApi.getKlines(selectedSymbol, timeframe, 200);
      const raw: unknown[][] = klinesRes.data?.data ?? [];
      const klines = raw
        .map((k) => ({
          time: (k[0] as number) / 1000,
          open: parseFloat(k[2] as string),
          high: parseFloat(k[3] as string),
          low: parseFloat(k[4] as string),
          close: parseFloat(k[5] as string),
          volume: parseFloat(k[7] as string) || 0,
        }))
        .sort((a, b) => a.time - b.time);
      dispatch(setKlines(klines));
    } catch {
      /* keep existing data */
    }

    try {
      const tickerRes = await marketApi.getTicker(selectedSymbol);
      const t = tickerRes.data?.result ?? {};
      dispatch(setTicker({
        symbol: selectedSymbol,
        lastPrice: parseFloat(t.closeRp ?? '0'),
        priceChange: parseFloat(t.closeRp ?? '0') - parseFloat(t.openRp ?? '0'),
        priceChangePercent:
          ((parseFloat(t.closeRp ?? '0') - parseFloat(t.openRp ?? '0')) /
            Math.max(parseFloat(t.openRp ?? '1'), 1)) *
          100,
        high: parseFloat(t.highRp ?? '0'),
        low: parseFloat(t.lowRp ?? '0'),
        volume: parseFloat(t.turnoverRv ?? '0'),
      }));
    } catch {
      /* keep existing data */
    }

    try {
      const indRes = await marketApi.getIndicators(selectedSymbol, timeframe, 200);
      if (indRes.data?.indicators) {
        dispatch(setIndicators(indRes.data.indicators));
        dispatch(setSignal(indRes.data.signal));
      }
    } catch {
      /* keep existing data */
    }
  };

  useEffect(() => {
    restFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, timeframe]);

  useEffect(() => {
    const onKline = (msg: unknown) => {
      const { symbol, data } = msg as { symbol?: string; data: { time: number; open: number; high: number; low: number; close: number; volume: number } };
      // Only apply kline updates for the currently selected symbol
      if (data && (!symbol || symbol === selectedSymbol)) dispatch(upsertKline(data));
    };

    const onTicker = (msg: unknown) => {
      const { symbol, data } = msg as { symbol: string; data: { lastPrice: number; priceChange: number; priceChangePercent: number; high: number; low: number; volume: number } };
      if (data) {
        const ticker = { symbol, ...data };
        dispatch(setTickerForSymbol({ symbol, ticker }));
        if (symbol === selectedSymbol) {
          dispatch(setTicker(ticker));
        }
      }
    };

    const onIndicators = (msg: unknown) => {
      const { symbol, data } = msg as { symbol?: string; data: Record<string, number | null> };
      if (data && (!symbol || symbol === selectedSymbol)) dispatch(setIndicators(data as any));
    };

    const onSignal = (msg: unknown) => {
      const { symbol, data } = msg as { symbol?: string; data: { action: 'buy' | 'sell' | 'hold'; confidence: number; reasoning: string } };
      if (data && (!symbol || symbol === selectedSymbol)) dispatch(setSignal(data));
    };

    wsClient.on('kline', onKline);
    wsClient.on('ticker', onTicker);
    wsClient.on('indicators', onIndicators);
    wsClient.on('signal', onSignal);

    return () => {
      wsClient.off('kline', onKline);
      wsClient.off('ticker', onTicker);
      wsClient.off('indicators', onIndicators);
      wsClient.off('signal', onSignal);
    };
  }, [dispatch, selectedSymbol]);

  const fallbackRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fallbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clearFallback = () => {
      if (fallbackRef.current) {
        clearInterval(fallbackRef.current);
        fallbackRef.current = null;
      }
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
        fallbackTimeoutRef.current = null;
      }
    };

    if (wsStatus === 'connected') {
      clearFallback();
    } else {
      if (!fallbackTimeoutRef.current && !fallbackRef.current) {
        fallbackTimeoutRef.current = setTimeout(() => {
          fallbackTimeoutRef.current = null;
          if (store.getState().ui.wsStatus !== 'connected') {
            fallbackRef.current = setInterval(restFetch, FALLBACK_POLL_MS);
          }
        }, FALLBACK_TIMEOUT_MS);
      }
    }

    return clearFallback;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsStatus, selectedSymbol, timeframe]);
}
