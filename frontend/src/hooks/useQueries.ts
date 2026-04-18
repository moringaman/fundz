import { useQuery, useQueryClient, QueryClient, useMutation } from '@tanstack/react-query';
import { tradingApi, agentApi, paperApi, automationApi, settingsApi, fundApi, traderApi, whaleApi } from '../lib/api';
import { wsClient } from '../lib/websocket';
import { useEffect } from 'react';

// Shared query client exported so WS handlers can invalidate queries
export let queryClient: QueryClient;

export function setQueryClient(qc: QueryClient) {
  queryClient = qc;
}

/** Fetch wrapper that throws on non-2xx so React Query treats HTTP errors as errors, not data. */
function safeFetch(url: string) {
  return fetch(url).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
}

// ─── Positions ────────────────────────────────────────────────────────────────
export function usePositions() {
  return useQuery({
    queryKey: ['positions'],
    queryFn: () => tradingApi.getPositions().then((r) => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

// ─── Balance ─────────────────────────────────────────────────────────────────
export function useBalance() {
  return useQuery({
    queryKey: ['balance'],
    queryFn: () => tradingApi.getBalance().then((r) => r.data),
    refetchInterval: 20_000,
    staleTime: 15_000,
  });
}

// ─── Open orders ─────────────────────────────────────────────────────────────
export function useOpenOrders(symbol?: string) {
  return useQuery({
    queryKey: ['openOrders', symbol],
    queryFn: () => tradingApi.getOpenOrders(symbol).then((r) => r.data),
    refetchInterval: 15_000,
  });
}

// ─── Trade history ────────────────────────────────────────────────────────────
export function useTradeHistory(symbol?: string, limit = 50) {
  return useQuery({
    queryKey: ['tradeHistory', symbol, limit],
    queryFn: () => tradingApi.getHistory(symbol, limit).then((r) => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

// ─── P&L ─────────────────────────────────────────────────────────────────────
export function usePnl() {
  return useQuery({
    queryKey: ['pnl'],
    queryFn: () => tradingApi.getPnl().then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Agents ──────────────────────────────────────────────────────────────────
export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: () => agentApi.getAgents().then((r) => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

// ─── Strategy registry ────────────────────────────────────────────────────────
export function useStrategies() {
  return useQuery({
    queryKey: ['strategies'],
    queryFn: () => agentApi.getStrategies().then((r) => r.data),
    staleTime: 60_000,
  });
}

export function useUpdateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
      fetch(`/api/strategies/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      }).then((r) => { if (!r.ok) throw new Error('Update failed'); return r.json(); }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
}

export function useResetStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/strategies/${id}/reset`, { method: 'POST' }).then((r) => {
        if (!r.ok) throw new Error('Reset failed');
        return r.json();
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
}

// ─── Agent signals ────────────────────────────────────────────────────────────
export function useAgentSignals(agentId?: string) {
  return useQuery({
    queryKey: ['agentSignals', agentId],
    queryFn: () => agentApi.getSignals(agentId).then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Automation metrics ───────────────────────────────────────────────────────
export function useAutomationMetrics() {
  return useQuery({
    queryKey: ['automationMetrics'],
    queryFn: () => automationApi.getMetrics().then((r) => r.data),
    refetchInterval: 60_000,
  });
}

// ─── Automation status ────────────────────────────────────────────────────────
export function useAutomationStatus() {
  return useQuery({
    queryKey: ['automationStatus'],
    queryFn: () => automationApi.getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Automation runs ─────────────────────────────────────────────────────────
export function useAutomationRuns(agentId?: string, limit = 50) {
  return useQuery({
    queryKey: ['automationRuns', agentId, limit],
    queryFn: () => automationApi.getRuns(agentId, limit).then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Paper trading status ────────────────────────────────────────────────────
export function usePaperStatus() {
  return useQuery({
    queryKey: ['paperStatus'],
    queryFn: () => paperApi.getStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Paper P&L ───────────────────────────────────────────────────────────────
export function usePaperPnl() {
  return useQuery({
    queryKey: ['paperPnl'],
    queryFn: () => paperApi.getPnl().then((r) => r.data),
    refetchInterval: 30_000,
  });
}

// ─── Paper balance (wallet) ──────────────────────────────────────────────────
export function usePaperBalance() {
  return useQuery({
    queryKey: ['paperBalance'],
    queryFn: () => paperApi.getBalance().then((r) => r.data),
    refetchInterval: 20_000,
    staleTime: 15_000,
  });
}

// ─── Paper portfolio (canonical summary) ─────────────────────────────────────
export function usePaperPortfolio() {
  return useQuery({
    queryKey: ['paperPortfolio'],
    queryFn: () => paperApi.getPortfolio().then((r) => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

// ─── Paper orders ────────────────────────────────────────────────────────────
export function usePaperOrders(symbol?: string, limit = 50) {
  return useQuery({
    queryKey: ['paperOrders', symbol, limit],
    queryFn: () => paperApi.getOrders(symbol, limit).then((r) => r.data),
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

// ─── Paper positions ─────────────────────────────────────────────────────────
export function usePaperPositions() {
  return useQuery({
    queryKey: ['paperPositions'],
    queryFn: () => paperApi.getPositions().then((r) => r.data),
    refetchInterval: 15_000,
  });
}

// ─── Update position SL/TP ──────────────────────────────────────────────────
export function useUpdatePositionSlTp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { positionId: string; stop_loss_price?: number; take_profit_price?: number; trailing_stop_pct?: number }) => {
      const { positionId, ...data } = vars;
      return paperApi.updatePositionSlTp(positionId, data).then((r) => r.data);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['paperPositions'] });
    },
  });
}

// ─── Close position ─────────────────────────────────────────────────────────
export function useClosePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (positionId: string) =>
      paperApi.closePosition(positionId).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['paperPositions'] });
      qc.invalidateQueries({ queryKey: ['closedTrades'] });
      qc.invalidateQueries({ queryKey: ['paperPnl'] });
    },
  });
}

// ─── Paper closed trades ─────────────────────────────────────────────────────
export function useClosedTrades(symbol?: string, limit = 100) {
  return useQuery({
    queryKey: ['closedTrades', symbol, limit],
    queryFn: () => paperApi.getClosedTrades(symbol, limit).then((r) => r.data),
    refetchInterval: 30_000,
  });
}

export function usePerformanceChart() {
  return useQuery({
    queryKey: ['performanceChart'],
    queryFn: () => paperApi.getPerformanceChart().then((r) => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

// ===== Fund Team API Hooks =====

export function useFundMarketAnalysis() {
  return useQuery({
    queryKey: ['fundMarketAnalysis'],
    queryFn: () => safeFetch('/api/fund/market-analysis'),
    staleTime: 60_000,      // 1 minute stale
    refetchInterval: 300_000, // Refresh every 5 minutes (aligns with team analysis tier)
  });
}

export function useFundAllocationDecision(totalCapital = 10000) {
  return useQuery({
    queryKey: ['fundAllocationDecision', totalCapital],
    queryFn: () => safeFetch(`/api/fund/allocation-decision?total_capital=${totalCapital}`),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

export function useFundRiskAssessment() {
  return useQuery({
    queryKey: ['fundRiskAssessment'],
    queryFn: () => safeFetch('/api/fund/risk-assessment'),
    staleTime: 30_000,       // 30 seconds stale - risk is more time sensitive
    refetchInterval: 120_000, // Refresh every 2 minutes
  });
}

export function useFundCIOReport(period = 'daily') {
  return useQuery({
    queryKey: ['fundCIOReport', period],
    queryFn: () => safeFetch(`/api/fund/cio-report?period=${period}`),
    staleTime: 120_000,
    refetchInterval: 600_000, // Refresh every 10 minutes - CIO report is less time sensitive
  });
}

export function useFundPerformanceAttribution() {
  return useQuery({
    queryKey: ['fundPerformanceAttribution'],
    queryFn: () => safeFetch('/api/fund/performance-attribution'),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

// ─── Firm Advisor ────────────────────────────────────────────────────────────
export function useAdvisorHistory(limit = 50) {
  return useQuery({
    queryKey: ['advisorHistory', limit],
    queryFn: () => fundApi.getAdvisorHistory(limit).then((r) => r.data),
    staleTime: 5_000,
  });
}

export function useAskAdvisor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => fundApi.askAdvisor(message).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['advisorHistory'] });
    },
  });
}

export function useClearAdvisorHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fundApi.clearAdvisorHistory(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['advisorHistory'] });
    },
  });
}

export function useFundTeamStatus() {
  return useQuery({
    queryKey: ['fundTeamStatus'],
    queryFn: () => safeFetch('/api/fund/team-status'),
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

export function useFundTeamRoster() {
  return useQuery({
    queryKey: ['fundTeamRoster'],
    queryFn: () => safeFetch('/api/fund/team-roster'),
    staleTime: 3600_000,  // 1 hour - roster doesn't change
    refetchInterval: undefined,  // Don't auto-refetch
  });
}

export function useFundTechnicalAnalysis(symbol = 'BTCUSDT') {
  return useQuery({
    queryKey: ['fundTechnicalAnalysis', symbol],
    queryFn: () => safeFetch(`/api/fund/technical-analysis?symbol=${symbol}`),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

export function useFundTechnicalAnalysisBatch() {
  return useQuery({
    queryKey: ['fundTechnicalAnalysisBatch'],
    queryFn: () => safeFetch('/api/fund/technical-analysis/batch'),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

// ─── Strategy Actions (FM + TA cooperation) ──────────────────────────────────
export function useStrategyActions(limit = 20) {
  return useQuery({
    queryKey: ['strategyActions', limit],
    queryFn: () => safeFetch(`/api/fund/strategy-actions?limit=${limit}`),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

// ─── Backtest History ────────────────────────────────────────────────────────
export function useBacktestHistory(agentId?: string, limit = 20) {
  return useQuery({
    queryKey: ['backtestHistory', agentId, limit],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (agentId) params.set('agent_id', agentId);
      return safeFetch(`/api/backtest/history?${params}`);
    },
    staleTime: 120_000,
  });
}

// ─── Trade Retrospective ─────────────────────────────────────────────────────
export function useTradeRetrospective() {
  return useQuery({
    queryKey: ['tradeRetrospective'],
    queryFn: () => safeFetch('/api/fund/trade-retrospective'),
    staleTime: 300_000,
    refetchInterval: 600_000,
  });
}

// ─── Settings ────────────────────────────────────────────────────────────────
export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: () => settingsApi.getSettings().then((r) => r.data),
    staleTime: 60_000,
  });
}

/** Returns { isPaper, isLive } based on the persisted server setting. */
export function useTradingMode() {
  const { data } = useSettings();
  const isPaper = data?.trading?.paper_trading_default ?? true;
  return { isPaper, isLive: !isPaper };
}

/** Persists the paper/live mode toggle to the backend. Invalidates settings cache. */
export function useSetTradingMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (isPaper: boolean) => {
      // Fetch current prefs first so we don't lose other fields
      const current = await settingsApi.getSettings().then((r) => r.data?.trading ?? {});
      return settingsApi.updateTradingPrefs({
        default_symbol: current.default_symbol ?? 'BTCUSDT',
        default_timeframe: current.default_timeframe ?? '1h',
        paper_trading_default: isPaper,
        auto_confirm_orders: current.auto_confirm_orders ?? false,
        default_order_type: current.default_order_type ?? 'limit',
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}

// ─── Gate Autopilot ──────────────────────────────────────────────────────────
export function useGateAutopilot() {
  return useQuery({
    queryKey: ['gateAutopilot'],
    queryFn: () => settingsApi.getGateAutopilot() as Promise<{
      enabled: boolean;
      regime: string;
      reason: string;
      last_run: string | null;
      changes: Record<string, { from: number; to: number }>;
      color: string;
    }>,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}

export function useTradingPairs() {
  return useQuery({
    queryKey: ['tradingPairs'],
    queryFn: () => settingsApi.getTradingPairs().then((r) => r.data?.pairs as string[] ?? []),
    staleTime: 120_000,
  });
}

// ─── Fund Team Conversations ─────────────────────────────────────────────────
export function useFundConversations(limit = 50) {
  return useQuery({
    queryKey: ['fundConversations', limit],
    queryFn: () => fundApi.getConversations(limit).then((r) => r.data),
    staleTime: 30_000,
    refetchInterval: 15_000,
  });
}

// ─── Daily Reports ───────────────────────────────────────────────────────────
export function useDailyReport(reportDate?: string) {
  return useQuery({
    queryKey: ['dailyReport', reportDate],
    queryFn: () => fundApi.getDailyReport(reportDate).then((r) => r.data),
    staleTime: 300_000,
    refetchInterval: 600_000,
  });
}

export function useDailyReports(limit = 30) {
  return useQuery({
    queryKey: ['dailyReports', limit],
    queryFn: () => fundApi.getDailyReports(limit).then((r) => r.data),
    staleTime: 300_000,
  });
}

/**
 * Wires WebSocket agent_run events to immediately invalidate relevant queries.
 * Mount this once near the root of the app (inside QueryClientProvider).
 */
export function useWsQueryInvalidation() {
  const qc = useQueryClient();

  useEffect(() => {
    const onAgentRun = () => {
      qc.invalidateQueries({ queryKey: ['automationMetrics'] });
      qc.invalidateQueries({ queryKey: ['automationRuns'] });
      qc.invalidateQueries({ queryKey: ['paperPnl'] });
      qc.invalidateQueries({ queryKey: ['paperPositions'] });
      qc.invalidateQueries({ queryKey: ['paperOrders'] });
    };

    const onTradeExecuted = () => {
      qc.invalidateQueries({ queryKey: ['balance'] });
      qc.invalidateQueries({ queryKey: ['positions'] });
      qc.invalidateQueries({ queryKey: ['tradeHistory'] });
      qc.invalidateQueries({ queryKey: ['pnl'] });
    };

    const onTeamChat = () => {
      qc.invalidateQueries({ queryKey: ['fundConversations'] });
    };

    wsClient.on('agent_run', onAgentRun);
    wsClient.on('trade_executed', onTradeExecuted);
    wsClient.on('team_chat', onTeamChat);

    return () => {
      wsClient.off('agent_run', onAgentRun);
      wsClient.off('trade_executed', onTradeExecuted);
      wsClient.off('team_chat', onTeamChat);
    };
  }, [qc]);
}

// ─── Traders ──────────────────────────────────────────────────────────────────
export function useTraders() {
  return useQuery({
    queryKey: ['traders'],
    queryFn: () => traderApi.list().then(r => r.data),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

export function useTraderLeaderboard() {
  return useQuery({
    queryKey: ['traderLeaderboard'],
    queryFn: () => traderApi.getLeaderboard().then(r => r.data),
    staleTime: 120_000,
    refetchInterval: 120_000,
  });
}

export function useTraderAllocation() {
  return useQuery({
    queryKey: ['traderAllocation'],
    queryFn: () => safeFetch('/api/fund/trader-allocation'),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

export function useTraderPerformance(traderId?: string) {
  return useQuery({
    queryKey: ['traderPerformance', traderId],
    queryFn: () => traderApi.getPerformance(traderId!).then(r => r.data),
    enabled: !!traderId,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
}

// ─── Whale Intelligence ───────────────────────────────────────────────────────
export function useWhaleWatchlist() {
  return useQuery({
    queryKey: ['whaleWatchlist'],
    queryFn: () => whaleApi.getWatchlist().then((r) => r.data),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useWhaleIntelligence() {
  return useQuery({
    queryKey: ['whaleIntelligence'],
    queryFn: () => whaleApi.getIntelligence().then((r) => r.data),
    staleTime: 55_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useAddWhaleAddress() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { address: string; label?: string; notes?: string }) =>
      whaleApi.addAddress(body).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['whaleWatchlist'] }),
  });
}

export function useDeleteWhaleAddress() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => whaleApi.deleteAddress(id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['whaleWatchlist'] }),
  });
}

export function useToggleWhaleAddress() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => whaleApi.toggleAddress(id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['whaleWatchlist'] }),
  });
}

export function useRefreshWhaleIntelligence() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => whaleApi.refresh().then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['whaleIntelligence'] }),
  });
}
