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
  leverage?: number;
  margin_used?: number;
  liquidation_price?: number;
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

const SideBadge = styled.span<{ $side: 'buy' | 'sell' }>`
  display: inline-block;
  padding: 0.2rem 0.55rem;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  background: ${({ $side }) => $side === 'buy'
    ? 'rgba(0,230,118,.14)'
    : 'rgba(255,83,112,.14)'};
  color: ${({ $side }) => $side === 'buy'
    ? 'var(--green, #00e676)'
    : 'var(--red, #ff5370)'};
`;

const PnlCell = styled.td<{ $profit: number }>`
  font-weight: 700 !important;
  color: ${({ $profit }) => $profit >= 0
    ? 'var(--green, #00e676) !important'
    : 'var(--red, #ff5370) !important'};
`;

const LeverageBadge = styled.span<{ $lev: number }>`
  display: inline-block;
  padding: 0.15rem 0.45rem;
  border-radius: 4px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  background: ${({ $lev }) => $lev >= 4
    ? 'rgba(255,83,112,.18)'
    : $lev >= 2
      ? 'rgba(255,180,0,.15)'
      : 'rgba(100,180,255,.10)'};
  color: ${({ $lev }) => $lev >= 4
    ? 'var(--red, #ff5370)'
    : $lev >= 2
      ? '#ffb400'
      : 'var(--text-secondary, #8ba3c7)'};
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
      const url = isPaper ? '/api/paper/positions' : '/api/trading/positions';
      const response = await axios.get(url);
      const data = response.data;
      setPositions(Array.isArray(data) ? data : []);
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
              <th>Qty</th>
              <th>Entry</th>
              <th>Current</th>
              <th>Leverage</th>
              <th>Margin Used</th>
              <th>Liq. Price</th>
              <th>Unrealised P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, idx) => {
              const lev = position.leverage ?? 1;
              const isLeveraged = lev > 1;
              return (
                <tr key={`${position.symbol}-${position.side}-${position.is_paper}-${idx}`}>
                  <td>{position.symbol}</td>
                  <td><SideBadge $side={position.side}>{position.side}</SideBadge></td>
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
                  <td>
                    {isLeveraged ? (
                      <LeverageBadge $lev={lev}>{lev.toFixed(1)}x</LeverageBadge>
                    ) : (
                      <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary, #8ba3c7)' }}>1x</span>
                    )}
                  </td>
                  <td>
                    {position.margin_used != null
                      ? <span style={{ color: 'var(--text-secondary, #8ba3c7)' }}>${position.margin_used.toFixed(2)}</span>
                      : <span style={{ color: 'var(--border-mid, #243650)' }}>—</span>}
                  </td>
                  <td>
                    {position.liquidation_price != null ? (
                      <span style={{
                        color: Math.abs(position.current_price - position.liquidation_price) / position.current_price < 0.12
                          ? 'var(--red, #ff5370)'
                          : 'var(--text-secondary, #8ba3c7)',
                        fontWeight: Math.abs(position.current_price - position.liquidation_price) / position.current_price < 0.12 ? 700 : 400,
                      }}>
                        ${position.liquidation_price.toFixed(2)}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--border-mid, #243650)' }}>—</span>
                    )}
                  </td>
                  <PnlCell $profit={position.unrealized_pnl}>
                    {position.unrealized_pnl >= 0 ? '+' : ''}${position.unrealized_pnl.toFixed(2)}
                  </PnlCell>
                </tr>
              );
            })}
          </tbody>
        </StyledTable>
      )}
    </PositionsContainer>
  );
};

export default PositionsTableComponent;
