import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const marketApi = {
  getKlines: (symbol: string, interval = '1h', limit = 100) =>
    api.get(`market/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`),
  
  getIndicators: (symbol: string, interval = '1h', limit = 200) =>
    api.get(`market/indicators?symbol=${symbol}&interval=${interval}&limit=${limit}`),
  
  getTicker: (symbol: string) =>
    api.get(`market/ticker?symbol=${symbol}`),
  
  getOrderbook: (symbol: string, limit = 20) =>
    api.get(`market/orderbook?symbol=${symbol}&limit=${limit}`),
  
  getTrades: (symbol: string, limit = 50) =>
    api.get(`market/trades?symbol=${symbol}&limit=${limit}`),
};

export const tradingApi = {
  placeOrder: (order: {
    symbol: string;
    side: string;
    quantity: number;
    price?: number;
    order_type?: string;
  }) => api.post('trading/order', order),
  
  cancelOrder: (orderId: string, symbol: string) =>
    api.delete(`trading/order/${orderId}?symbol=${symbol}`),
  
  getOpenOrders: (symbol?: string) =>
    api.get(`trading/orders${symbol ? `?symbol=${symbol}` : ''}`),
  
  getPositions: () => api.get('trading/positions'),
  
  getBalance: () => api.get('trading/balance'),
  
  getHistory: (symbol?: string, limit = 50) =>
    api.get(`trading/history${symbol ? `?symbol=${symbol}&limit=${limit}` : `?limit=${limit}`}`),
  
  getPnl: () => api.get('trading/pnl'),
};

export const agentApi = {
  getAgents: () => api.get('agents'),
  
  getStrategies: () => api.get('agents/strategies'),
  
  createAgent: (agent: any) => api.post('agents', agent),
  
  updateAgent: (id: string, agent: any) => api.put(`agents/${id}`, agent),
  
  deleteAgent: (id: string) => api.delete(`agents/${id}`),
  
  runAgent: (id: string) => api.post(`agents/${id}/run`),
  
  getSignals: (agentId?: string) =>
    api.get(`agents/signals${agentId ? `?agent_id=${agentId}` : ''}`),
  
  runBacktest: (agentId: string, symbol: string, interval: string) =>
    api.post(`agents/${agentId}/backtest?symbol=${symbol}&interval=${interval}`),
};

export const backtestApi = {
  runBacktest: (config: {
    symbol: string;
    interval: string;
    initial_balance: number;
    position_size_pct: number;
    stop_loss_pct: number;
    take_profit_pct: number;
    strategy: string;
  }) => api.post('backtest/run', config),
  
  getStrategies: () => api.get('backtest/strategies'),
};

export const paperApi = {
  getStatus: () => api.get('paper/status'),
  
  enable: () => api.post('paper/enable'),
  
  disable: () => api.post('paper/disable'),
  
  reset: () => api.post('paper/reset'),
  
  getBalance: () => api.get('paper/balance'),

  adjustBalance: (asset: string, amount: number) =>
    api.post('paper/balance/adjust', { asset, amount }),

  getPortfolio: () => api.get('paper/portfolio'),
  
  placeOrder: (order: {
    symbol: string;
    side: string;
    quantity: number;
    price: number;
  }) => api.post('paper/order', order),
  
  cancelOrder: (orderId: string) => api.delete(`paper/order/${orderId}`),
  
  getOrders: (symbol?: string, limit?: number) =>
    api.get(`paper/orders${symbol ? `?symbol=${symbol}&limit=${limit || 50}` : ''}`),
  
  getPositions: () => api.get('paper/positions'),

  updatePositionSlTp: (positionId: string, data: { stop_loss_price?: number; take_profit_price?: number; trailing_stop_pct?: number }) =>
    api.patch(`paper/positions/${positionId}`, data),
  
  closePosition: (positionId: string) =>
    api.post(`paper/positions/${positionId}/close`),
  
  getClosedTrades: (symbol?: string, limit?: number) =>
    api.get(`paper/closed-trades${symbol ? `?symbol=${symbol}&limit=${limit || 100}` : `?limit=${limit || 100}`}`),
  
  getPerformanceChart: () => api.get('paper/performance-chart?limit=2000'),

  getPnl: () => api.get('paper/pnl'),
};

export const automationApi = {
  getStatus: () => api.get('automation/status'),
  
  start: () => api.post('automation/start'),
  
  stop: () => api.post('automation/stop'),
  
  runAgent: (agent: {
    agent_id: string;
    name: string;
    strategy_type: string;
    allocation_percentage: number;
    max_position_size: number;
    stop_loss_pct?: number;
    take_profit_pct?: number;
  }, usePaper: boolean = true) => 
    api.post(`automation/run-agent?use_paper=${usePaper}`, agent),
  
  getMetrics: () => api.get('automation/metrics'),
  
  getAgentMetrics: (agentId: string) => api.get(`automation/metrics/${agentId}`),
  
  getRuns: (agentId?: string, limit?: number) =>
    api.get(`automation/runs${agentId ? `?agent_id=${agentId}&limit=${limit || 50}` : `?limit=${limit || 50}`}`),
  
  getMarketAnalysis: () => api.get('automation/market-analysis'),
  
  getRecommendations: () => api.get('automation/agent-recommendations'),
  
  getFundAllocation: (totalCapital?: number) =>
    api.get(`automation/fund-allocation${totalCapital ? `?total_capital=${totalCapital}` : ''}`),
};

