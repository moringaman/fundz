import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';
import { useTradingPairs } from './useQueries';

const FALLBACK_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'];

/**
 * Manages the WebSocket lifecycle for the app.
 * - Connects on mount.
 * - Subscribes to configured trading pairs so the ticker strip has live data.
 */
export function useWebSocket() {
  const { data: configuredPairs } = useTradingPairs();
  const symbols = configuredPairs && configuredPairs.length > 0 ? configuredPairs : FALLBACK_SYMBOLS;

  useEffect(() => {
    wsClient.connect();
  }, []);

  // Re-subscribe whenever the configured pairs change
  useEffect(() => {
    wsClient.subscribe(symbols);
  }, [symbols.join(',')]);
}
