import { useState } from 'react';
import {
  useTradeHistory,
  usePaperOrders,
  usePnl,
  usePaperPnl,
  usePaperPositions,
  useAgents,
} from '../hooks/useQueries';
import { timeAgo } from '../utils/timeAgo';

export function HistoryPage() {
  const [tab, setTab] = useState<'paper' | 'live'>('paper');

  const { data: trades = [] } = useTradeHistory();
  const { data: pnl } = usePnl();
  const { data: paperTrades = [] } = usePaperOrders();
  const { data: paperPnl } = usePaperPnl();
  const { data: paperPositions = [] } = usePaperPositions();
  const { data: agentsData = [] } = useAgents();

  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const activePnl = tab === 'paper' ? paperPnl : pnl;
  const activeTrades: any[] = tab === 'paper'
    ? (Array.isArray(paperTrades) ? paperTrades : [])
    : (Array.isArray(trades) ? trades : []);
  const positions: any[] = Array.isArray(paperPositions) ? paperPositions : [];

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
            <p className="stat-label">Unrealized P&L</p>
            <p className={`stat-value ${(activePnl.unrealized_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`}>
              ${activePnl.unrealized_pnl?.toFixed(2) || '0.00'}
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
        </div>
      )}

      {/* Open Positions */}
      {tab === 'paper' && positions.length > 0 && (
        <div className="card">
          <h2 className="card-title">Open Positions ({positions.length})</h2>
          <div className="trades-table">
            <div className="trades-header" style={{ gridTemplateColumns: '1fr 0.5fr 0.7fr 0.9fr 0.9fr 0.8fr 0.8fr 1fr 0.7fr 0.9fr' }}>
              <span>Symbol</span><span>Side</span><span>Qty</span>
              <span>Entry Price</span><span>Current Price</span>
              <span>Stop Loss</span><span>Take Profit</span>
              <span>Unrealized P&L</span><span>P&L %</span><span>Agent</span>
            </div>
            {positions.map((pos: any) => (
              <div key={pos.symbol} className="trades-row" style={{ gridTemplateColumns: '1fr 0.5fr 0.7fr 0.9fr 0.9fr 0.8fr 0.8fr 1fr 0.7fr 0.9fr' }}>
                <span style={{ fontWeight: 600 }}>{pos.symbol}</span>
                <span className={pos.side === 'buy' ? 'positive' : 'negative'}>{pos.side?.toUpperCase()}</span>
                <span>{pos.quantity?.toFixed(6)}</span>
                <span>${pos.entry_price?.toFixed(2)}</span>
                <span>${pos.current_price?.toFixed(2)}</span>
                <span style={{ color: 'var(--red)', fontSize: '.75rem', fontFamily: 'var(--mono)' }}>
                  {pos.stop_loss_price ? `$${pos.stop_loss_price.toFixed(2)}` : '—'}
                </span>
                <span style={{ color: 'var(--green)', fontSize: '.75rem', fontFamily: 'var(--mono)' }}>
                  {pos.take_profit_price ? `$${pos.take_profit_price.toFixed(2)}` : '—'}
                </span>
                <span className={pos.unrealized_pnl >= 0 ? 'positive' : 'negative'} style={{ fontWeight: 600 }}>
                  {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl?.toFixed(2)}
                </span>
                <span className={pos.unrealized_pnl_pct >= 0 ? 'positive' : 'negative'}>
                  {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct?.toFixed(2)}%
                </span>
                <span className="text-gray-300">{agentName(pos.agent_id)}</span>
              </div>
            ))}
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