export const llmApi = {
  getStatus: () => api.get('llm/status'),
  
  analyzeMarket: (marketData: {
    symbol: string;
    price: number;
    price_change_percent: number;
    high: number;
    low: number;
    volume: number;
    rsi?: number;
    macd?: number;
    macd_signal?: number;
    bb_upper?: number;
    bb_middle?: number;
    bb_lower?: number;
    sma_20?: number;
    sma_50?: number;
  }) => api.post('llm/analyze-market', marketData),
  
  generateSignal: (indicators: Record<string, number>, currentPrice: number) =>
    api.post('llm/generate-signal', { indicators, current_price: currentPrice }),
  
  evaluateStrategy: (strategyConfig: Record<string, any>, performance: Record<string, any>) =>
    api.post('llm/evaluate-strategy', { strategy_config: strategyConfig, performance }),
};

export const settingsApi = {
  getSettings: () => api.get('settings'),

  getTradingPairs: () => api.get('settings/trading-pairs'),

  updateApiKeys: (data: {
    phemex_api_key: string;
    phemex_api_secret: string;
    phemex_testnet: boolean;
  }) => api.put('settings/api-keys', data),

  updateRiskLimits: (data: {
    max_position_size_pct: number;
    max_daily_loss_pct: number;
    max_open_positions: number;
    default_stop_loss_pct: number;
    default_take_profit_pct: number;
    max_leverage: number;
  }) => api.put('settings/risk-limits', data),

  updateTradingPrefs: (data: {
    default_symbol: string;
    default_timeframe: string;
    paper_trading_default: boolean;
    auto_confirm_orders: boolean;
    default_order_type: string;
  }) => api.put('settings/trading', data),

  updateTradingGates: (data: Record<string, number | boolean>) => api.put('settings/gates', data),

  getGateAutopilot: () => api.get('settings/gates/autopilot').then((r: { data: unknown }) => r.data),

  setGateAutopilot: (enabled: boolean) =>
    api.post('settings/gates/autopilot', { enabled }).then((r: { data: unknown }) => r.data),

  runGateAutopilotNow: () =>
    api.post('settings/gates/autopilot/run').then((r: { data: unknown }) => r.data),

  updateLlmConfig: (data: {
    provider?: string;
    model?: string;
    temperature?: number;
    max_tokens?: number;
    openai_api_key?: string;
    anthropic_api_key?: string;
    openrouter_api_key?: string;
  }) => api.put('settings/llm', data),

  getTelegramSettings: () => api.get('settings/telegram').then((r: { data: unknown }) => r.data),

  updateTelegramSettings: (data: object) => api.put('settings/telegram', data).then((r: { data: unknown }) => r.data),

  testTelegram: (data: object) => api.post('settings/test-telegram', data).then((r: { data: unknown }) => r.data),
};

export const fundApi = {
  getConversations: (limit = 50) =>
    api.get(`fund/conversations?limit=${limit}`),

  getDailyReport: (reportDate?: string) =>
    api.get(`fund/daily-report${reportDate ? `?report_date=${reportDate}` : ''}`),

  getDailyReports: (limit = 30) =>
    api.get(`fund/daily-reports?limit=${limit}`),

  generateDailyReport: (reportDate?: string, force = false) =>
    api.post(`fund/daily-report/generate?force=${force}${reportDate ? `&report_date=${reportDate}` : ''}`),

  askAdvisor: (message: string) =>
    api.post('fund/advisor/ask', { message }),

  getAdvisorHistory: (limit = 50) =>
    api.get(`fund/advisor/history?limit=${limit}`),

  clearAdvisorHistory: () =>
    api.post('fund/advisor/clear'),
};

export const whaleApi = {
  getWatchlist: () => api.get('whale/watchlist'),
  addAddress: (body: { address: string; label?: string; notes?: string }) =>
    api.post('whale/watchlist', body),
  deleteAddress: (id: string) => api.delete(`whale/watchlist/${id}`),
  toggleAddress: (id: string) => api.patch(`whale/watchlist/${id}/toggle`),
  getIntelligence: () => api.get('whale/intelligence'),
  getSymbolBias: (symbol: string) => api.get(`whale/intelligence/${symbol}`),
  refresh: () => api.post('whale/refresh'),
};

export const traderApi = {
  list: () => api.get('traders'),
  get: (id: string) => api.get(`traders/${id}`),
  create: (data: { name: string; llm_provider?: string; llm_model?: string; allocation_pct?: number; config?: Record<string, unknown> }) =>
    api.post('traders', data),
  update: (id: string, data: Record<string, unknown>) => api.put(`traders/${id}`, data),
  delete: (id: string) => api.delete(`traders/${id}`),
  toggle: (id: string) => api.post(`traders/${id}/toggle`),
  getPerformance: (id: string) => api.get(`traders/${id}/performance`),
  getLeaderboard: () => api.get('fund/traders/leaderboard'),
};

export default api;
