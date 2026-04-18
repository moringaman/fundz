import React, { useState, useEffect } from 'react';
import styled from 'styled-components';
import { tradingApi } from '../lib/api';

interface Trade {
  id: string;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  total: number;
  fee: number;
  status: string;
  created_at: string;
  phemex_order_id?: string;
}

interface Position {
  id?: string;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  risk_level?: string;
  margin_type?: string;
  opened_at?: string;
}

interface PnLSummary {
  total_pnl: number;
  unrealized_pnl: number;
  realized_pnl: number;
  buy_volume: number;
  sell_volume: number;
  trade_count: number;
  position_count: number;
}

const Container = styled.div`
  background: var(--bg-panel, #0d1220);
  border: 1px solid var(--border-mid, #243650);
  border-radius: 10px;
  padding: 1.5rem;
  margin: 1rem 0;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
`;

const SectionTitle = styled.h3`
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-secondary, #8ba3c7);
  margin-bottom: 0.75rem;
`;

const PnLGrid = styled.div`
  display: flex;
  gap: 1.25rem;
  flex-wrap: wrap;
`;

const PnLCard = styled.div`
  background: var(--bg-elevated, #121929);
  border: 1px solid var(--border, #1c2b42);
  border-radius: 8px;
  padding: 0.85rem 1.1rem;
  min-width: 120px;
`;

const PnLLabel = styled.div`
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  font-weight: 600;
  color: var(--text-secondary, #8ba3c7);
  margin-bottom: 0.3rem;
`;

const PnLValue = styled.div<{ positive?: boolean; negative?: boolean }>`
  font-family: 'Share Tech Mono', monospace;
  font-size: 1rem;
  font-weight: 700;
  color: ${({ positive, negative }) =>
    positive ? 'var(--green, #00e676)' :
    negative ? 'var(--red, #ff5370)' :
    'var(--text-primary, #e8f0fe)'};
`;

const DataTable = styled.table`
  width: 100%;
  border-collapse: collapse;

  th {
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--text-secondary, #8ba3c7);
    padding: 0.45rem 0.75rem;
    border-bottom: 1px solid var(--border, #1c2b42);
    text-align: left;
    white-space: nowrap;
  }

  td {
    font-size: 0.8rem;
    color: var(--text-primary, #e8f0fe);
    padding: 0.6rem 0.75rem;
    border-bottom: 1px solid var(--border, #1c2b42);
    font-family: 'Share Tech Mono', monospace;
  }

  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--bg-hover, #1a2438); }
`;

const SideBadge = styled.span<{ side: 'buy' | 'sell' }>`
  display: inline-block;
  padding: 0.18rem 0.5rem;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  background: ${({ side }) => side === 'buy' ? 'rgba(0,230,118,.14)' : 'rgba(255,83,112,.14)'};
  color: ${({ side }) => side === 'buy' ? 'var(--green, #00e676)' : 'var(--red, #ff5370)'};
`;

const RiskBadge = styled.span<{ level: string }>`
  display: inline-block;
  padding: 0.18rem 0.5rem;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  background: ${({ level }) =>
    level === 'low' ? 'rgba(0,230,118,.14)' :
    level === 'medium' ? 'rgba(255,208,96,.14)' :
    'rgba(255,83,112,.14)'};
  color: ${({ level }) =>
    level === 'low' ? 'var(--green, #00e676)' :
    level === 'medium' ? 'var(--amber, #ffd060)' :
    'var(--red, #ff5370)'};
`;

const PnlTd = styled.td<{ value: number }>`
  font-weight: 700 !important;
  color: ${({ value }) => value >= 0 ? 'var(--green, #00e676) !important' : 'var(--red, #ff5370) !important'};
`;

const EmptyState = styled.p`
  font-size: 0.82rem;
  color: var(--text-secondary, #8ba3c7);
  font-style: italic;
  padding: 0.25rem 0;
`;

const StateMessage = styled.div`
  font-size: 0.82rem;
  color: var(--text-secondary, #8ba3c7);
  padding: 1rem 0;
  text-align: center;
  font-style: italic;
`;

