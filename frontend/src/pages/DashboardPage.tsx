import { useNavigate } from 'react-router-dom';
import { ArrowUpRight, ArrowDownRight, Minus, HelpCircle, RotateCcw, Wallet, Users, Shield } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../store/hooks';
import { setSelectedSymbol } from '../store/slices/marketSlice';
import { paperApi } from '../lib/api';
import {
  usePaperStatus,
  usePaperPnl,
  usePaperPortfolio,
  useBalance,
  useAgents,
  useAutomationMetrics,
  useAutomationRuns,
  useFundTeamStatus,
} from '../hooks/useQueries';
import { WsIndicator } from '../components/common/WsIndicator';
import { MiniChart } from '../components/common/MiniChart';
import { timeAgo } from '../utils/timeAgo';

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'];

export function DashboardPage() {
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const ticker = useAppSelector((s) => s.market.ticker);
  const tickers = useAppSelector((s) => s.market.tickers);
  const signal = useAppSelector((s) => s.market.signal);
  const indicators = useAppSelector((s) => s.market.indicators);
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const wsStatus = useAppSelector((s) => s.ui.wsStatus);
  const klines = useAppSelector((s) => s.market.klines);

  const { data: paperStatus, refetch: refetchStatus } = usePaperStatus();
  const { data: paperPnl, refetch: refetchPnl } = usePaperPnl();
  const { data: portfolio } = usePaperPortfolio();
  const { data: balancesRaw } = useBalance();
  const { data: agentsData = [] } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();
  const { data: runsData = [] } = useAutomationRuns(undefined, 12);
  const { data: teamStatus } = useFundTeamStatus();

  const paperEnabled = paperStatus?.enabled ?? false;
  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const metrics: any[] = Array.isArray(metricsData) ? metricsData : [];
  const runs: any[] = Array.isArray(runsData) ? runsData : [];

  const balances = Array.isArray(balancesRaw?.data)
    ? balancesRaw.data
    : Array.isArray(balancesRaw) ? balancesRaw : [];

  const enabledAgents = agents.filter((a) => a.is_enabled);
  const totalPnl = metrics.reduce((s: number, m: any) => s + (m.total_pnl || 0), 0);
  const upChange = (ticker?.priceChangePercent ?? 0) >= 0;

  const togglePaper = async () => {
    if (paperEnabled) { await paperApi.disable(); } else { await paperApi.enable(); }
    await refetchStatus();
    await refetchPnl();
  };

  const sigAction = signal?.action ?? 'hold';
  const sigConf = signal?.confidence ?? 0;

  // ── Portfolio totals (from canonical backend endpoint) ──
  const portfolioTotal = portfolio?.total_capital ?? 0;
  const usdtTotal = portfolio?.usdt_total ?? 0;
  const holdingsValue = portfolio?.positions_value ?? 0;
  const exposurePct = portfolio?.exposure_pct ?? 0;
  const portfolioBalances: { asset: string; available: number; locked: number }[] =
    Array.isArray(portfolio?.balances) ? portfolio.balances : [];

  return (
    <div style={{ padding: '1rem 1.1rem', display: 'flex', flexDirection: 'column', gap: '.75rem', height: '100%', overflow: 'auto' }}>

      {/* ── Ticker bar ── */}
      <div className="ticker-bar">
        <span className="ticker-symbol">{ticker?.symbol ?? selectedSymbol}</span>
        <span className="ticker-price">
          ${ticker?.lastPrice?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '—'}
        </span>
        <span className={`ticker-change ${upChange ? 'up' : 'down'}`}>
          {upChange ? '+' : ''}{ticker?.priceChangePercent?.toFixed(2) ?? '0.00'}%
        </span>
        <div className="ticker-meta">
          <div className="ticker-meta-item">
            <span className="ticker-meta-label">24h High</span>
            <span className="ticker-meta-value">${ticker?.high?.toLocaleString() ?? '—'}</span>
          </div>
          <div className="ticker-meta-item">
            <span className="ticker-meta-label">24h Low</span>
            <span className="ticker-meta-value">${ticker?.low?.toLocaleString() ?? '—'}</span>
          </div>
          <div className="ticker-meta-item">
            <span className="ticker-meta-label">Volume</span>
            <span className="ticker-meta-value">${ticker?.volume?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '—'}</span>
          </div>
        </div>
        <WsIndicator />
      </div>

      {/* ── Live Ticker Strip (One-Click Selection) ── */}
      <div className="symbol-strip">
        {SYMBOLS.map((sym) => {
          const symTicker = tickers[sym];
          const isSelected = sym === selectedSymbol;
          const symChange = symTicker?.priceChangePercent ?? 0;
          const symUp = symChange >= 0;
          return (
            <button
              key={sym}
              type="button"
              onClick={() => dispatch(setSelectedSymbol(sym))}
              className={`symbol-chip clickable ${isSelected ? 'active' : ''}`}
              style={{
                cursor: 'pointer',
                opacity: isSelected ? 1 : 0.7,
                transform: isSelected ? 'scale(1.05)' : 'scale(1)',
                transition: 'all 0.2s ease',
              }}
            >
              <span className="symbol-chip-name">{sym.replace('USDT', '')}</span>
              <span className="symbol-chip-price">
                {symTicker ? `$${symTicker.lastPrice?.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—'}
              </span>
              <span className={`symbol-chip-chg ${symUp ? 'up' : 'down'}`}>
                {symUp ? '+' : ''}{symChange?.toFixed(2) ?? '0.00'}%
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Main dashboard grid ── */}
      <div className="dash-grid">

        {/* Column 1 — Signal + Key Indicators */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>

          {/* Signal */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <span className="panel-title">AI Signal</span>
                <span title="ML-based buy/sell/hold signal generated from technical indicators and market structure. Confidence level indicates model certainty.">
                  <HelpCircle
                    size={14}
                    style={{ color: 'var(--text-dim)', cursor: 'help' }}
                  />
                </span>
              </div>
              <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-dim)' }}>
                {wsStatus === 'connected' ? 'LIVE' : 'DELAYED'}
              </span>
            </div>
            <div className="panel-body">
              <div className={`signal-badge ${sigAction}`}>
                <span style={{ fontSize: '1.6rem' }}>
                  {sigAction === 'buy' ? <ArrowUpRight size={32} /> : sigAction === 'sell' ? <ArrowDownRight size={32} /> : <Minus size={28} />}
                </span>
                <div className="signal-meta">
                  <span className="signal-action">{sigAction.toUpperCase()}</span>
                  <span className="signal-conf">{(sigConf * 100).toFixed(0)}% confidence</span>
                  {signal?.reasoning && (
                    <span className="signal-reasoning-text">{signal.reasoning}</span>
                  )}
                </div>
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                  <div className="confidence-bar">
                    <div className="confidence-fill" style={{ width: `${sigConf * 100}%` }} />
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Mini Chart */}
          {klines && klines.length > 0 && (
            <div className="panel" style={{ display: 'flex', flexDirection: 'column' }}>
              <div className="panel-header">
                <span className="panel-title">Price Action</span>
              </div>
              <div style={{ flex: 1, minHeight: '120px', position: 'relative' }}>
                <MiniChart data={klines.slice(-24)} />
              </div>
            </div>
          )}

          {/* Key Indicators */}
          {indicators && (
            <div className="panel">
              <div className="panel-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                  <span className="panel-title">Key Indicators</span>
                  <span title="Technical indicators used by the AI model. RSI: momentum (oversold <30, overbought >70). MACD: trend strength. Bollinger Bands: volatility zones. SMA: trend direction. ATR: volatility magnitude.">
                    <HelpCircle
                      size={14}
                      style={{ color: 'var(--text-dim)', cursor: 'help' }}
                    />
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => navigate('/trading')}
                  style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}
                >
                  CHART →
                </button>
              </div>
              <div className="indicators-compact">
                {[
                  { label: 'RSI (14)', val: indicators.rsi?.toFixed(1), color: indicators.rsi != null ? (indicators.rsi < 30 ? 'positive' : indicators.rsi > 70 ? 'negative' : '') : '' },
                  { label: 'MACD', val: indicators.macd?.toFixed(3), color: (indicators.macd ?? 0) > (indicators.macd_signal ?? 0) ? 'positive' : 'negative' },
                  { label: 'BB Upper', val: indicators.bb_upper?.toFixed(0), color: '' },
                  { label: 'BB Lower', val: indicators.bb_lower?.toFixed(0), color: '' },
                  { label: 'SMA 20', val: indicators.sma_20?.toFixed(0), color: '' },
                  { label: 'SMA 50', val: indicators.sma_50?.toFixed(0), color: '' },
                  { label: 'ATR', val: indicators.atr?.toFixed(2), color: 'amber' },
                ].map(({ label, val, color }) => (
                  <div key={label} className="indicator-row">
                    <span className="indicator-label">{label}</span>
                    <span className={`indicator-value ${color}`}>{val ?? '—'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Column 2 — Agent Activity Feed */}
        <div className="panel" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <span className="panel-title">Agent Activity</span>
              <span title="Real-time feed of AI agent signals and executions. Shows which agents are active, what signals they've generated, and whether trades were executed.">
                <HelpCircle
                  size={14}
                  style={{ color: 'var(--text-dim)', cursor: 'help' }}
                />
              </span>
            </div>
            <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-secondary)' }}>
              {enabledAgents.length} active
            </span>
          </div>

          {/* Agent status row */}
          <div style={{ padding: '.6rem 1.1rem', borderBottom: '1px solid var(--border)', display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
            {agents.length === 0 ? (
              <span style={{ fontSize: '.75rem', color: 'var(--text-dim)' }}>No agents configured</span>
            ) : agents.map((a) => (
              <span
                key={a.id}
                style={{
                  padding: '.2rem .55rem',
                  borderRadius: '4px',
                  fontSize: '.68rem',
                  fontFamily: 'var(--mono)',
                  background: a.is_enabled ? 'var(--green-dim)' : 'var(--bg-hover)',
                  border: `1px solid ${a.is_enabled ? 'rgba(0,230,118,.25)' : 'var(--border)'}`,
                  color: a.is_enabled ? 'var(--green)' : 'var(--text-dim)',
                }}
              >
                {a.name}
              </span>
            ))}
          </div>

          {/* Runs feed */}
          <div className="activity-feed" style={{ flex: 1 }}>
            {runs.length === 0 ? (
              <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-dim)', fontSize: '.78rem' }}>
                No agent runs yet. Start the scheduler to see activity.
              </div>
            ) : runs.map((run: any, i: number) => {
              const agent = agents.find((a) => a.id === run.agent_id);
              return (
                <div key={i} className="activity-item">
                  <div className={`activity-icon ${run.signal}`}>
                    {run.signal === 'buy' ? 'B' : run.signal === 'sell' ? 'S' : 'H'}
                  </div>
                  <div className="activity-body">
                    <div className="activity-agent">{agent?.name ?? run.agent_id?.slice(0, 8)}</div>
                    <div className="activity-detail">
                      {run.signal?.toUpperCase()} {run.symbol} @ ${run.price?.toFixed ? run.price.toFixed(2) : '—'}
                      {run.executed ? ' · executed' : ' · signal only'}
                      {run.error ? ` · ${run.error}` : ''}
                    </div>
                  </div>
                  <span className="activity-time">{timeAgo(run.timestamp)}</span>
                </div>
              );
            })}
          </div>

          {/* Quick actions */}
          <div style={{ padding: '.75rem 1.1rem', borderTop: '1px solid var(--border)', display: 'flex', gap: '.5rem' }}>
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => navigate('/agents')}>Agents</button>
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => navigate('/automation')}>Scheduler</button>
            <button type="button" className="qa-btn qa-btn-ghost" onClick={() => navigate('/trading')}>Trade</button>
          </div>
        </div>

        {/* Column 3 — Portfolio & Paper P&L */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>

          {/* Portfolio Summary */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <Wallet size={14} />
                <span className="panel-title">Portfolio</span>
              </div>
              <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: paperEnabled ? 'var(--amber)' : 'var(--green)' }}>
                {paperEnabled ? 'PAPER' : 'LIVE'}
              </span>
            </div>
            <div className="panel-body">
              <div style={{ textAlign: 'center', marginBottom: '.5rem' }}>
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)', marginBottom: '.15rem' }}>Total Value</div>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text-primary)' }}>
                  ${portfolioTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
                <div className="stat-card">
                  <div className="stat-label">Cash (USDT)</div>
                  <div className="stat-value" style={{ fontSize: '.82rem' }}>
                    ${usdtTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Positions Value</div>
                  <div className="stat-value" style={{ fontSize: '.82rem' }}>
                    ${holdingsValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Exposure</div>
                  <div className="stat-value" style={{
                    fontSize: '.82rem',
                    color: exposurePct > 80 ? 'var(--red)' : exposurePct > 50 ? 'var(--amber)' : 'var(--green)',
                  }}>
                    {exposurePct.toFixed(1)}%
                  </div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Concentration</div>
                  <div className="stat-value" style={{
                    fontSize: '.82rem',
                    color: portfolio?.concentration === 'high' ? 'var(--red)' : portfolio?.concentration === 'medium' ? 'var(--amber)' : 'var(--green)',
                  }}>
                    {(portfolio?.concentration ?? 'low').toUpperCase()}
                  </div>
                </div>
              </div>
              {portfolioBalances.filter(b => b.asset !== 'USDT' && (b.available + b.locked) > 0).length > 0 && (
                <div style={{ marginTop: '.4rem', display: 'flex', flexWrap: 'wrap', gap: '.3rem' }}>
                  {portfolioBalances.filter(b => b.asset !== 'USDT' && (b.available + b.locked) > 0).map(b => {
                    const price = tickers[`${b.asset}USDT`]?.lastPrice ?? 0;
                    const val = (b.available + b.locked) * price;
                    return (
                      <div key={b.asset} style={{
                        fontSize: '.65rem',
                        fontFamily: 'var(--mono)',
                        padding: '.15rem .4rem',
                        borderRadius: 4,
                        background: 'var(--bg-hover)',
                        color: 'var(--text-secondary)',
                      }}>
                        {b.asset} {b.available.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                        <span style={{ color: 'var(--text-dim)', marginLeft: '.25rem' }}>
                          ≈${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">Paper Trading</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <label className="toggle-switch">
                  <input type="checkbox" checked={paperEnabled} onChange={togglePaper} />
                  <span className="toggle-slider" />
                </label>
                <button
                  type="button"
                  title="Clear all dummy trades and reset balances to defaults"
                  onClick={async () => {
                    if (window.confirm('Reset all trading data? This will clear all trades and restore default balances.')) {
                      try {
                        await fetch('/api/paper/reset', { method: 'POST' });
                        await refetchPnl();
                        await refetchStatus();
                      } catch (err) {
                        console.error('Reset failed:', err);
                      }
                    }
                  }}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: 'pointer',
                    padding: '0',
                    display: 'flex',
                    alignItems: 'center',
                    transition: 'color 0.2s',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--accent)')}
                  onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-dim)')}
                >
                  <RotateCcw size={14} />
                </button>
              </div>
            </div>
            <div className="panel-body">
              {paperEnabled && paperPnl ? (
                <>
                  <div className="pnl-display">
                    <div className="pnl-label">Total P&L</div>
                    <div className={`pnl-value ${paperPnl.total_pnl > 0 ? 'positive' : paperPnl.total_pnl < 0 ? 'negative' : 'neutral'}`}>
                      {paperPnl.total_pnl >= 0 ? '+' : ''}${paperPnl.total_pnl?.toFixed(2) ?? '0.00'}
                    </div>
                    <div className="pnl-subtext">{paperPnl.trade_count ?? 0} trades executed</div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem', marginTop: '.5rem' }}>
                    <div className="stat-card">
                      <div className="stat-label">Buy Vol</div>
                      <div className="stat-value" style={{ fontSize: '.88rem' }}>${paperPnl.buy_volume?.toFixed(0) ?? '0'}</div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">Sell Vol</div>
                      <div className="stat-value" style={{ fontSize: '.88rem' }}>${paperPnl.sell_volume?.toFixed(0) ?? '0'}</div>
                    </div>
                  </div>
                </>
              ) : (
                <p style={{ fontSize: '.78rem', color: 'var(--text-dim)', textAlign: 'center', padding: '1rem 0' }}>
                  {paperEnabled ? 'Loading P&L...' : 'Enable to practice without real money'}
                </p>
              )}
            </div>
          </div>

          {/* Agent Performance Summary */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <span className="panel-title">Agent Performance</span>
                <span title="Historical P&L and win rate for each AI agent. Shows which agents are most profitable and accurate over time.">
                  <HelpCircle
                    size={14}
                    style={{ color: 'var(--text-dim)', cursor: 'help' }}
                  />
                </span>
              </div>
              <button
                type="button"
                onClick={() => navigate('/automation')}
                style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}
              >
                DETAILS →
              </button>
            </div>
            <div className="panel-body-compact">
              {metrics.length === 0 ? (
                <p style={{ fontSize: '.75rem', color: 'var(--text-dim)', textAlign: 'center', padding: '.75rem 0' }}>No runs yet</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                  {metrics.slice(0, 4).map((m: any) => {
                    const agent = agents.find((a) => a.id === m.agent_id);
                    return (
                      <div key={m.agent_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '.4rem .5rem', background: 'var(--bg-elevated)', borderRadius: '6px', border: '1px solid var(--border)' }}>
                        <span style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                          {agent?.name ?? m.agent_id?.slice(0, 8)}
                        </span>
                        <div style={{ display: 'flex', gap: '.75rem', fontFamily: 'var(--mono)', fontSize: '.72rem' }}>
                          <span className={m.total_pnl >= 0 ? 'positive' : 'negative'}>
                            {m.total_pnl >= 0 ? '+' : ''}${m.total_pnl?.toFixed(2)}
                          </span>
                          <span style={{ color: 'var(--text-secondary)' }}>
                            {(m.win_rate * 100).toFixed(0)}% win
                          </span>
                        </div>
                      </div>
                    );
                  })}
                  <div style={{ paddingTop: '.35rem', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--mono)', fontSize: '.72rem' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Total P&L</span>
                    <span className={totalPnl >= 0 ? 'positive' : 'negative'}>
                      {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Team Status Summary */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <Users size={14} />
                <span className="panel-title">Fund Team</span>
              </div>
              <button
                type="button"
                onClick={() => navigate('/fundteam')}
                style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}
              >
                DETAILS →
              </button>
            </div>
            <div className="panel-body-compact">
              {teamStatus ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '.35rem .5rem', background: 'var(--bg-elevated)', borderRadius: 6, border: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                      <Shield size={13} />
                      <span style={{ fontSize: '.75rem', color: 'var(--text-secondary)' }}>Risk Level</span>
                    </div>
                    <span style={{
                      fontSize: '.75rem',
                      fontWeight: 700,
                      fontFamily: 'var(--mono)',
                      color: teamStatus.risk_level === 'danger' ? 'var(--red)' : teamStatus.risk_level === 'caution' ? 'var(--amber)' : 'var(--green)',
                    }}>
                      {(teamStatus.risk_level ?? 'unknown').toUpperCase()}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.35rem' }}>
                    <div className="stat-card">
                      <div className="stat-label">Market</div>
                      <div className="stat-value" style={{ fontSize: '.78rem', textTransform: 'capitalize' }}>
                        {teamStatus.market_sentiment ?? '—'}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">CIO View</div>
                      <div className="stat-value" style={{ fontSize: '.78rem', textTransform: 'capitalize' }}>
                        {teamStatus.cio_sentiment ?? '—'}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">Fund P&L</div>
                      <div className={`stat-value ${(teamStatus.fund_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '.78rem' }}>
                        {(teamStatus.fund_pnl ?? 0) >= 0 ? '+' : ''}${(teamStatus.fund_pnl ?? 0).toFixed(2)}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">Top Agent</div>
                      <div className="stat-value" style={{ fontSize: '.72rem' }}>
                        {teamStatus.top_agent ?? '—'}
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: '.68rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textAlign: 'right' }}>
                    {teamStatus.agents_active ?? 0} agents active
                  </div>
                </div>
              ) : (
                <p style={{ fontSize: '.75rem', color: 'var(--text-dim)', textAlign: 'center', padding: '.75rem 0' }}>Loading team status…</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
