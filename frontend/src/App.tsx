import { useState } from 'react';
import { Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { useWebSocket } from './hooks/useWebSocket';
import { useMarketStream } from './hooks/useMarketStream';
import { useWsQueryInvalidation } from './hooks/useQueries';
import { useTeamChatStream } from './hooks/useTeamChatStream';
import { useBrowserNotifications } from './hooks/useBrowserNotifications';
import { useEventSounds } from './hooks/useEventSounds';
import { Sidebar } from './components/layout/Sidebar';
import { WsIndicator } from './components/common/WsIndicator';
import { TeamChatToasts } from './components/TeamChatToasts';
import { LiveModeBanner } from './components/LiveModeBanner';
import { DashboardPage } from './pages/DashboardPage';
import { TradingPage } from './pages/TradingPage';
import { AgentsPage } from './pages/AgentsPage';
import { AutomationPage } from './pages/AutomationPage';
import { HistoryPage } from './pages/HistoryPage';
import { FundTeamPage } from './pages/FundTeamPage';
import { TradersPage } from './pages/TradersPage';
import { FirmAdvisorPage } from './pages/FirmAdvisorPage';
import { WalletPage } from './pages/WalletPage';
import { SettingsPage } from './pages/SettingsPage';
import { WhalePage } from './pages/WhalePage';
import { WorkflowsPage } from './pages/WorkflowsPage';
import { useAppSelector, useAppDispatch } from './store/hooks';
import { setSidebarOpen } from './store/slices/uiSlice';
import './index.css';

const routeToPage: Record<string, string> = {
  '/': 'dashboard',
  '/trading': 'trading',
  '/agents': 'agents',
  '/automation': 'automation',
  '/history': 'history',
  '/fund-team': 'fundteam',
  '/traders': 'traders',
  '/advisor': 'advisor',
  '/wallet': 'wallet',
  '/whales': 'whales',
  '/workflows': 'workflows',
  '/settings': 'settings',
};

function App() {
  const [timeframe, setTimeframe] = useState('1h');
  useAppSelector((s) => s.ui.sidebarOpen);
  const dispatch = useAppDispatch();
  const location = useLocation();
  const navigate = useNavigate();

  useWebSocket();
  useMarketStream(timeframe);
  useWsQueryInvalidation();
  useTeamChatStream();
  useBrowserNotifications();
  useEventSounds();

  const activePage = routeToPage[location.pathname] || 'dashboard';
  const handleNavigate = (page: string) => {
    const path = Object.entries(routeToPage).find(([, v]) => v === page)?.[0] || '/';
    navigate(path);
  };

  return (
    <div className="app-container">
      <TeamChatToasts />

      <Sidebar activePage={activePage} onNavigate={handleNavigate} />

      <main className="main-content">
        <LiveModeBanner />
        <div className="mobile-header">
          <button type="button" onClick={() => dispatch(setSidebarOpen(true))} className="mobile-menu-btn">
            <Menu size={22} />
          </button>
          <span className="sidebar-logo">AAAAAAAI</span>PX
          <WsIndicator />
        </div>

        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/trading" element={<TradingPage timeframe={timeframe} onTimeframeChange={setTimeframe} />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/automation" element={<AutomationPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/fund-team" element={<FundTeamPage />} />
          <Route path="/traders" element={<TradersPage />} />
          <Route path="/advisor" element={<FirmAdvisorPage />} />
          <Route path="/wallet" element={<WalletPage />} />
          <Route path="/whales" element={<WhalePage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
