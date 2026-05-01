import { useEffect, useState, useCallback } from 'react';
import { pendingOrderApi } from '../lib/api';
import { Clock, X, RefreshCw } from 'lucide-react';

interface PendingOrder {
  id: string;
  agent_id: string | null;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  status: string;
  created_at: string;
}

export default function PendingOrders() {
  const [orders, setOrders] = useState<PendingOrder[]>([]);
  const [loading, setLoading] = useState(true);

  const fetch = useCallback(async () => {
    try {
      const res = await pendingOrderApi.list();
      setOrders(res.data);
    } catch {
      setOrders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch();
    const iv = setInterval(fetch, 15000);
    return () => clearInterval(iv);
  }, [fetch]);

  const cancel = async (id: string) => {
    await pendingOrderApi.cancel(id);
    fetch();
  };

  if (loading) return null;
  if (!orders.length) return null;

  return (
    <div className="panel" style={{ marginTop: '1rem' }}>
      <div className="panel-header">
        <span className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Clock size={13} /> Pending Orders ({orders.length})
        </span>
        <button onClick={fetch} className="header-btn" title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      <div className="panel-body" style={{ padding: '0.5rem 1rem' }}>
        {orders.map((o) => (
          <div
            key={o.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.5rem 0',
              borderBottom: '1px solid var(--border)',
              fontSize: 'var(--text-sm)',
              gap: '0.75rem',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0, flex: 1 }}>
              <span style={{ fontFamily: 'var(--mono)', color: 'var(--accent)', fontWeight: 600 }}>
                {o.symbol}
              </span>
              <span
                style={{
                  fontFamily: 'var(--mono)',
                  fontWeight: 700,
                  fontSize: 'var(--text-xs)',
                  color: o.side === 'buy' ? 'var(--green)' : 'var(--red)',
                }}
              >
                {o.side.toUpperCase()}
              </span>
              <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-xs)' }}>
                {o.quantity.toFixed(2)} @ ${o.price.toFixed(6)}
              </span>
            </div>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
              {new Date(o.created_at).toLocaleTimeString()}
            </span>
            <button
              onClick={() => cancel(o.id)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-dim)',
                cursor: 'pointer',
                padding: '0.2rem',
                display: 'flex',
              }}
              title="Cancel order"
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
