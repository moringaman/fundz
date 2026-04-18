import { useEffect, useState } from 'react';
import { Activity, Bot, Wallet, Settings, X, TrendingUp, History, Zap, Users, MessageCircle, BarChart2, GitBranch } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../../store/hooks';
import { setSidebarOpen } from '../../store/slices/uiSlice';
import { useAutomationStatus, useAgents, usePaperOrders, useTradeHistory, useFundTeamStatus } from '../../hooks/useQueries';
import { NavBadge } from '../common/NavBadge';
import { SidebarTicker } from '../common/SidebarTicker';
import { SidebarTeamFeed } from '../common/SidebarTeamFeed';

interface SidebarProps {
  activePage: string;
  onNavigate: (page: string) => void;
}

const MARKET_CLOCKS = [
  { label: 'London', timeZone: 'Europe/London' },
  { label: 'New York', timeZone: 'America/New_York' },
  { label: 'Tokyo', timeZone: 'Asia/Tokyo' },
];

function formatMarketTime(now: Date, timeZone: string) {
  return new Intl.DateTimeFormat([], {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
  }).format(now);
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
          {MARKET_CLOCKS.map((market) => (
            <div key={market.label} className="sidebar-market-clock">
              <div className="sidebar-market-clock-label">{market.label}</div>
              <div className="sidebar-market-clock-time">{formatMarketTime(clockNow, market.timeZone)}</div>
            </div>
          ))}
        </div>

        <nav className="sidebar-nav">
          {/* Overview */}
          <button type="button" onClick={() => navigate('dashboard')} className={`nav-item ${activePage === 'dashboard' ? 'active' : ''}`}>
            <Activity size={16} />
            <span>Overview</span>
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
