import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import styled from 'styled-components';

interface Position {
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  is_paper?: boolean;
}

const PositionsContainer = styled.div`
  background: var(--bg-panel, #0d1220);
  border: 1px solid var(--border-mid, #243650);
  border-radius: 10px;
  padding: 1.25rem;
  margin: 1rem 0;
`;

const TableTitle = styled.h3`
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-primary, #e8f0fe);
  letter-spacing: 0.02em;
  margin-bottom: 1rem;
  text-transform: uppercase;
`;

const StyledTable = styled.table`
  width: 100%;
  border-collapse: collapse;

  th {
    font-size: 0.68rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--text-secondary, #8ba3c7);
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--border, #1c2b42);
    text-align: left;
    white-space: nowrap;
  }

  td {
    font-size: 0.82rem;
    color: var(--text-primary, #e8f0fe);
    padding: 0.6rem 0.75rem;
    border-bottom: 1px solid var(--border, #1c2b42);
    font-family: 'Share Tech Mono', monospace;
  }

  tr:last-child td {
    border-bottom: none;
  }

  tr:hover td {
    background: var(--bg-hover, #1a2438);
  }
`;

const SideBadge = styled.span<{ side: 'buy' | 'sell' }>`
  display: inline-block;
  padding: 0.2rem 0.55rem;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  background: ${({ side }) => side === 'buy'
    ? 'rgba(0,230,118,.14)'
    : 'rgba(255,83,112,.14)'};
  color: ${({ side }) => side === 'buy'
    ? 'var(--green, #00e676)'
    : 'var(--red, #ff5370)'};
`;

const PnlCell = styled.td<{ profit: number }>`
  font-weight: 700 !important;
  color: ${({ profit }) => profit >= 0
    ? 'var(--green, #00e676) !important'
    : 'var(--red, #ff5370) !important'};
`;

const StateMessage = styled.div`
  font-size: 0.82rem;
  color: var(--text-secondary, #8ba3c7);
  padding: 0.5rem 0;
  font-style: italic;
`;

export const PositionsTableComponent: React.FC = () => {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isPaper, setIsPaper] = useState(true);

  // Load current mode once on mount (and re-check every 60s)
  useEffect(() => {
    const checkMode = () =>
      axios.get('/api/settings').then((r) => {
        setIsPaper(r.data?.trading?.paper_trading_default ?? true);
      }).catch(() => {});
    checkMode();
    const id = setInterval(checkMode, 60_000);
    return () => clearInterval(id);
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      setLoading(true);
      // Paper mode → /api/paper/positions, Live mode → /api/trading/positions
      const url = isPaper ? '/api/paper/positions' : '/api/trading/positions';
      const response = await axios.get(url);
      setPositions(response.data);
      setError(null);
    } catch (err) {
      setError('Failed to fetch positions');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [isPaper]);

  useEffect(() => {
    fetchPositions();
    const intervalId = setInterval(fetchPositions, 30000);
    return () => clearInterval(intervalId);
  }, [fetchPositions]);

  return (
    <PositionsContainer>
      <TableTitle>
        Open Positions
        <span style={{ marginLeft: '10px', fontSize: '0.65rem', fontWeight: 700, color: isPaper ? '#888' : '#ff4444', letterSpacing: '0.1em' }}>
          {isPaper ? 'PAPER' : 'LIVE'}
        </span>
      </TableTitle>
      {loading && <StateMessage>Loading positions…</StateMessage>}
      {error && <StateMessage style={{ color: 'var(--red, #ff5370)' }}>{error}</StateMessage>}
      {!loading && !error && positions.length === 0 && (
        <StateMessage>There are currently no open positions.</StateMessage>
      )}
      {!loading && !error && positions.length > 0 && (
        <StyledTable>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Mode</th>
              <th>Quantity</th>
              <th>Entry Price</th>
              <th>Current Price</th>
              <th>Unrealised P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <tr key={`${position.symbol}-${position.side}-${position.is_paper}`}>
                <td>{position.symbol}</td>
                <td><SideBadge side={position.side}>{position.side}</SideBadge></td>
                <td>
                  {position.is_paper === false ? (
                    <span style={{ fontSize: '0.68rem', fontWeight: 700, color: '#ff4444', letterSpacing: '0.08em' }}>LIVE</span>
                  ) : (
                    <span style={{ fontSize: '0.68rem', color: '#888', letterSpacing: '0.05em' }}>PAPER</span>
                  )}
                </td>
                <td>{position.quantity.toFixed(4)}</td>
                <td>${position.entry_price.toFixed(2)}</td>
                <td>${position.current_price.toFixed(2)}</td>
                <PnlCell profit={position.unrealized_pnl}>
                  {position.unrealized_pnl >= 0 ? '+' : ''}${position.unrealized_pnl.toFixed(2)}
                </PnlCell>
              </tr>
            ))}
          </tbody>
        </StyledTable>
      )}
    </PositionsContainer>
  );
};

export default PositionsTableComponent;
