import { useQuery, useQueryClient, QueryClient } from '@tanstack/react-query';
import { tradingApi, agentApi, paperApi, automationApi, settingsApi } from '../lib/api';
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
    refetchInterval: 60_000,
    staleTime: 30_000,
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

// ─── Paper orders ────────────────────────────────────────────────────────────
export function usePaperOrders(symbol?: string, limit = 50) {
  return useQuery({
    queryKey: ['paperOrders', symbol, limit],
    queryFn: () => paperApi.getOrders(symbol, limit).then((r) => r.data),
    refetchInterval: 30_000,
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

// ─── Paper balance ───────────────────────────────────────────────────────────
export function usePaperBalance() {
  return useQuery({
    queryKey: ['paperBalance'],
    queryFn: () => paperApi.getBalance().then((r) => r.data),
    refetchInterval: 20_000,
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

export function useFundRiskAssessment(totalCapital = 10000) {
  return useQuery({
    queryKey: ['fundRiskAssessment'],
    queryFn: () => fetch(`/api/fund/risk-assessment?total_capital=${totalCapital}`).then(r => r.json()),
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

// ─── Settings ────────────────────────────────────────────────────────────────
export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: () => settingsApi.getSettings().then((r) => r.data),
    staleTime: 60_000,
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

    wsClient.on('agent_run', onAgentRun);
    wsClient.on('trade_executed', onTradeExecuted);

    return () => {
      wsClient.off('agent_run', onAgentRun);
      wsClient.off('trade_executed', onTradeExecuted);
    };
  }, [qc]);
}
