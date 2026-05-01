import { useEffect, useState } from 'react';
import { Activity, Bot, Wallet, Settings, X, TrendingUp, History, Zap, Users, MessageCircle, BarChart2, GitBranch, Clock } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../../store/hooks';
import { setSidebarOpen } from '../../store/slices/uiSlice';
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

function getVolumeTier(now: Date): 'high' | 'good' | 'low' {
  const utcHour = new Date().getUTCHours();
  
  // NY + London overlap (14:00-16:00 UTC) - peak volume
  if (utcHour >= 14 && utcHour < 16) return 'high';
  
  // London session (08:00-16:00 UTC)
  if (utcHour >= 8 && utcHour < 16) return 'good';
  
  // NY session (14:00-21:00 UTC)
  if (utcHour >= 14 && utcHour < 21) return 'good';
  
  // Everything else is low volume (dead zones)
  return 'low';
}

export function Sidebar({ activePage, onNavigate }: SidebarProps) {
  const [clockNow, setClockNow] = useState(() => new Date());
  const dispatch = useAppDispatch();
  const sidebarOpen = useAppSelector((s) => s.ui.sidebarOpen);
  const signal = useAppSelector((s) => s.market.signal);
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);

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

  const closeSidebar = () => dispatch(setSidebarOpen(false));

  const navigate = (page: string) => {
    onNavigate(page);
    closeSidebar();
  };

  return (
    <>
      <button
        className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''}`}
        onClick={closeSidebar}
        onKeyDown={(e) => e.key === 'Escape' && closeSidebar()}
        aria-label="Close sidebar"
        type="button"
      />

      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <span className="sidebar-logo">
            PX<span>·</span>AI
          </span>
          <button type="button" onClick={closeSidebar} className="header-btn">
            <X size={16} />
          </button>
        </div>

        <div className="sidebar-market-clocks">
          {(() => {
            const tier = getVolumeTier(clockNow);
            return (
              <div className={`volume-indicator volume-${tier}`}>
                <span className="volume-label">Volume</span>
                <span className="volume-tier">
                  {tier === 'high' ? '● High' : tier === 'good' ? '○ Good' : '○ Low'}
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

          {/* Settings */}
          <button type="button" onClick={() => navigate('settings')} className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}>
            <Settings size={16} />
            <span>Settings</span>
          </button>
        </nav>

        <SidebarTeamFeed onNavigate={navigate} />

        <SidebarTicker />
      </aside>
    </>
  );
}
