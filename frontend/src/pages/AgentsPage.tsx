import { useState } from 'react';
import { Play } from 'lucide-react';
import { useAppSelector } from '../store/hooks';
import { automationApi } from '../lib/api';
import { useAgents, useAutomationMetrics } from '../hooks/useQueries';
import { timeAgo } from '../utils/timeAgo';

export function AgentsPage() {
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const { data: agentsData = [], refetch: refetchAgents } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();

  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const agentMetrics: Record<string, any> = {};
  for (const m of (Array.isArray(metricsData) ? metricsData : [])) {
    agentMetrics[m.agent_id] = m;
  }

  const [runningAgent, setRunningAgent] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [backtestResults, setBacktestResults] = useState<Record<string, any>>({});
  const [backtestLoading, setBacktestLoading] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    strategy_type: 'momentum',
    trading_pairs: [selectedSymbol],
    allocation_percentage: 10,
    max_position_size: 0.1,
    risk_limit: 2.0,
    stop_loss_pct: 2.0,
    take_profit_pct: 4.0,
    trailing_stop_pct: 0,
    run_interval_seconds: 3600,
  });

  const runAgentNow = async (agent: any) => {
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
      }, true);
      await refetchAgents();
    } catch (error) {
      console.error('Failed to run agent:', error);
    } finally {
      setRunningAgent(null);
    }
  };

  const createAgent = async () => {
    try {
      await fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });
      setShowForm(false);
      setFormData({
        name: '',
        strategy_type: 'momentum',
        trading_pairs: [selectedSymbol],
        allocation_percentage: 10,
        max_position_size: 0.1,
        risk_limit: 2.0,
        stop_loss_pct: 2.0,
        take_profit_pct: 4.0,
        trailing_stop_pct: 0,
        run_interval_seconds: 3600,
      });
      refetchAgents();
    } catch (error) {
      console.error('Failed to create agent:', error);
    }
  };

  const updateAgent = async (id: string) => {
    try {
      await fetch(`/api/agents/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });
      setEditingId(null);
      refetchAgents();
    } catch (error) {
      console.error('Failed to update agent:', error);
    }
  };

  const toggleAgent = async (id: string) => {
    try {
      await fetch(`/api/agents/${id}/toggle`, { method: 'POST' });
      refetchAgents();
    } catch (error) {
      console.error('Failed to toggle agent:', error);
    }
  };

  const deleteAgent = async (id: string) => {
    try {
      await fetch(`/api/agents/${id}`, { method: 'DELETE' });
      refetchAgents();
    } catch (error) {
      console.error('Failed to delete agent:', error);
    }
  };

  const runBacktest = async (agentId: string, symbol: string = 'BTCUSDT') => {
    setBacktestLoading(agentId);
    try {
      const res = await fetch(`/api/agents/${agentId}/backtest?symbol=${symbol}&interval=1h`, { method: 'POST' });
      const data = await res.json();
      setBacktestResults((prev) => ({ ...prev, [agentId]: data }));
    } catch (error) {
      console.error('Failed to run backtest:', error);
    } finally {
      setBacktestLoading(null);
    }
  };

  const startEdit = (agent: any) => {
    setFormData({
      name: agent.name,
      strategy_type: agent.strategy_type,
      trading_pairs: agent.trading_pairs,
      allocation_percentage: agent.allocation_percentage,
      max_position_size: agent.max_position_size,
      risk_limit: agent.risk_limit,
      stop_loss_pct: agent.stop_loss_pct || 2.0,
      take_profit_pct: agent.take_profit_pct || 4.0,
      trailing_stop_pct: agent.trailing_stop_pct || 0,
      run_interval_seconds: agent.run_interval_seconds,
    });
    setEditingId(agent.id);
    setShowForm(false);
  };

  const strategies = [
    { value: 'momentum', label: 'Momentum', desc: 'Follows trend strength' },
    { value: 'mean_reversion', label: 'Mean Reversion', desc: 'Trades around average price' },
    { value: 'breakout', label: 'Breakout', desc: 'Trades price breakouts' },
    { value: 'ai', label: 'AI Agent', desc: 'LLM-powered analysis and signals' },
  ];

  const availablePairs = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT',
    'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'MATICUSDT', 'LINKUSDT',
    'BNBUSDT', 'LTCUSDT',
  ];

  const togglePair = (pair: string) => {
    setFormData((prev) => {
      const has = prev.trading_pairs.includes(pair);
      const next = has
        ? prev.trading_pairs.filter((p) => p !== pair)
        : [...prev.trading_pairs, pair];
      return { ...prev, trading_pairs: next.length > 0 ? next : prev.trading_pairs };
    });
  };

  const intervals = [
    { value: 300, label: '5 min' },
    { value: 900, label: '15 min' },
    { value: 3600, label: '1 hour' },
    { value: 14400, label: '4 hours' },
  ];

  return (
    <div className="space-y-6">
      <div className="agent-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Trading Agents</h1>
        <button type="button" className="agent-btn" onClick={() => { setShowForm(!showForm); setEditingId(null); }}>
          {showForm ? 'Cancel' : '+ New Agent'}
        </button>
      </div>

      {showForm && (
        <div className="agent-form">
          <h3 className="form-title">Create New Agent</h3>

          <div className="form-group">
            <label className="form-label">Agent Name</label>
            <input
              type="text"
              className="settings-input"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="My Trading Bot"
            />
          </div>

          <div className="form-group">
            <label className="form-label">Strategy</label>
            <div className="strategy-options">
              {strategies.map((s) => (
                <label key={s.value} className={`strategy-option ${formData.strategy_type === s.value ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="strategy"
                    value={s.value}
                    checked={formData.strategy_type === s.value}
                    onChange={(e) => setFormData({ ...formData, strategy_type: e.target.value })}
                  />
                  <span className="strategy-label">{s.label}</span>
                  <span className="strategy-desc">{s.desc}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Trading Pairs</label>
            <div className="pair-pills-grid">
              {availablePairs.map((pair) => {
                const isSelected = formData.trading_pairs.includes(pair);
                return (
                  <button
                    key={pair}
                    type="button"
                    className={`pair-pill ${isSelected ? 'pair-pill-selected' : ''}`}
                    onClick={() => togglePair(pair)}
                  >
                    {pair.replace('USDT', '')}
                    {isSelected && <span className="pair-pill-check">✓</span>}
                  </button>
                );
              })}
            </div>
            <div className="pair-selection-summary">
              {formData.trading_pairs.length} pair{formData.trading_pairs.length !== 1 ? 's' : ''} selected: {formData.trading_pairs.join(', ')}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Capital Allocation: {formData.allocation_percentage}%</label>
            <input
              type="range"
              min="1"
              max="100"
              value={formData.allocation_percentage}
              onChange={(e) => setFormData({ ...formData, allocation_percentage: parseInt(e.target.value) })}
              className="slider"
            />
            <div className="slider-labels"><span>1%</span><span>100%</span></div>
          </div>

          <div className="form-group">
            <label className="form-label">Max Position Size: {formData.max_position_size}</label>
            <input
              type="range"
              min="0.01"
              max="1"
              step="0.01"
              value={formData.max_position_size}
              onChange={(e) => setFormData({ ...formData, max_position_size: parseFloat(e.target.value) })}
              className="slider"
            />
            <div className="slider-labels"><span>1%</span><span>100%</span></div>
          </div>

          <div className="form-group">
            <label className="form-label">Stop Loss: {formData.stop_loss_pct}%</label>
            <input
              type="range"
              min="0.5"
              max="10"
              step="0.5"
              value={formData.stop_loss_pct}
              onChange={(e) => setFormData({ ...formData, stop_loss_pct: parseFloat(e.target.value) })}
              className="slider"
            />
            <div className="slider-labels"><span>0.5%</span><span>10%</span></div>
          </div>

          <div className="form-group">
            <label className="form-label">Take Profit: {formData.take_profit_pct}%</label>
            <input
              type="range"
              min="1"
              max="20"
              step="0.5"
              value={formData.take_profit_pct}
              onChange={(e) => setFormData({ ...formData, take_profit_pct: parseFloat(e.target.value) })}
              className="slider"
            />
            <div className="slider-labels"><span>1%</span><span>20%</span></div>
          </div>

          <div className="form-group">
            <label className="form-label">Trailing Stop: {formData.trailing_stop_pct ? `${formData.trailing_stop_pct}%` : 'Off'}</label>
            <input
              type="range"
              min="0"
              max="10"
              step="0.5"
              value={formData.trailing_stop_pct}
              onChange={(e) => setFormData({ ...formData, trailing_stop_pct: parseFloat(e.target.value) })}
              className="slider"
            />
            <div className="slider-labels"><span>Off</span><span>10%</span></div>
          </div>

          <div className="form-group">
            <label className="form-label">Run Interval</label>
            <select
              className="settings-input"
              value={formData.run_interval_seconds}
              onChange={(e) => setFormData({ ...formData, run_interval_seconds: parseInt(e.target.value) })}
            >
              {intervals.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
            </select>
          </div>

          <button type="button" className="settings-btn" onClick={createAgent}>
            Create Agent
          </button>
        </div>
      )}

      {agents.length === 0 && !showForm ? (
        <div className="card">
          <p className="text-gray-400">No agents configured yet. Create your first agent to start automated trading.</p>
        </div>
      ) : (
        <div className="agents-grid">
          {agents.map((agent) => (
            <div key={agent.id} className={`agent-card ${agent.is_enabled ? 'enabled' : ''}`}>
              {editingId === agent.id ? (
                <div className="agent-edit-form">
                  <div className="form-group">
                    <label className="form-label">Agent Name</label>
                    <input
                      type="text"
                      className="settings-input"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Strategy</label>
                    <div className="strategy-options">
                      {strategies.map((s) => (
                        <label key={s.value} className={`strategy-option ${formData.strategy_type === s.value ? 'selected' : ''}`}>
                          <input
                            type="radio"
                            name={`edit-strategy-${agent.id}`}
                            value={s.value}
                            checked={formData.strategy_type === s.value}
                            onChange={(e) => setFormData({ ...formData, strategy_type: e.target.value })}
                          />
                          <span className="strategy-label">{s.label}</span>
                          <span className="strategy-desc">{s.desc}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Trading Pairs</label>
                    <div className="pair-pills-grid">
                      {availablePairs.map((pair) => {
                        const isSelected = formData.trading_pairs.includes(pair);
                        return (
                          <button
                            key={pair}
                            type="button"
                            className={`pair-pill ${isSelected ? 'pair-pill-selected' : ''}`}
                            onClick={() => togglePair(pair)}
                          >
                            {pair.replace('USDT', '')}
                            {isSelected && <span className="pair-pill-check">✓</span>}
                          </button>
                        );
                      })}
                    </div>
                    <div className="pair-selection-summary">
                      {formData.trading_pairs.length} pair{formData.trading_pairs.length !== 1 ? 's' : ''} selected
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Capital Allocation: {formData.allocation_percentage}%</label>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      value={formData.allocation_percentage}
                      onChange={(e) => setFormData({ ...formData, allocation_percentage: parseInt(e.target.value) })}
                      className="slider"
                    />
                  </div>

                  <div className="form-group">
                    <label className="form-label">Max Position Size: {formData.max_position_size}</label>
                    <input
                      type="range"
                      min="0.01"
                      max="1"
                      step="0.01"
                      value={formData.max_position_size}
                      onChange={(e) => setFormData({ ...formData, max_position_size: parseFloat(e.target.value) })}
                      className="slider"
                    />
                  </div>

                  <div className="form-row-pair">
                    <div className="form-group">
                      <label className="form-label">Stop Loss: {formData.stop_loss_pct}%</label>
                      <input
                        type="range"
                        min="0.5"
                        max="10"
                        step="0.5"
                        value={formData.stop_loss_pct}
                        onChange={(e) => setFormData({ ...formData, stop_loss_pct: parseFloat(e.target.value) })}
                        className="slider"
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Take Profit: {formData.take_profit_pct}%</label>
                      <input
                        type="range"
                        min="1"
                        max="20"
                        step="0.5"
                        value={formData.take_profit_pct}
                        onChange={(e) => setFormData({ ...formData, take_profit_pct: parseFloat(e.target.value) })}
                        className="slider"
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Trailing Stop: {formData.trailing_stop_pct ? `${formData.trailing_stop_pct}%` : 'Off'}</label>
                    <input
                      type="range"
                      min="0"
                      max="10"
                      step="0.5"
                      value={formData.trailing_stop_pct}
                      onChange={(e) => setFormData({ ...formData, trailing_stop_pct: parseFloat(e.target.value) })}
                      className="slider"
                    />
                    <div className="slider-labels"><span>Off</span><span>10%</span></div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Run Interval</label>
                    <div className="strategy-options" style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                      {intervals.map((iv) => (
                        <label key={iv.value} className={`strategy-option ${formData.run_interval_seconds === iv.value ? 'selected' : ''}`} style={{ flex: '1 1 auto', minWidth: 'auto' }}>
                          <input
                            type="radio"
                            name={`edit-interval-${agent.id}`}
                            value={iv.value}
                            checked={formData.run_interval_seconds === iv.value}
                            onChange={() => setFormData({ ...formData, run_interval_seconds: iv.value })}
                          />
                          <span className="strategy-label" style={{ fontSize: '.75rem' }}>{iv.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <div className="edit-actions">
                    <button type="button" className="save-btn" onClick={() => updateAgent(agent.id)}>Save Changes</button>
                    <button type="button" className="cancel-btn" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="agent-card-header">
                    <div>
                      <h3>{agent.name}</h3>
                      <span className="strategy-tag">{agent.strategy_type}</span>
                    </div>
                    <label className="toggle-switch">
                      <input
                        type="checkbox"
                        checked={agent.is_enabled}
                        onChange={() => toggleAgent(agent.id)}
                      />
                      <span className="toggle-slider" />
                    </label>
                  </div>
                  <div className="agent-details">
                    <div className="detail-row">
                      <span className="detail-label">Pairs</span>
                      <span className="detail-value">{agent.trading_pairs.join(', ')}</span>
                    </div>
                    <div className="detail-row">
                      <span className="detail-label">Allocation</span>
                      <div className="detail-progress">
                        <div className="progress-bar" style={{ width: `${agent.allocation_percentage}%` }} />
                        <span>{agent.allocation_percentage}%</span>
                      </div>
                    </div>
                    <div className="detail-row">
                      <span className="detail-label">Max Position</span>
                      <span className="detail-value">{agent.max_position_size}</span>
                    </div>
                    <div className="detail-row">
                      <span className="detail-label">Interval</span>
                      <span className="detail-value">{agent.run_interval_seconds / 3600}h</span>
                    </div>
                    <div className="detail-row risk-row">
                      <span className="detail-label">Risk Settings</span>
                      <div className="risk-values">
                        <span className="risk-item">
                          <span className="risk-label">SL:</span>
                          <span className="risk-value negative">-{agent.stop_loss_pct || 2}%</span>
                        </span>
                        <span className="risk-item">
                          <span className="risk-label">TP:</span>
                          <span className="risk-value positive">+{agent.take_profit_pct || 4}%</span>
                        </span>
                        {agent.trailing_stop_pct ? (
                          <span className="risk-item">
                            <span className="risk-label">TS:</span>
                            <span className="risk-value">{agent.trailing_stop_pct}%</span>
                          </span>
                        ) : null}
                      </div>
                    </div>
                    {backtestResults[agent.id] && (
                      <div className="backtest-results">
                        <div className="backtest-metrics">
                          <span className={backtestResults[agent.id].metrics.net_pnl >= 0 ? 'positive' : 'negative'}>
                            Net P&L: ${backtestResults[agent.id].metrics.net_pnl?.toFixed(2)}
                          </span>
                          <span className={backtestResults[agent.id].metrics.total_pnl >= 0 ? 'positive' : 'negative'}>
                            Gross: ${backtestResults[agent.id].metrics.total_pnl?.toFixed(2)}
                          </span>
                          <span>Win Rate: {(backtestResults[agent.id].metrics.win_rate * 100).toFixed(1)}%</span>
                          <span>Trades: {backtestResults[agent.id].metrics.total_trades}</span>
                          <span>Fees: ${backtestResults[agent.id].metrics.total_fees?.toFixed(2)}</span>
                          <span>PF: {backtestResults[agent.id].metrics.profit_factor?.toFixed(2)}</span>
                          <span>Max DD: {(backtestResults[agent.id].metrics.max_drawdown * 100)?.toFixed(1)}%</span>
                          <span>Sharpe: {backtestResults[agent.id].metrics.sharpe_ratio?.toFixed(2)}</span>
                        </div>
                        {backtestResults[agent.id].equity_curve && backtestResults[agent.id].equity_curve.length > 0 && (
                          <div className="equity-curve-mini">
                            <svg viewBox={`0 0 ${backtestResults[agent.id].equity_curve.length} 40`} preserveAspectRatio="none" className="equity-sparkline">
                              {(() => {
                                const curve = backtestResults[agent.id].equity_curve;
                                const min = Math.min(...curve);
                                const max = Math.max(...curve);
                                const range = max - min || 1;
                                const points = curve.map((v: number, i: number) =>
                                  `${i},${40 - ((v - min) / range) * 38}`
                                ).join(' ');
                                const isPositive = curve[curve.length - 1] >= curve[0];
                                return <polyline points={points} fill="none" stroke={isPositive ? '#22c55e' : '#ef4444'} strokeWidth="1.5" />;
                              })()}
                            </svg>
                          </div>
                        )}
                        {backtestResults[agent.id].trades && backtestResults[agent.id].trades.length > 0 && (
                          <div className="backtest-trades-mini">
                            {backtestResults[agent.id].trades.filter((t: any) => t.type === 'EXIT').slice(-5).map((t: any, i: number) => (
                              <span key={i} className={`trade-pill ${(t.net_pnl ?? t.pnl) >= 0 ? 'positive' : 'negative'}`}>
                                {t.side?.toUpperCase()} ${(t.net_pnl ?? t.pnl)?.toFixed(2)} ({t.exit_reason || 'signal'})
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  {agentMetrics[agent.id] && (() => {
                    const m = agentMetrics[agent.id];
                    return (
                      <div className="agent-metrics-strip">
                        <div className="metric-pill">
                          <span className="metric-pill-label">Runs</span>
                          <span className="metric-pill-value">{m.total_runs}</span>
                        </div>
                        <div className="metric-pill">
                          <span className="metric-pill-label">Win%</span>
                          <span className={`metric-pill-value ${m.win_rate >= 0.5 ? 'positive' : 'negative'}`}>
                            {(m.win_rate * 100).toFixed(0)}%
                          </span>
                        </div>
                        <div className="metric-pill">
                          <span className="metric-pill-label">P&L</span>
                          <span className={`metric-pill-value ${m.total_pnl >= 0 ? 'positive' : 'negative'}`}>
                            ${m.total_pnl?.toFixed(2)}
                          </span>
                        </div>
                        <div className="metric-pill">
                          <span className="metric-pill-label">Avg</span>
                          <span className={`metric-pill-value ${m.avg_pnl >= 0 ? 'positive' : 'negative'}`}>
                            ${m.avg_pnl?.toFixed(2)}
                          </span>
                        </div>
                        <div className="metric-pill">
                          <span className="metric-pill-label">B/S/H</span>
                          <span className="metric-pill-value">
                            {m.buy_signals}/{m.sell_signals}/{m.hold_signals}
                          </span>
                        </div>
                        {m.last_run && (
                          <div className="metric-pill">
                            <span className="metric-pill-label">Last</span>
                            <span className="metric-pill-value">{timeAgo(m.last_run)}</span>
                          </div>
                        )}
                      </div>
                    );
                  })()}
                  <div className="agent-actions">
                    <button
                      type="button"
                      className="run-now-btn"
                      onClick={() => runAgentNow(agent)}
                      disabled={runningAgent === agent.id}
                    >
                      {runningAgent === agent.id ? 'Running...' : <><Play size={14} /> Run</>}
                    </button>
                    <button
                      type="button"
                      className="backtest-btn"
                      onClick={() => runBacktest(agent.id, agent.trading_pairs[0])}
                      disabled={backtestLoading === agent.id}
                    >
                      {backtestLoading === agent.id ? 'Running...' : 'Backtest'}
                    </button>
                    <button type="button" className="edit-btn" onClick={() => startEdit(agent)}>Edit</button>
                    <button type="button" className="delete-btn" onClick={() => deleteAgent(agent.id)}>Delete</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
