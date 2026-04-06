import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';

const ALL_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'];

/**
 * Manages the WebSocket lifecycle for the app.
 * - Connects on mount.
 * - Subscribes to ALL symbols so the ticker strip always has live data.
 */
export function useWebSocket() {
  useEffect(() => {
    wsClient.connect();
    // Subscribe to all symbols immediately so every ticker chip shows live data
    wsClient.subscribe(ALL_SYMBOLS);
    // wsClient is a module-level singleton — don't disconnect on cleanup
    // as React StrictMode double-mounts would kill the connection before
    // it finishes establishing.
  }, []);
}
