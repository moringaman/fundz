import { useState, useEffect } from 'react';
import {
  useAutomationStatus,
  useAutomationMetrics,
  useAgents,
  useTradingMode,
  useSetTradingMode,
} from '../hooks/useQueries';
import { automationApi } from '../lib/api';
import { SkeletonStats, SkeletonCard } from '../components/common/Skeleton';

export function AutomationPage() {
  const { data: statusData, refetch: refetchStatus } = useAutomationStatus();
  const { data: metricsData = [], isPending: metricsLoading } = useAutomationMetrics();
  const { data: agentsData = [] } = useAgents();
  const [runningAgent, setRunningAgent] = useState<string | null>(null);

  // Mode is server-persisted — read from settings, write via mutation
  const { isPaper } = useTradingMode();
  const setTradingMode = useSetTradingMode();
  const [modeUpdating, setModeUpdating] = useState(false);

  const status = statusData ?? null;
  const metrics: any[] = Array.isArray(metricsData) ? metricsData : [];
  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];

  // Market analysis via plain fetch (not hot-path — no polling needed)
  const [market, setMarket] = useState<any>(null);
  const [recommendations, setRecommendations] = useState<any[]>([]);

  // Load market analysis once on mount
  useState(() => {
    automationApi.getMarketAnalysis().then((r) => setMarket(r.data)).catch(() => {});
    automationApi.getRecommendations().then((r) => setRecommendations(r.data)).catch(() => {});
  });

  const toggleScheduler = async () => {
    try {
      if (status?.scheduler_running) {
        await automationApi.stop();
      } else {
        await automationApi.start();
      }
      await refetchStatus();
    } catch (error) {
      console.error('Failed to toggle scheduler:', error);
    }
  };

  const runAgent = async (agent: any) => {
    if (!agent) return;
    setRunningAgent(agent.id);
    try {
      await automationApi.runAgent({
        agent_id: agent.id,
        name: agent.name,
        strategy_type: agent.strategy_type,
        trading_pairs: agent.trading_pairs,
        allocation_percentage: agent.allocation_percentage,
        max_position_size: agent.max_position_size,
        stop_loss_pct: agent.stop_loss_pct || 2.0,
        take_profit_pct: agent.take_profit_pct || 4.0,
      }, isPaper);
    } catch (error) {
      console.error('Failed to run agent:', error);
    } finally {
      setRunningAgent(null);
    }
  };

  return (
    <div className="space-y-6" style={{ margin: '1.75rem' }}>
      <h1 className="page-title">Strategy Automation</h1>

      <div className="card">
        <div className="card-header-row">
          <div>
            <h2 className="card-title">Trading Mode</h2>
            <p className="text-gray-400 text-sm">
              {isPaper ? 'Paper Trading — No real trades executed' : '🔴 Live Trading — Real trades will be executed on the exchange'}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {modeUpdating && <span style={{ fontSize: '12px', color: '#888' }}>Saving…</span>}
            <label className="toggle-switch">
              <input
                type="checkbox"
                checked={!isPaper}
                disabled={modeUpdating}
                onChange={async () => {
                  setModeUpdating(true);
                  try { await setTradingMode.mutateAsync(!isPaper); } finally { setModeUpdating(false); }
                }}
              />
              <span className="toggle-slider" />
            </label>
          </div>
        </div>
        {!isPaper && (
          <div className="warning-banner">
            ⚠️ Live Trading Enabled — Actual trades will be executed on the exchange
          </div>
        )}
      </div>

      <div className="stats-grid">
        {metricsLoading ? (
          <div style={{ gridColumn: '1 / -1' }}><SkeletonStats count={4} /></div>
        ) : (<>
        <div className="stat-card">
          <p className="stat-label">Scheduler</p>
          <p className={`stat-value ${status?.scheduler_running ? 'positive' : ''}`}>
            {status?.scheduler_running ? 'Running' : 'Stopped'}
          </p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Total Runs</p>
          <p className="stat-value">{status?.total_runs || 0}</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Tracked Strategies</p>
          <p className="stat-value">{status?.tracked_agents || 0}</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Market</p>
          <p className="stat-value">{market?.trend || 'N/A'}</p>
        </div>
        </>)}
      </div>

      <div className="card">
        <div className="card-header-row">
          <h2 className="card-title">Scheduler Control</h2>
          <button
            type="button"
            className={`toggle-btn ${status?.scheduler_running ? 'stop' : 'start'}`}
            onClick={toggleScheduler}
          >
            {status?.scheduler_running ? 'Stop' : 'Start'}
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="card-title">Trading Setup</h2>
        <div className="setup-grid">
          <div className="setup-item">
            <span className="setup-label">Trading Mode</span>
            <span className={`setup-value ${isPaper ? 'paper' : 'real'}`}>
              {isPaper ? 'Paper Trading' : '🔴 Live Trading'}
            </span>
          </div>
          <div className="setup-item">
            <span className="setup-label">Active Strategies</span>
            <span className="setup-value">{agents.filter((a) => a.is_enabled).length}</span>
          </div>
          <div className="setup-item">
            <span className="setup-label">Scheduler</span>
            <span className={`setup-value ${status?.scheduler_running ? 'active' : 'inactive'}`}>
              {status?.scheduler_running ? 'Running' : 'Stopped'}
            </span>
          </div>
          <div className="setup-item">
            <span className="setup-label">Total Runs</span>
            <span className="setup-value">{status?.total_runs || 0}</span>
          </div>
        </div>
      </div>

      {market && (
        <div className="card">
          <h2 className="card-title">Market Analysis</h2>
          <div className="stats-grid">
            <div className="stat-card"><p className="stat-label">Trend</p><p className="stat-value">{market.trend}</p></div>
            <div className="stat-card"><p className="stat-label">Volatility</p><p className="stat-value">{market.volatility}</p></div>
            <div className="stat-card"><p className="stat-label">RSI</p><p className="stat-value">{market.rsi?.toFixed(1)}</p></div>
            <div className="stat-card"><p className="stat-label">Recommendation</p><p className="stat-value">{market.recommendation}</p></div>
          </div>
        </div>
      )}

      <div className="card">
        <h2 className="card-title">Strategy Performance</h2>
        {metrics.length === 0 ? (
          <p className="text-gray-400">No strategy metrics yet. Run strategies to see performance data.</p>
        ) : (
          <div className="metrics-table">
            {metrics.map((m: any) => (
              <div key={m.agent_id} className="metric-row">
                <div className="metric-info">
                  <span className="metric-name">{agents.find((a) => a.id === m.agent_id)?.name || 'Unknown'}</span>
                  <span className="metric-runs">{m.total_runs} runs</span>
                </div>
                <div className="metric-stats">
                  <span className={m.win_rate >= 0.5 ? 'positive' : 'negative'}>
                    Win: {(m.win_rate * 100).toFixed(0)}%
                  </span>
                  <span>P&L: ${m.total_pnl?.toFixed(2)}</span>
                  <span>Buy: {m.buy_signals} | Sell: {m.sell_signals}</span>
                </div>
                <button
                  type="button"
                  className="run-btn"
                  onClick={() => runAgent(agents.find((a) => a.id === m.agent_id))}
                  disabled={runningAgent === m.agent_id}
                >
                  {runningAgent === m.agent_id ? 'Running...' : 'Run'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {recommendations.length > 0 && (
        <div className="card">
          <h2 className="card-title">Fund Manager Recommendations</h2>
          <div className="recommendations-list">
            {recommendations.map((r: any) => (
              <div key={r.agent_id} className={`recommendation-item ${r.action}`}>
                <div className="rec-header">
                  <span className="rec-name">{r.agent_name}</span>
                  <span className={`rec-action ${r.action}`}>{r.action.toUpperCase()}</span>
                </div>
                <p className="rec-reason">{r.reason}</p>
                <p className="rec-confidence">Confidence: {(r.confidence * 100).toFixed(0)}%</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
