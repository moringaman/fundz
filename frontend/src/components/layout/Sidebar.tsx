import { useEffect, useState } from 'react';
import { Activity, Bot, Wallet, Settings, TrendingUp, History, Zap, Users, MessageCircle, BarChart2, GitBranch, Clock, PiggyBank, PanelLeftClose, PanelLeftOpen, LogOut } from 'lucide-react';
import { useClerk } from '@clerk/clerk-react';
import { useAppSelector, useAppDispatch } from '../../store/hooks';
import { setSidebarOpen, toggleSidebarCollapsed } from '../../store/slices/uiSlice';
import { useAutomationStatus, useAgents, usePaperOrders, useTradeHistory, useFundTeamStatus } from '../../hooks/useQueries';
import { pendingOrderApi } from '../../lib/api';
import { NavBadge } from '../common/NavBadge';
import { SidebarTicker } from '../common/SidebarTicker';
import { SidebarTeamFeed } from '../common/SidebarTeamFeed';

interface SidebarProps {
  activePage: string;
  onNavigate: (page: string) => void;
}

const MARKET_CLOCKS = [
  { label: 'London', timeZone: 'Europe/London', hours: [{ start: 8, end: 16 }] },
  { label: 'New York', timeZone: 'America/New_York', hours: [{ start: 9, end: 16 }] },
  { label: 'Tokyo', timeZone: 'Asia/Tokyo', hours: [{ start: 0, end: 6 }, { start: 9, end: 15 }] },
];

function formatMarketTime(now: Date, timeZone: string) {
  return new Intl.DateTimeFormat([], {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
  }).format(now);
}

function getHourInTimeZone(now: Date, timeZone: string): number {
  return parseInt(
    new Intl.DateTimeFormat([], { hour: 'numeric', hour12: false, timeZone }).format(now)
  );
}

function getVolumeTier(klines: { volume: number }[]): 'high' | 'good' | 'low' {
  if (klines.length < 48) {
    return 'good';
  }
  const recent = klines.slice(-24).reduce((a, b) => a + b.volume, 0);
  const prior = klines.slice(-48, -24).reduce((a, b) => a + b.volume, 0);
  if (prior <= 0) return 'good';
  const ratio = recent / prior;
  if (ratio >= 1.5) return 'high';
  if (ratio >= 0.7) return 'good';
  return 'low';
}

function fmtVolume(volume24h: number | undefined): string {
  if (!volume24h || volume24h <= 0) return '—';
  if (volume24h >= 1_000_000_000) return `$${(volume24h / 1_000_000_000).toFixed(2)}B`;
  if (volume24h >= 1_000_000) return `$${(volume24h / 1_000_000).toFixed(1)}M`;
  if (volume24h >= 1_000) return `$${(volume24h / 1_000).toFixed(0)}K`;
  return `$${volume24h.toFixed(0)}`;
}

