import { Activity, Bot, Wallet, Settings, X, TrendingUp, History, Zap, Users, MessageCircle } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../../store/hooks';
import { setSidebarOpen } from '../../store/slices/uiSlice';
import { useAutomationStatus, useAgents, usePaperOrders, useTradeHistory, useFundTeamStatus } from '../../hooks/useQueries';
import { NavBadge } from '../common/NavBadge';
import { SidebarTicker } from '../common/SidebarTicker';

interface SidebarProps {
  activePage: string;
  onNavigate: (page: string) => void;
}

export function Sidebar({ activePage, onNavigate }: SidebarProps) {
  const dispatch = useAppDispatch();
  const sidebarOpen = useAppSelector((s) => s.ui.sidebarOpen);
  const signal = useAppSelector((s) => s.market.signal);
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);

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

          {/* Settings */}
          <button type="button" onClick={() => navigate('settings')} className={`nav-item ${activePage === 'settings' ? 'active' : ''}`}>
            <Settings size={16} />
            <span>Settings</span>
          </button>
        </nav>

        <SidebarTicker />
      </aside>
    </>
  );
}