const TradeHistoryComponent: React.FC = () => {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [pnl, setPnl] = useState<PnLSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadHistory = async () => {
      try {
        const [historyRes, positionsRes, pnlRes] = await Promise.all([
          tradingApi.getHistory(),
          tradingApi.getPositions(),
          tradingApi.getPnl(),
        ]);
        setTrades(Array.isArray(historyRes.data) ? historyRes.data : []);
        setPositions(Array.isArray(positionsRes.data) ? positionsRes.data : []);
        setPnl(pnlRes.data);
      } catch (error) {
        console.error('Failed to load history:', error);
      } finally {
        setLoading(false);
      }
    };
    loadHistory();
  }, []);

  if (loading) {
    return <StateMessage>Loading trade history\u2026</StateMessage>;
  }

  return (
    <Container>
      {pnl && (
        <div>
          <SectionTitle>Portfolio Summary</SectionTitle>
          <PnLGrid>
            <PnLCard>
              <PnLLabel>Total P&amp;L</PnLLabel>
              <PnLValue positive={pnl.total_pnl > 0} negative={pnl.total_pnl < 0}>
                {pnl.total_pnl >= 0 ? '+' : ''}${pnl.total_pnl.toFixed(2)}
              </PnLValue>
            </PnLCard>
            <PnLCard>
              <PnLLabel>Realised</PnLLabel>
              <PnLValue positive={pnl.realized_pnl > 0} negative={pnl.realized_pnl < 0}>
                {pnl.realized_pnl >= 0 ? '+' : ''}${pnl.realized_pnl.toFixed(2)}
              </PnLValue>
            </PnLCard>
            <PnLCard>
              <PnLLabel>Unrealised</PnLLabel>
              <PnLValue positive={pnl.unrealized_pnl > 0} negative={pnl.unrealized_pnl < 0}>
                {pnl.unrealized_pnl >= 0 ? '+' : ''}${pnl.unrealized_pnl.toFixed(2)}
              </PnLValue>
            </PnLCard>
            <PnLCard>
              <PnLLabel>Trades</PnLLabel>
              <PnLValue>{pnl.trade_count}</PnLValue>
            </PnLCard>
            <PnLCard>
              <PnLLabel>Positions</PnLLabel>
              <PnLValue>{pnl.position_count}</PnLValue>
            </PnLCard>
          </PnLGrid>
        </div>
      )}

      <div>
        <SectionTitle>Open Positions</SectionTitle>
        {positions.length === 0 ? (
          <EmptyState>There are currently no open positions.</EmptyState>
        ) : (
          <DataTable>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Quantity</th>
                <th>Entry</th>
                <th>Current</th>
                <th>Unrealised P&amp;L</th>
                <th>Risk</th>
                <th>Opened</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr key={pos.id || pos.symbol}>
                  <td>{pos.symbol}</td>
                  <td><SideBadge side={pos.side}>{pos.side}</SideBadge></td>
                  <td>{pos.quantity.toFixed(4)}</td>
                  <td>${pos.entry_price.toFixed(2)}</td>
                  <td>${pos.current_price.toFixed(2)}</td>
                  <PnlTd value={pos.unrealized_pnl}>
                    {pos.unrealized_pnl >= 0 ? '+' : ''}${pos.unrealized_pnl.toFixed(2)}
                  </PnlTd>
                  <td>
                    {pos.risk_level
                      ? <RiskBadge level={pos.risk_level}>{pos.risk_level}</RiskBadge>
                      : <span style={{ color: 'var(--text-muted, #6b85a8)' }}>\u2014</span>}
                  </td>
                  <td style={{ color: 'var(--text-muted, #6b85a8)', fontSize: '0.72rem' }}>
                    {pos.opened_at ? new Date(pos.opened_at).toLocaleString() : '\u2014'}
                  </td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </div>

      <div>
        <SectionTitle>Recent Trades</SectionTitle>
        {trades.length === 0 ? (
          <EmptyState>No trades recorded yet.</EmptyState>
        ) : (
          <DataTable>
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Price</th>
                <th>Total</th>
                <th>Fee</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <tr key={trade.id}>
                  <td style={{ color: 'var(--text-muted, #6b85a8)', fontSize: '0.72rem' }}>
                    {new Date(trade.created_at).toLocaleString()}
                  </td>
                  <td>{trade.symbol}</td>
                  <td><SideBadge side={trade.side}>{trade.side}</SideBadge></td>
                  <td>{trade.quantity.toFixed(4)}</td>
                  <td>${trade.price.toFixed(2)}</td>
                  <td>${trade.total.toFixed(2)}</td>
                  <td style={{ color: 'var(--text-secondary, #8ba3c7)' }}>${trade.fee.toFixed(2)}</td>
                  <td style={{ color: 'var(--text-secondary, #8ba3c7)', textTransform: 'uppercase', fontSize: '0.72rem' }}>
                    {trade.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </div>
    </Container>
  );
};

export default TradeHistoryComponent;
