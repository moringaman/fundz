import { useState, useRef, useEffect } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, Time } from 'lightweight-charts';
import { Activity, Bot, Wallet, Settings, Menu, X, TrendingUp, History, Zap, Play, ArrowUpRight, ArrowDownRight, Minus, HelpCircle, RotateCcw, Users, AlertTriangle, BarChart3, Shield, ChevronUp, ChevronDown, Key, Brain, Save, Eye, EyeOff, Check, RefreshCw, Info, MessageCircle, FileText, Calendar } from 'lucide-react';
import { useAppStore } from './lib/store';
import { paperApi, automationApi, settingsApi } from './lib/api';
import { Chart } from './components/Chart';
import { useWebSocket } from './hooks/useWebSocket';
import { useMarketStream } from './hooks/useMarketStream';
import {
  useWsQueryInvalidation,
  usePaperStatus,
  usePaperPnl,
  useBalance,
  useAgents,
  useAutomationMetrics,
  useAutomationStatus,
  useAutomationRuns,
  useTradeHistory,
  usePaperOrders,
  usePnl,
  useFundMarketAnalysis,
  useFundRiskAssessment,
  useFundCIOReport,
  useFundPerformanceAttribution,
  useFundTeamStatus,
  useFundTeamRoster,
  useFundAllocationDecision,
  useFundTechnicalAnalysis,
  useSettings,
  useFundConversations,
  useDailyReport,
  useDailyReports,
} from './hooks/useQueries';
import { useTeamChatStream } from './hooks/useTeamChatStream';
import './index.css';

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'];

function WsIndicator() {
  const wsStatus = useAppStore((s) => s.wsStatus);
  const dotClass = `live-dot ${wsStatus !== 'connected' ? wsStatus : ''}`;
  const label = wsStatus === 'connected' ? 'LIVE' : wsStatus === 'connecting' ? 'CONN' : 'OFF';
  return (
    <span className="live-badge">
      <span className={dotClass} />
      {label}
    </span>
  );
}

