import { useEffect, useState } from 'react';
import { wsClient } from '../lib/websocket';

export interface CoinWhaleBias {
  coin: string;
  bias: 'bullish' | 'bearish' | 'neutral';
  long_notional: number;
  short_notional: number;
  net_notional: number;
  whale_count: number;
  avg_leverage: number;
}

export interface WhaleIntelligenceData {
  timestamp: string;
  coin_biases: Record<string, CoinWhaleBias>;
  total_whales_tracked: number;
  total_whales_with_positions: number;
}

/**
 * Subscribes to real-time whale intelligence updates via WebSocket.
 * Returns the latest data snapshot, or null if no broadcast received yet.
 */
export function useWhaleStream(): WhaleIntelligenceData | null {
  const [data, setData] = useState<WhaleIntelligenceData | null>(null);

  useEffect(() => {
    const handler = (msg: unknown) => {
      const m = msg as { type?: string; data?: WhaleIntelligenceData };
      if (m?.data) setData(m.data);
    };

    wsClient.on('whale_intelligence', handler);
    return () => wsClient.off('whale_intelligence', handler);
  }, []);

  return data;
}
