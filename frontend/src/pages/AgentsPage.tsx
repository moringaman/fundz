import { useState, useEffect, useCallback, useRef } from 'react';
import { Play } from 'lucide-react';
import { useAppSelector } from '../store/hooks';
import { automationApi } from '../lib/api';
import { useAgents, useAutomationMetrics, useTraders, useStrategies } from '../hooks/useQueries';
import { timeAgo } from '../utils/timeAgo';
import { usePagination, Paginator } from '../components/common/Paginator';
import { StrategyManager } from '../components/StrategyManager';
import { Skeleton, SkeletonCard, SkeletonRows } from '../components/common/Skeleton';

export function AgentsPage() {
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const { data: agentsData = [], refetch: refetchAgents, isPending: agentsLoading } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();
  const { data: tradersData = [] } = useTraders();
  const { data: strategiesData = [] } = useStrategies();

  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const traders: any[] = Array.isArray(tradersData) ? tradersData : [];
  const traderMap: Record<string, any> = {};
  for (const t of traders) traderMap[t.id] = t;

  const agentMetrics: Record<string, any> = {};
  for (const m of (Array.isArray(metricsData) ? metricsData : [])) {
    agentMetrics[m.agent_id] = m;
  }

  const [runningAgent, setRunningAgent] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [backtestResults, setBacktestResults] = useState<Record<string, any>>({});
  const [backtestLoading, setBacktestLoading] = useState<string | null>(null);
  const [backtestProgress, setBacktestProgress] = useState<{ phase: string; pct: number } | null>(null);
  const backtestTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [gridSummaries, setGridSummaries] = useState<Record<string, any>>({});
  const [cancellingGrid, setCancellingGrid] = useState<string | null>(null);

  const fetchGridSummary = useCallback(async (agentId: string) => {
    try {
      const res = await fetch(`/api/grid/${agentId}/summary`);
      if (res.ok) {
        const data = await res.json();
        setGridSummaries(prev => ({ ...prev, [agentId]: data }));
      }
    } catch { /* ignore */ }
  }, []);

  const cancelGrid = useCallback(async (agentId: string) => {
    setCancellingGrid(agentId);
    try {
      const res = await fetch(`/api/grid/${agentId}/cancel`, { method: 'POST' });
      if (res.ok) {
        await fetchGridSummary(agentId);
      }
    } catch { /* ignore */ }
    setCancellingGrid(null);
  }, [fetchGridSummary]);

  // Search / filter / sort
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [activeTab, setActiveTab] = useState<'agents' | 'registry'>('agents');

  const filteredAgents = agents
    .filter((a) => {
      const q = search.toLowerCase();
      if (q && !(
        a.name?.toLowerCase().includes(q) ||
        a.strategy_type?.toLowerCase().includes(q) ||
        (a.trading_pairs ?? []).join(' ').toLowerCase().includes(q)
      )) return false;
      if (filterStatus === 'enabled' && !a.is_enabled) return false;
      if (filterStatus === 'disabled' && a.is_enabled) return false;
      return true;
    })
    .sort((a, b) => {
      // Enabled first, then by name
      if (a.is_enabled === b.is_enabled) return (a.name ?? '').localeCompare(b.name ?? '');
      return a.is_enabled ? -1 : 1;
    });

  const agentsPager = usePagination(filteredAgents, 8);

  // Fetch grid summaries for grid-type agents
  useEffect(() => {
    const gridAgents = agents.filter(a => a.strategy_type === 'grid');
    for (const a of gridAgents) fetchGridSummary(a.id);
  }, [agents.length, fetchGridSummary]); // eslint-disable-line react-hooks/exhaustive-deps

  const [formData, setFormData] = useState({
    name: '',
    strategy_type: 'momentum',
    allocation_percentage: 10,
    max_position_size: 0.1,
    risk_limit: 2.0,
    stop_loss_pct: 2.0,
    take_profit_pct: 4.0,
    trailing_stop_pct: 0,
    run_interval_seconds: 3600,
    timeframe: '15m',
    venue: 'phemex' as 'phemex' | 'hyperliquid',
  });

  const runAgentNow = async (agent: any) => {
    setRunningAgent(agent.id);
    try {
      await automationApi.runAgent({
        agent_id: agent.id,
        name: agent.name,
        strategy_type: agent.strategy_type,
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
        allocation_percentage: 10,
        max_position_size: 0.1,
        risk_limit: 2.0,
        stop_loss_pct: 2.0,
        take_profit_pct: 4.0,
        trailing_stop_pct: 0,
        run_interval_seconds: 3600,
        timeframe: '15m',
        venue: 'phemex' as 'phemex' | 'hyperliquid',
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

  const BACKTEST_PHASES = [
    { phase: 'Fetching candles…',        pct: 0  },
    { phase: 'Fetching candles…',        pct: 18 },
    { phase: 'Calculating indicators…', pct: 32 },
    { phase: 'Calculating indicators…', pct: 48 },
    { phase: 'Simulating trades…',       pct: 60 },
    { phase: 'Simulating trades…',       pct: 72 },
    { phase: 'Simulating trades…',       pct: 82 },
    { phase: 'Computing metrics…',       pct: 91 },
    { phase: 'Computing metrics…',       pct: 96 },
  ];

  const runBacktest = async (agentId: string, symbol: string = 'BTCUSDT', timeframe: string = '1h') => {
    setBacktestLoading(agentId);
    setBacktestProgress(BACKTEST_PHASES[0]);

    let step = 0;
    backtestTimerRef.current = setInterval(() => {
      step = Math.min(step + 1, BACKTEST_PHASES.length - 1);
      setBacktestProgress(BACKTEST_PHASES[step]);
    }, 2800);

    try {
      const res = await fetch(`/api/agents/${agentId}/backtest?symbol=${symbol}&interval=${timeframe}`, { method: 'POST' });
      const data = await res.json();
      setBacktestResults((prev) => ({ ...prev, [agentId]: data }));
    } catch (error) {
      console.error('Failed to run backtest:', error);
    } finally {
      if (backtestTimerRef.current) clearInterval(backtestTimerRef.current);
      setBacktestLoading(null);
      setBacktestProgress(null);
    }
  };

  const startEdit = (agent: any) => {
    setFormData({
      name: agent.name,
      strategy_type: agent.strategy_type,
      allocation_percentage: agent.allocation_percentage,
      max_position_size: agent.max_position_size,
      risk_limit: agent.risk_limit,
      stop_loss_pct: agent.stop_loss_pct || 2.0,
      take_profit_pct: agent.take_profit_pct || 4.0,
      trailing_stop_pct: agent.trailing_stop_pct || 0,
      run_interval_seconds: agent.run_interval_seconds,
      timeframe: agent.timeframe || '1h',
      venue: (agent.venue || 'phemex') as 'phemex' | 'hyperliquid',
    });
    setEditingId(agent.id);
    setShowForm(false);
  };

  // Map registry response to the shape used by the rest of the component
  const strategies: any[] = Array.isArray(strategiesData) && strategiesData.length > 0
    ? strategiesData.map((s: any) => ({
        value: s.value,
        label: s.label,
        desc: s.description,
        timeframes: s.timeframes || ['1h'],
        defaultTf: s.defaultTf || '1h',
        risk: s.risk,
        indicators: s.indicators,
      }))
    : [
        { value: 'momentum',        label: 'Momentum',        desc: 'Follows trend strength',                   timeframes: ['15m', '30m', '1h'],                  defaultTf: '15m' },
        { value: 'ema_crossover',   label: 'EMA Crossover',   desc: 'EMA 9/21 crossover signals',               timeframes: ['15m', '30m', '1h', '4h'],            defaultTf: '1h'  },
        { value: 'mean_reversion',  label: 'Mean Reversion',  desc: 'Trades around average price',              timeframes: ['15m', '30m', '1h', '4h'],            defaultTf: '1h'  },
        { value: 'breakout',        label: 'Breakout',        desc: 'Trades price breakouts',                   timeframes: ['30m', '1h', '4h'],                   defaultTf: '1h'  },
        { value: 'grid',            label: 'Grid',            desc: 'Buy/sell at range levels — sideways mkts', timeframes: ['5m', '15m', '30m', '1h'],            defaultTf: '15m' },
        { value: 'scalping',        label: 'Scalping',        desc: 'Fast entries on small moves',              timeframes: ['1m', '5m', '15m'],                   defaultTf: '5m'  },
        { value: 'trend_following', label: 'Trend Following', desc: 'Rides multi-hour / multi-day trends',      timeframes: ['1h', '4h', '1d'],                    defaultTf: '4h'  },
        { value: 'ai',              label: 'AI Strategy',     desc: 'LLM-powered analysis and signals',         timeframes: ['5m', '15m', '30m', '1h', '4h', '1d'], defaultTf: '1h' },
      ];

  const currentStrategyDef = strategies.find((s) => s.value === formData.strategy_type);

  const intervals = [
    { value: 300, label: '5 min' },
    { value: 900, label: '15 min' },
    { value: 3600, label: '1 hour' },
    { value: 14400, label: '4 hours' },
  ];

  return (
    <div className="space-y-6" style={{ padding: '1.5rem 1.5rem 2rem' }}>
      {/* Tab bar */}
      <div style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', paddingBottom: '0', marginBottom: '0' }}>
        {([['agents', 'Agents'], ['registry', 'Strategy Registry']] as const).map(([tab, label]) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.45rem 1rem',
              fontSize: '0.82rem',
              fontWeight: activeTab === tab ? 600 : 400,
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === tab ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === tab ? 'var(--accent)' : 'var(--text-muted)',
              cursor: 'pointer',
              marginBottom: '-1px',
              transition: 'color 0.15s',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'registry' ? (
        <StrategyManager />
      ) : agentsLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
            <Skeleton width={80} height={24} />
            <Skeleton width={200} height={32} rounded />
            <div style={{ flex: 1 }} />
            <Skeleton width={100} height={32} rounded />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            {Array.from({ length: 6 }, (_, i) => (
              <SkeletonCard key={i} lines={4} height={180} />
            ))}
          </div>
        </div>
      ) : (<>
      <div className="agent-header">
        <h1 className="page-title" style={{ marginBottom: 0 }}>Agents</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem', flexWrap: 'wrap' }}>
          {/* Search */}
          <div style={{ position: 'relative' }}>
            <span style={{ position: 'absolute', left: '.55rem', top: '50%', transform: 'translateY(-50%)', fontSize: '.8rem', color: 'var(--text-muted)', pointerEvents: 'none' }}>🔍</span>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search agents…"
              style={{
                paddingLeft: '1.8rem', paddingRight: '.6rem', paddingTop: '.3rem', paddingBottom: '.3rem',
                borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-elevated)',
                color: 'var(--text)', fontSize: '.8rem', width: 180, outline: 'none',
              }}
            />
          </div>
          {/* Status filter */}
          {(['all', 'enabled', 'disabled'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setFilterStatus(s)}
              style={{
                padding: '.25rem .65rem', borderRadius: 20, fontSize: '.75rem', fontWeight: 600,
                border: '1px solid',
                borderColor: filterStatus === s
                  ? s === 'enabled' ? 'var(--green)' : s === 'disabled' ? 'var(--red)' : 'var(--accent)'
                  : 'var(--border)',
                background: filterStatus === s
                  ? s === 'enabled' ? 'rgba(0,230,118,.12)' : s === 'disabled' ? 'rgba(231,76,60,.12)' : 'rgba(100,130,255,.12)'
                  : 'transparent',
                color: filterStatus === s
                  ? s === 'enabled' ? 'var(--green)' : s === 'disabled' ? 'var(--red)' : 'var(--accent)'
                  : 'var(--text-muted)',
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {s === 'all'
                ? `All (${agents.length})`
                : s === 'enabled'
                ? `✓ Enabled (${agents.filter(a => a.is_enabled).length})`
                : `✕ Disabled (${agents.filter(a => !a.is_enabled).length})`}
            </button>
          ))}
          <button type="button" className="agent-btn" onClick={() => { setShowForm(!showForm); setEditingId(null); }}>
            {showForm ? 'Cancel' : '+ New Agent'}
          </button>
        </div>
      </div>

      {showForm && (
        <div className="agent-form">
          <h3 className="form-title">Create New Strategy</h3>

          <div className="form-group">
            <label className="form-label">Strategy Name</label>
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
                    onChange={(e) => setFormData({ ...formData, strategy_type: e.target.value, timeframe: s.defaultTf })}
                  />
                  <span className="strategy-label">{s.label}</span>
                  <span className="strategy-desc">{s.desc}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">
              Chart Timeframe
              {currentStrategyDef && (
                <span style={{ marginLeft: '.5rem', fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
                  — recommended for {currentStrategyDef.label}: {currentStrategyDef.timeframes.join(', ')}
                </span>
              )}
            </label>
            <div className="strategy-options" style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
              {(currentStrategyDef?.timeframes ?? ['1m','5m','15m','30m','1h','4h','1d']).map((tf) => (
                <label key={tf} className={`strategy-option ${formData.timeframe === tf ? 'selected' : ''}`} style={{ flex: '0 0 auto', minWidth: 'auto' }}>
                  <input
                    type="radio"
                    name="timeframe"
                    value={tf}
                    checked={formData.timeframe === tf}
                    onChange={() => setFormData({ ...formData, timeframe: tf })}
                  />
                  <span className="strategy-label" style={{ fontSize: '.75rem' }}>{tf}</span>
                </label>
              ))}
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

          <div className="form-group">
            <label className="form-label">Trading Venue</label>
            <div className="strategy-options" style={{ flexDirection: 'row' }}>
              {(['phemex', 'hyperliquid'] as const).map((v) => (
                <label key={v} className={`strategy-option ${formData.venue === v ? 'selected' : ''}`} style={{ flex: '1 1 auto' }}>
                  <input
                    type="radio"
                    name="venue"
                    value={v}
                    checked={formData.venue === v}
                    onChange={() => setFormData({ ...formData, venue: v })}
                  />
                  <span className="strategy-label">{v === 'phemex' ? '🔷 Phemex' : 'Ξ Hyperliquid'}</span>
                  <span className="strategy-desc">{v === 'phemex' ? '0.06% taker' : '0.035% taker'}</span>
                </label>
              ))}
            </div>
          </div>

          <button type="button" className="settings-btn" onClick={createAgent}>
            Create Strategy
          </button>
        </div>
      )}

      {agents.length === 0 && !showForm ? (
        <div className="card">
          <p className="text-gray-400">No agents configured yet. Create your first agent to start automated trading.</p>
        </div>
      ) : filteredAgents.length === 0 && !showForm ? (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}>
          No agents match <strong>"{search || filterStatus}"</strong>.{' '}
          <button type="button" onClick={() => { setSearch(''); setFilterStatus('all'); }} style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 'inherit' }}>Clear filters</button>
        </div>
      ) : (
        /* ── Grouped by trader ─────────────────────────────────────────── */
        <>
          {(() => {
            // Build trader groups; agents with no trader_id go into "Unassigned"
            const groups: Array<{ trader: any; agents: any[] }> = [];
            const traderOrder = traders.length > 0 ? traders : [{ id: '__unassigned__', name: 'Unassigned' }];
            for (const trader of traderOrder) {
              const ta = filteredAgents.filter(a => a.trader_id === trader.id || (!a.trader_id && trader.id === '__unassigned__'));
              if (ta.length > 0) groups.push({ trader, agents: ta });
            }
            // Catch any remaining unassigned
            const assignedIds = new Set(groups.flatMap(g => g.agents.map((a: any) => a.id)));
            const unassigned = filteredAgents.filter((a: any) => !assignedIds.has(a.id));
            if (unassigned.length > 0) groups.push({ trader: { id: '__unassigned__', name: 'Unassigned' }, agents: unassigned });
            return groups.map(({ trader, agents: groupAgents }) => {
              const traderMetrics = metricsData as any[];
              const agentIds = new Set(groupAgents.map((a: any) => a.id));
              const groupPnl = traderMetrics.filter((m: any) => agentIds.has(m.agent_id)).reduce((s: number, m: any) => s + (m.total_pnl ?? 0), 0);
              const enabledCount = groupAgents.filter((a: any) => a.is_enabled).length;
              return (
                <div key={trader.id} style={{ marginBottom: '1.5rem' }}>
                  {/* Trader section header */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '0.75rem',
                    marginBottom: '0.75rem', paddingBottom: '0.5rem',
                    borderBottom: '1px solid var(--border)',
                  }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: '50%',
                      background: 'linear-gradient(135deg, var(--accent), #7c3aed)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '0.75rem', fontWeight: 700, color: 'white', flexShrink: 0,
                    }}>
                      {(trader.name ?? 'U').charAt(0).toUpperCase()}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text)' }}>
                        {trader.name ?? 'Unassigned'}
                      </div>
                      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'flex', gap: '0.75rem' }}>
                        <span>{enabledCount}/{groupAgents.length} active</span>
                        <span style={{ color: groupPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {groupPnl >= 0 ? '+' : ''}{groupPnl.toFixed(2)} USDT
                        </span>

                      </div>
                    </div>
                  </div>
                  <div className="agents-grid">
                    {groupAgents.map((agent: any) => (
            <div key={agent.id} className={`agent-card ${agent.is_enabled ? 'enabled' : ''}`}>
              {editingId === agent.id ? (
                <div className="agent-edit-form">
                  <div className="form-group">
                    <label className="form-label">Strategy Name</label>
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
                    <label className="form-label">
                      Chart Timeframe
                      {(() => { const sd = strategies.find(s => s.value === formData.strategy_type); return sd ? <span style={{ marginLeft: '.5rem', fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>recommended: {sd.timeframes.join(', ')}</span> : null; })()}
                    </label>
                    <div className="strategy-options" style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                      {(strategies.find(s => s.value === formData.strategy_type)?.timeframes ?? ['1m','5m','15m','30m','1h','4h','1d']).map((tf) => (
                        <label key={tf} className={`strategy-option ${formData.timeframe === tf ? 'selected' : ''}`} style={{ flex: '0 0 auto', minWidth: 'auto' }}>
                          <input
                            type="radio"
                            name={`edit-tf-${agent.id}`}
                            value={tf}
                            checked={formData.timeframe === tf}
                            onChange={() => setFormData({ ...formData, timeframe: tf })}
                          />
                          <span className="strategy-label" style={{ fontSize: '.75rem' }}>{tf}</span>
                        </label>
                      ))}
                    </div>
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

                  <div className="form-group">
                    <label className="form-label">Trading Venue</label>
                    <div className="strategy-options" style={{ flexDirection: 'row' }}>
                      {(['phemex', 'hyperliquid'] as const).map((v) => (
                        <label key={v} className={`strategy-option ${formData.venue === v ? 'selected' : ''}`} style={{ flex: '1 1 auto' }}>
                          <input
                            type="radio"
                            name={`edit-venue-${agent.id}`}
                            value={v}
                            checked={formData.venue === v}
                            onChange={() => setFormData({ ...formData, venue: v })}
                          />
                          <span className="strategy-label" style={{ fontSize: '.75rem' }}>{v === 'phemex' ? '🔷 Phemex' : 'Ξ Hyperliquid'}</span>
                          <span className="strategy-desc" style={{ fontSize: '.65rem' }}>{v === 'phemex' ? '0.06%' : '0.035%'}</span>
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
                      <div style={{ display: 'flex', gap: '.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
                        <span className="strategy-tag">{agent.strategy_type}</span>
                        <span style={{
                          fontSize: '.6rem', fontFamily: 'var(--mono)', padding: '.15rem .4rem',
                          borderRadius: '4px', background: 'rgba(255,200,0,.08)', color: '#f5c842',
                          border: '1px solid rgba(255,200,0,.2)',
                        }}>{agent.timeframe || '1h'}</span>
                        {agent.trader_id && traderMap[agent.trader_id] && (
                          <span style={{
                            fontSize: '.6rem', fontFamily: 'var(--mono)', padding: '.15rem .4rem',
                            borderRadius: '4px', background: 'var(--accent-dim)', color: 'var(--accent)',
                            border: '1px solid rgba(0,194,255,.2)',
                          }}>
                            {traderMap[agent.trader_id].config?.avatar || '🤖'} {traderMap[agent.trader_id].name}
                          </span>
                        )}
                        {agent.venue === 'hyperliquid' && (
                          <span style={{
                            fontSize: '.6rem', fontFamily: 'var(--mono)', padding: '.15rem .4rem',
                            borderRadius: '4px', background: 'rgba(98,126,234,.12)', color: '#818cf8',
                            border: '1px solid rgba(98,126,234,.25)',
                          }}>Ξ HL</span>
                        )}
                      </div>
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
                      <span className="detail-label">Traded Pairs</span>
                      <span className="detail-value" style={{ color: (agent.trading_pairs ?? []).length === 0 ? 'var(--text-muted)' : undefined }}>
                        {(agent.trading_pairs ?? []).length > 0 ? agent.trading_pairs.map((p: string) => p.replace('USDT', '')).join(', ') : 'None yet'}
                      </span>
                    </div>
                    <div className="detail-row">
                      <span className="detail-label">Allocation</span>
                      <div className="detail-progress">
                        <div className="progress-track">
                          <div
                            className="progress-fill"
                            style={{
                              width: `${Math.min(agent.allocation_percentage, 100)}%`,
                              background: agent.allocation_percentage >= 30 ? 'var(--green)' : agent.allocation_percentage >= 15 ? 'var(--amber)' : 'var(--text-secondary)',
                            }}
                          />
                        </div>
                        <span className="progress-pct">{agent.allocation_percentage}%</span>
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
                    {backtestLoading === agent.id && backtestProgress && (
                      <div className="backtest-progress-card">
                        <div className="backtest-progress-phase">{backtestProgress.phase}</div>
                        <div className="backtest-progress-track">
                          <div
                            className="backtest-progress-fill"
                            style={{ width: `${backtestProgress.pct}%` }}
                          />
                        </div>
                        <div className="backtest-progress-pct">{backtestProgress.pct}%</div>
                      </div>
                    )}
                    {!backtestLoading && backtestResults[agent.id] && (
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
                          <span className="metric-pill-label">Trades</span>
                          <span className="metric-pill-value">{m.actual_trades ?? 0}</span>
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
                   {agent.strategy_type === 'grid' && (() => {
                    const gs = gridSummaries[agent.id];
                    if (!gs) return null;
                    const hasActive = gs.active_grid;
                    const g = gs.active_grid;
                    const hist = gs.historical_grids ?? [];
                    const totalPnl = (g?.realized_pnl ?? 0) + hist.reduce((s: number, x: any) => s + (x.realized_pnl ?? 0), 0);
                    if (!hasActive && hist.length === 0) return null;
                    return (
                      <div style={{
                        margin: '.75rem 0', padding: '.75rem', borderRadius: '8px',
                        background: 'rgba(0,194,255,.04)', border: '1px solid rgba(0,194,255,.12)',
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.5rem' }}>
                          <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: 'var(--accent)', letterSpacing: '.05em', textTransform: 'uppercase' }}>
                            🔲 Grid Engine
                          </span>
                          {hasActive && (
                            <span style={{
                              fontSize: '.55rem', padding: '.1rem .4rem', borderRadius: '4px',
                              background: g.status === 'active' ? 'rgba(34,197,94,.12)' : 'rgba(245,200,66,.1)',
                              color: g.status === 'active' ? '#22c55e' : '#f5c842',
                              border: `1px solid ${g.status === 'active' ? 'rgba(34,197,94,.3)' : 'rgba(245,200,66,.25)'}`,
                              fontFamily: 'var(--mono)',
                            }}>{g.status?.toUpperCase()}</span>
                          )}
                        </div>
                        {hasActive && (
                          <>
                            <div style={{ display: 'flex', gap: '.75rem', flexWrap: 'wrap', marginBottom: '.4rem' }}>
                              {[
                                { label: 'Range', value: `$${g.grid_low?.toFixed(4)} – $${g.grid_high?.toFixed(4)}` },
                                { label: 'Levels', value: `${g.open_levels ?? 0}/${g.grid_levels ?? 0} open` },
                                { label: 'Invested', value: `$${(g.total_invested ?? 0).toFixed(2)}` },
                                { label: 'Realised', value: `$${(g.realized_pnl ?? 0).toFixed(2)}`, color: (g.realized_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' },
                              ].map(({ label, value, color }) => (
                                <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: '.1rem' }}>
                                  <span style={{ fontSize: '.55rem', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{label}</span>
                                  <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: color ?? 'var(--text-secondary)' }}>{value}</span>
                                </div>
                              ))}
                            </div>
                            {/* Level visualisation bar */}
                            {g.levels && g.levels.length > 0 && (
                              <div style={{ display: 'flex', gap: '2px', marginTop: '.25rem', height: '8px' }}>
                                {g.levels.map((lv: any, i: number) => {
                                  const col = lv.status === 'open' ? '#22c55e'
                                    : lv.status === 'filled' ? '#3b82f6'
                                    : lv.status === 'closed' ? '#6b7280'
                                    : lv.status === 'cancelled' ? '#374151'
                                    : 'rgba(0,194,255,.25)';
                                  return (
                                    <div key={i} title={`Level ${lv.level_index}: ${lv.side} @ $${lv.price?.toFixed(4)} — ${lv.status}`}
                                      style={{ flex: 1, background: col, borderRadius: '2px', cursor: 'default' }} />
                                  );
                                })}
                              </div>
                            )}
                            <div style={{ marginTop: '.4rem', display: 'flex', gap: '.5rem' }}>
                              <button
                                type="button"
                                onClick={() => cancelGrid(agent.id)}
                                disabled={cancellingGrid === agent.id}
                                style={{
                                  fontSize: '.6rem', padding: '.2rem .5rem', borderRadius: '4px', cursor: 'pointer',
                                  background: 'rgba(239,68,68,.1)', color: '#ef4444',
                                  border: '1px solid rgba(239,68,68,.3)',
                                }}
                              >
                                {cancellingGrid === agent.id ? 'Cancelling…' : '⊗ Cancel Grid'}
                              </button>
                              <button
                                type="button"
                                onClick={() => fetchGridSummary(agent.id)}
                                style={{
                                  fontSize: '.6rem', padding: '.2rem .5rem', borderRadius: '4px', cursor: 'pointer',
                                  background: 'rgba(0,194,255,.06)', color: 'var(--accent)',
                                  border: '1px solid rgba(0,194,255,.2)',
                                }}
                              >
                                ↺ Refresh
                              </button>
                            </div>
                          </>
                        )}
                        {!hasActive && hist.length > 0 && (
                          <div style={{ fontSize: '.6rem', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                            No active grid · {hist.length} historical · Total P&L:&nbsp;
                            <span style={{ color: totalPnl >= 0 ? '#22c55e' : '#ef4444' }}>${totalPnl.toFixed(2)}</span>
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
                      onClick={() => runBacktest(agent.id, (agent.trading_pairs ?? [])[0] || 'BTCUSDT', agent.timeframe || '1h')}
                      disabled={backtestLoading === agent.id}
                    >
                      {backtestLoading === agent.id ? backtestProgress?.phase.replace('…', '') || 'Running…' : 'Backtest'}
                    </button>
                    <button type="button" className="edit-btn" onClick={() => startEdit(agent)}>Edit</button>
                    <button type="button" className="delete-btn" onClick={() => deleteAgent(agent.id)}>Delete</button>
                  </div>
                </>
              )}
            </div>
          ))}
          </div>
                </div>
              );
            });
          })()}
        </>
      )}
    </>
    )}
    </div>
  );
}
