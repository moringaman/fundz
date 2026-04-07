export interface Kline {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Ticker {
  symbol: string;
  lastPrice: number;
  priceChange: number;
  priceChangePercent: number;
  high: number;
  low: number;
  volume: number;
}

export interface Indicators {
  rsi: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  sma_20: number | null;
  sma_50: number | null;
  sma_200: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  atr: number | null;
  volume_sma: number | null;
}

export interface Signal {
  action: 'buy' | 'sell' | 'hold';
  confidence: number;
  reasoning: string;
}

export interface Agent {
  id: string;
  name: string;
  strategy_type: string;
  trading_pairs: string[];
  is_enabled: boolean;
  allocation_percentage: number;
  max_position_size: number;
}

export interface Trade {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  status: string;
  created_at: string;
}

export interface Balance {
  asset: string;
  available: number;
  locked: number;
}

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface TeamChatMessage {
  id: string;
  agent_id: string;
  agent_name: string;
  agent_role: string;
  avatar: string;
  content: string;
  message_type: string;
  timestamp: string;
  mentions: string[];
  metadata: Record<string, any>;
}
