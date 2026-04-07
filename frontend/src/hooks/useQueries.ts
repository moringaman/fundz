import { useQuery, useQueryClient, QueryClient, useMutation } from '@tanstack/react-query';
import { tradingApi, agentApi, paperApi, automationApi, settingsApi, fundApi } from '../lib/api';
import { wsClient } from '../lib/websocket';
import { useEffect } from 'react';

// Shared query client exported so WS handlers can invalidate queries
export let queryClient: QueryClient;

export function setQueryClient(qc: QueryClient) {
  queryClient = qc;
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

// ===== Fund Team API Hooks =====

export function useFundMarketAnalysis() {
  return useQuery({
    queryKey: ['fundMarketAnalysis'],
    queryFn: () => fetch('/api/fund/market-analysis').then(r => r.json()),
    staleTime: 60_000,      // 1 minute stale
    refetchInterval: 300_000, // Refresh every 5 minutes (aligns with team analysis tier)
  });
}

export function useFundAllocationDecision(totalCapital = 10000) {
  return useQuery({
    queryKey: ['fundAllocationDecision', totalCapital],
    queryFn: () => fetch(`/api/fund/allocation-decision?total_capital=${totalCapital}`).then(r => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

export function useFundRiskAssessment() {
  return useQuery({
    queryKey: ['fundRiskAssessment'],
    queryFn: () => fetch('/api/fund/risk-assessment').then(r => r.json()),
    staleTime: 30_000,       // 30 seconds stale - risk is more time sensitive
    refetchInterval: 120_000, // Refresh every 2 minutes
  });
}

export function useFundCIOReport(period = 'daily') {
  return useQuery({
    queryKey: ['fundCIOReport', period],
    queryFn: () => fetch(`/api/fund/cio-report?period=${period}`).then(r => r.json()),
    staleTime: 120_000,
    refetchInterval: 600_000, // Refresh every 10 minutes - CIO report is less time sensitive
  });
}

export function useFundPerformanceAttribution() {
  return useQuery({
    queryKey: ['fundPerformanceAttribution'],
    queryFn: () => fetch('/api/fund/performance-attribution').then(r => r.json()),
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
    queryFn: () => fetch('/api/fund/team-status').then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 120_000,
  });
}

export function useFundTeamRoster() {
  return useQuery({
    queryKey: ['fundTeamRoster'],
    queryFn: () => fetch('/api/fund/team-roster').then(r => r.json()),
    staleTime: 3600_000,  // 1 hour - roster doesn't change
    refetchInterval: undefined,  // Don't auto-refetch
  });
}

export function useFundTechnicalAnalysis(symbol = 'BTCUSDT') {
  return useQuery({
    queryKey: ['fundTechnicalAnalysis', symbol],
    queryFn: () => fetch(`/api/fund/technical-analysis?symbol=${symbol}`).then(r => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

export function useFundTechnicalAnalysisBatch() {
  return useQuery({
    queryKey: ['fundTechnicalAnalysisBatch'],
    queryFn: () => fetch('/api/fund/technical-analysis/batch').then(r => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });
}

// ─── Strategy Actions (FM + TA cooperation) ──────────────────────────────────
export function useStrategyActions(limit = 20) {
  return useQuery({
    queryKey: ['strategyActions', limit],
    queryFn: () => fetch(`/api/fund/strategy-actions?limit=${limit}`).then(r => r.json()),
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
      return fetch(`/api/backtest/history?${params}`).then(r => r.json());
    },
    staleTime: 120_000,
  });
}

// ─── Trade Retrospective ─────────────────────────────────────────────────────
export function useTradeRetrospective() {
  return useQuery({
    queryKey: ['tradeRetrospective'],
    queryFn: () => fetch('/api/fund/trade-retrospective').then(r => r.json()),
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
    refetchInterval: 60_000,
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
