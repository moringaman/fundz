import { useState } from 'react';
import { TrendingUp, Activity, Target, BarChart3, Cpu } from 'lucide-react';
import { useTraderLeaderboard, useTraders, useAgents, useAutomationMetrics } from '../hooks/useQueries';
import { SkeletonCard } from '../components/common/Skeleton';

const RISK_COLORS: Record<string, string> = {
  low: 'var(--green)',
  moderate: 'var(--amber)',
  high: 'var(--red)',
};

const RISK_LABELS: Record<string, string> = {
  low: 'Conservative',
  moderate: 'Moderate',
  high: 'Aggressive',
};

const MODEL_LABELS: Record<string, string> = {
  'anthropic/claude-sonnet-4': 'Claude Sonnet',
  'openai/gpt-4o': 'GPT-4o',
  'google/gemini-2.0-flash-001': 'Gemini Flash',
  'claude-3-sonnet': 'Claude Sonnet',
  'gpt-4o': 'GPT-4o',
  'gemini-flash': 'Gemini Flash',
};

function modelLabel(m: string) {
  return MODEL_LABELS[m] || m.split('/').pop() || m;
}

function PnlBadge({ value }: { value: number }) {
  if (value > 0) return <span className="positive" style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>+${value.toFixed(2)}</span>;
  if (value < 0) return <span className="negative" style={{ fontFamily: 'var(--mono)', fontWeight: 700 }}>-${Math.abs(value).toFixed(2)}</span>;
  return <span style={{ fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>$0.00</span>;
}

function WinBar({ rate }: { rate: number }) {
  const pct = (rate * 100);
  const color = pct >= 60 ? 'var(--green)' : pct >= 45 ? 'var(--amber)' : 'var(--red)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
      <div style={{ flex: 1, height: 5, background: 'var(--bg-hover)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width .6s ease' }} />
      </div>
      <span style={{ fontFamily: 'var(--mono)', fontSize: '.72rem', color, minWidth: '2.8rem', textAlign: 'right' }}>
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

const CONSISTENCY_STYLES: Record<string, { bg: string; border: string; color: string; label: string }> = {
  CONSISTENT:        { bg: 'rgba(0,230,118,.08)',  border: 'rgba(0,230,118,.3)',  color: 'var(--green)', label: '✓ CONSISTENT' },
  INCONSISTENT:      { bg: 'rgba(255,59,48,.08)',  border: 'rgba(255,59,48,.35)', color: 'var(--red)',   label: '⚠ INCONSISTENT' },
  INSUFFICIENT_DATA: { bg: 'var(--bg-hover)',       border: 'var(--border)',       color: 'var(--text-dim)', label: '— N/A' },
};
const SHARPE_STYLES: Record<string, { color: string; label: string }> = {
  high:   { color: 'var(--green)', label: '▲ Sharpe' },
  medium: { color: 'var(--amber)', label: '— Sharpe' },
  low:    { color: 'var(--red)',   label: '▼ Sharpe' },
};

function ConsistencyBadge({ flag, score, sharpe, sharpeTier }: {
  flag: string; score: number; sharpe: number; sharpeTier: string;
}) {
  const cs = CONSISTENCY_STYLES[flag] || CONSISTENCY_STYLES.INSUFFICIENT_DATA;
  const ss = SHARPE_STYLES[sharpeTier] || SHARPE_STYLES.medium;
  return (
    <div style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }}>
      <span
        title={flag === 'INCONSISTENT' ? 'A single trade > 40% of period profit — capital increase blocked' : `Consistency score: ${(score * 100).toFixed(0)}%`}
        style={{
          fontSize: '.54rem', fontFamily: 'var(--mono)', padding: '.2rem .45rem',
          background: cs.bg, border: `1px solid ${cs.border}`, color: cs.color,
          letterSpacing: '.06em', cursor: 'default',
        }}
      >{cs.label}</span>
      {sharpeTier !== undefined && (
        <span
          title={`Rolling Sharpe: ${sharpe.toFixed(2)}`}
          style={{
            fontSize: '.54rem', fontFamily: 'var(--mono)', padding: '.2rem .45rem',
            background: 'var(--bg-hover)', border: '1px solid var(--border)',
            color: ss.color, letterSpacing: '.06em', cursor: 'default',
          }}
        >{ss.label} {sharpe.toFixed(2)}</span>
      )}
    </div>
  );
}

const DRAWDOWN_STYLES: Record<string, { icon: string; color: string; border: string; bg: string }> = {
  caution:    { icon: '⚠️', color: 'var(--amber)', border: 'rgba(255,176,0,.3)',  bg: 'rgba(255,176,0,.07)' },
  warning:    { icon: '💀', color: 'var(--red)',   border: 'rgba(255,59,48,.35)', bg: 'rgba(255,59,48,.08)' },
  terminated: { icon: '🔴', color: 'var(--text-dim)', border: 'var(--border)',    bg: 'var(--bg-hover)' },
};

function DrawdownBadge({ level, drawdownPct }: { level: string; drawdownPct?: number }) {
  const s = DRAWDOWN_STYLES[level];
  if (!s) return null;
  return (
    <span
      title={`Drawdown warning: ${level.toUpperCase()}${drawdownPct != null ? ` (${drawdownPct.toFixed(1)}% from peak)` : ''}`}
      style={{
        fontSize: '.54rem', fontFamily: 'var(--mono)', padding: '.2rem .45rem',
        background: s.bg, border: `1px solid ${s.border}`, color: s.color,
        letterSpacing: '.06em', cursor: 'default',
      }}
    >{s.icon} {level.toUpperCase()}</span>
  );
}

export function TradersPage() {
  const { data: leaderboard = [], isPending: tradersLoading } = useTraderLeaderboard();
  const { data: tradersData = [] } = useTraders();
  const { data: agentsData = [] } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();

  const [selected, setSelected] = useState<string | null>(null);

  const traders: any[] = Array.isArray(leaderboard) ? leaderboard : [];
  const traderConfigs: any[] = Array.isArray(tradersData) ? tradersData : [];
  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const metrics: any[] = Array.isArray(metricsData) ? metricsData : [];

  // Config map for bio/preferred strategies
  const configMap: Record<string, any> = {};
  for (const t of traderConfigs) configMap[t.id] = t.config || {};

  // Agents grouped by trader
  const agentsByTrader: Record<string, any[]> = {};
  for (const a of agents) {
    if (!a.trader_id) continue;
    if (!agentsByTrader[a.trader_id]) agentsByTrader[a.trader_id] = [];
    agentsByTrader[a.trader_id].push(a);
  }

  // Metrics by agent id
  const metricsByAgent: Record<string, any> = {};
  for (const m of metrics) metricsByAgent[m.agent_id] = m;

  // Strategy usage stats per trader
  function traderStrategyStats(traderId: string) {
    const tAgents = agentsByTrader[traderId] || [];
    const stratStats: Record<string, { count: number; total_pnl: number; win_rate: number; runs: number }> = {};
    for (const a of tAgents) {
      const s = a.strategy_type || 'unknown';
      const m = metricsByAgent[a.id] || {};
      if (!stratStats[s]) stratStats[s] = { count: 0, total_pnl: 0, win_rate: 0, runs: 0 };
      stratStats[s].count += 1;
      stratStats[s].total_pnl += m.total_pnl || 0;
      stratStats[s].runs += m.total_runs || 0;
      // Weighted win rate
      const prevRuns = stratStats[s].runs - (m.total_runs || 0);
      stratStats[s].win_rate = stratStats[s].runs > 0
        ? (stratStats[s].win_rate * prevRuns + (m.win_rate || 0) * (m.total_runs || 0)) / stratStats[s].runs
        : 0;
    }
    return stratStats;
  }

  const selectedTrader = selected ? traders.find(t => t.id === selected) : traders[0];
  const activeId = selectedTrader?.id;

  const ranks = ['🥇', '🥈', '🥉'];

  return (
    <div style={{ padding: '1.25rem 1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', height: '100%', overflow: 'auto' }}>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Traders</h1>
        <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)', padding: '.2rem .55rem', border: '1px solid var(--border)', borderRadius: 3 }}>
          {traders.length} active
        </span>
      </div>

      {tradersLoading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
          {Array.from({ length: 3 }, (_, i) => (
            <SkeletonCard key={i} lines={5} height={220} />
          ))}
        </div>
      ) : (
      <>

      {/* ── Leaderboard cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${traders.length || 3}, 1fr)`, gap: '1rem' }}>
        {traders.map((t: any, i: number) => {
          const cfg = configMap[t.id] || t.config || {};
          const isActive = t.id === activeId;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setSelected(t.id)}
              style={{
                background: isActive ? 'var(--bg-elevated)' : 'var(--surface)',
                border: `1px solid ${isActive ? 'var(--accent)' : 'var(--border)'}`,
                borderRadius: 0, padding: '1.25rem', cursor: 'pointer',
                textAlign: 'left', position: 'relative', overflow: 'hidden',
                transition: 'border-color .2s, background .2s',
              }}
            >
              {isActive && (
                <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: 'var(--accent)' }} />
              )}
              {/* Rank + avatar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '.75rem', marginBottom: '.85rem' }}>
                <span style={{ fontSize: '1.5rem' }}>{ranks[i] || '🏅'}</span>
                <span style={{ fontSize: '1.8rem' }}>{cfg.avatar || t.config?.avatar || '🤖'}</span>
                <div>
                  <div style={{ fontSize: '.95rem', fontWeight: 700, color: 'var(--text-primary)' }}>{t.name}</div>
                  <div style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--accent)', marginTop: '.1rem' }}>
                    {modelLabel(t.llm_model)}
                  </div>
                </div>
                <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                  <div style={{ fontSize: '.65rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: '.2rem' }}>Allocation</div>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: '1rem', fontWeight: 700, color: 'var(--accent)' }}>
                    {t.allocation_pct?.toFixed(1)}%
                  </div>
                </div>
              </div>

              {/* P&L */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem', marginBottom: '.85rem' }}>
                <div className="stat-card" style={{ padding: '.5rem .65rem' }}>
                  <div className="stat-label">Total P&L</div>
                  <div className="stat-value" style={{ fontSize: '.88rem' }}>
                    <PnlBadge value={t.total_pnl || 0} />
                  </div>
                </div>
                <div className="stat-card" style={{ padding: '.5rem .65rem' }}>
                  <div className="stat-label">Trades</div>
                  <div className="stat-value" style={{ fontSize: '.88rem', fontFamily: 'var(--mono)' }}>{t.total_trades || 0}</div>
                </div>
              </div>

              {/* Win rate bar */}
              <div style={{ marginBottom: '.6rem' }}>
                <div style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: '.3rem' }}>Win Rate</div>
                <WinBar rate={t.win_rate || 0} />
              </div>

              {/* Strategies badge */}
              <div style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap', marginTop: '.5rem' }}>
                {(cfg.preferred_strategies || []).map((s: string) => (
                  <span key={s} style={{
                    fontSize: '.54rem', fontFamily: 'var(--mono)', padding: '.2rem .45rem',
                    background: 'var(--bg-hover)', border: '1px solid var(--border)',
                    color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.08em',
                  }}>{s}</span>
                ))}
              </div>

              {/* 9.1 Consistency & Sharpe badges */}
              {(t.consistency_flag || t.sharpe_tier) && (
                <div style={{ marginTop: '.4rem' }}>
                  <ConsistencyBadge
                    flag={t.consistency_flag || 'INSUFFICIENT_DATA'}
                    score={t.consistency_score ?? 0.5}
                    sharpe={t.sharpe ?? 0}
                    sharpeTier={t.sharpe_tier || 'medium'}
                  />
                </div>
              )}

              {/* 9.2 Drawdown warning badge */}
              {t.drawdown_warning_level && (
                <div style={{ marginTop: '.3rem' }}>
                  <DrawdownBadge level={t.drawdown_warning_level} drawdownPct={t.lifetime_drawdown_pct} />
                </div>
              )}
            </button>
          );
        })}
        {traders.length === 0 && (
          <div className="card" style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '3rem', color: 'var(--text-dim)' }}>
            <Cpu size={32} style={{ marginBottom: '.75rem', opacity: .4 }} />
            <p>No traders loaded yet. Start the scheduler to initialise the trading team.</p>
          </div>
        )}
      </div>

      {/* ── Detail panel for selected trader ── */}
      {selectedTrader && (() => {
        const cfg = configMap[selectedTrader.id] || selectedTrader.config || {};
        const tAgents = agentsByTrader[selectedTrader.id] || [];
        const stratStats = traderStrategyStats(selectedTrader.id);

        // Most used strategy
        const sortedByRuns = Object.entries(stratStats).sort(([,a],[,b]) => b.runs - a.runs);
        const sortedByPnl  = Object.entries(stratStats).sort(([,a],[,b]) => b.total_pnl - a.total_pnl);

        const tradeFreq = selectedTrader.total_trades > 0
          ? `~${(selectedTrader.total_trades / Math.max(tAgents.length, 1)).toFixed(0)} trades / strategy`
          : 'No trades yet';

        return (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>

            {/* Bio + profile */}
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '.85rem' }}>
                <span style={{ fontSize: '2.4rem' }}>{cfg.avatar || '🤖'}</span>
                <div>
                  <h2 style={{ fontSize: '1.1rem', fontWeight: 700, marginBottom: '.15rem' }}>
                    {selectedTrader.name}
                  </h2>
                  <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: 'var(--mono)', fontSize: '.62rem', color: 'var(--accent)', background: 'rgba(0,212,255,.08)', padding: '.18rem .45rem' }}>
                      <Cpu size={9} style={{ display: 'inline', marginRight: '.2rem' }} />
                      {modelLabel(selectedTrader.llm_model)}
                    </span>
                    {cfg.risk_tolerance && (
                      <span style={{ fontFamily: 'var(--mono)', fontSize: '.6rem', padding: '.18rem .45rem', border: '1px solid var(--border)', color: RISK_COLORS[cfg.risk_tolerance] || 'var(--text-dim)' }}>
                        {RISK_LABELS[cfg.risk_tolerance] || cfg.risk_tolerance}
                      </span>
                    )}
                    <span style={{ fontFamily: 'var(--mono)', fontSize: '.6rem', color: selectedTrader.is_enabled ? 'var(--green)' : 'var(--text-dim)', padding: '.18rem .45rem', border: `1px solid ${selectedTrader.is_enabled ? 'rgba(0,230,118,.2)' : 'var(--border)'}` }}>
                      {selectedTrader.is_enabled ? '● ACTIVE' : '○ INACTIVE'}
                    </span>
                    {selectedTrader.consistency_flag && (
                      <ConsistencyBadge
                        flag={selectedTrader.consistency_flag}
                        score={selectedTrader.consistency_score ?? 0.5}
                        sharpe={selectedTrader.sharpe ?? 0}
                        sharpeTier={selectedTrader.sharpe_tier || 'medium'}
                      />
                    )}
                    {selectedTrader.drawdown_warning_level && (
                      <DrawdownBadge
                        level={selectedTrader.drawdown_warning_level}
                        drawdownPct={selectedTrader.lifetime_drawdown_pct}
                      />
                    )}
                  </div>
                </div>
              </div>

              {/* Bio */}
              {cfg.bio && (
                <div>
                  <div className="stat-label" style={{ marginBottom: '.4rem' }}>Bio</div>
                  <p style={{ fontSize: '.78rem', color: 'var(--text-secondary)', lineHeight: 1.7, padding: '.65rem .75rem', background: 'var(--bg-hover)', borderLeft: '2px solid var(--accent)' }}>
                    {cfg.bio}
                  </p>
                </div>
              )}

              {/* 9.2 Evolution lineage */}
              {selectedTrader.successor_of && (
                <div style={{
                  fontSize: '.72rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)',
                  padding: '.45rem .65rem', background: 'var(--bg-hover)',
                  border: '1px solid var(--border)', borderLeft: '2px solid var(--amber)',
                }}>
                  🌱 Successor trader — replaced a terminated predecessor
                </div>
              )}

              {/* Trading Style */}
              <div>
                <div className="stat-label" style={{ marginBottom: '.4rem' }}>Trading Style</div>
                <p style={{ fontSize: '.78rem', color: 'var(--text-secondary)', lineHeight: 1.7, padding: '.65rem .75rem', background: 'var(--bg-hover)', borderLeft: '2px solid var(--accent)' }}>
                  {cfg.style || 'No style description available.'}
                </p>
              </div>

              {/* Stats grid */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '.5rem' }}>
                <div className="stat-card">
                  <div className="stat-label">Allocation</div>
                  <div className="stat-value" style={{ fontFamily: 'var(--mono)', fontSize: '.9rem' }}>
                    {selectedTrader.allocation_pct?.toFixed(1)}%
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Strategies</div>
                  <div className="stat-value" style={{ fontFamily: 'var(--mono)', fontSize: '.9rem' }}>
                    {selectedTrader.agent_count || tAgents.length}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Total Trades</div>
                  <div className="stat-value" style={{ fontFamily: 'var(--mono)', fontSize: '.9rem' }}>
                    {selectedTrader.total_trades || 0}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Total P&L</div>
                  <div className="stat-value" style={{ fontSize: '.85rem' }}>
                    <PnlBadge value={selectedTrader.total_pnl || 0} />
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Win Rate</div>
                  <div className="stat-value" style={{ fontFamily: 'var(--mono)', fontSize: '.9rem', color: (selectedTrader.win_rate || 0) >= 0.5 ? 'var(--green)' : 'var(--red)' }}>
                    {((selectedTrader.win_rate || 0) * 100).toFixed(0)}%
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Trade Freq</div>
                  <div className="stat-value" style={{ fontSize: '.72rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>
                    {tradeFreq}
                  </div>
                </div>
              </div>

              {/* Preferred strategies */}
              <div>
                <div className="stat-label" style={{ marginBottom: '.4rem' }}>Preferred Strategies</div>
                <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap' }}>
                  {(cfg.preferred_strategies || []).map((s: string) => (
                    <span key={s} style={{
                      fontSize: '.65rem', fontFamily: 'var(--mono)', padding: '.28rem .65rem',
                      background: 'var(--bg-hover)', border: '1px solid var(--accent)',
                      color: 'var(--accent)', letterSpacing: '.08em', textTransform: 'uppercase',
                    }}>{s}</span>
                  ))}
                  {(!cfg.preferred_strategies || cfg.preferred_strategies.length === 0) && (
                    <span style={{ fontSize: '.72rem', color: 'var(--text-dim)' }}>No preferences configured</span>
                  )}
                </div>
              </div>
            </div>

            {/* Right column */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

              {/* Strategies managed */}
              <div className="card">
                <h3 className="card-title" style={{ marginBottom: '.85rem', display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                  <Activity size={13} /> Managed Strategies
                </h3>
                {tAgents.length === 0 ? (
                  <p style={{ fontSize: '.75rem', color: 'var(--text-dim)' }}>No strategies assigned to this trader yet.</p>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                    {tAgents.map((a: any) => {
                      const m = metricsByAgent[a.id] || {};
                      return (
                        <div key={a.id} style={{
                          display: 'grid', gridTemplateColumns: '1fr auto auto auto',
                          alignItems: 'center', gap: '.75rem',
                          padding: '.5rem .65rem',
                          background: 'var(--bg-hover)',
                          border: `1px solid ${a.is_enabled ? 'rgba(0,230,118,.12)' : 'var(--border)'}`,
                        }}>
                          <div>
                            <div style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-primary)' }}>{a.name}</div>
                            <div style={{ fontSize: '.58rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                              {a.strategy_type}
                            </div>
                          </div>
                          <span style={{ fontFamily: 'var(--mono)', fontSize: '.68rem', color: (m.total_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                            {(m.total_pnl || 0) >= 0 ? '+' : ''}${(m.total_pnl || 0).toFixed(2)}
                          </span>
                          <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-secondary)' }}>
                            {((m.win_rate || 0) * 100).toFixed(0)}% win
                          </span>
                          <span style={{
                            fontSize: '.55rem', fontWeight: 700, padding: '.15rem .4rem',
                            background: a.is_enabled ? 'var(--green-dim)' : 'var(--bg-hover)',
                            color: a.is_enabled ? 'var(--green)' : 'var(--text-dim)',
                            border: `1px solid ${a.is_enabled ? 'rgba(0,230,118,.25)' : 'var(--border)'}`,
                          }}>
                            {a.is_enabled ? 'ON' : 'OFF'}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Strategy effectiveness */}
              {Object.keys(stratStats).length > 0 && (
                <div className="card">
                  <h3 className="card-title" style={{ marginBottom: '.85rem', display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                    <Target size={13} /> Strategy Effectiveness
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: '.5rem', padding: '0 .25rem .3rem', borderBottom: '1px solid var(--border)' }}>
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>Strategy</span>
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)', textAlign: 'right' }}>P&L</span>
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)', textAlign: 'right' }}>Win %</span>
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--text-dim)', textAlign: 'right' }}>Runs</span>
                    </div>
                    {sortedByPnl.map(([strat, s]) => (
                      <div key={strat} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: '.5rem', alignItems: 'center', padding: '0 .25rem' }}>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '.68rem', color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{strat}</span>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '.68rem', color: s.total_pnl >= 0 ? 'var(--green)' : 'var(--red)', textAlign: 'right' }}>
                          {s.total_pnl >= 0 ? '+' : ''}${s.total_pnl.toFixed(2)}
                        </span>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '.68rem', color: 'var(--text-secondary)', textAlign: 'right' }}>
                          {(s.win_rate * 100).toFixed(0)}%
                        </span>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-dim)', textAlign: 'right' }}>{s.runs}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Most used strategy callout */}
              {sortedByRuns.length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.75rem' }}>
                  <div className="card" style={{ background: 'var(--bg-elevated)', textAlign: 'center', padding: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '.35rem', marginBottom: '.4rem' }}>
                      <BarChart3 size={12} style={{ color: 'var(--accent)' }} />
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>Most Used</span>
                    </div>
                    <div style={{ fontFamily: 'var(--mono)', fontSize: '.82rem', fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                      {sortedByRuns[0]?.[0] || '—'}
                    </div>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.2rem' }}>
                      {sortedByRuns[0]?.[1]?.runs || 0} runs
                    </div>
                  </div>
                  <div className="card" style={{ background: 'var(--bg-elevated)', textAlign: 'center', padding: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '.35rem', marginBottom: '.4rem' }}>
                      <TrendingUp size={12} style={{ color: 'var(--green)' }} />
                      <span style={{ fontSize: '.58rem', fontWeight: 700, letterSpacing: '.12em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>Most Profitable</span>
                    </div>
                    <div style={{ fontFamily: 'var(--mono)', fontSize: '.82rem', fontWeight: 700, color: (sortedByPnl[0]?.[1]?.total_pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                      {sortedByPnl[0]?.[0] || '—'}
                    </div>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.2rem' }}>
                      {(sortedByPnl[0]?.[1]?.total_pnl || 0) >= 0 ? '+' : ''}${(sortedByPnl[0]?.[1]?.total_pnl || 0).toFixed(2)}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {traders.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)', fontSize: '.85rem' }}>
          Start the scheduler to load and initialise the trading team.
        </div>
      )}
      </>
      )}
    </div>
  );
}