function SidebarTicker() {
  const { ticker } = useAppStore();
  if (!ticker) return null;
  const up = ticker.priceChangePercent >= 0;
  return (
    <div className="sidebar-footer">
      <div className="sidebar-ticker">
        <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
          {ticker.symbol}
        </span>
        <WsIndicator />
      </div>
      <div className="sidebar-ticker">
        <span className="sidebar-ticker-price">${ticker.lastPrice?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
        <span className={`sidebar-ticker-change ${up ? 'up' : 'down'}`}>
          {up ? '+' : ''}{ticker.priceChangePercent?.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

function NavBadge({ children, variant = 'default' }: { children: React.ReactNode; variant?: 'default' | 'green' | 'red' | 'amber' }) {
  const colors: Record<string, React.CSSProperties> = {
    default: { background: 'var(--bg-hover)', color: 'var(--text-secondary)', border: '1px solid var(--border)' },
    green:   { background: 'var(--green-dim)', color: 'var(--green)', border: '1px solid rgba(0,230,118,.2)' },
    red:     { background: 'var(--red-dim)', color: 'var(--red)', border: '1px solid rgba(255,61,96,.2)' },
    amber:   { background: 'var(--amber-dim)', color: 'var(--amber)', border: '1px solid rgba(255,179,0,.2)' },
  };
  return (
    <span style={{
      marginLeft: 'auto',
      padding: '1px 6px',
      borderRadius: '4px',
      fontSize: '.65rem',
      fontFamily: 'var(--mono)',
      fontWeight: 600,
      lineHeight: '1.6',
      flexShrink: 0,
      ...colors[variant],
    }}>
      {children}
    </span>
  );
}

// ─── Team Chat Toast Notifications ──────────────────────────────────────────

function TeamChatToasts() {
  const { teamChatToasts, dismissTeamChatToast } = useAppStore();

  useEffect(() => {
    if (teamChatToasts.length === 0) return;
    const timers = teamChatToasts.map((msg) =>
      setTimeout(() => dismissTeamChatToast(msg.id), 8000)
    );
    return () => timers.forEach(clearTimeout);
  }, [teamChatToasts, dismissTeamChatToast]);

  if (teamChatToasts.length === 0) return null;

  const typeColors: Record<string, string> = {
    warning: 'var(--red)',
    analysis: 'var(--accent)',
    decision: 'var(--green)',
    recommendation: 'var(--amber)',
    greeting: 'var(--text-secondary)',
  };
  const typeBgColors: Record<string, string> = {
    warning: 'var(--red-dim)',
    analysis: 'var(--accent-dim)',
    decision: 'var(--green-dim)',
    recommendation: 'var(--amber-dim)',
    greeting: 'var(--bg-elevated)',
  };

  return (
    <div className="team-chat-toasts">
      {teamChatToasts.map((msg) => (
        <div
          key={msg.id}
          className="team-chat-toast"
          style={{
            borderLeft: `3px solid ${typeColors[msg.message_type] || 'var(--accent)'}`,
            background: typeBgColors[msg.message_type] || 'var(--bg-panel)',
          }}
          onClick={() => dismissTeamChatToast(msg.id)}
        >
          <div className="team-chat-toast-header">
            <span className="team-chat-toast-avatar">{msg.avatar}</span>
            <span className="team-chat-toast-name">{msg.agent_name}</span>
            <span className="team-chat-toast-type">{msg.message_type}</span>
          </div>
          <div className="team-chat-toast-content">
            {msg.content.replace(/\*\*/g, '').slice(0, 120)}
            {msg.content.length > 120 ? '…' : ''}
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Team Chat Panel (for Fund Team page) ───────────────────────────────────

function TeamChatPanel() {
  const { data: conversations = [], isLoading } = useFundConversations(100);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const msgs: any[] = Array.isArray(conversations) ? conversations : [];

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [msgs.length]);

  const typeColors: Record<string, string> = {
    warning: 'var(--red)',
    analysis: 'var(--accent)',
    decision: 'var(--green)',
    recommendation: 'var(--amber)',
    greeting: 'var(--text-secondary)',
  };

  const formatContent = (content: string) => {
    // Convert **bold** to styled spans
    return content.split(/\*\*(.*?)\*\*/g).map((part, i) =>
      i % 2 === 1
        ? <strong key={i} style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{part}</strong>
        : <span key={i}>{part}</span>
    );
  };

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  };

  return (
    <div className="team-chat-panel">
      <div className="team-chat-header">
        <MessageCircle size={15} style={{ color: 'var(--accent)' }} />
        <span>Team Discussion</span>
        <span className="team-chat-badge">{msgs.length}</span>
      </div>
      <div className="team-chat-messages">
        {isLoading && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
            Loading conversations…
          </div>
        )}
        {!isLoading && msgs.length === 0 && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
            <MessageCircle size={24} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
            No conversations yet. Team discussions appear when the scheduler runs.
          </div>
        )}
        {msgs.map((msg: any) => (
          <div key={msg.id} className="team-chat-msg">
            <div className="team-chat-msg-avatar">{msg.avatar}</div>
            <div className="team-chat-msg-body">
              <div className="team-chat-msg-meta">
                <span className="team-chat-msg-name">{msg.agent_name}</span>
                <span
                  className="team-chat-msg-type-badge"
                  style={{ color: typeColors[msg.message_type] || 'var(--text-secondary)' }}
                >
                  {msg.message_type}
                </span>
                <span className="team-chat-msg-time">{formatTime(msg.timestamp)}</span>
              </div>
              <div className="team-chat-msg-content">
                {formatContent(msg.content)}
              </div>
              {msg.mentions && msg.mentions.length > 0 && (
                <div className="team-chat-msg-mentions">
                  {msg.mentions.map((m: string) => (
                    <span key={m} className="team-chat-mention">@{m.replace('_', ' ')}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>
    </div>
  );
}

// ─── Daily Report Panel (for Fund Team page) ────────────────────────────────

function DailyReportPanel() {
  const { data: todayReport, isLoading: reportLoading } = useDailyReport();
  const { data: pastReports = [] } = useDailyReports(7);
  const [generating, setGenerating] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const { data: selectedReport } = useDailyReport(selectedDate || undefined);

  const report: any = selectedDate ? selectedReport : todayReport;
  const reports: any[] = Array.isArray(pastReports) ? pastReports : [];
  const hasReport = report && !report.message;

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const { fundApi: fApi } = await import('./lib/api');
      await fApi.generateDailyReport(undefined, true);
      window.location.reload();
    } catch { /* ignore */ }
    finally { setGenerating(false); }
  };

  const pnlColor = (val: number) => val > 0 ? 'var(--green)' : val < 0 ? 'var(--red)' : 'var(--text-secondary)';

  return (
    <div className="daily-report-panel">
      <div className="daily-report-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
          <FileText size={15} style={{ color: 'var(--accent)' }} />
          <span>Daily Report</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
          {/* Past report date pills */}
          {reports.slice(0, 5).map((r: any) => (
            <button
              key={r.report_date}
              type="button"
              onClick={() => setSelectedDate(r.report_date === selectedDate ? null : r.report_date)}
              className="daily-report-date-pill"
              style={{
                background: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                color: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent)' : 'var(--text-secondary)',
                borderColor: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent)' : 'var(--border-mid)',
              }}
            >
              {r.report_date?.slice(5)}
            </button>
          ))}
          <button
            type="button"
            className="settings-btn"
            onClick={handleGenerate}
            disabled={generating}
            style={{ fontSize: '.68rem', padding: '.3rem .6rem' }}
          >
            <RefreshCw size={11} /> {generating ? 'Generating…' : 'Generate'}
          </button>
        </div>
      </div>

      {reportLoading && (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
          Loading report…
        </div>
      )}

      {!reportLoading && !hasReport && (
        <div style={{ textAlign: 'center', padding: '2.5rem 1rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
          <Calendar size={28} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
          No report available yet. Reports are generated automatically every hour when the scheduler is running.
        </div>
      )}

      {hasReport && (
        <div className="daily-report-body">
          {/* Key Metrics Row */}
          <div className="daily-report-metrics-grid">
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Total P&L</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.total_pnl || 0) }}>
                ${(report.total_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Realized</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.realized_pnl || 0) }}>
                ${(report.realized_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Unrealized</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.unrealized_pnl || 0) }}>
                ${(report.unrealized_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Daily Return</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.daily_return_pct || 0) }}>
                {(report.daily_return_pct || 0).toFixed(3)}%
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Trades Opened</span>
              <span className="daily-report-metric-value">{report.trades_opened || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Trades Closed</span>
              <span className="daily-report-metric-value">{report.trades_closed || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Open Positions</span>
              <span className="daily-report-metric-value">{report.open_positions_count || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Portfolio Value</span>
              <span className="daily-report-metric-value">${(report.portfolio_value || 0).toFixed(0)}</span>
            </div>
          </div>

          {/* Market Conditions + Risk */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.75rem', marginTop: '.75rem' }}>
            {report.market_conditions && Object.keys(report.market_conditions).length > 0 && (
              <div className="daily-report-section">
                <div className="daily-report-section-title">Market Conditions</div>
                <div className="daily-report-kv">
                  {report.market_conditions.regime && <div><span>Regime</span><span>{report.market_conditions.regime}</span></div>}
                  {report.market_conditions.sentiment && <div><span>Sentiment</span><span>{report.market_conditions.sentiment}</span></div>}
                  {report.market_conditions.volatility && <div><span>Volatility</span><span>{report.market_conditions.volatility}</span></div>}
                  {report.market_conditions.analyst_recommendation && <div><span>Recommendation</span><span>{report.market_conditions.analyst_recommendation}</span></div>}
                </div>
              </div>
            )}

            {report.risk_summary && Object.keys(report.risk_summary).length > 0 && (
              <div className="daily-report-section">
                <div className="daily-report-section-title">Risk Summary</div>
                <div className="daily-report-kv">
                  {report.risk_summary.risk_level && (
                    <div>
                      <span>Level</span>
                      <span style={{
                        color: report.risk_summary.risk_level === 'safe' ? 'var(--green)' :
                               report.risk_summary.risk_level === 'danger' ? 'var(--red)' : 'var(--amber)',
                        fontWeight: 700,
                      }}>{report.risk_summary.risk_level.toUpperCase()}</span>
                    </div>
                  )}
                  {report.risk_summary.exposure_pct != null && <div><span>Exposure</span><span>{report.risk_summary.exposure_pct?.toFixed(1)}%</span></div>}
                  {report.risk_summary.concentration_risk && <div><span>Concentration</span><span>{report.risk_summary.concentration_risk}</span></div>}
                </div>
              </div>
            )}
          </div>

          {/* Agent Leaderboard */}
          {report.agent_leaderboard && report.agent_leaderboard.length > 0 && (
            <div className="daily-report-section" style={{ marginTop: '.75rem' }}>
              <div className="daily-report-section-title">Agent Leaderboard</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.3rem' }}>
                {report.agent_leaderboard.map((a: any, i: number) => (
                  <div key={a.agent_id || i} style={{
                    display: 'flex', alignItems: 'center', gap: '.6rem',
                    padding: '.4rem .6rem', borderRadius: 6,
                    background: i === 0 ? 'var(--green-dim)' : 'transparent',
                  }}>
                    <span style={{
                      fontSize: '.7rem', fontWeight: 700, width: 20, textAlign: 'center',
                      color: i === 0 ? 'var(--green)' : 'var(--text-dim)',
                    }}>#{a.rank || i + 1}</span>
                    <span style={{ flex: 1, fontSize: '.75rem', color: 'var(--text-primary)' }}>
                      {a.name || a.agent_id}
                    </span>
                    <span style={{
                      fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700,
                      color: pnlColor(a.total_pnl || 0),
                    }}>
                      ${(a.total_pnl || 0).toFixed(2)}
                    </span>
                    <span style={{ fontSize: '.65rem', color: 'var(--text-dim)' }}>
                      WR {((a.win_rate || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* CIO Summary */}
          {report.cio_summary && (
            <div className="daily-report-section" style={{ marginTop: '.75rem' }}>
              <div className="daily-report-section-title">
                CIO Commentary
                {report.cio_sentiment && (
                  <span style={{
                    marginLeft: '.5rem', fontSize: '.6rem', padding: '.1rem .4rem', borderRadius: 4,
                    background: 'var(--accent-dim)', color: 'var(--accent)',
                  }}>{report.cio_sentiment.replace('_', ' ')}</span>
                )}
              </div>
              <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0 }}>
                {report.cio_summary}
              </p>
            </div>
          )}

          {/* Team Discussion Summary */}
          {report.team_discussion_summary && (
            <div className="daily-report-section" style={{ marginTop: '.75rem' }}>
              <div className="daily-report-section-title">
                Team Discussions
                <span style={{
                  marginLeft: '.5rem', fontSize: '.6rem', padding: '.1rem .4rem', borderRadius: 4,
                  background: 'var(--bg-elevated)', color: 'var(--text-dim)',
                }}>{report.team_message_count || 0} messages</span>
              </div>
              <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0 }}>
                {report.team_discussion_summary}
              </p>
            </div>
          )}

          {/* Generated timestamp */}
          {report.generated_at && (
            <div style={{ marginTop: '.75rem', fontSize: '.62rem', color: 'var(--text-dim)', textAlign: 'right', fontFamily: 'var(--mono)' }}>
              Generated: {new Date(report.generated_at).toLocaleString()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function App() {
  const [activePage, setActivePage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [timeframe, setTimeframe] = useState('1h');
  const { selectedSymbol, signal } = useAppStore();

  useWebSocket();
  useMarketStream(timeframe);
  useWsQueryInvalidation();
  useTeamChatStream();

  // Data for sidebar badges
  const { data: automationStatus } = useAutomationStatus();
  const { data: agentsData = [] } = useAgents();
  const { data: tradeHistoryData = [] } = useTradeHistory(undefined, 100);

  const enabledAgentCount = (Array.isArray(agentsData) ? agentsData : []).filter((a: any) => a.is_enabled).length;
  const schedulerRunning = (automationStatus as any)?.scheduler_running ?? false;

  // Fund Team status
  const { data: fundTeamStatusData } = useFundTeamStatus();
  const fundTeamRisk = (fundTeamStatusData as any)?.risk_level ?? 'safe';

  const oneDayAgo = Date.now() - 86_400_000;
  const trades24h = (Array.isArray(tradeHistoryData) ? tradeHistoryData : []).filter(
    (t: any) => new Date(t.created_at ?? t.timestamp ?? 0).getTime() > oneDayAgo
  ).length;

  const sigAction = signal?.action;

  return (
    <div className="app-container">
      <TeamChatToasts />
      <button
        className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''}`}
        onClick={() => setSidebarOpen(false)}
        onKeyDown={(e) => e.key === 'Escape' && setSidebarOpen(false)}
        aria-label="Close sidebar"
        type="button"
      />

      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <span className="sidebar-logo">
            PX<span>·</span>AI
          </span>
          <button type="button" onClick={() => setSidebarOpen(false)} className="header-btn">
            <X size={16} />
          </button>
        </div>

        <nav className="sidebar-nav">
          {/* Overview */}
          <button type="button" onClick={() => { setActivePage('dashboard'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'dashboard' ? 'active' : ''}`}>
            <Activity size={16} />
            <span>Overview</span>
            {sigAction && sigAction !== 'hold' && (
              <NavBadge variant={sigAction === 'buy' ? 'green' : 'red'}>{sigAction.toUpperCase()}</NavBadge>
            )}
          </button>

          {/* Trading */}
          <button type="button" onClick={() => { setActivePage('trading'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'trading' ? 'active' : ''}`}>
            <TrendingUp size={16} />
            <span>Trading</span>
            <NavBadge>{selectedSymbol.replace('USDT', '')}</NavBadge>
          </button>

          {/* Agents */}
          <button type="button" onClick={() => { setActivePage('agents'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'agents' ? 'active' : ''}`}>
            <Bot size={16} />
            <span>Agents</span>
            {enabledAgentCount > 0
              ? <NavBadge variant="green">{enabledAgentCount} ON</NavBadge>
              : <NavBadge>0 ON</NavBadge>
            }
          </button>

          {/* Automation */}
          <button type="button" onClick={() => { setActivePage('automation'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'automation' ? 'active' : ''}`}>
            <Zap size={16} />
            <span>Automation</span>
            <NavBadge variant={schedulerRunning ? 'green' : 'default'}>{schedulerRunning ? 'ON' : 'OFF'}</NavBadge>
          </button>

          {/* History */}
          <button type="button" onClick={() => { setActivePage('history'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'history' ? 'active' : ''}`}>
            <History size={16} />
            <span>History</span>
            {trades24h > 0
              ? <NavBadge variant="amber">{trades24h} 24h</NavBadge>
              : <NavBadge>0 today</NavBadge>
            }
          </button>

          {/* Fund Team */}
          <button type="button" onClick={() => { setActivePage('fundteam'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'fundteam' ? 'active' : ''}`}>
            <Users size={16} />
            <span>Fund Team</span>
            {fundTeamRisk === 'danger' ? (
              <NavBadge variant="red">RISK</NavBadge>
            ) : fundTeamRisk === 'caution' ? (
              <NavBadge variant="amber">CAUTION</NavBadge>
            ) : (
              <NavBadge variant="green">OK</NavBadge>
            )}
          </button>

          {/* Wallet */}
          <button type="button" onClick={() => { setActivePage('wallet'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'wallet' ? 'active' : ''}`}>
            <Wallet size={16} />
            <span>Wallet</span>
          </button>

          {/* Settings */}
          <button type="button" onClick={() => { setActivePage('settings'); setSidebarOpen(false); }} className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}>
            <Settings size={16} />
            <span>Settings</span>
          </button>
        </nav>

        <SidebarTicker />
      </aside>

      <main className="main-content">
        <div className="mobile-header">
          <button type="button" onClick={() => setSidebarOpen(true)} className="mobile-menu-btn">
            <Menu size={22} />
          </button>
          <span className="sidebar-logo">PX·AI</span>
          <WsIndicator />
        </div>

        {activePage === 'dashboard'  && <Dashboard onNavigate={setActivePage} />}
        {activePage === 'trading'    && <Trading timeframe={timeframe} onTimeframeChange={setTimeframe} />}
        {activePage === 'agents'     && <Agents />}
        {activePage === 'automation' && <AutomationPage />}
        {activePage === 'fundteam'   && <FundTeamPage />}
        {activePage === 'history'    && <HistoryPage />}
        {activePage === 'wallet'     && <WalletPage />}
        {activePage === 'settings'   && <SettingsPage />}
      </main>
    </div>
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

function Dashboard({ onNavigate }: { onNavigate: (page: string) => void }) {
  const { ticker, tickers, signal, indicators, selectedSymbol, wsStatus, klines, setSelectedSymbol } = useAppStore();

  const { data: paperStatus, refetch: refetchStatus } = usePaperStatus();
  const { data: paperPnl, refetch: refetchPnl } = usePaperPnl();
  const { data: balancesRaw } = useBalance();
  const { data: agentsData = [] } = useAgents();
  const { data: metricsData = [] } = useAutomationMetrics();
  const { data: runsData = [] } = useAutomationRuns(undefined, 12);

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
              onClick={() => setSelectedSymbol(sym)}
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
                  onClick={() => onNavigate('trading')}
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
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => onNavigate('agents')}>Agents</button>
            <button type="button" className="qa-btn qa-btn-primary" onClick={() => onNavigate('automation')}>Scheduler</button>
            <button type="button" className="qa-btn qa-btn-ghost" onClick={() => onNavigate('trading')}>Trade</button>
          </div>
        </div>

        {/* Column 3 — Portfolio & Paper P&L */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>

          {/* Paper P&L */}
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
                onClick={() => onNavigate('automation')}
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

          {/* Balances */}
          {balances.length > 0 && (
            <div className="panel">
              <div className="panel-header"><span className="panel-title">Balances</span></div>
              <div className="panel-body-compact">
                {balances.slice(0, 4).map((b: any) => (
                  <div key={b.asset} style={{ display: 'flex', justifyContent: 'space-between', padding: '.3rem 0', borderBottom: '1px solid var(--border)', fontFamily: 'var(--mono)', fontSize: '.78rem' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{b.asset}</span>
                    <span style={{ color: 'var(--text-primary)' }}>{b.available?.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Trading ─────────────────────────────────────────────────────────────────

function Trading({ timeframe, onTimeframeChange }: { timeframe: string; onTimeframeChange: (tf: string) => void }) {
  const { selectedSymbol, ticker, klines, indicators, signal } = useAppStore();
  const [orderSide, setOrderSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');

  const sigAction = signal?.action ?? 'hold';
  const sigConf   = signal?.confidence ?? 0;
  const price     = ticker?.lastPrice;
  const upChange  = (ticker?.priceChangePercent ?? 0) >= 0;

  const getRsiColor = (rsi: number | null) => {
    if (rsi == null) return '';
    if (rsi < 30) return 'positive';
    if (rsi > 70) return 'negative';
    if (rsi > 55) return 'amber';
    return '';
  };

  return (
    <div style={{ padding: '1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '.75rem', height: 'calc(100vh - 48px)' }}>

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '.75rem' }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '1rem', color: 'var(--accent)', letterSpacing: '.06em' }}>{selectedSymbol}</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '1.4rem', color: 'var(--text-primary)' }}>
            ${price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '—'}
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '.82rem', padding: '.2rem .5rem', borderRadius: '5px', background: upChange ? 'var(--green-dim)' : 'var(--red-dim)', color: upChange ? 'var(--green)' : 'var(--red)' }}>
            {upChange ? '+' : ''}{ticker?.priceChangePercent?.toFixed(2) ?? '0.00'}%
          </span>
        </div>
        <WsIndicator />
      </div>

      {/* Main layout */}
      <div className="trading-layout" style={{ flex: 1, minHeight: 0 }}>

        {/* Chart */}
        <div className="trading-chart-area">
          <div className="chart-wrapper" style={{ flex: 1, minHeight: 0 }}>
            <div className="chart-header">
              <span className="chart-symbol">{selectedSymbol}</span>
              <div className="timeframe-selector">
                {['1m','5m','15m','1h','4h','1d'].map((tf) => (
                  <button key={tf} type="button" className={`timeframe-btn ${timeframe === tf ? 'active' : ''}`} onClick={() => onTimeframeChange(tf)}>{tf}</button>
                ))}
              </div>
            </div>
            <div className="chart-container">
              {klines.length > 0
                ? <Chart data={klines} symbol={selectedSymbol} timeframe={timeframe} onTimeframeChange={onTimeframeChange} />
                : <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-dim)', fontFamily: 'var(--mono)', fontSize: '.8rem' }}>LOADING MARKET DATA...</div>
              }
            </div>
          </div>
        </div>

        {/* Right sidebar */}
        <div className="trading-sidebar">

          {/* Signal */}
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">AI Signal</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-dim)' }}>{timeframe.toUpperCase()}</span>
            </div>
            <div className="panel-body">
              <div className={`signal-badge ${sigAction}`} style={{ marginBottom: '.6rem' }}>
                <span>
                  {sigAction === 'buy' ? <ArrowUpRight size={24} /> : sigAction === 'sell' ? <ArrowDownRight size={24} /> : <Minus size={20} />}
                </span>
                <div className="signal-meta">
                  <span className="signal-action">{sigAction.toUpperCase()}</span>
                  <span className="signal-conf">{(sigConf * 100).toFixed(0)}% confidence</span>
                </div>
                <div style={{ flex: 1 }}>
                  <div className="confidence-bar">
                    <div className="confidence-fill" style={{ width: `${sigConf * 100}%` }} />
                  </div>
                </div>
              </div>
              {signal?.reasoning && (
                <p style={{ fontSize: '.72rem', color: 'var(--text-secondary)', lineHeight: 1.5, borderTop: '1px solid var(--border)', paddingTop: '.5rem' }}>
                  {signal.reasoning}
                </p>
              )}
            </div>
          </div>

          {/* Indicators */}
          {indicators && (
            <div className="indicators-compact panel">
              <div className="panel-header">
                <span className="panel-title">Indicators</span>
              </div>
              {[
                { label: 'RSI (14)', val: indicators.rsi?.toFixed(1), color: getRsiColor(indicators.rsi) },
                { label: 'MACD', val: indicators.macd?.toFixed(3), color: (indicators.macd ?? 0) > (indicators.macd_signal ?? 0) ? 'positive' : 'negative' },
                { label: 'MACD Sig', val: indicators.macd_signal?.toFixed(3), color: '' },
                { label: 'BB Upper', val: `$${indicators.bb_upper?.toFixed(0)}`, color: '' },
                { label: 'BB Mid', val: `$${indicators.bb_middle?.toFixed(0)}`, color: '' },
                { label: 'BB Lower', val: `$${indicators.bb_lower?.toFixed(0)}`, color: '' },
                { label: 'SMA 20', val: `$${indicators.sma_20?.toFixed(0)}`, color: '' },
                { label: 'SMA 50', val: `$${indicators.sma_50?.toFixed(0)}`, color: '' },
                { label: 'ATR', val: indicators.atr?.toFixed(2), color: 'amber' },
              ].map(({ label, val, color }) => val && (
                <div key={label} className="indicator-row">
                  <span className="indicator-label">{label}</span>
                  <span className={`indicator-value ${color}`}>{val}</span>
                </div>
              ))}
            </div>
          )}

          {/* Order form */}
          <div className="order-form">
            <div className="order-tabs">
              <button type="button" className={`order-tab buy ${orderSide === 'buy' ? 'active' : ''}`} onClick={() => setOrderSide('buy')}>
                BUY
              </button>
              <button type="button" className={`order-tab sell ${orderSide === 'sell' ? 'active' : ''}`} onClick={() => setOrderSide('sell')}>
                SELL
              </button>
            </div>
            <div className="order-form-body">
              <div className="order-price-display">
                <span className="order-price-label">Market Price</span>
                <span className="order-price-val">${price?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? '—'}</span>
              </div>
              <div className="order-field">
                <label className="order-field-label">Quantity</label>
                <input
                  type="number"
                  placeholder="0.000"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  className="order-input"
                />
              </div>
              {quantity && price && (
                <div className="order-price-display">
                  <span className="order-price-label">Total Value</span>
                  <span className="order-price-val">${(parseFloat(quantity) * price).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                </div>
              )}
              <button type="button" className={`execute-btn ${orderSide}`}>
                {orderSide === 'buy' ? '↑' : '↓'} {orderSide.toUpperCase()} {selectedSymbol}
              </button>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}

// ─── Agents ──────────────────────────────────────────────────────────────────

function Agents() {
  const { selectedSymbol } = useAppStore();
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
            <input
              type="text"
              className="settings-input"
              value={formData.trading_pairs.join(', ')}
              onChange={(e) => setFormData({ ...formData, trading_pairs: e.target.value.split(',').map((s) => s.trim()).filter((s) => s) })}
              placeholder="BTCUSDT, ETHUSDT, SOLUSDT"
            />
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
                    <label className="form-label">Name</label>
                    <input
                      type="text"
                      className="settings-input"
                      value={formData.name}
                      onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Allocation: {formData.allocation_percentage}%</label>
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
                    <label className="form-label">Max Position: {formData.max_position_size}</label>
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
                  <div className="edit-actions">
                    <button type="button" className="save-btn" onClick={() => updateAgent(agent.id)}>Save</button>
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
                      </div>
                    </div>
                    {backtestResults[agent.id] && (
                      <div className="backtest-results">
                        <div className="backtest-metrics">
                          <span className={backtestResults[agent.id].metrics.total_pnl >= 0 ? 'positive' : 'negative'}>
                            P&L: ${backtestResults[agent.id].metrics.total_pnl?.toFixed(2)}
                          </span>
                          <span>Win Rate: {(backtestResults[agent.id].metrics.win_rate * 100).toFixed(1)}%</span>
                          <span>Trades: {backtestResults[agent.id].metrics.total_trades}</span>
                        </div>
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

// ─── Wallet ───────────────────────────────────────────────────────────────────

function WalletPage() {
  const { data: balancesRaw } = useBalance();
  const balances: any[] = Array.isArray(balancesRaw?.data)
    ? balancesRaw.data
    : Array.isArray(balancesRaw)
      ? balancesRaw
      : [];

  return (
    <div className="space-y-6">
      <h1 className="page-title">Wallet</h1>
      <div className="wallet-grid">
        {balances.length === 0 ? (
          <p className="text-gray-400">No balances found. Configure API key to sync.</p>
        ) : (
          balances.map((balance) => (
            <div key={balance.asset} className="balance-card">
              <p className="balance-asset">{balance.asset}</p>
              <p className="balance-amount">Available: {balance.available.toFixed(4)}</p>
              <p className="balance-amount">Locked: {balance.locked.toFixed(4)}</p>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ─── Automation ───────────────────────────────────────────────────────────────

function AutomationPage() {
  const { data: statusData, refetch: refetchStatus } = useAutomationStatus();
  const { data: metricsData = [] } = useAutomationMetrics();
  const { data: agentsData = [] } = useAgents();
  const [runningAgent, setRunningAgent] = useState<string | null>(null);
  const [usePaperMode, setUsePaperMode] = useState(true);

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
      }, usePaperMode);
    } catch (error) {
      console.error('Failed to run agent:', error);
    } finally {
      setRunningAgent(null);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="page-title">Agent Automation</h1>

      <div className="card">
        <div className="card-header-row">
          <div>
            <h2 className="card-title">Trading Mode</h2>
            <p className="text-gray-400 text-sm">{usePaperMode ? 'Paper Trading - No real trades executed' : 'Real Trading - Actual trades will be executed'}</p>
          </div>
          <label className="toggle-switch">
            <input type="checkbox" checked={usePaperMode} onChange={() => setUsePaperMode(!usePaperMode)} />
            <span className="toggle-slider" />
          </label>
        </div>
        {!usePaperMode && (
          <div className="warning-banner">
            ⚠️ Real Trading Enabled - Actual trades will be executed on the exchange
          </div>
        )}
      </div>

      <div className="stats-grid">
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
          <p className="stat-label">Tracked Agents</p>
          <p className="stat-value">{status?.tracked_agents || 0}</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Market</p>
          <p className="stat-value">{market?.trend || 'N/A'}</p>
        </div>
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
            <span className={`setup-value ${usePaperMode ? 'paper' : 'real'}`}>
              {usePaperMode ? 'Paper Trading' : 'Real Trading'}
            </span>
          </div>
          <div className="setup-item">
            <span className="setup-label">Active Agents</span>
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
        <h2 className="card-title">Agent Performance</h2>
        {metrics.length === 0 ? (
          <p className="text-gray-400">No agent metrics yet. Run agents to see performance data.</p>
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

// ─── History ─────────────────────────────────────────────────────────────────

function HistoryPage() {
  const [tab, setTab] = useState<'paper' | 'live'>('paper');

  const { data: trades = [] } = useTradeHistory();
  const { data: pnl } = usePnl();
  const { data: paperTrades = [] } = usePaperOrders();
  const { data: paperPnl } = usePaperPnl();
  const { data: agentsData = [] } = useAgents();

  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const activePnl = tab === 'paper' ? paperPnl : pnl;
  const activeTrades: any[] = tab === 'paper'
    ? (Array.isArray(paperTrades) ? paperTrades : [])
    : (Array.isArray(trades) ? trades : []);

  const agentName = (id: string | null) => {
    if (!id) return '-';
    return agents.find((a: any) => a.id === id)?.name || id.slice(0, 8) + '...';
  };

  const agentStrategy = (id: string | null) => {
    if (!id) return '-';
    return agents.find((a: any) => a.id === id)?.strategy_type || '-';
  };

  return (
    <div className="space-y-6">
      <h1 className="page-title">Trade History</h1>

      <div className="tab-row">
        <button type="button" className={`tab-btn ${tab === 'paper' ? 'active' : ''}`} onClick={() => setTab('paper')}>
          Paper Trades
        </button>
        <button type="button" className={`tab-btn ${tab === 'live' ? 'active' : ''}`} onClick={() => setTab('live')}>
          Live Trades
        </button>
      </div>

      {activePnl && (
        <div className="stats-grid">
          <div className="stat-card">
            <p className="stat-label">Total P&L</p>
            <p className={`stat-value ${activePnl.total_pnl >= 0 ? 'positive' : 'negative'}`}>
              ${activePnl.total_pnl?.toFixed(2) || '0.00'}
            </p>
          </div>
          <div className="stat-card">
            <p className="stat-label">Realized P&L</p>
            <p className={`stat-value ${(activePnl.realized_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`}>
              ${activePnl.realized_pnl?.toFixed(2) || '0.00'}
            </p>
          </div>
          <div className="stat-card">
            <p className="stat-label">Buy Volume</p>
            <p className="stat-value">${activePnl.buy_volume?.toFixed(2) || '0.00'}</p>
          </div>
          <div className="stat-card">
            <p className="stat-label">Sell Volume</p>
            <p className="stat-value">${activePnl.sell_volume?.toFixed(2) || '0.00'}</p>
          </div>
          <div className="stat-card">
            <p className="stat-label">Total Trades</p>
            <p className="stat-value">{activePnl.trade_count || 0}</p>
          </div>
          <div className="stat-card">
            <p className="stat-label">Open Positions</p>
            <p className="stat-value">{activePnl.open_positions || 0}</p>
          </div>
        </div>
      )}

      <div className="card">
        <h2 className="card-title">{tab === 'paper' ? 'Paper' : 'Live'} Trades</h2>
        {activeTrades.length === 0 ? (
          <p className="text-gray-400">No {tab} trades yet.</p>
        ) : (
          <div className="trades-table">
            <div className="trades-header" style={{ gridTemplateColumns: 'repeat(10, 1fr)' }}>
              <span>Time</span><span>Symbol</span><span>Side</span><span>Qty</span>
              <span>Price</span><span>Total</span><span>Fee</span>
              <span>Agent</span><span>Strategy</span><span>Status</span>
            </div>
            {activeTrades.map((trade: any) => (
              <div key={trade.id} className="trades-row" style={{ gridTemplateColumns: 'repeat(10, 1fr)' }}>
                <span title={new Date(trade.created_at).toLocaleString()}>{timeAgo(trade.created_at)}</span>
                <span>{trade.symbol}</span>
                <span className={trade.side === 'buy' ? 'positive' : 'negative'}>{trade.side?.toUpperCase()}</span>
                <span>{trade.quantity}</span>
                <span>${trade.price?.toFixed(2)}</span>
                <span>${trade.total?.toFixed(2)}</span>
                <span className="text-gray-400">${trade.fee?.toFixed(4) || '0.0000'}</span>
                <span className="text-gray-300">{agentName(trade.agent_id)}</span>
                <span className="strategy-tag" style={{ fontSize: '0.7rem' }}>{agentStrategy(trade.agent_id)}</span>
                <span className={`status-${trade.status}`}>{trade.status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Settings ────────────────────────────────────────────────────────────────

type SettingsTab = 'api' | 'risk' | 'trading' | 'llm';

function SettingsPage() {
  const { data: settingsData, refetch: refetchSettings } = useSettings();
  const [activeTab, setActiveTab] = useState<SettingsTab>('api');
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  // ── API Keys state ──
  const [showSecret, setShowSecret] = useState(false);
  const [apiForm, setApiForm] = useState({
    phemex_api_key: '',
    phemex_api_secret: '',
    phemex_testnet: true,
  });

  // ── Risk Limits state ──
  const [riskForm, setRiskForm] = useState({
    max_position_size_pct: 5,
    max_daily_loss_pct: 5,
    max_open_positions: 5,
    default_stop_loss_pct: 2,
    default_take_profit_pct: 4,
    max_leverage: 1,
  });

  // ── Trading Preferences state ──
  const [tradingForm, setTradingForm] = useState({
    default_symbol: 'BTCUSDT',
    default_timeframe: '1h',
    paper_trading_default: true,
    auto_confirm_orders: false,
    default_order_type: 'limit',
  });

  // ── LLM Config state ──
  const [llmForm, setLlmForm] = useState({
    provider: 'openrouter',
    model: 'openai/gpt-4o-mini',
    temperature: 0.7,
    max_tokens: 1000,
    openai_api_key: '',
    anthropic_api_key: '',
    openrouter_api_key: '',
  });

  // Hydrate forms from server data
  useEffect(() => {
    if (!settingsData) return;
    setApiForm(prev => ({ ...prev, phemex_testnet: settingsData.api_keys?.phemex_testnet ?? true }));
    if (settingsData.risk_limits) setRiskForm(settingsData.risk_limits);
    if (settingsData.trading) setTradingForm(settingsData.trading);
    if (settingsData.llm) {
      setLlmForm(prev => ({
        ...prev,
        provider: settingsData.llm.provider,
        model: settingsData.llm.model,
        temperature: settingsData.llm.temperature,
        max_tokens: settingsData.llm.max_tokens,
      }));
    }
  }, [settingsData]);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleSaveApiKeys = async () => {
    if (!apiForm.phemex_api_key || !apiForm.phemex_api_secret) {
      showToast('Both API key and secret are required', 'error');
      return;
    }
    setSaving(true);
    try {
      await settingsApi.updateApiKeys(apiForm);
      showToast('API keys saved successfully', 'success');
      setApiForm(prev => ({ ...prev, phemex_api_key: '', phemex_api_secret: '' }));
      refetchSettings();
    } catch (err) {
      showToast('Failed to save API keys', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveRiskLimits = async () => {
    setSaving(true);
    try {
      await settingsApi.updateRiskLimits(riskForm);
      showToast('Risk limits updated', 'success');
      refetchSettings();
    } catch (err) {
      showToast('Failed to update risk limits', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveTradingPrefs = async () => {
    setSaving(true);
    try {
      await settingsApi.updateTradingPrefs(tradingForm);
      showToast('Trading preferences updated', 'success');
      refetchSettings();
    } catch (err) {
      showToast('Failed to update trading preferences', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveLlmConfig = async () => {
    setSaving(true);
    try {
      const payload: Record<string, any> = {
        provider: llmForm.provider,
        model: llmForm.model,
        temperature: llmForm.temperature,
        max_tokens: llmForm.max_tokens,
      };
      if (llmForm.openai_api_key) payload.openai_api_key = llmForm.openai_api_key;
      if (llmForm.anthropic_api_key) payload.anthropic_api_key = llmForm.anthropic_api_key;
      if (llmForm.openrouter_api_key) payload.openrouter_api_key = llmForm.openrouter_api_key;
      await settingsApi.updateLlmConfig(payload);
      showToast('LLM configuration updated', 'success');
      setLlmForm(prev => ({ ...prev, openai_api_key: '', anthropic_api_key: '', openrouter_api_key: '' }));
      refetchSettings();
    } catch (err) {
      showToast('Failed to update LLM config', 'error');
    } finally {
      setSaving(false);
    }
  };

  const tabs: { id: SettingsTab; label: string; icon: React.ReactNode }[] = [
    { id: 'api', label: 'API Keys', icon: <Key size={14} /> },
    { id: 'risk', label: 'Risk Limits', icon: <Shield size={14} /> },
    { id: 'trading', label: 'Trading', icon: <TrendingUp size={14} /> },
    { id: 'llm', label: 'AI / LLM', icon: <Brain size={14} /> },
  ];

  const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT'];
  const timeframes = ['1m', '5m', '15m', '1h', '4h', '1d'];
  const orderTypes = ['limit', 'market'];
  const llmProviders = ['openrouter', 'openai', 'anthropic', 'azure'];

  return (
    <div className="space-y-6">
      {/* Toast notification */}
      {toast && (
        <div
          style={{
            position: 'fixed', top: '1.5rem', right: '1.5rem', zIndex: 9999,
            padding: '.75rem 1.25rem', borderRadius: 8,
            background: toast.type === 'success' ? 'var(--green-dim)' : 'var(--red-dim)',
            border: `1px solid ${toast.type === 'success' ? 'rgba(0,230,118,.3)' : 'rgba(255,61,96,.3)'}`,
            color: toast.type === 'success' ? 'var(--green)' : 'var(--red)',
            fontSize: '.82rem', fontWeight: 600,
            display: 'flex', alignItems: 'center', gap: '.5rem',
            animation: 'fadeIn .2s ease-out',
          }}
        >
          {toast.type === 'success' ? <Check size={14} /> : <AlertTriangle size={14} />}
          {toast.message}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Settings</h1>
        <button
          type="button"
          className="settings-btn"
          onClick={() => refetchSettings()}
          style={{ display: 'flex', alignItems: 'center', gap: '.35rem' }}
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {/* Tab navigation */}
      <div style={{
        display: 'flex', gap: '.35rem', padding: '.25rem',
        background: 'var(--bg-panel)', borderRadius: 10,
        border: '1px solid var(--border)',
      }}>
        {tabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            style={{
              flex: 1, padding: '.55rem .75rem', borderRadius: 7,
              border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '.4rem',
              fontSize: '.78rem', fontWeight: 600,
              fontFamily: 'var(--sans)',
              background: activeTab === tab.id ? 'var(--accent-dim)' : 'transparent',
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-secondary)',
              transition: 'all .15s',
            }}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* ── API Keys Tab ── */}
      {activeTab === 'api' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Key size={16} style={{ color: 'var(--accent)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Phemex API Configuration</h2>
          </div>

          {/* Current key status */}
          {settingsData?.api_keys && (
            <div style={{
              padding: '.75rem 1rem', borderRadius: 8,
              background: settingsData.api_keys.has_phemex_key ? 'var(--green-dim)' : 'var(--amber-dim)',
              border: `1px solid ${settingsData.api_keys.has_phemex_key ? 'rgba(0,230,118,.2)' : 'rgba(255,179,0,.2)'}`,
              display: 'flex', alignItems: 'center', gap: '.6rem',
              fontSize: '.78rem',
            }}>
              <Info size={14} style={{ color: settingsData.api_keys.has_phemex_key ? 'var(--green)' : 'var(--amber)', flexShrink: 0 }} />
              <span style={{ color: settingsData.api_keys.has_phemex_key ? 'var(--green)' : 'var(--amber)' }}>
                {settingsData.api_keys.has_phemex_key
                  ? `API key configured (${settingsData.api_keys.key_hint}) — ${settingsData.api_keys.phemex_testnet ? 'Testnet' : 'Mainnet'}`
                  : 'No API key configured — set your Phemex credentials below'}
              </span>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">API Key</label>
            <input
              type="text"
              className="settings-input"
              placeholder="Enter your Phemex API key"
              value={apiForm.phemex_api_key}
              onChange={e => setApiForm({ ...apiForm, phemex_api_key: e.target.value })}
              autoComplete="off"
            />
          </div>

          <div className="form-group">
            <label className="form-label">API Secret</label>
            <div style={{ position: 'relative' }}>
              <input
                type={showSecret ? 'text' : 'password'}
                className="settings-input"
                style={{ paddingRight: '2.5rem' }}
                placeholder="Enter your Phemex API secret"
                value={apiForm.phemex_api_secret}
                onChange={e => setApiForm({ ...apiForm, phemex_api_secret: e.target.value })}
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                style={{
                  position: 'absolute', right: '.6rem', top: '.55rem',
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-secondary)', padding: 0,
                }}
              >
                {showSecret ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Network</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: '.75rem', marginTop: '.25rem' }}>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={apiForm.phemex_testnet}
                  onChange={e => setApiForm({ ...apiForm, phemex_testnet: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
              <span style={{ fontSize: '.8rem', color: apiForm.phemex_testnet ? 'var(--amber)' : 'var(--green)' }}>
                {apiForm.phemex_testnet ? '⚠ Testnet Mode' : '● Live / Mainnet'}
              </span>
            </div>
          </div>

          {!apiForm.phemex_testnet && (
            <div className="warning-banner" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <AlertTriangle size={14} />
              <span>Mainnet mode uses real funds. Ensure your credentials are correct before trading.</span>
            </div>
          )}

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveApiKeys}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save API Keys'}
          </button>
        </div>
      )}

      {/* ── Risk Limits Tab ── */}
      {activeTab === 'risk' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Shield size={16} style={{ color: 'var(--red)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Risk Management</h2>
          </div>

          <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', marginBottom: '.5rem' }}>
            Set guardrails to protect your capital. These limits apply across all agents and manual trades.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Max Position Size: {riskForm.max_position_size_pct}%</label>
              <input
                type="range" min="0.5" max="50" step="0.5"
                className="slider"
                value={riskForm.max_position_size_pct}
                onChange={e => setRiskForm({ ...riskForm, max_position_size_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>50%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Daily Loss: {riskForm.max_daily_loss_pct}%</label>
              <input
                type="range" min="0.5" max="25" step="0.5"
                className="slider"
                value={riskForm.max_daily_loss_pct}
                onChange={e => setRiskForm({ ...riskForm, max_daily_loss_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>25%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Stop Loss: {riskForm.default_stop_loss_pct}%</label>
              <input
                type="range" min="0.5" max="20" step="0.5"
                className="slider"
                value={riskForm.default_stop_loss_pct}
                onChange={e => setRiskForm({ ...riskForm, default_stop_loss_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>20%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Take Profit: {riskForm.default_take_profit_pct}%</label>
              <input
                type="range" min="0.5" max="50" step="0.5"
                className="slider"
                value={riskForm.default_take_profit_pct}
                onChange={e => setRiskForm({ ...riskForm, default_take_profit_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>50%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Open Positions</label>
              <input
                type="number" min="1" max="50"
                className="settings-input"
                style={{ marginBottom: 0 }}
                value={riskForm.max_open_positions}
                onChange={e => setRiskForm({ ...riskForm, max_open_positions: parseInt(e.target.value) || 1 })}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Max Leverage: {riskForm.max_leverage}x</label>
              <input
                type="range" min="1" max="50" step="1"
                className="slider"
                value={riskForm.max_leverage}
                onChange={e => setRiskForm({ ...riskForm, max_leverage: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>1x</span><span>50x</span></div>
            </div>
          </div>

          {/* Risk summary */}
          <div style={{
            marginTop: '.5rem', padding: '.75rem 1rem', borderRadius: 8,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '.75rem',
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Risk Profile</div>
              <div style={{
                fontSize: '.9rem', fontWeight: 700, marginTop: '.2rem',
                color: riskForm.max_leverage > 10 || riskForm.max_daily_loss_pct > 10 ? 'var(--red)' :
                       riskForm.max_leverage > 3 || riskForm.max_daily_loss_pct > 5 ? 'var(--amber)' : 'var(--green)',
              }}>
                {riskForm.max_leverage > 10 || riskForm.max_daily_loss_pct > 10 ? 'Aggressive' :
                 riskForm.max_leverage > 3 || riskForm.max_daily_loss_pct > 5 ? 'Moderate' : 'Conservative'}
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Risk:Reward</div>
              <div style={{ fontSize: '.9rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: '.2rem' }}>
                1:{(riskForm.default_take_profit_pct / riskForm.default_stop_loss_pct).toFixed(1)}
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Max Exposure</div>
              <div style={{ fontSize: '.9rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: '.2rem' }}>
                {(riskForm.max_position_size_pct * riskForm.max_open_positions).toFixed(0)}%
              </div>
            </div>
          </div>

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveRiskLimits}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save Risk Limits'}
          </button>
        </div>
      )}

      {/* ── Trading Preferences Tab ── */}
      {activeTab === 'trading' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <TrendingUp size={16} style={{ color: 'var(--green)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Trading Preferences</h2>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Default Symbol</label>
              <select
                className="settings-input"
                style={{ marginBottom: 0, cursor: 'pointer' }}
                value={tradingForm.default_symbol}
                onChange={e => setTradingForm({ ...tradingForm, default_symbol: e.target.value })}
              >
                {symbols.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Default Timeframe</label>
              <div style={{ display: 'flex', gap: '.3rem' }}>
                {timeframes.map(tf => (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTradingForm({ ...tradingForm, default_timeframe: tf })}
                    style={{
                      flex: 1, padding: '.45rem .25rem', borderRadius: 6,
                      border: '1px solid',
                      borderColor: tradingForm.default_timeframe === tf ? 'var(--accent)' : 'var(--border-mid)',
                      background: tradingForm.default_timeframe === tf ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                      color: tradingForm.default_timeframe === tf ? 'var(--accent)' : 'var(--text-secondary)',
                      fontSize: '.72rem', fontWeight: 600, cursor: 'pointer',
                      fontFamily: 'var(--mono)',
                      transition: 'all .15s',
                    }}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Order Type</label>
              <div style={{ display: 'flex', gap: '.4rem' }}>
                {orderTypes.map(ot => (
                  <button
                    key={ot}
                    type="button"
                    onClick={() => setTradingForm({ ...tradingForm, default_order_type: ot })}
                    style={{
                      flex: 1, padding: '.5rem .75rem', borderRadius: 7,
                      border: '1px solid',
                      borderColor: tradingForm.default_order_type === ot ? 'var(--accent)' : 'var(--border-mid)',
                      background: tradingForm.default_order_type === ot ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                      color: tradingForm.default_order_type === ot ? 'var(--accent)' : 'var(--text-secondary)',
                      fontSize: '.78rem', fontWeight: 600, cursor: 'pointer',
                      fontFamily: 'var(--sans)', textTransform: 'capitalize',
                      transition: 'all .15s',
                    }}
                  >
                    {ot}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '.85rem', marginTop: '.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Paper Trading by Default</div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)' }}>New agents and manual trades use paper mode</div>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={tradingForm.paper_trading_default}
                  onChange={e => setTradingForm({ ...tradingForm, paper_trading_default: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Auto-Confirm Orders</div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)' }}>Execute agent signals without manual confirmation</div>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={tradingForm.auto_confirm_orders}
                  onChange={e => setTradingForm({ ...tradingForm, auto_confirm_orders: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
            </div>
          </div>

          {tradingForm.auto_confirm_orders && (
            <div className="warning-banner" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <AlertTriangle size={14} />
              <span>Auto-confirm is enabled. Agent signals will be executed automatically without review.</span>
            </div>
          )}

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveTradingPrefs}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save Preferences'}
          </button>
        </div>
      )}

      {/* ── LLM / AI Tab ── */}
      {activeTab === 'llm' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Brain size={16} style={{ color: 'var(--accent)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>AI / LLM Configuration</h2>
          </div>

          <div className="form-group">
            <label className="form-label">Provider</label>
            <div style={{ display: 'flex', gap: '.35rem' }}>
              {llmProviders.map(p => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setLlmForm({ ...llmForm, provider: p })}
                  style={{
                    flex: 1, padding: '.5rem .5rem', borderRadius: 7,
                    border: '1px solid',
                    borderColor: llmForm.provider === p ? 'var(--accent)' : 'var(--border-mid)',
                    background: llmForm.provider === p ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                    color: llmForm.provider === p ? 'var(--accent)' : 'var(--text-secondary)',
                    fontSize: '.75rem', fontWeight: 600, cursor: 'pointer',
                    fontFamily: 'var(--sans)', textTransform: 'capitalize',
                    transition: 'all .15s',
                  }}
                >
                  {p === 'openrouter' ? 'OpenRouter' : p === 'openai' ? 'OpenAI' : p === 'anthropic' ? 'Anthropic' : 'Azure'}
                </button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Model</label>
            <input
              type="text"
              className="settings-input"
              value={llmForm.model}
              onChange={e => setLlmForm({ ...llmForm, model: e.target.value })}
              placeholder="e.g. openai/gpt-4o-mini"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Temperature: {llmForm.temperature.toFixed(1)}</label>
              <input
                type="range" min="0" max="2" step="0.1"
                className="slider"
                value={llmForm.temperature}
                onChange={e => setLlmForm({ ...llmForm, temperature: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>Precise (0)</span><span>Creative (2)</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Tokens</label>
              <input
                type="number" min="100" max="32000" step="100"
                className="settings-input"
                style={{ marginBottom: 0 }}
                value={llmForm.max_tokens}
                onChange={e => setLlmForm({ ...llmForm, max_tokens: parseInt(e.target.value) || 1000 })}
              />
            </div>
          </div>

          {/* Provider API key status indicators */}
          <div style={{
            padding: '.75rem 1rem', borderRadius: 8,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          }}>
            <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em', marginBottom: '.6rem' }}>
              Provider Key Status
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '.4rem' }}>
              {[
                { label: 'OpenAI', has: settingsData?.llm?.has_openai_key },
                { label: 'Anthropic', has: settingsData?.llm?.has_anthropic_key },
                { label: 'OpenRouter', has: settingsData?.llm?.has_openrouter_key },
                { label: 'Azure', has: settingsData?.llm?.has_azure_key },
              ].map(({ label, has }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '.4rem', fontSize: '.75rem' }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: has ? 'var(--green)' : 'var(--text-dim)',
                    flexShrink: 0,
                  }} />
                  <span style={{ color: has ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{label}</span>
                  <span style={{ color: has ? 'var(--green)' : 'var(--text-dim)', fontSize: '.7rem' }}>
                    {has ? 'Configured' : 'Not set'}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Conditional API key input based on selected provider */}
          {llmForm.provider === 'openai' && (
            <div className="form-group">
              <label className="form-label">OpenAI API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_openai_key ? 'Key already set — enter new value to update' : 'sk-...'}
                value={llmForm.openai_api_key}
                onChange={e => setLlmForm({ ...llmForm, openai_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}
          {llmForm.provider === 'anthropic' && (
            <div className="form-group">
              <label className="form-label">Anthropic API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_anthropic_key ? 'Key already set — enter new value to update' : 'sk-ant-...'}
                value={llmForm.anthropic_api_key}
                onChange={e => setLlmForm({ ...llmForm, anthropic_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}
          {llmForm.provider === 'openrouter' && (
            <div className="form-group">
              <label className="form-label">OpenRouter API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_openrouter_key ? 'Key already set — enter new value to update' : 'sk-or-...'}
                value={llmForm.openrouter_api_key}
                onChange={e => setLlmForm({ ...llmForm, openrouter_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveLlmConfig}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save LLM Config'}
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Mini Chart Component ────────────────────────────────────────────────────

function MiniChart({ data }: { data: any[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || !data.length) return;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#5a7394' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { mode: 0 },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      width: containerRef.current.clientWidth,
      height: 100,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#00e676',
      downColor: '#ff3d60',
      borderUpColor: '#00e676',
      borderDownColor: '#ff3d60',
      wickUpColor: '#00e676',
      wickDownColor: '#ff3d60',
    });

    const candleData = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    candleSeries.setData(candleData);
    if (chart) chart.timeScale().fitContent();
    chartRef.current = chart;

    return () => chart.remove();
  }, [data]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function timeAgo(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ===========================
// FUND TEAM PAGE
// ===========================

function FundTeamPage() {
  const { data: marketAnalysis, isLoading: marketLoading } = useFundMarketAnalysis();
  const { data: technicalAnalysis, isLoading: technicalLoading } = useFundTechnicalAnalysis();
  const { data: riskData, isLoading: riskLoading } = useFundRiskAssessment();
  const { data: cioReport, isLoading: cioLoading } = useFundCIOReport();
  const { data: attribution, isLoading: attrLoading } = useFundPerformanceAttribution();
  const { data: allocation, isLoading: allocLoading } = useFundAllocationDecision();
  const { data: teamRoster } = useFundTeamRoster();
  const { data: agents } = useAgents();

  const riskLevel: string = (riskData as any)?.risk_level ?? 'unknown';
  const riskColors: Record<string, string> = {
    safe: 'var(--green)',
    caution: 'var(--amber)',
    danger: 'var(--red)',
    unknown: 'var(--text-dim)',
  };
  const riskColor = riskColors[riskLevel] ?? 'var(--text-dim)';

  const sentiment: string = (cioReport as any)?.cio_sentiment ?? 'neutral';
  const sentimentColors: Record<string, string> = {
    very_bullish: 'var(--green)',
    bullish: 'var(--green)',
    neutral: 'var(--text-secondary)',
    bearish: 'var(--red)',
    very_bearish: 'var(--red)',
  };
  const sentimentColor = sentimentColors[sentiment] ?? 'var(--text-secondary)';

  // Helper to get team member info
  const getTeamMember = (role: string) => {
    const roster = (teamRoster as any) || [];
    return roster.find((m: any) => m.role === role) || {
      name: 'Unknown Agent',
      avatar: '🤖',
      title: 'Agent',
      bio: 'Loading...'
    };
  };

  // Helper to get agent display name
  const getAgentName = (agentId: string) => {
    const agentsList = (agents as any) || [];
    const agent = agentsList.find((a: any) => a.id === agentId);
    return agent?.name || agentId;
  };

  return (
    <div className="page-content" style={{ paddingTop: '2rem', paddingBottom: '2rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2.5rem', flexWrap: 'wrap', gap: '1.25rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--sans)', margin: 0, color: 'var(--text)', letterSpacing: '-0.02em' }}>
            Fund Management Team
          </h1>
          <p style={{ fontSize: '.8rem', color: 'var(--text-dim)', margin: '.5rem 0 0', fontFamily: 'var(--mono)', lineHeight: 1.5 }}>
            AI-driven multi-agent fund coordination — team decisions update every 5 minutes
          </p>
        </div>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{
            padding: '6px 14px',
            borderRadius: '8px',
            fontSize: '.75rem',
            fontFamily: 'var(--mono)',
            fontWeight: 700,
            background: riskLevel === 'safe' ? 'var(--green-dim)' : riskLevel === 'danger' ? 'var(--red-dim)' : 'var(--amber-dim)',
            color: riskColor,
            border: `1px solid ${riskColor}40`,
          }}>
            {riskLevel.toUpperCase()} RISK
          </span>
          <span style={{
            padding: '6px 14px',
            borderRadius: '8px',
            fontSize: '.75rem',
            fontFamily: 'var(--mono)',
            fontWeight: 700,
            background: 'var(--bg-card)',
            color: sentimentColor,
            border: `1px solid ${sentimentColor}40`,
          }}>
            CIO: {sentiment.replace('_', ' ').toUpperCase()}
          </span>
        </div>
      </div>
      <div style={{  marginBottom: '2rem' }}>
      <TeamChatPanel />
      </div>

      {/* Main Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.5rem' }}>
        {/* === Research Analyst === */}
        <div className="panel" style={{ gridColumn: 'span 2' }}>
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('research_analyst').avatar} alt={getTeamMember('research_analyst').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('research_analyst').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('research_analyst').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
              MARKET ANALYSIS
            </span>
          </div>

          {marketLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem', color: 'var(--text-dim)' }}>Analyzing markets...</div>
          ) : marketAnalysis ? (
            <div>
              {/* Analyst Summary */}
              <div style={{ fontSize: '.75rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.7, padding: '.75rem 1rem', background: 'var(--bg-hover)', borderRadius: '6px', marginBottom: '1rem', borderLeft: '3px solid var(--accent)' }}>
                <strong style={{ color: 'var(--text)', display: 'block', marginBottom: '.5rem' }}>Market Analysis Summary:</strong>
                {(marketAnalysis as any).reasoning || 'Analyzing market conditions across major symbols...'}
              </div>

              {/* Regime & Sentiment */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '.75rem', marginBottom: '1rem' }}>
                {[
                  { label: 'Regime', value: (marketAnalysis as any).market_regime?.regime?.replace('_', ' ') ?? 'N/A' },
                  { label: 'Sentiment', value: (marketAnalysis as any).market_regime?.sentiment?.replace('_', ' ') ?? 'N/A' },
                  { label: 'Volatility', value: (marketAnalysis as any).market_regime?.volatility_regime ?? 'N/A' },
                  { label: 'Correlation', value: (marketAnalysis as any).market_regime?.correlation_status?.replace('_', ' ') ?? 'N/A' },
                ].map(item => (
                  <div key={item.label} style={{ textAlign: 'center', padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '6px' }}>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.25rem' }}>{item.label}</div>
                    <div style={{ fontSize: '.8rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text)', textTransform: 'capitalize' }}>{item.value}</div>
                  </div>
                ))}
              </div>

              {/* Opportunities */}
              {((marketAnalysis as any).opportunities?.length > 0) && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginBottom: '.5rem', textTransform: 'uppercase', letterSpacing: '.06em' }}>
                    Opportunities Identified
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                    {((marketAnalysis as any).opportunities as any[])?.slice(0, 4).map((opp: any, i: number) => (
                      <div key={i} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '.4rem .6rem', background: 'var(--bg-hover)', borderRadius: '4px',
                        borderLeft: `2px solid ${opp.recommended_action === 'buy' ? 'var(--green)' : opp.recommended_action === 'sell' ? 'var(--red)' : 'var(--border)'}`,
                      }}>
                        <div>
                          <span style={{ fontFamily: 'var(--mono)', fontSize: '.75rem', fontWeight: 700, color: 'var(--text)' }}>{opp.symbol}</span>
                          <span style={{ marginLeft: '.5rem', fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{opp.opportunity_type?.replace(/_/g, ' ')}</span>
                        </div>
                        <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
                          <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{(opp.confidence * 100).toFixed(0)}%</span>
                          <span style={{
                            padding: '1px 6px',
                            borderRadius: '3px',
                            fontSize: '.6rem',
                            fontFamily: 'var(--mono)',
                            fontWeight: 700,
                            background: opp.recommended_action === 'buy' ? 'var(--green-dim)' : opp.recommended_action === 'sell' ? 'var(--red-dim)' : 'var(--bg-card)',
                            color: opp.recommended_action === 'buy' ? 'var(--green)' : opp.recommended_action === 'sell' ? 'var(--red)' : 'var(--text-dim)',
                          }}>
                            {opp.recommended_action?.toUpperCase()}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Macro Context */}
              {(marketAnalysis as any).market_regime?.macro_context && (
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.6, padding: '.5rem .75rem', background: 'var(--bg-hover)', borderRadius: '6px', borderLeft: '2px solid var(--accent)' }}>
                  {(marketAnalysis as any).market_regime.macro_context}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No market analysis available</div>
          )}
        </div>

        {/* === Technical Analyst === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('technical_analyst').avatar} alt={getTeamMember('technical_analyst').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('technical_analyst').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('technical_analyst').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
              CHART ANALYSIS
            </span>
          </div>

          {technicalLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Analyzing charts...</div>
          ) : technicalAnalysis ? (
            <div>
              {/* Current Price & Signal */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <div>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text)' }}>
                    ${((technicalAnalysis as any).current_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                  <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>BTCUSDT</div>
                </div>
                <div style={{
                  padding: '4px 12px',
                  borderRadius: '6px',
                  fontSize: '.75rem',
                  fontFamily: 'var(--mono)',
                  fontWeight: 700,
                  background: (technicalAnalysis as any).overall_signal === 'bullish' ? 'var(--green-dim)' : (technicalAnalysis as any).overall_signal === 'bearish' ? 'var(--red-dim)' : 'var(--bg-hover)',
                  color: (technicalAnalysis as any).overall_signal === 'bullish' ? 'var(--green)' : (technicalAnalysis as any).overall_signal === 'bearish' ? 'var(--red)' : 'var(--text-dim)',
                }}>
                  {(technicalAnalysis as any).overall_signal?.toUpperCase() || 'HOLD'} ({((technicalAnalysis as any).confidence || 0) * 100}%)
                </div>
              </div>

              {/* Price Levels */}
              {(technicalAnalysis as any).price_levels && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Key Levels</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
                    {(technicalAnalysis as any).price_levels.resistance?.slice(0, 2).map((r: number, i: number) => (
                      <div key={`r${i}`} style={{ display: 'flex', justifyContent: 'space-between', padding: '.3rem .5rem', background: 'var(--red-dim)', borderRadius: '4px' }}>
                        <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>R{i + 1}</span>
                        <span style={{ fontSize: '.7rem', color: 'var(--red)', fontFamily: 'var(--mono)', fontWeight: 600 }}>${r.toLocaleString()}</span>
                      </div>
                    ))}
                    {(technicalAnalysis as any).price_levels.support?.slice(0, 2).map((s: number, i: number) => (
                      <div key={`s${i}`} style={{ display: 'flex', justifyContent: 'space-between', padding: '.3rem .5rem', background: 'var(--green-dim)', borderRadius: '4px' }}>
                        <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>S{i + 1}</span>
                        <span style={{ fontSize: '.7rem', color: 'var(--green)', fontFamily: 'var(--mono)', fontWeight: 600 }}>${s.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Fibonacci */}
              {(technicalAnalysis as any).price_levels?.fibonacci_retracements && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Fibonacci Retracements</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.25rem' }}>
                    {Object.entries((technicalAnalysis as any).price_levels.fibonacci_retracements).slice(0, 3).map(([level, price]: [string, any]) => (
                      <div key={level} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.65rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>
                        <span>{level}</span>
                        <span>${(price || 0).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Patterns */}
              {((technicalAnalysis as any).patterns?.length > 0) && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Pattern Signals</div>
                  {((technicalAnalysis as any).patterns as any[]).slice(0, 2).map((pattern: any, i: number) => (
                    <div key={i} style={{
                      padding: '.4rem .5rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '4px',
                      marginBottom: '.3rem',
                      borderLeft: `2px solid ${pattern.direction === 'bullish' ? 'var(--green)' : 'var(--red)'}`,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.2rem' }}>
                        <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', fontWeight: 600, color: 'var(--text)' }}>{pattern.pattern_type?.replace(/_/g, ' ')}</span>
                        <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: pattern.direction === 'bullish' ? 'var(--green)' : 'var(--red)' }}>{pattern.direction?.toUpperCase()} {(pattern.confidence * 100).toFixed(0)}%</span>
                      </div>
                      <div style={{ display: 'flex', gap: '.5rem', fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                        <span>SL: ${pattern.stop_loss?.toFixed(0)}</span>
                        <span>TP1: ${pattern.take_profit_1?.toFixed(0)}</span>
                        <span>RR: {pattern.risk_reward?.toFixed(1)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Multi-Timeframe */}
              {(technicalAnalysis as any).multi_timeframe && (
                <div style={{ marginTop: '.75rem', padding: '.4rem .5rem', background: 'var(--bg-hover)', borderRadius: '4px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase' }}>Multi-TF</span>
                    <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: (technicalAnalysis as any).multi_timeframe.trend_confirmation ? 'var(--green)' : 'var(--amber)' }}>
                      {(technicalAnalysis as any).multi_timeframe.alignment?.toUpperCase()} | {((technicalAnalysis as any).multi_timeframe.confluence_score || 0) * 100}%
                    </span>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No technical analysis available</div>
          )}
        </div>

        {/* === Risk Manager === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('risk_manager').avatar} alt={getTeamMember('risk_manager').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('risk_manager').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('risk_manager').title}</div>
              </div>
            </div>
            <span style={{
              fontSize: '.65rem',
              fontFamily: 'var(--mono)',
              fontWeight: 700,
              color: riskColor,
              padding: '2px 8px',
              background: `${riskColor}18`,
              borderRadius: '4px',
            }}>
              {riskLevel.toUpperCase()}
            </span>
          </div>

          {riskLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Assessing risk...</div>
          ) : riskData ? (
            <div>
              {/* Risk Level Gauge */}
              <div style={{ marginBottom: '1rem', textAlign: 'center' }}>
                <div style={{
                  fontSize: '1.75rem',
                  fontWeight: 900,
                  fontFamily: 'var(--mono)',
                  color: riskColor,
                  lineHeight: 1,
                }}>
                  {riskLevel.toUpperCase()}
                </div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-dim)', marginTop: '.25rem', fontFamily: 'var(--mono)' }}>Portfolio Risk Level</div>
              </div>

              {/* Risk Explanation */}
              <div style={{ fontSize: '.72rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.6, padding: '.6rem .8rem', background: 'var(--bg-hover)', borderRadius: '6px', marginBottom: '.75rem', borderLeft: '3px solid ' + riskColor }}>
                {(riskData as any).reasoning || 'Monitoring portfolio risk exposure and limits.'}
              </div>

              {/* Risk Metrics */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem', marginBottom: '.75rem' }}>
                {[
                  { label: 'Daily P&L', value: `$${((riskData as any).daily_pnl ?? 0).toFixed(2)}`, color: ((riskData as any).daily_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' },
                  { label: 'Exposure', value: `${((riskData as any).exposure_pct_of_capital ?? 0).toFixed(1)}%`, color: 'var(--text)' },
                  { label: 'Concentration', value: ((riskData as any).concentration_risk ?? 'N/A').toUpperCase(), color: 'var(--text)' },
                ].map(item => (
                  <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '.7rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{item.label}</span>
                    <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: item.color }}>{item.value}</span>
                  </div>
                ))}
              </div>

              {/* Recommendations */}
              {((riskData as any).recommendations as string[])?.length > 0 && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>Recommendations</div>
                  {((riskData as any).recommendations as string[]).map((rec: string, i: number) => (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'flex-start', gap: '.4rem',
                      fontSize: '.7rem', color: 'var(--text-secondary)',
                      padding: '.3rem .5rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '4px',
                      marginBottom: '.3rem',
                      lineHeight: 1.5,
                    }}>
                      <AlertTriangle size={10} style={{ color: 'var(--amber)', flexShrink: 0, marginTop: '2px' }} />
                      {rec}
                    </div>
                  ))}
                </div>
              )}

              {/* Reasoning */}
              {/* {(riskData as any).reasoning && (
                <div style={{ marginTop: '.75rem', fontSize: '.68rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.5, padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '4px', borderLeft: '2px solid var(--border)' }}>
                  {(riskData as any).reasoning}
                </div>
              )} */}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No risk data available</div>
          )}
        </div>

        {/* === CIO Report === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('cio_agent').avatar} alt={getTeamMember('cio_agent').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('cio_agent').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('cio_agent').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>OVERSIGHT</span>
          </div>

          {cioLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Generating report...</div>
          ) : cioReport ? (
            <div>
              {/* Sentiment */}
              <div style={{ textAlign: 'center', marginBottom: '1rem' }}>
                <div style={{ fontSize: '1rem', fontWeight: 800, fontFamily: 'var(--mono)', color: sentimentColor, textTransform: 'uppercase' }}>
                  {sentiment.replace('_', ' ')}
                </div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>CIO Sentiment</div>
              </div>

              {/* Executive Summary */}
              {(cioReport as any).executive_summary && (
                <div style={{ marginBottom: '1rem', fontSize: '.72rem', color: 'var(--text-secondary)', lineHeight: 1.6, padding: '.5rem .75rem', background: 'var(--bg-hover)', borderRadius: '6px' }}>
                  {(cioReport as any).executive_summary}
                </div>
              )}

              {/* Strategic Recommendations */}
              {((cioReport as any).strategic_recommendations as any[])?.length > 0 && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.5rem' }}>Strategic Actions</div>
                  {((cioReport as any).strategic_recommendations as any[]).slice(0, 3).map((rec: any, i: number) => (
                    <div key={i} style={{
                      padding: '.5rem .75rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '6px',
                      marginBottom: '.4rem',
                      borderLeft: `2px solid ${rec.confidence > 0.7 ? 'var(--accent)' : 'var(--border)'}`,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.2rem' }}>
                        <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--text)', textTransform: 'capitalize' }}>
                          {rec.recommendation?.replace(/_/g, ' ')}
                        </span>
                        <span style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                          {(rec.confidence * 100).toFixed(0)}% conf
                        </span>
                      </div>
                      <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{rec.rationale}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No CIO report available</div>
          )}
        </div>

        {/* === Performance Attribution === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <BarChart3 size={14} />
              Performance Attribution
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>BY AGENT</span>
          </div>

          {attrLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Computing attribution...</div>
          ) : attribution ? (
            <div>
              {/* Total P&L */}
              <div style={{ textAlign: 'center', marginBottom: '1rem' }}>
                <div style={{
                  fontSize: '1.5rem',
                  fontWeight: 900,
                  fontFamily: 'var(--mono)',
                  color: ((attribution as any).total_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)',
                }}>
                  {((attribution as any).total_pnl ?? 0) >= 0 ? '+' : ''}{((attribution as any).total_pnl ?? 0).toFixed(2)}
                </div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>Total Fund P&L</div>
              </div>

              {/* Agent contributions */}
              {Object.keys((attribution as any).agent_contributions ?? {}).length > 0 ? (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>Agent Contributions</div>
                  {Object.entries((attribution as any).agent_contributions as Record<string, number>)
                    .sort(([, a], [, b]) => b - a)
                    .map(([agentId, pnl]: [string, number]) => (
                      <div key={agentId} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.35rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                          {pnl >= 0 ? <ChevronUp size={12} style={{ color: 'var(--green)' }} /> : <ChevronDown size={12} style={{ color: 'var(--red)' }} />}
                          <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>{agentId.substring(0, 12)}...</span>
                        </div>
                        <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                        </span>
                      </div>
                    ))
                  }
                </div>
              ) : (
                <div style={{ color: 'var(--text-dim)', fontSize: '.7rem', textAlign: 'center', padding: '1rem', fontFamily: 'var(--mono)' }}>No agent data yet</div>
              )}

              {/* Strategy breakdown */}
              {Object.keys((attribution as any).strategy_contributions ?? {}).length > 0 && (
                <div style={{ marginTop: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>By Strategy</div>
                  {Object.entries((attribution as any).strategy_contributions as Record<string, number>).map(([strat, pnl]: [string, number]) => (
                    <div key={strat} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.3rem' }}>
                      <span style={{ fontSize: '.7rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'capitalize' }}>{strat}</span>
                      <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No attribution data</div>
          )}
        </div>

        {/* === Capital Allocation === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('portfolio_manager').avatar} alt={getTeamMember('portfolio_manager').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('portfolio_manager').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('portfolio_manager').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>ALLOCATION</span>
          </div>

          {allocLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Calculating allocation...</div>
          ) : allocation ? (
            <div>
              {/* Allocation chart */}
              {Object.keys((allocation as any).allocation_pct ?? {}).length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
                  {Object.entries((allocation as any).allocation_pct as Record<string, number>)
                    .sort(([, a], [, b]) => b - a)
                    .map(([agentId, pct]: [string, number]) => (
                      <div key={agentId}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.35rem' }}>
                          <span style={{ fontSize: '.75rem', fontFamily: 'var(--sans)', color: 'var(--text)', fontWeight: 500, flexShrink: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{getAgentName(agentId)}</span>
                          <span style={{ fontSize: '.8rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--accent)', flexShrink: 0, marginLeft: '.5rem' }}>{pct.toFixed(1)}%</span>
                        </div>
                        <div style={{ height: '14px', background: 'var(--bg-hover)', borderRadius: '4px', overflow: 'hidden', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.2)' }}>
                          <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: 'linear-gradient(90deg, var(--accent), var(--accent-bright))', borderRadius: '4px', transition: 'width 0.3s ease' }} />
                        </div>
                      </div>
                    ))}
                </div>
              ) : (
                <div style={{ color: 'var(--text-dim)', fontSize: '.7rem', textAlign: 'center', padding: '1.5rem', fontFamily: 'var(--mono)' }}>No agents to allocate</div>
              )}

              {/* PM Reasoning */}
              {(allocation as any).reasoning && (
                <div style={{ marginTop: '.75rem', fontSize: '.68rem', color: 'var(--text-secondary)', lineHeight: 1.5, padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '4px', borderLeft: '2px solid var(--accent)' }}>
                  {(allocation as any).reasoning}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No allocation data</div>
          )}
        </div>

        {/* === Agent Leaderboard (from CIO) === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Users size={14} />
              Agent Leaderboard
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>RANKED BY P&L</span>
          </div>

          {cioLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Ranking agents...</div>
          ) : ((cioReport as any)?.agent_leaderboard as any[])?.length > 0 ? (
            <div>
              {((cioReport as any).agent_leaderboard as any[]).map((entry: any, i: number) => (
                <div key={entry.agent_id} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '.45rem .6rem',
                  background: i === 0 ? 'rgba(0, 194, 255, .05)' : 'var(--bg-hover)',
                  borderRadius: '5px',
                  marginBottom: '.35rem',
                  borderLeft: i === 0 ? '2px solid var(--accent)' : '2px solid transparent',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                    <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: i === 0 ? 'var(--accent)' : 'var(--text-dim)', minWidth: '14px' }}>#{i + 1}</span>
                    <div>
                      <div style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--text)' }}>{entry.agent_name ?? entry.agent_id?.substring(0, 10)}</div>
                      <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{(entry.win_rate * 100).toFixed(0)}% WR · {entry.total_runs} runs</div>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{
                      fontSize: '.8rem',
                      fontFamily: 'var(--mono)',
                      fontWeight: 700,
                      color: entry.total_pnl >= 0 ? 'var(--green)' : 'var(--red)',
                    }}>
                      {entry.total_pnl >= 0 ? '+' : ''}{entry.total_pnl.toFixed(2)}
                    </div>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{entry.contribution_pct.toFixed(1)}% of fund</div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1.5rem' }}>
              <Users size={28} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
              No agent data yet. Agents need to run first.
            </div>
          )}
        </div>

      </div>

      {/* Daily Report Panel */}
      <DailyReportPanel />
    </div>
  );
}

export default App;
