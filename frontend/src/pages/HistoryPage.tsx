import { useState, useEffect } from 'react';
import {
  useTradeHistory,
  usePaperOrders,
  usePnl,
  usePaperPnl,
  usePaperPositions,
  useClosedTrades,
  useAgents,
  useUpdatePositionSlTp,
  useClosePosition,
  useTraders,
  useTradingMode,
} from '../hooks/useQueries';
import { timeAgo } from '../utils/timeAgo';
import { formatPrice, formatPnl, formatPnlPct } from '../utils/formatPrice';
import { usePagination, Paginator } from '../components/common/Paginator';
import { SkeletonStats, SkeletonTable } from '../components/common/Skeleton';
import { PositionEmptyState } from '../components/PositionsTable';

export function HistoryPage() {
  const { isPaper } = useTradingMode();
  const [tab, setTab] = useState<'paper' | 'live'>('paper');
  const [view, setView] = useState<'closed' | 'orders'>('closed');

  // Auto-switch tab when global trading mode changes
  useEffect(() => {
    setTab(isPaper ? 'paper' : 'live');
  }, [isPaper]);

  const { data: trades = [] } = useTradeHistory();
  const { data: pnl } = usePnl();
  const { data: paperTrades = [] } = usePaperOrders();
  const { data: paperPnl } = usePaperPnl();
  const { data: paperPositions = [] } = usePaperPositions();
  const { data: closedTrades = [], isPending: closedLoading } = useClosedTrades();
  const { data: agentsData = [] } = useAgents();
  const { data: tradersData = [] } = useTraders();

  // SL/TP inline editing
  const updateSlTp = useUpdatePositionSlTp();
  const closePos = useClosePosition();
  const [editingPos, setEditingPos] = useState<string | null>(null);
  const [closingPos, setClosingPos] = useState<string | null>(null);
  const [editSL, setEditSL] = useState('');
  const [editTP, setEditTP] = useState('');

  const startEdit = (pos: any) => {
    setEditingPos(pos.id);
    setEditSL(pos.stop_loss_price != null ? String(pos.stop_loss_price) : '');
    setEditTP(pos.take_profit_price != null ? String(pos.take_profit_price) : '');
  };

  const cancelEdit = () => { setEditingPos(null); setEditSL(''); setEditTP(''); };

  const saveEdit = (posId: string) => {
    const payload: any = {};
    if (editSL !== '') payload.stop_loss_price = parseFloat(editSL);
    if (editTP !== '') payload.take_profit_price = parseFloat(editTP);
    if (!payload.stop_loss_price && !payload.take_profit_price) { cancelEdit(); return; }
    updateSlTp.mutate({ positionId: posId, ...payload }, { onSuccess: () => cancelEdit() });
  };

  const agents: any[] = Array.isArray(agentsData) ? agentsData : [];
  const activePnl = tab === 'paper' ? paperPnl : pnl;
  const activeTrades: any[] = tab === 'paper'
    ? (Array.isArray(paperTrades) ? paperTrades : [])
    : (Array.isArray(trades) ? trades : []);
  const positions: any[] = Array.isArray(paperPositions) ? paperPositions : [];
  const closed: any[] = Array.isArray(closedTrades) ? closedTrades : [];

  // Pagination
  const closedPager = usePagination(closed, 10);
  const ordersPager = usePagination(activeTrades, 10);

  const agentName = (id: string | null) => {
    if (!id) return '-';
    return agents.find((a: any) => a.id === id)?.name || id.slice(0, 8) + '...';
  };

  const agentStrategy = (id: string | null) => {
    if (!id) return '-';
    return agents.find((a: any) => a.id === id)?.strategy_type || '-';
  };

  const traders: any[] = Array.isArray(tradersData) ? tradersData : [];
  const traderMap: Record<string, any> = {};
  for (const t of traders) traderMap[t.id] = t;

  const traderName = (agentId: string | null) => {
    if (!agentId) return '-';
    const agent = agents.find((a: any) => a.id === agentId);
    if (!agent?.trader_id) return '-';
    const t = traderMap[agent.trader_id];
    return t ? `${t.config?.avatar || '🤖'} ${t.name}` : '-';
  };

  // Closed-trade stats
  const wins = closed.filter((t: any) => t.result === 'win');
  const losses = closed.filter((t: any) => t.result === 'loss');
  const totalNetPnl = closed.reduce((s: number, t: any) => s + (t.net_pnl || 0), 0);
  const avgWin = wins.length ? wins.reduce((s: number, t: any) => s + t.net_pnl, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s: number, t: any) => s + t.net_pnl, 0) / losses.length : 0;
  const winRate = closed.length ? (wins.length / closed.length * 100) : 0;

  return (
    <div className="space-y-6" style={{ margin: '1.75rem'}}>
      <h1 className="page-title" style={{ marginTop: '2rem'}}>Trade History</h1>

      <div className="tab-row">
        <button type="button" className={`tab-btn ${tab === 'paper' ? 'active' : ''}`} onClick={() => setTab('paper')}>
          Paper Trades
        </button>
        <button type="button" className={`tab-btn ${tab === 'live' ? 'active' : ''}`} onClick={() => setTab('live')}>
          Live Trades
        </button>
      </div>

      {closedLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <SkeletonStats count={5} />
          <SkeletonTable rows={8} cols={8} />
        </div>
      ) : (
      <>

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
          {tab === 'paper' && closed.length > 0 && (
            <>
              <div className="stat-card">
                <p className="stat-label">Win Rate</p>
                <p className={`stat-value ${winRate >= 50 ? 'positive' : 'negative'}`}>
                  {winRate.toFixed(1)}%
                </p>
              </div>
              <div className="stat-card">
                <p className="stat-label">Avg Win</p>
                <p className="stat-value positive">{formatPnl(avgWin)}</p>
              </div>
              <div className="stat-card">
                <p className="stat-label">Avg Loss</p>
                <p className="stat-value negative">{formatPnl(avgLoss)}</p>
              </div>
            </>
          )}
        </div>
      )}

      {/* Open Positions */}
      {tab === 'paper' && (
        <div className="card">
          <h2 className="card-title">Open Positions ({positions.length})</h2>
          {positions.length === 0 ? (
            <PositionEmptyState isPaper={tab === 'paper'} />
          ) : (
          <div className="trades-table">
            <div className="trades-header" style={{ gridTemplateColumns: '1fr 0.5fr 0.7fr 0.9fr 0.9fr 0.7fr 0.9fr 0.9fr 1fr 1fr 0.9fr 0.7fr 0.7fr 0.7fr 0.6fr' }}>
              <span>Symbol</span><span>Side</span><span>Qty</span>
              <span>Entry Price</span><span>Current Price</span>
              <span>Lev</span><span>Margin</span><span>Liq Price</span>
              <span>Stop Loss</span><span>Take Profit</span>
              <span>Unrealized P&L</span><span>P&L %</span><span>Trader</span><span>Strategy</span><span></span>
            </div>
            {positions.map((pos: any) => {
              const danger = pos.sl_danger ?? 'safe';
              const isCritical = danger === 'critical';
              const isWarning = danger === 'warning';
              const rowBg = isCritical
                ? 'rgba(231,76,60,0.10)'
                : isWarning
                ? 'rgba(243,156,18,0.08)'
                : undefined;
              const rowBorder = isCritical
                ? '1px solid rgba(231,76,60,0.45)'
                : isWarning
                ? '1px solid rgba(243,156,18,0.3)'
                : undefined;
              return (
              <div key={pos.id || pos.symbol} className="trades-row" style={{ gridTemplateColumns: '1fr 0.5fr 0.7fr 0.9fr 0.9fr 0.7fr 0.9fr 0.9fr 1fr 1fr 0.9fr 0.7fr 0.7fr 0.7fr 0.6fr', background: rowBg, borderRadius: rowBorder ? 6 : undefined, outline: rowBorder }}>
                <span style={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: '.3rem' }}>
                  {isCritical && (
                    <span title="Stop-out imminent — within 1% of stop loss" style={{
                      fontSize: '.75rem', animation: 'pulse 1s infinite',
                    }}>🚨</span>
                  )}
                  {isWarning && !isCritical && (
                    <span title="Approaching stop loss — within 2.5%" style={{ fontSize: '.75rem' }}>⚠️</span>
                  )}
                  {pos.symbol}
                </span>
                <span className={pos.side === 'buy' ? 'positive' : 'negative'}>{pos.side?.toUpperCase()}</span>
                <span>{pos.quantity?.toFixed(6)}</span>
                <span>${formatPrice(pos.entry_price)}</span>
                <span>${formatPrice(pos.current_price)}</span>
                <span style={{
                  fontFamily: 'var(--mono)',
                  fontSize: '.74rem',
                  color: (pos.leverage ?? 1) > 1 ? 'var(--amber, #f39c12)' : 'var(--text-muted)'
                }}>
                  {(pos.leverage ?? 1).toFixed(1)}x
                </span>
                <span style={{ fontFamily: 'var(--mono)', fontSize: '.74rem' }}>
                  {pos.margin_used != null ? `$${Number(pos.margin_used).toFixed(2)}` : '—'}
                </span>
                <span style={{
                  fontFamily: 'var(--mono)',
                  fontSize: '.74rem',
                  color: pos.liquidation_price != null
                    && Math.abs((pos.current_price ?? 0) - pos.liquidation_price) / Math.max(pos.current_price ?? 1, 1) < 0.12
                    ? 'var(--red, #e74c3c)'
                    : 'var(--text-primary)'
                }}>
                  {pos.liquidation_price != null ? `$${formatPrice(pos.liquidation_price)}` : '—'}
                </span>

                {/* ── Stop Loss (editable) ── */}
                <span style={{ display: 'flex', flexDirection: 'column', gap: '.1rem' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '.25rem' }}>
                  {editingPos === pos.id ? (
                    <input
                      type="number" step="0.01" value={editSL}
                      onChange={e => setEditSL(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && saveEdit(pos.id)}
                      style={{
                        width: '100%', padding: '.2rem .35rem', borderRadius: 4,
                        border: '1px solid var(--red)', background: 'var(--bg-elevated)',
                        color: 'var(--red)', fontSize: '.75rem', fontFamily: 'var(--mono)',
                      }}
                      placeholder="SL price"
                      autoFocus
                    />
                  ) : (
                    <span
                      onClick={() => startEdit(pos)}
                      title="Click to edit SL/TP"
                      style={{
                        color: isCritical ? '#ff4d4d' : isWarning ? '#f39c12' : 'var(--red)',
                        fontSize: '.75rem', fontFamily: 'var(--mono)', cursor: 'pointer',
                        borderBottom: `1px dashed ${isCritical ? '#ff4d4d' : isWarning ? '#f39c12' : 'var(--red)'}`,
                        paddingBottom: 1, fontWeight: isCritical ? 700 : undefined,
                      }}
                    >
                      {pos.stop_loss_price ? `$${formatPrice(pos.stop_loss_price)}` : '— set'}
                    </span>
                  )}
                  </span>
                  {pos.distance_to_sl_pct != null && editingPos !== pos.id && (
                    <span style={{
                      fontSize: '.62rem',
                      color: isCritical ? '#ff4d4d' : isWarning ? '#f39c12' : 'var(--text-muted)',
                      fontFamily: 'var(--mono)',
                    }}>
                      {pos.distance_to_sl_pct >= 0
                        ? `${pos.distance_to_sl_pct.toFixed(2)}% away`
                        : `PAST SL`}
                    </span>
                  )}
                  {pos.sl_below_entry && pos.pnl_at_sl != null && editingPos !== pos.id && (
                    <span
                      title="Stop loss is below entry — this position will close at a loss if stopped out"
                      style={{ fontSize: '.62rem', color: '#ff4d4d', fontFamily: 'var(--mono)' }}
                    >
                      loss if hit: ${Math.abs(pos.pnl_at_sl).toFixed(2)}
                    </span>
                  )}
                </span>

                {/* ── Take Profit (editable) ── */}
                <span style={{ display: 'flex', alignItems: 'center', gap: '.25rem' }}>
                  {editingPos === pos.id ? (
                    <>
                      <input
                        type="number" step="0.01" value={editTP}
                        onChange={e => setEditTP(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && saveEdit(pos.id)}
                        style={{
                          width: '60%', padding: '.2rem .35rem', borderRadius: 4,
                          border: '1px solid var(--green)', background: 'var(--bg-elevated)',
                          color: 'var(--green)', fontSize: '.75rem', fontFamily: 'var(--mono)',
                        }}
                        placeholder="TP price"
                      />
                      <button
                        type="button"
                        onClick={() => saveEdit(pos.id)}
                        disabled={updateSlTp.isPending}
                        style={{
                          padding: '.15rem .4rem', borderRadius: 4, border: 'none',
                          background: 'var(--accent)', color: '#fff', fontSize: '.65rem',
                          fontWeight: 700, cursor: 'pointer', opacity: updateSlTp.isPending ? 0.5 : 1,
                        }}
                      >
                        {updateSlTp.isPending ? '…' : '✓'}
                      </button>
                      <button
                        type="button"
                        onClick={cancelEdit}
                        style={{
                          padding: '.15rem .35rem', borderRadius: 4, border: 'none',
                          background: 'var(--surface-2, #2a2d35)', color: 'var(--text-secondary)',
                          fontSize: '.65rem', cursor: 'pointer',
                        }}
                      >
                        ✕
                      </button>
                    </>
                  ) : (
                    <span
                      onClick={() => startEdit(pos)}
                      title="Click to edit SL/TP"
                      style={{ color: 'var(--green)', fontSize: '.75rem', fontFamily: 'var(--mono)', cursor: 'pointer', borderBottom: '1px dashed var(--green)', paddingBottom: 1 }}
                    >
                      {pos.take_profit_price ? `$${formatPrice(pos.take_profit_price)}` : '— set'}
                    </span>
                  )}
                </span>

                <span className={pos.unrealized_pnl >= 0 ? 'positive' : 'negative'} style={{ fontWeight: 600 }}>
                  {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl?.toFixed(2)}
                </span>
                <span className={pos.unrealized_pnl_pct >= 0 ? 'positive' : 'negative'}>
                  {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct?.toFixed(2)}%
                </span>
                <span style={{ fontSize: '.68rem', color: 'var(--accent)' }}>{traderName(pos.agent_id)}</span>
                <span className="text-gray-300" style={{ fontSize: '.72rem' }}>{agentName(pos.agent_id)}</span>
                <span style={{ display: 'flex', justifyContent: 'center' }}>
                  <button
                    type="button"
                    onClick={() => {
                      if (closingPos === pos.id) return;
                      if (!window.confirm(`Close ${pos.symbol} ${pos.side} position at market price?`)) return;
                      setClosingPos(pos.id);
                      closePos.mutate(pos.id, {
                        onSettled: () => setClosingPos(null),
                        onError: (err: unknown) => {
                          const msg = (err as { response?: { data?: { detail?: string } } })
                            ?.response?.data?.detail || 'Failed to close position';
                          alert(msg);
                        },
                      });
                    }}
                    disabled={closingPos === pos.id}
                    title="Close position at market price"
                    style={{
                      padding: '.2rem .5rem', borderRadius: 4, border: 'none',
                      background: 'var(--red, #e74c3c)', color: '#fff', fontSize: '.65rem',
                      fontWeight: 700, cursor: closingPos === pos.id ? 'wait' : 'pointer',
                      opacity: closingPos === pos.id ? 0.5 : 1,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {closingPos === pos.id ? '…' : 'Close'}
                  </button>
                </span>
              </div>
              );
            })}
          </div>
          )}
        </div>
      )}

      {/* Sub-tabs: Closed Trades vs Order Book */}
      {tab === 'paper' && (
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
            <button
              type="button"
              onClick={() => setView('closed')}
              style={{
                padding: '0.35rem 0.9rem',
                borderRadius: '6px',
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: 'pointer',
                border: 'none',
                background: view === 'closed' ? 'var(--accent)' : 'var(--surface-2, #2a2d35)',
                color: view === 'closed' ? '#fff' : 'var(--text-secondary, #aaa)',
              }}
            >
              Closed Trades ({closed.length})
            </button>
            <button
              type="button"
              onClick={() => setView('orders')}
              style={{
                padding: '0.35rem 0.9rem',
                borderRadius: '6px',
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: 'pointer',
                border: 'none',
                background: view === 'orders' ? 'var(--accent)' : 'var(--surface-2, #2a2d35)',
                color: view === 'orders' ? '#fff' : 'var(--text-secondary, #aaa)',
              }}
            >
              Order Book ({activeTrades.length})
            </button>
            {view === 'closed' && closed.length > 0 && (
              <span style={{ marginLeft: 'auto', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                <span className={totalNetPnl >= 0 ? 'positive' : 'negative'} style={{ fontWeight: 700 }}>
                  Net: {formatPnl(totalNetPnl)}
                </span>
                {' · '}
                <span className="positive">{wins.length}W</span>
                {' / '}
                <span className="negative">{losses.length}L</span>
              </span>
            )}
          </div>

          {view === 'closed' ? (
            closed.length === 0 ? (
              <p className="text-gray-400">No closed trades yet. Trades appear here once a sell closes a buy position.</p>
            ) : (
              <>
                <div className="trades-table">
                  <div className="trades-header" style={{ gridTemplateColumns: '0.6fr 0.9fr 0.6fr 0.9fr 0.9fr 0.9fr 0.7fr 0.9fr 0.7fr 0.7fr 0.6fr' }}>
                    <span>Result</span><span>Symbol</span><span>Qty</span>
                    <span>Entry</span><span>Exit</span><span>Net P&L</span>
                    <span>P&L %</span><span>Closed</span><span>Trader</span><span>Strategy</span><span>Fees</span>
                  </div>
                  {closedPager.pageItems.map((t: any, i: number) => (
                    <div
                      key={`${t.symbol}-${t.exit_time}-${i}`}
                      className="trades-row"
                      style={{
                        gridTemplateColumns: '0.6fr 0.9fr 0.6fr 0.9fr 0.9fr 0.9fr 0.7fr 0.9fr 0.7fr 0.7fr 0.6fr',
                        borderLeft: `3px solid ${t.result === 'win' ? 'var(--green)' : t.result === 'loss' ? 'var(--red)' : 'var(--text-secondary)'}`,
                      }}
                    >
                      <span style={{
                        fontWeight: 700,
                        fontSize: '0.75rem',
                        color: t.result === 'win' ? 'var(--green)' : t.result === 'loss' ? 'var(--red)' : 'var(--text-secondary)',
                        textTransform: 'uppercase',
                      }}>
                        {t.result === 'win' ? '✓ WIN' : t.result === 'loss' ? '✗ LOSS' : '— EVEN'}
                      </span>
                      <span style={{ fontWeight: 600 }}>{t.symbol}</span>
                      <span>{t.quantity}</span>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: '0.78rem' }}>${formatPrice(t.entry_price)}</span>
                      <span style={{ fontFamily: 'var(--mono)', fontSize: '0.78rem' }}>${formatPrice(t.exit_price)}</span>
                      <span className={t.net_pnl >= 0 ? 'positive' : 'negative'} style={{ fontWeight: 700 }}>
                        {formatPnl(t.net_pnl)}
                      </span>
                      <span className={t.pnl_pct >= 0 ? 'positive' : 'negative'}>
                        {formatPnlPct(t.pnl_pct)}
                      </span>
                      <span title={t.exit_time ? new Date(t.exit_time).toLocaleString() : ''}>{t.exit_time ? timeAgo(t.exit_time) : '-'}</span>
                      <span style={{ fontSize: '.68rem', color: 'var(--accent)' }}>{traderName(t.agent_id)}</span>
                      <span className="text-gray-300">{agentName(t.agent_id)}</span>
                      <span className="text-gray-400" style={{ fontSize: '0.72rem' }}>${formatPrice(t.fee)}</span>
                    </div>
                  ))}
                </div>
                <Paginator page={closedPager.page} totalPages={closedPager.totalPages} total={closedPager.total} pageSize={10} onPage={closedPager.setPage} label="trades" />
              </>
            )
          ) : (
            /* Order Book (fills/orders) */
            activeTrades.length === 0 ? (
              <p className="text-gray-400">No {tab} orders yet.</p>
            ) : (
              <div className="trades-table">
                <div className="trades-header" style={{ gridTemplateColumns: '1fr 0.9fr 0.6fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 0.7fr 0.9fr 0.9fr 0.9fr 0.8fr' }}>
                  <span>Time</span><span>Symbol</span><span>Side</span><span>Qty</span>
                  <span>Price</span><span>Total</span><span>Leverage</span><span>Margin</span><span>Fee</span>
                  <span>Trader</span><span>Strategy</span><span>Type</span><span>Status</span>
                </div>
                {ordersPager.pageItems.map((trade: any) => (
                  <div key={trade.id} className="trades-row" style={{ gridTemplateColumns: '1fr 0.9fr 0.6fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 0.7fr 0.9fr 0.9fr 0.9fr 0.8fr' }}>
                    <span title={new Date(trade.created_at).toLocaleString()}>{timeAgo(trade.created_at)}</span>
                    <span>{trade.symbol}</span>
                    <span className={trade.side === 'buy' ? 'positive' : 'negative'}>{trade.side?.toUpperCase()}</span>
                    <span>{trade.quantity}</span>
                    <span>${formatPrice(trade.price)}</span>
                    <span>${trade.total?.toFixed(2)}</span>
                    <span style={{ color: (trade.leverage ?? 1) > 1 ? 'var(--amber, #f39c12)' : 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                      {(trade.leverage ?? 1).toFixed(1)}x
                    </span>
                    <span className="text-gray-400" style={{ fontSize: '.72rem', fontFamily: 'var(--mono)' }}>
                      {trade.margin_used != null ? `$${Number(trade.margin_used).toFixed(2)}` : '—'}
                    </span>
                    <span className="text-gray-400">${trade.fee?.toFixed(4) || '0.0000'}</span>
                    <span style={{ fontSize: '.68rem', color: 'var(--accent)' }}>{traderName(trade.agent_id)}</span>
                    <span className="text-gray-300">{agentName(trade.agent_id)}</span>
                    <span className="strategy-tag" style={{ fontSize: '0.7rem' }}>{agentStrategy(trade.agent_id)}</span>
                    <span className={`status-${trade.status}`}>{trade.status}</span>
                  </div>
                ))}
              </div>
            )
          )}
          {view === 'orders' && activeTrades.length > 0 && (
            <Paginator page={ordersPager.page} totalPages={ordersPager.totalPages} total={ordersPager.total} pageSize={10} onPage={ordersPager.setPage} label="orders" />
          )}
        </div>
      )}

      {/* Live trades tab */}
      {tab === 'live' && (
        <div className="card">
          <h2 className="card-title">Live Trades</h2>
          {activeTrades.length === 0 ? (
            <p className="text-gray-400">No live trades yet.</p>
          ) : (
            <>
              <div className="trades-table">
                <div className="trades-header" style={{ gridTemplateColumns: '1fr 0.9fr 0.6fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 0.9fr 0.9fr 0.8fr 0.8fr' }}>
                  <span>Time</span><span>Symbol</span><span>Side</span><span>Qty</span>
                  <span>Price</span><span>Total</span><span>Leverage</span><span>Margin</span><span>Fee</span>
                  <span>Strategy</span><span>Type</span><span>Status</span>
                </div>
                {ordersPager.pageItems.map((trade: any) => (
                  <div key={trade.id} className="trades-row" style={{ gridTemplateColumns: '1fr 0.9fr 0.6fr 0.7fr 0.8fr 0.8fr 0.8fr 0.8fr 0.9fr 0.9fr 0.8fr 0.8fr' }}>
                    <span title={new Date(trade.created_at).toLocaleString()}>{timeAgo(trade.created_at)}</span>
                    <span>{trade.symbol}</span>
                    <span className={trade.side === 'buy' ? 'positive' : 'negative'}>{trade.side?.toUpperCase()}</span>
                    <span>{trade.quantity}</span>
                    <span>${formatPrice(trade.price)}</span>
                    <span>${trade.total?.toFixed(2)}</span>
                    <span style={{ color: (trade.leverage ?? 1) > 1 ? 'var(--amber, #f39c12)' : 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                      {(trade.leverage ?? 1).toFixed(1)}x
                    </span>
                    <span className="text-gray-400" style={{ fontSize: '.72rem', fontFamily: 'var(--mono)' }}>
                      {trade.margin_used != null ? `$${Number(trade.margin_used).toFixed(2)}` : '—'}
                    </span>
                    <span className="text-gray-400">${trade.fee?.toFixed(4) || '0.0000'}</span>
                    <span className="text-gray-300">{agentName(trade.agent_id)}</span>
                    <span className="strategy-tag" style={{ fontSize: '0.7rem' }}>{agentStrategy(trade.agent_id)}</span>
                    <span className={`status-${trade.status}`}>{trade.status}</span>
                  </div>
                ))}
              </div>
              <Paginator page={ordersPager.page} totalPages={ordersPager.totalPages} total={ordersPager.total} pageSize={10} onPage={ordersPager.setPage} label="trades" />
            </>
          )}
        </div>
      )}
      </>
      )}
    </div>
  );
}
