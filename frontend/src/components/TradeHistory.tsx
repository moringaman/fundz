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

const HistoryContainer = styled.div`
  background-color: #f9f9f9;
  border-radius: 8px;
  padding: 20px;
  margin: 20px 0;
`;

const HistoryTable = styled.table`
  width: 100%;
  border-collapse: collapse;
  
  th, td {
    border: 1px solid #ddd;
    padding: 12px;
    text-align: left;
  }
  
  th {
    background-color: #f2f2f2;
    font-weight: bold;
  }
`;

const RiskBadge = styled.span<{ level: string }>`
  padding: 4px 8px;
  border-radius: 4px;
  font-weight: bold;
  background-color: ${props => 
    props.level === 'low' ? '#e6f3e6' : 
    props.level === 'medium' ? '#fff3e0' : 
    '#ffebee'};
  color: ${props => 
    props.level === 'low' ? 'green' : 
    props.level === 'medium' ? 'orange' : 
    'red'};
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
        setTrades(historyRes.data);
        setPositions(positionsRes.data);
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
    return <div>Loading trade history...</div>;
  }

  return (
    <HistoryContainer>
      <h2>Trade History and Open Positions</h2>
      
      {pnl && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px' }}>
          <div>
            <h3>Total P&L</h3>
            <p style={{ color: pnl.total_pnl >= 0 ? 'green' : 'red' }}>
              ${pnl.total_pnl.toFixed(2)}
            </p>
          </div>
          <div>
            <h3>Realized P&L</h3>
            <p>${pnl.realized_pnl.toFixed(2)}</p>
          </div>
          <div>
            <h3>Unrealized P&L</h3>
            <p>${pnl.unrealized_pnl.toFixed(2)}</p>
          </div>
          <div>
            <h3>Total Trades</h3>
            <p>{pnl.trade_count}</p>
          </div>
          <div>
            <h3>Open Positions</h3>
            <p>{pnl.position_count}</p>
          </div>
        </div>
      )}

      <h3>Open Positions</h3>
      {positions.length === 0 ? (
        <p>No open positions</p>
      ) : (
        <HistoryTable>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Entry Price</th>
              <th>Current Price</th>
              <th>Unrealized P&L</th>
              <th>Risk Level</th>
              <th>Opened At</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <tr key={position.id || position.symbol}>
                <td>{position.symbol}</td>
                <td style={{ color: position.side === 'buy' ? 'green' : 'red' }}>
                  {position.side.toUpperCase()}
                </td>
                <td>{position.quantity.toFixed(4)}</td>
                <td>${position.entry_price.toFixed(2)}</td>
                <td>${position.current_price.toFixed(2)}</td>
                <td 
                  style={{ 
                    color: position.unrealized_pnl >= 0 ? 'green' : 'red',
                    fontWeight: 'bold' 
                  }}
                >
                  ${position.unrealized_pnl.toFixed(2)}
                </td>
                <td>
                  {position.risk_level && (
                    <RiskBadge level={position.risk_level}>
                      {position.risk_level.toUpperCase()}
                    </RiskBadge>
                  )}
                </td>
                <td>{position.opened_at ? new Date(position.opened_at).toLocaleString() : 'N/A'}</td>
              </tr>
            ))}
          </tbody>
        </HistoryTable>
      )}

      <h3>Recent Trades</h3>
      {trades.length === 0 ? (
        <p>No trades yet</p>
      ) : (
        <HistoryTable>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Quantity</th>
              <th>Price</th>
              <th>Total</th>
              <th>Fee</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade) => (
              <tr key={trade.id}>
                <td>{new Date(trade.created_at).toLocaleString()}</td>
                <td>{trade.symbol}</td>
                <td style={{ color: trade.side === 'buy' ? 'green' : 'red' }}>
                  {trade.side.toUpperCase()}
                </td>
                <td>{trade.quantity.toFixed(4)}</td>
                <td>${trade.price.toFixed(2)}</td>
                <td>${trade.total.toFixed(2)}</td>
                <td>${trade.fee.toFixed(2)}</td>
                <td>{trade.status.toUpperCase()}</td>
              </tr>
            ))}
          </tbody>
        </HistoryTable>
      )}
    </HistoryContainer>
  );
};

export default TradeHistoryComponent;