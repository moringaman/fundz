import { useEffect, useState } from 'react';
import { Brain, TrendingUp, Download, RefreshCw } from 'lucide-react';
import api from '../lib/api';

interface Bucket {
  strategy: string;
  regime: string;
  sl_bucket: string;
  tp_bucket: string;
  confidence_bucket: string;
  wins: number;
  losses: number;
  total: number;
  win_rate: number;
}

export default function LearningDashboard() {
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  const fetch = async () => {
    try {
      const res = await api.get('automation/learner/summary');
      setBuckets(res.data.buckets || []);
    } catch {
      setBuckets([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const triggerTune = async () => {
    await api.post('automation/learner/tune', null, { params: { hours: 168 } });
    fetch();
  };

  const exportDataset = async () => {
    setExporting(true);
    try {
      const res = await api.post('automation/learner/export-openai');
      alert(`Exported ${res.data.count} examples to ${res.data.path}`);
    } catch {
      alert('Export failed');
    } finally {
      setExporting(false);
    }
  };

  if (loading) return null;
  if (!buckets.length) return null;

  const totalWins = buckets.reduce((s, b) => s + b.wins, 0);
  const totalLosses = buckets.reduce((s, b) => s + b.losses, 0);
  const totalTrades = totalWins + totalLosses;
  const overallWr = totalTrades ? (totalWins / totalTrades * 100).toFixed(1) : '—';

  return (
    <div className="panel" style={{ marginTop: '1rem' }}>
      <div className="panel-header">
        <span className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Brain size={13} /> Learning Dashboard
        </span>
        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--text-secondary)' }}>
            {totalTrades} trades · {overallWr}% WR
          </span>
          <button onClick={triggerTune} className="header-btn" title="Run parameter tuning">
            <TrendingUp size={14} />
          </button>
          <button onClick={exportDataset} className="header-btn" title="Export RL dataset" disabled={exporting}>
            <Download size={14} />
          </button>
          <button onClick={fetch} className="header-btn" title="Refresh">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>
      <div className="panel-body" style={{ padding: '0.75rem 1rem', maxHeight: 300, overflowY: 'auto' }}>
        {buckets.map((b, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.35rem 0',
              borderBottom: '1px solid var(--border)',
              fontSize: 'var(--text-sm)',
              gap: '0.5rem',
            }}
          >
            <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', minWidth: 0, flex: 1 }}>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--accent)', fontWeight: 600 }}>
                {b.strategy}
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-dim)' }}>
                {b.sl_bucket}/{b.tp_bucket} {b.confidence_bucket} · {b.wins}W/{b.losses}L
              </span>
            </div>
            <span
              style={{
                fontFamily: 'var(--mono)',
                fontWeight: 700,
                fontSize: 'var(--text-sm)',
                color: b.win_rate >= 50 ? 'var(--green)' : 'var(--red)',
                whiteSpace: 'nowrap',
              }}
            >
              {b.win_rate}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