export function Sidebar({ activePage, onNavigate }: SidebarProps) {
  const [clockNow, setClockNow] = useState(() => new Date());
  const dispatch = useAppDispatch();
  const { signOut } = useClerk();
  const sidebarOpen = useAppSelector((s) => s.ui.sidebarOpen);
  const signal = useAppSelector((s) => s.market.signal);
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const tickerVolume = useAppSelector((s) => s.market.ticker?.volume);
  const klines = useAppSelector((s) => s.market.klines);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setClockNow(new Date());
    }, 1000);

    return () => {
      window.clearInterval(timer);
    };
  }, []);

  // Data for sidebar badges
  const { data: automationStatus } = useAutomationStatus();
  const { data: agentsData = [] } = useAgents();
  const { data: tradeHistoryData = [] } = useTradeHistory(undefined, 100);
  const { data: paperOrdersData = [] } = usePaperOrders(undefined, 100);
  const { data: fundTeamStatusData } = useFundTeamStatus();

  const enabledAgentCount = (Array.isArray(agentsData) ? agentsData : []).filter((a: any) => a.is_enabled).length;
  const schedulerRunning = (automationStatus as any)?.scheduler_running ?? false;
  const fundTeamRisk = (fundTeamStatusData as any)?.risk_level ?? 'safe';

  const oneDayAgo = Date.now() - 86_400_000;
  const liveTrades24h = (Array.isArray(tradeHistoryData) ? tradeHistoryData : []).filter(
    (t: any) => new Date(t.created_at ?? t.timestamp ?? 0).getTime() > oneDayAgo
  );
  const paperTrades24h = (Array.isArray(paperOrdersData) ? paperOrdersData : []).filter(
    (t: any) => new Date(t.created_at ?? t.timestamp ?? 0).getTime() > oneDayAgo
  );
  const trades24h = liveTrades24h.length + paperTrades24h.length;

  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    const fetch = async () => {
      try {
        const res = await pendingOrderApi.list();
        setPendingCount(Array.isArray(res.data) ? res.data.length : 0);
      } catch {
        setPendingCount(0);
      }
    };
    fetch();
    const iv = setInterval(fetch, 15000);
    return () => clearInterval(iv);
  }, []);

  const sigAction = signal?.action;

  // On mobile, sidebar is always visible — collapsed strip by default.
  // The hamburger menu toggles between collapsed (56px) and expanded.
  useEffect(() => {
    if (window.innerWidth <= 1024) {
      dispatch(setSidebarOpen(true));
    }
  }, []);

  const closeSidebar = () => {
    // On mobile, collapse instead of closing
    if (window.innerWidth <= 1024) {
      if (!sidebarCollapsed) dispatch(toggleSidebarCollapsed());
    } else {
      dispatch(setSidebarOpen(false));
    }
  };
  const sidebarCollapsed = useAppSelector((s) => s.ui.sidebarCollapsed);
  const toggleCollapsed = () => dispatch(toggleSidebarCollapsed());

  const navigate = (page: string) => {
    onNavigate(page);
    closeSidebar();
  };

  return (
    <>
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''} ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <span className="sidebar-logo">
            PX<span>·</span>AI
          </span>
          <button type="button" onClick={toggleCollapsed} className="header-btn" title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>
        </div>

        <div className="sidebar-market-clocks">
          {(() => {
            const tier = getVolumeTier(klines);
            const volStr = fmtVolume(tickerVolume);
            return (
              <div className={`volume-indicator volume-${tier}`}>
                <span className="volume-label">Volume 24h</span>
                <span className="volume-tier">
                  {tier === 'high' ? '● High' : tier === 'good' ? '○ Med' : '○ Low'} · {volStr}
                </span>
              </div>
            );
          })()}
          {MARKET_CLOCKS.map((market) => {
            const hour = getHourInTimeZone(clockNow, market.timeZone);
            const isOpen = market.hours.some(({ start, end }) =>
              start <= end ? hour >= start && hour < end : hour >= start || hour < end
            );
            return (
              <div key={market.label} className={`sidebar-market-clock ${isOpen ? 'active' : ''}`}>
                <div className="sidebar-market-clock-label">
                  {market.label}
                  {isOpen && <span className="session-dot" />}
                </div>
                <div className="sidebar-market-clock-time">{formatMarketTime(clockNow, market.timeZone)}</div>
              </div>
            );
          })}
        </div>

        <nav className="sidebar-nav">
          {/* Overview */}
          <button type="button" onClick={() => navigate('dashboard')} className={`nav-item ${activePage === 'dashboard' ? 'active' : ''}`}>
            <Activity size={16} />
            <span>Overview</span>
            {pendingCount > 0 && (
              <NavBadge variant="amber"><Clock size={10} style={{ marginRight: 2 }} />{pendingCount}</NavBadge>
            )}
            {sigAction && sigAction !== 'hold' && (
              <NavBadge variant={sigAction === 'buy' ? 'green' : 'red'}>{sigAction.toUpperCase()}</NavBadge>
            )}
          </button>

          {/* Trading */}
          <button type="button" onClick={() => navigate('trading')} className={`nav-item ${activePage === 'trading' ? 'active' : ''}`}>
            <TrendingUp size={16} />
            <span>Trading</span>
            <NavBadge>{selectedSymbol.replace('USDT', '')}</NavBadge>
          </button>

          {/* Agents */}
          <button type="button" onClick={() => navigate('agents')} className={`nav-item ${activePage === 'agents' ? 'active' : ''}`}>
            <Bot size={16} />
            <span>Agents</span>
            {enabledAgentCount > 0
              ? <NavBadge variant="green">{enabledAgentCount} ON</NavBadge>
              : <NavBadge>0 ON</NavBadge>
            }
          </button>

          {/* Automation */}
          <button type="button" onClick={() => navigate('automation')} className={`nav-item ${activePage === 'automation' ? 'active' : ''}`}>
            <Zap size={16} />
            <span>Automation</span>
            <NavBadge variant={schedulerRunning ? 'green' : 'default'}>{schedulerRunning ? 'ON' : 'OFF'}</NavBadge>
          </button>

          {/* History */}
          <button type="button" onClick={() => navigate('history')} className={`nav-item ${activePage === 'history' ? 'active' : ''}`}>
            <History size={16} />
            <span>History</span>
            {trades24h > 0
              ? <NavBadge variant="amber">{trades24h} 24h</NavBadge>
              : <NavBadge>0 today</NavBadge>
            }
          </button>

          {/* Fund Team */}
          <button type="button" onClick={() => navigate('fundteam')} className={`nav-item ${activePage === 'fundteam' ? 'active' : ''}`}>
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

          {/* Traders */}
          <button type="button" onClick={() => navigate('traders')} className={`nav-item ${activePage === 'traders' ? 'active' : ''}`}>
            <BarChart2 size={16} />
            <span>Traders</span>
          </button>

          {/* Firm Advisor */}
          <button type="button" onClick={() => navigate('advisor')} className={`nav-item ${activePage === 'advisor' ? 'active' : ''}`}>
            <MessageCircle size={16} />
            <span>Advisor</span>
          </button>

          {/* Wallet */}
          <button type="button" onClick={() => navigate('wallet')} className={`nav-item ${activePage === 'wallet' ? 'active' : ''}`}>
            <Wallet size={16} />
            <span>Wallet</span>
          </button>

          <button type="button" onClick={() => navigate('whales')} className={`nav-item ${activePage === 'whales' ? 'active' : ''}`}>
            <Activity size={16} />
            <span>Whales</span>
          </button>

          <button type="button" onClick={() => navigate('workflows')} className={`nav-item ${activePage === 'workflows' ? 'active' : ''}`}>
            <GitBranch size={16} />
            <span>Workflows</span>
          </button>

          {/* Accumulation */}
          <button type="button" onClick={() => navigate('accumulation')} className={`nav-item ${activePage === 'accumulation' ? 'active' : ''}`}>
            <PiggyBank size={16} />
            <span>Accumulation</span>
          </button>

          {/* Settings */}
          <button type="button" onClick={() => navigate('settings')} className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}>
            <Settings size={16} />
            <span>Settings</span>
          </button>
        </nav>

        <div className="sidebar-footer">
          <SidebarTeamFeed onNavigate={navigate} />
          <SidebarTicker />

          <button
            type="button"
            onClick={() => signOut()}
            className="collapse-btn header-btn"
            title="Sign Out"
            style={{ marginBottom: '0.25rem', color: 'var(--text-dim)' }}
          >
            <LogOut size={14} />
            {!sidebarCollapsed && <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem' }}>Sign Out</span>}
          </button>

          <button type="button" onClick={toggleCollapsed} className="collapse-btn header-btn" title={sidebarCollapsed ? 'Expand' : 'Collapse'}>
            {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>
        </div>
      </aside>
    </>
  );
}
