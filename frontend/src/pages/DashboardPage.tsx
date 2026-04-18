import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpRight, ArrowDownRight, Minus, HelpCircle, RotateCcw, Wallet, Users, Shield, Zap, ZapOff } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../store/hooks';
import { setSelectedSymbol } from '../store/slices/marketSlice';
import { paperApi } from '../lib/api';
import { formatPrice } from '../utils/formatPrice';
import {
  usePaperStatus,
  usePaperPnl,
  usePaperOrders,
  useClosedTrades,
  usePaperPortfolio,
  useSettings,
  useAgents,
  useAutomationMetrics,
  useAutomationRuns,
  useFundTeamStatus,
  useTraderLeaderboard,
  useTradingPairs,
  useGateAutopilot,
} from '../hooks/useQueries';
import { WsIndicator } from '../components/common/WsIndicator';
import { MiniChart } from '../components/common/MiniChart';
import { PerformanceCharts } from '../components/PerformanceCharts';
import { WhaleIntelligencePanel } from '../components/WhaleIntelligencePanel';
import PositionsTableComponent from '../components/PositionsTable';
import { timeAgo } from '../utils/timeAgo';
import { Skeleton, SkeletonCard, SkeletonChart, SkeletonRows, SkeletonStats } from '../components/common/Skeleton';

