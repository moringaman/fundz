import { useEffect, useRef } from 'react';
import { wsClient } from '../lib/websocket';
import { useAppStore } from '../lib/store';
import { marketApi } from '../lib/api';

const FALLBACK_POLL_MS = 10_000;
const FALLBACK_TIMEOUT_MS = 5_000;

/**
 * Drives all market data into the Zustand store.
 *
 * Strategy:
 *  1. On mount / symbol / timeframe change: fetch REST immediately as baseline.
 *  2. WebSocket messages update state in real-time (kline upsert, ticker, indicators, signal).
 *  3. If WS is not connected within FALLBACK_TIMEOUT_MS, activate 10s REST polling.
 *  4. When WS reconnects the polling stops.
 */
export function useMarketStream(timeframe: string) {
  const {
    selectedSymbol,
    wsStatus,
    setKlines,
    upsertKline,
    setTicker,
    setTickerForSymbol,
    setIndicators,
    setSignal,
  } = useAppStore();

  // ─── Initial REST fetch ───────────────────────────────────────────────────
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
      setKlines(klines);
    } catch {
      /* keep existing data */
    }

    try {
      const tickerRes = await marketApi.getTicker(selectedSymbol);
      const t = tickerRes.data?.result ?? {};
      setTicker({
        symbol: selectedSymbol,
        lastPrice: parseFloat(t.closeRp ?? '0') / 100000,
        priceChange: (parseFloat(t.closeRp ?? '0') - parseFloat(t.openRp ?? '0')) / 100000,
        priceChangePercent:
          ((parseFloat(t.closeRp ?? '0') - parseFloat(t.openRp ?? '0')) /
            Math.max(parseFloat(t.openRp ?? '1'), 1)) *
          100,
        high: parseFloat(t.highRp ?? '0') / 100000,
        low: parseFloat(t.lowRp ?? '0') / 100000,
        volume: parseFloat(t.turnoverRv ?? '0') / 100000,
      });
    } catch {
      /* keep existing data */
    }

    try {
      const indRes = await marketApi.getIndicators(selectedSymbol, timeframe, 200);
      if (indRes.data?.indicators) {
        setIndicators(indRes.data.indicators);
        setSignal(indRes.data.signal);
      }
    } catch {
      /* keep existing data */
    }
  };

  // ─── Initial fetch on symbol / timeframe change ───────────────────────────
  useEffect(() => {
    restFetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol, timeframe]);

  // ─── WebSocket message handlers ───────────────────────────────────────────
  useEffect(() => {
    const onKline = (msg: unknown) => {
      const { data } = msg as { data: { time: number; open: number; high: number; low: number; close: number; volume: number } };
      if (data) upsertKline(data);
    };

    const onTicker = (msg: unknown) => {
      const { symbol, data } = msg as { symbol: string; data: { lastPrice: number; priceChange: number; priceChangePercent: number; high: number; low: number; volume: number } };
      if (data) {
        const ticker = { symbol, ...data };
        // Update all-symbols ticker map
        setTickerForSymbol(symbol, ticker);
        // Keep the single ticker for backward compatibility (selected symbol)
        if (symbol === selectedSymbol) {
          setTicker(ticker);
        }
      }
    };

    const onIndicators = (msg: unknown) => {
      const { data } = msg as { data: Record<string, number | null> };
      if (data) setIndicators(data as unknown as Parameters<typeof setIndicators>[0]);
    };

    const onSignal = (msg: unknown) => {
      const { data } = msg as { data: { action: 'buy' | 'sell' | 'hold'; confidence: number; reasoning: string } };
      if (data) setSignal(data);
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
  }, [upsertKline, setTicker, setIndicators, setSignal]);

  // ─── Fallback polling when WS is not connected ────────────────────────────
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
      // Start polling after a grace period if WS hasn't connected
      if (!fallbackTimeoutRef.current && !fallbackRef.current) {
        fallbackTimeoutRef.current = setTimeout(() => {
          fallbackTimeoutRef.current = null;
          if (useAppStore.getState().wsStatus !== 'connected') {
            fallbackRef.current = setInterval(restFetch, FALLBACK_POLL_MS);
          }
        }, FALLBACK_TIMEOUT_MS);
      }
    }

    return clearFallback;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsStatus, selectedSymbol, timeframe]);
}
