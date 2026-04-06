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
}

const PositionsContainer = styled.div`
  background-color: #f9f9f9;
  border-radius: 8px;
  padding: 20px;
  margin: 20px 0;
`;

const PositionsTable = styled.table`
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
  
  tr:nth-child(even) {
    background-color: #f8f8f8;
  }
`;

interface ProfitCellProps {
  profit: number;
}

const ProfitCell = styled.td<ProfitCellProps>`
  color: ${({ profit }) => profit >= 0 ? 'green' : 'red'};
  font-weight: bold;
`;

export const PositionsTableComponent: React.FC = () => {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    try {
      setLoading(true);
      const response = await axios.get('/api/trading/positions');
      setPositions(response.data);
      setError(null);
    } catch (err) {
      setError('Failed to fetch positions');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPositions();
    const intervalId = setInterval(fetchPositions, 30000);
    return () => clearInterval(intervalId);
  }, [fetchPositions]);

  if (loading) return <div>Loading positions...</div>;
  if (error) return <div>Error: {error}</div>;
  if (positions.length === 0) return <div>No open positions</div>;

  return (
    <PositionsContainer>
      <h2>Open Positions</h2>
      <PositionsTable>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Quantity</th>
            <th>Entry Price</th>
            <th>Current Price</th>
            <th>Unrealized P&L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={position.symbol}>
              <td>{position.symbol}</td>
              <td>{position.side.toUpperCase()}</td>
              <td>{position.quantity.toFixed(4)}</td>
              <td>${position.entry_price.toFixed(2)}</td>
              <td>${position.current_price.toFixed(2)}</td>
              <ProfitCell profit={position.unrealized_pnl}>
                ${position.unrealized_pnl.toFixed(2)}
              </ProfitCell>
            </tr>
          ))}
        </tbody>
      </PositionsTable>
    </PositionsContainer>
  );
};

export default PositionsTableComponent;