const FALLBACK_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'];

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
  const { data: paperOrdersData = [] } = usePaperOrders(undefined, 1000);
  const { data: closedTradesData = [] } = useClosedTrades(undefined, 1000);
  const { data: settingsData } = useSettings();
  const { data: portfolio, isPending: portfolioLoading } = usePaperPortfolio();
  const { data: agentsData = [], isPending: agentsLoading } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();
  const { data: runsData = [] } = useAutomationRuns(undefined, 12);
  const { data: teamStatus } = useFundTeamStatus();
  const { data: traderLeaderboard = [] } = useTraderLeaderboard();
  const { data: tradingPairsData = [] } = useTradingPairs();
  const { data: autopilot } = useGateAutopilot();
  const SYMBOLS = (tradingPairsData.length > 0 ? tradingPairsData : FALLBACK_SYMBOLS) as string[];

  const paperEnabled = paperStatus?.enabled ?? false;
  const maxDailyFeesPct = settingsData?.gates?.max_daily_fees_pct ?? 0.5;
  const feeCoverageGuardEnabled = settingsData?.gates?.fee_coverage_guard_enabled ?? true;
  const feeCoverageMinRatio = settingsData?.gates?.fee_coverage_min_ratio ?? 2.5;
  const feeCoverageMinFeesUsd = settingsData?.gates?.fee_coverage_min_fees_usd ?? 25;
  const feeBudgetUsd = (50000 * maxDailyFeesPct) / 100;
  const dailyFeesForBudget =
    (paperPnl?.daily_fees_with_estimated_exit ?? paperPnl?.daily_fees_paid ?? paperPnl?.total_fees ?? 0);
  const feeBudgetUsedRatio = feeBudgetUsd > 0 ? dailyFeesForBudget / feeBudgetUsd : 0;
  const feeBudgetRemainingUsd = Math.max(feeBudgetUsd - dailyFeesForBudget, 0);
  const feeCoverageRealizedPnl = paperPnl?.realized_pnl ?? 0;
  const feeCoverageFees = paperPnl?.total_fees ?? 0;
  const feeCoverageRatio = feeCoverageFees > 0 ? (feeCoverageRealizedPnl / feeCoverageFees) : null;
  const feeCoverageProgress = feeCoverageRatio != null && feeCoverageMinRatio > 0
    ? Math.min(Math.max(feeCoverageRatio / feeCoverageMinRatio, 0), 1.2)
    : 0;
  const feeCoverageGuardActive = Boolean(
    feeCoverageGuardEnabled
    && feeCoverageFees >= feeCoverageMinFeesUsd
    && feeCoverageRatio != null
    && feeCoverageRatio < feeCoverageMinRatio
  );
  const feeCoverageTrend = useMemo(() => {
    const now = Date.now();
    const hourMs = 60 * 60 * 1000;
    const start = now - (23 * hourMs);

    const orders = Array.isArray(paperOrdersData) ? paperOrdersData : [];
    const closedTrades = Array.isArray(closedTradesData) ? closedTradesData : [];

    const feesByHour = new Array<number>(24).fill(0);
    const pnlByHour = new Array<number>(24).fill(0);

    for (const order of orders) {
      if (!order || String(order.status || '').toLowerCase() !== 'filled') continue;
      const fee = Number(order.fee ?? 0);
      if (!Number.isFinite(fee) || fee <= 0) continue;
      const ts = Date.parse(order.created_at || '');
      if (!Number.isFinite(ts) || ts < start || ts > now) continue;
      const idx = Math.max(0, Math.min(23, Math.floor((ts - start) / hourMs)));
      feesByHour[idx] += fee;
    }

    for (const trade of closedTrades) {
      if (!trade) continue;
      const pnl = Number(trade.net_pnl ?? 0);
      if (!Number.isFinite(pnl)) continue;
      const ts = Date.parse(trade.exit_time || '');
      if (!Number.isFinite(ts) || ts < start || ts > now) continue;
      const idx = Math.max(0, Math.min(23, Math.floor((ts - start) / hourMs)));
      pnlByHour[idx] += pnl;
    }

    const ratioSeries: number[] = [];
    let cumFees = 0;
    let cumPnl = 0;
    for (let i = 0; i < 24; i += 1) {
      cumFees += feesByHour[i];
      cumPnl += pnlByHour[i];
      if (cumFees > 0.001) {
        ratioSeries.push(cumPnl / cumFees);
      } else {
        ratioSeries.push(0);
      }
    }

    const maxAbs = ratioSeries.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    const safeMax = Math.max(maxAbs, feeCoverageMinRatio, 0.5);
    const width = 300;
    const height = 64;
    const points = ratioSeries.map((v, i) => {
      const x = (i / 23) * width;
      const y = height - ((Math.max(-safeMax, Math.min(safeMax, v)) + safeMax) / (2 * safeMax)) * height;
      return `${x},${y}`;
    });

    return {
      ratioSeries,
      polyline: points.join(' '),
      lastRatio: ratioSeries[ratioSeries.length - 1] ?? 0,
      firstRatio: ratioSeries[0] ?? 0,
      safeMax,
      width,
      height,
    };
  }, [paperOrdersData, closedTradesData, feeCoverageMinRatio]);
  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const metrics: any[] = Array.isArray(metricsData) ? metricsData : [];
  const runs: any[] = Array.isArray(runsData) ? runsData : [];

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

  // ── Loading skeleton ──
  if (portfolioLoading && agentsLoading) {
    return (
      <div style={{ padding: '1rem 1.1rem', display: 'flex', flexDirection: 'column', gap: '.75rem', height: '100%', overflow: 'auto' }}>
        {/* Ticker bar skeleton */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '.75rem 1rem', background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 8 }}>
          <Skeleton width={80} height={16} />
          <Skeleton width={100} height={22} />
          <Skeleton width={60} height={14} />
          <div style={{ flex: 1 }} />
          <Skeleton width={60} height={12} />
          <Skeleton width={60} height={12} />
          <Skeleton width={60} height={12} />
        </div>
        {/* Symbol strip skeleton */}
        <div style={{ display: 'flex', gap: '.5rem' }}>
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} width={110} height={40} rounded />
          ))}
        </div>
        {/* 3-column grid skeleton */}
        <div className="dash-grid">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
            <SkeletonCard lines={4} height={180} />
            <SkeletonChart height={140} />
            <SkeletonCard lines={5} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
            <SkeletonCard lines={6} height={260} />
            <SkeletonChart height={180} />
            <SkeletonCard lines={3} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
            <SkeletonCard lines={4} height={200} />
            <SkeletonCard lines={3} />
            <SkeletonCard lines={4} />
          </div>
        </div>
      </div>
    );
  }


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
          ${formatPrice(ticker?.lastPrice)}
        </span>
        <span className={`ticker-change ${upChange ? 'up' : 'down'}`}>
          {upChange ? '+' : ''}{ticker?.priceChangePercent?.toFixed(2) ?? '0.00'}%
        </span>
        <div className="ticker-meta">
          <div className="ticker-meta-item">
            <span className="ticker-meta-label">24h High</span>
            <span className="ticker-meta-value">${formatPrice(ticker?.high)}</span>
          </div>
          <div className="ticker-meta-item">
            <span className="ticker-meta-label">24h Low</span>
            <span className="ticker-meta-value">${formatPrice(ticker?.low)}</span>
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
                {symTicker ? `$${formatPrice(symTicker.lastPrice)}` : '—'}
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

          {/* Trader Leaderboard */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <span className="panel-title">Trader Leaderboard</span>
                <span title="Competing traders backed by different LLMs. Each manages its own agents and capital pool.">
                  <HelpCircle size={14} style={{ color: 'var(--text-dim)', cursor: 'help' }} />
                </span>
              </div>
              <button type="button" onClick={() => navigate('/agents')} style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}>
                VIEW →
              </button>
            </div>
            <div className="panel-body-compact">
              {(traderLeaderboard as any[]).length === 0 ? (
                <p style={{ fontSize: '.75rem', color: 'var(--text-dim)', textAlign: 'center', padding: '.75rem 0' }}>No traders yet</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                  {(traderLeaderboard as any[]).map((t: any, i: number) => (
                    <div key={t.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '.4rem .5rem', background: 'var(--bg-elevated)', borderRadius: '6px', border: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                        <span style={{ fontSize: '.85rem' }}>{t.config?.avatar || ['🥇','🥈','🥉'][i] || '🏅'}</span>
                        <div>
                          <div style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-primary)' }}>{t.name}</div>
                          <div style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                            {t.agent_count} agents · {t.allocation_pct?.toFixed(0)}%
                          </div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', fontFamily: 'var(--mono)', fontSize: '.72rem' }}>
                        <span className={t.total_pnl >= 0 ? 'positive' : 'negative'}>
                          {t.total_pnl >= 0 ? '+' : ''}${t.total_pnl?.toFixed(2)}
                        </span>
                        <span style={{ fontSize: '.6rem', color: 'var(--text-secondary)' }}>
                          {(t.win_rate * 100).toFixed(0)}% win
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Whale Intelligence — mini strip */}
          <WhaleIntelligencePanel compact mini />

        </div>

        {/* Column 2 — Agent Activity + Performance/Team */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
        <div className="panel" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <span className="panel-title">Strategy Activity</span>
              <span title="Real-time feed of AI strategy signals and executions. Shows which strategies are active, what signals they've generated, and whether trades were executed.">
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
              <span style={{ fontSize: '.75rem', color: 'var(--text-dim)' }}>No strategies configured</span>
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
                No strategy runs yet. Start the scheduler to see activity.
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
                      {run.signal?.toUpperCase()} {run.symbol} @ ${run.price != null ? formatPrice(run.price) : '—'}
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
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => navigate('/agents')}>Strategies</button>
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => navigate('/automation')}>Scheduler</button>
            <button type="button" className="qa-btn qa-btn-ghost" onClick={() => navigate('/trading')}>Trade</button>
          </div>
        </div>

          <PerformanceCharts />

          {/* Agent Performance */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <span className="panel-title">Strategy Performance</span>
                <span title="Historical P&L and win rate for each AI strategy.">
                  <HelpCircle size={14} style={{ color: 'var(--text-dim)', cursor: 'help' }} />
                </span>
              </div>
              <button type="button" onClick={() => navigate('/automation')} style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}>
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
                    <div className="pnl-label">Net Total P&L (After Fees)</div>
                    <div className={`pnl-value ${paperPnl.total_pnl > 0 ? 'positive' : paperPnl.total_pnl < 0 ? 'negative' : 'neutral'}`}>
                      {paperPnl.total_pnl >= 0 ? '+' : ''}${paperPnl.total_pnl?.toFixed(2) ?? '0.00'}
                    </div>
                    <div className="pnl-subtext">
                      Gross Realized: {(paperPnl.realized_pnl ?? 0) >= 0 ? '+' : ''}${(paperPnl.realized_pnl ?? 0).toFixed(2)} · {paperPnl.trade_count ?? 0} orders filled
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem', marginTop: '.5rem' }}>
                    <div className="stat-card">
                      <div className="stat-label">Gross Realized</div>
                      <div className={`stat-value ${(paperPnl.realized_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '.88rem' }}>
                        {(paperPnl.realized_pnl ?? 0) >= 0 ? '+' : ''}${(paperPnl.realized_pnl ?? 0).toFixed(2)}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">Unrealized (Net Exit Fees)</div>
                      <div className={`stat-value ${(paperPnl.unrealized_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '.88rem' }}>
                        {(paperPnl.unrealized_pnl ?? 0) >= 0 ? '+' : ''}${(paperPnl.unrealized_pnl ?? 0).toFixed(2)}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label">Total Fees</div>
                      <div className="stat-value" style={{ fontSize: '.88rem' }}>${paperPnl.total_fees?.toFixed(2) ?? '0.00'}</div>
                    </div>
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

          {/* Daily Fee Budget Status */}
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">Daily Fee Budget</span>
            </div>
            <div className="panel-body">
              {paperEnabled && paperPnl ? (
                <>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem' }}>
                      <div className="stat-card">
                        <div className="stat-label" style={{ fontSize: '.7rem' }}>Fees Today</div>
                        <div className="stat-value" style={{ fontSize: '.95rem', fontFamily: 'var(--mono)' }}>
                          ${dailyFeesForBudget.toFixed(2)}
                        </div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-label" style={{ fontSize: '.7rem' }}>Budget Left</div>
                        <div className="stat-value" style={{ fontSize: '.95rem', fontFamily: 'var(--mono)' }}>
                          ${feeBudgetRemainingUsd.toFixed(2)}
                        </div>
                      </div>
                    </div>
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.25rem' }}>
                        <span style={{ fontSize: '.75rem', color: 'var(--text-secondary)' }}>Daily Fees</span>
                        <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 600 }}>
                          ${dailyFeesForBudget.toFixed(2)} / ${feeBudgetUsd.toFixed(2)} (50k @ {maxDailyFeesPct.toFixed(2)}%)
                        </span>
                      </div>
                      <div style={{ width: '100%', height: '8px', background: 'var(--bg-elevated)', borderRadius: 4, overflow: 'hidden' }}>
                        <div style={{
                          width: `${Math.min(feeBudgetUsedRatio * 100, 100)}%`,
                          height: '100%',
                          background: feeBudgetUsedRatio > 1 ? 'var(--red)' : feeBudgetUsedRatio > 0.75 ? 'var(--amber)' : 'var(--green)',
                          transition: 'width 0.3s ease',
                        }} />
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem', marginTop: '.25rem' }}>
                      <div className="stat-card">
                        <div className="stat-label" style={{ fontSize: '.7rem' }}>Budget Used</div>
                        <div className="stat-value" style={{ fontSize: '.8rem', fontFamily: 'var(--mono)' }}>
                          {(feeBudgetUsedRatio * 100).toFixed(1)}%
                        </div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-label" style={{ fontSize: '.7rem' }}>Entries Status</div>
                        <div className="stat-value" style={{
                          fontSize: '.8rem',
                          color: feeBudgetUsedRatio > 1 ? 'var(--red)' : 'var(--green)',
                          fontWeight: 600,
                        }}>
                          {feeBudgetUsedRatio > 1 ? 'BLOCKED' : 'ACTIVE'}
                        </div>
                      </div>
                    </div>
                    <p style={{ fontSize: '.7rem', color: 'var(--text-dim)', marginTop: '.25rem', lineHeight: 1.4 }}>
                      {feeBudgetUsedRatio > 1
                        ? '🚫 Daily fee budget exceeded. New entries blocked until midnight UTC.'
                        : `✓ $${dailyFeesForBudget.toFixed(2)} paid today. $${feeBudgetRemainingUsd.toFixed(2)} remaining before the UTC reset.`}
                    </p>
                  </div>
                </>
              ) : (
                <p style={{ fontSize: '.78rem', color: 'var(--text-dim)', textAlign: 'center', padding: '1rem 0' }}>
                  Enable paper trading to see fee budget status
                </p>
              )}
            </div>
          </div>

          {/* Fee Coverage Efficiency */}
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">Fee Coverage</span>
            </div>
            <div className="panel-body">
              {paperEnabled && paperPnl ? (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem' }}>
                    <div className="stat-card">
                      <div className="stat-label" style={{ fontSize: '.7rem' }}>Realized / Fees</div>
                      <div
                        className="stat-value"
                        style={{
                          fontSize: '1rem',
                          fontFamily: 'var(--mono)',
                          color: feeCoverageRatio == null
                            ? 'var(--text-secondary)'
                            : feeCoverageRatio >= feeCoverageMinRatio
                              ? 'var(--green)'
                              : 'var(--red)',
                        }}
                      >
                        {feeCoverageRatio == null ? 'N/A' : `${feeCoverageRatio.toFixed(2)}x`}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label" style={{ fontSize: '.7rem' }}>Target</div>
                      <div className="stat-value" style={{ fontSize: '1rem', fontFamily: 'var(--mono)' }}>
                        {feeCoverageMinRatio.toFixed(2)}x
                      </div>
                    </div>
                  </div>
                  <div style={{ marginTop: '.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.25rem' }}>
                      <span style={{ fontSize: '.75rem', color: 'var(--text-secondary)' }}>Coverage Progress</span>
                      <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 600 }}>
                        ${feeCoverageRealizedPnl.toFixed(2)} / ${feeCoverageFees.toFixed(2)}
                      </span>
                    </div>
                    <div style={{ width: '100%', height: '8px', background: 'var(--bg-elevated)', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{
                        width: `${Math.min(feeCoverageProgress * 100, 100)}%`,
                        height: '100%',
                        background: feeCoverageRatio == null
                          ? 'var(--text-dim)'
                          : feeCoverageRatio >= feeCoverageMinRatio
                            ? 'var(--green)'
                            : 'var(--red)',
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                  </div>
                  <div style={{ marginTop: '.6rem', padding: '.45rem .5rem', border: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '.28rem' }}>
                      <span style={{ fontSize: '.68rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.08em' }}>24h Fee Coverage Trend</span>
                      <span
                        style={{
                          fontSize: '.68rem',
                          fontFamily: 'var(--mono)',
                          color: (feeCoverageTrend.lastRatio - feeCoverageTrend.firstRatio) >= 0 ? 'var(--green)' : 'var(--red)',
                        }}
                      >
                        {(feeCoverageTrend.lastRatio - feeCoverageTrend.firstRatio) >= 0 ? '+' : ''}
                        {(feeCoverageTrend.lastRatio - feeCoverageTrend.firstRatio).toFixed(2)}x
                      </span>
                    </div>
                    <svg
                      viewBox={`0 0 ${feeCoverageTrend.width} ${feeCoverageTrend.height}`}
                      style={{ width: '100%', height: '56px', display: 'block' }}
                      preserveAspectRatio="none"
                    >
                      <line
                        x1="0"
                        y1={feeCoverageTrend.height / 2}
                        x2={feeCoverageTrend.width}
                        y2={feeCoverageTrend.height / 2}
                        stroke="var(--border)"
                        strokeWidth="1"
                        opacity="0.8"
                      />
                      <polyline
                        fill="none"
                        stroke={feeCoverageTrend.lastRatio >= feeCoverageMinRatio ? 'var(--green)' : 'var(--accent)'}
                        strokeWidth="2"
                        points={feeCoverageTrend.polyline}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '.2rem' }}>
                      <span style={{ fontSize: '.64rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>-24h</span>
                      <span style={{ fontSize: '.64rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>Now</span>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.5rem', marginTop: '.5rem' }}>
                    <div className="stat-card">
                      <div className="stat-label" style={{ fontSize: '.7rem' }}>Guard</div>
                      <div
                        className="stat-value"
                        style={{
                          fontSize: '.82rem',
                          color: !feeCoverageGuardEnabled
                            ? 'var(--text-dim)'
                            : feeCoverageGuardActive
                              ? 'var(--red)'
                              : 'var(--green)',
                          fontWeight: 700,
                        }}
                      >
                        {!feeCoverageGuardEnabled ? 'OFF' : feeCoverageGuardActive ? 'ACTIVE' : 'STANDBY'}
                      </div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-label" style={{ fontSize: '.7rem' }}>Activation Fees</div>
                      <div className="stat-value" style={{ fontSize: '.82rem', fontFamily: 'var(--mono)' }}>
                        ${feeCoverageMinFeesUsd.toFixed(0)}
                      </div>
                    </div>
                  </div>
                  <p style={{ fontSize: '.7rem', color: 'var(--text-dim)', marginTop: '.35rem', lineHeight: 1.4 }}>
                    {!feeCoverageGuardEnabled
                      ? 'Fee coverage guard is disabled in Trade Gates.'
                      : feeCoverageFees < feeCoverageMinFeesUsd
                        ? `Tracking phase: guard activates after $${feeCoverageMinFeesUsd.toFixed(0)} fees.`
                        : feeCoverageGuardActive
                          ? `Guard active: ratio ${feeCoverageRatio?.toFixed(2)}x below target ${feeCoverageMinRatio.toFixed(2)}x. New entries are being tightened.`
                          : `Healthy edge: ratio ${feeCoverageRatio?.toFixed(2)}x is meeting target ${feeCoverageMinRatio.toFixed(2)}x.`}
                  </p>
                </>
              ) : (
                <p style={{ fontSize: '.78rem', color: 'var(--text-dim)', textAlign: 'center', padding: '1rem 0' }}>
                  Enable paper trading to see fee coverage status
                </p>
              )}
            </div>
          </div>

          {/* Fund Team */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                <Users size={14} />
                <span className="panel-title">Fund Team</span>
              </div>
              <button type="button" onClick={() => navigate('/fundteam')} style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}>
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
                      fontSize: '.75rem', fontWeight: 700, fontFamily: 'var(--mono)',
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
                      <div className="stat-label">Top Strategy</div>
                      <div className="stat-value" style={{ fontSize: '.72rem' }}>
                        {teamStatus.top_agent ?? '—'}
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: '.68rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textAlign: 'right' }}>
                    {teamStatus.agents_active ?? 0} strategies active
                  </div>
                </div>
              ) : (
                <p style={{ fontSize: '.75rem', color: 'var(--text-dim)', textAlign: 'center', padding: '.75rem 0' }}>Loading team status…</p>
              )}
            </div>
          </div>

          {/* Gate Autopilot status */}
          <div className="panel">
            <div className="panel-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                {autopilot?.enabled
                  ? <Zap size={13} style={{ color: 'var(--accent)' }} />
                  : <ZapOff size={13} style={{ color: 'var(--text-dim)' }} />}
                <span className="panel-title">Gate Autopilot</span>
              </div>
              <button
                type="button"
                onClick={() => navigate('/settings')}
                style={{ fontSize: '.65rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'var(--mono)' }}
              >
                CONFIG →
              </button>
            </div>
            <div className="panel-body">
              {!autopilot ? (
                <p style={{ fontSize: '.75rem', color: 'var(--text-dim)', textAlign: 'center', padding: '.5rem 0' }}>Loading…</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
                  {/* Status row */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{
                      fontSize: '.7rem', fontWeight: 700, padding: '.2rem .6rem', borderRadius: '20px',
                      background: autopilot.enabled ? 'var(--accent-dim)' : 'var(--bg-hover)',
                      color: autopilot.enabled ? 'var(--accent)' : 'var(--text-dim)',
                      border: `1px solid ${autopilot.enabled ? 'rgba(99,179,237,.25)' : 'var(--border)'}`,
                    }}>
                      {autopilot.enabled ? 'ACTIVE' : 'OFF'}
                    </span>
                    {autopilot.enabled && (
                      <span style={{
                        fontSize: '.7rem', fontWeight: 700, letterSpacing: '.05em',
                        padding: '.2rem .6rem', borderRadius: '20px',
                        textTransform: 'uppercase',
                        background: `var(--${autopilot.color}-dim, var(--bg-elevated))`,
                        color: `var(--${autopilot.color}, var(--text-secondary))`,
                        border: `1px solid var(--${autopilot.color}, var(--border))`,
                      }}>
                        {autopilot.regime}
                      </span>
                    )}
                  </div>
                  {/* Reason */}
                  {autopilot.enabled && autopilot.reason && (
                    <p style={{ fontSize: '.7rem', color: 'var(--text-secondary)', lineHeight: 1.45, margin: 0 }}>
                      {autopilot.reason.length > 110
                        ? autopilot.reason.slice(0, 110) + '…'
                        : autopilot.reason}
                    </p>
                  )}
                  {!autopilot.enabled && (
                    <p style={{ fontSize: '.7rem', color: 'var(--text-dim)', lineHeight: 1.45, margin: 0 }}>
                      Auto-adjusts gate thresholds from win rate, P&L and session timing.
                    </p>
                  )}
                  {/* Last run */}
                  {autopilot.last_run && (
                    <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textAlign: 'right' }}>
                      evaluated {new Date(autopilot.last_run).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

        </div>

      </div>{/* end dash-grid */}

      {/* Open positions with leverage diagnostics */}
      <PositionsTableComponent />
    </div>
  );
}
