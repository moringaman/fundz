import { useState } from 'react';
import { Activity, Plus, Trash2, ToggleLeft, ToggleRight, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import {
  useWhaleIntelligence,
  useWhaleWatchlist,
  useAddWhaleAddress,
  useDeleteWhaleAddress,
  useToggleWhaleAddress,
  useRefreshWhaleIntelligence,
} from '../hooks/useQueries';
import { useWhaleStream, type WhaleIntelligenceData, type CoinWhaleBias } from '../hooks/useWhaleStream';

function fmtUsd(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function fmtAge(isoTs: string): string {
  const diff = Math.floor((Date.now() - new Date(isoTs).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function BiasPill({ bias, coin, longNotional, shortNotional }: {
  bias: CoinWhaleBias;
  coin: string;
  longNotional: number;
  shortNotional: number;
}) {
  const colors: Record<string, string> = {
    bullish: 'var(--green)',
    bearish: 'var(--red)',
    neutral: 'var(--text-muted)',
  };
  const bgs: Record<string, string> = {
    bullish: 'var(--green-dim)',
    bearish: 'var(--red-dim)',
    neutral: 'rgba(75,107,150,.15)',
  };
  const total = longNotional + shortNotional;
  return (
    <div style={{
      display: 'inline-flex',
      flexDirection: 'column',
      alignItems: 'center',
      padding: '6px 10px',
      borderRadius: 6,
      background: bgs[bias.bias] || bgs.neutral,
      border: `1px solid ${colors[bias.bias] || colors.neutral}33`,
      minWidth: 72,
      flexShrink: 0,
      // flexWrap: 'wrap',
    }}>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-sm)', color: 'var(--text-primary)', fontWeight: 600 }}>
        {coin}
      </span>
      <span style={{ fontSize: 'var(--text-xs)', color: colors[bias.bias] || colors.neutral, textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600 }}>
        {bias.bias}
      </span>
      {total >= 10_000 && (
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
          {fmtUsd(total)}
        </span>
      )}
    </div>
  );
}

interface Props {
  compact?: boolean;
  mini?: boolean;
}

export function WhaleIntelligencePanel({ compact = false, mini = false }: Props) {
  const { data: restData } = useWhaleIntelligence();
  const streamData = useWhaleStream();
  const { data: watchlist = [] } = useWhaleWatchlist();
  const addAddress = useAddWhaleAddress();
  const deleteAddress = useDeleteWhaleAddress();
  const toggleAddress = useToggleWhaleAddress();
  const refreshIntel = useRefreshWhaleIntelligence();

  const [showWatchlist, setShowWatchlist] = useState(false);
  const [showAllPositions, setShowAllPositions] = useState(false);
  const [newAddress, setNewAddress] = useState('');
  const [newLabel, setNewLabel] = useState('');
  const [addError, setAddError] = useState('');

  const intel: WhaleIntelligenceData | null = streamData || restData || null;

  const trackedCount = (intel?.total_whales_tracked ?? 0) > 0
    ? intel!.total_whales_tracked
    : watchlist.length;

  const coinBiases = intel ? Object.entries(intel.coin_biases) : [];
  const sortedBiases = [...coinBiases].sort(
    ([, a], [, b]) => (b.long_notional + b.short_notional) - (a.long_notional + a.short_notional)
  );

  const allPositions: Array<{
    label: string; address: string; coin: string; side: string;
    notional_usd: number; leverage: number; unrealized_pnl: number; entry_price: number;
  }> = [];
  if (intel) {
    for (const bias of Object.values(intel.coin_biases)) {
      for (const p of ((bias as any).top_positions || [])) {
        allPositions.push(p);
      }
    }
  }
  const sortedPositions = [...allPositions].sort((a, b) => b.notional_usd - a.notional_usd);
  const displayedPositions = showAllPositions ? sortedPositions : sortedPositions.slice(0, 10);

  const isStale = intel ? (Date.now() - new Date(intel.timestamp).getTime()) > 90_000 : true;

  function handleAddAddress(e: React.FormEvent) {
    e.preventDefault();
    setAddError('');
    if (!newAddress.trim()) return;
    addAddress.mutate(
      { address: newAddress.trim(), label: newLabel.trim() || undefined },
      {
        onSuccess: () => { setNewAddress(''); setNewLabel(''); },
        onError: (err: any) => setAddError(err?.response?.data?.detail || 'Failed to add address'),
      }
    );
  }

  return (
    <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', margin: '1.75rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Activity size={16} color="var(--accent)" />
          <span style={{ fontWeight: 600, fontSize: 'var(--text-md)', color: 'var(--text-primary)' }}>
            Hyperliquid Whale Intelligence
          </span>
          {(intel || watchlist.length > 0) && (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
              {trackedCount} wallets tracked
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {intel && (
            <span style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
              {fmtAge(intel.timestamp)}
            </span>
          )}
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: isStale ? 'var(--amber)' : 'var(--green)', display: 'inline-block' }} title={isStale ? 'Data stale' : 'Live'} />
          <button onClick={() => refreshIntel.mutate()} disabled={refreshIntel.isPending} title="Force refresh"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', padding: 2 }}>
            <RefreshCw size={13} style={{ opacity: refreshIntel.isPending ? 0.4 : 1 }} />
          </button>
        </div>
      </div>

      {/* Bias heatmap strip */}
      {coinBiases.length > 0 ? (
        <div style={{ display: 'flex', gap: 8, padding: '12px 18px', overflowX: 'auto', borderBottom: '1px solid var(--border)', flexWrap: mini ? 'wrap' : 'wrap' }}>
          {sortedBiases.map(([coin, bias]) => (
            (bias.long_notional + bias.short_notional) >= 10_000 && (
              <BiasPill key={coin} coin={coin} bias={bias} longNotional={bias.long_notional} shortNotional={bias.short_notional} />
            )
          ))}
        </div>
      ) : (
        <div style={{ padding: '20px 18px', color: 'var(--text-dim)', fontSize: 'var(--text-sm)', textAlign: 'center' }}>
          {intel === null ? 'Loading whale intelligence…' : 'No whale positions detected on tracked wallets.'}
        </div>
      )}

      {/* Mini summary footer */}
      {mini && intel && intel.total_whales_with_positions > 0 && (
        <div style={{ padding: '6px 18px 10px', display: 'flex', gap: 16, fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
          <span>{intel.total_whales_with_positions}/{trackedCount} whales active</span>
          <span>{Object.keys(intel.coin_biases).length} coins tracked</span>
        </div>
      )}

      {/* Positions table */}
      {!mini && sortedPositions.length > 0 && (
        <div style={{ padding: '0 0 4px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Whale', 'Coin', 'Side', 'Notional', 'Leverage', 'Entry', 'Unr. PnL'].map(h => (
                  <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--text-dim)', fontWeight: 500, fontSize: 'var(--text-xs)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayedPositions.map((p, i) => {
                const isLong = p.side === 'long';
                const pnlColor = p.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)';
                return (
                  <tr key={`${p.address}-${p.coin}-${i}`} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,.015)' }}>
                    <td style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)' }}>
                      {p.label || (p.address ? p.address.slice(0, 8) + '…' : '—')}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)', fontFamily: 'var(--mono)', fontWeight: 600 }}>{p.coin}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{ display: 'inline-block', padding: '2px 6px', borderRadius: 4, fontSize: 'var(--text-xs)', fontWeight: 700, letterSpacing: '0.04em', background: isLong ? 'var(--green-dim)' : 'var(--red-dim)', color: isLong ? 'var(--green)' : 'var(--red)' }}>
                        {isLong ? 'LONG' : 'SHORT'}
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)', fontFamily: 'var(--mono)' }}>{fmtUsd(p.notional_usd)}</td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontFamily: 'var(--mono)' }}>{p.leverage.toFixed(0)}x</td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-secondary)', fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)' }}>
                      {p.entry_price > 0 ? p.entry_price.toPrecision(5) : '—'}
                    </td>
                    <td style={{ padding: '8px 12px', color: pnlColor, fontFamily: 'var(--mono)', fontWeight: 600 }}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{fmtUsd(p.unrealized_pnl)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {sortedPositions.length > 10 && (
            <button onClick={() => setShowAllPositions(!showAllPositions)}
              style={{ width: '100%', padding: '8px', background: 'none', border: 'none', borderTop: '1px solid var(--border)', color: 'var(--accent)', cursor: 'pointer', fontSize: 'var(--text-xs)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
              {showAllPositions ? <><ChevronUp size={12} /> Show less</> : <><ChevronDown size={12} /> Show {sortedPositions.length - 10} more positions</>}
            </button>
          )}
        </div>
      )}

      {/* Watchlist manager */}
      {!compact && !mini && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          <button onClick={() => setShowWatchlist(!showWatchlist)}
            style={{ width: '100%', padding: '10px 18px', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>
            <span>Watchlist ({(watchlist as any[]).length} addresses)</span>
            {showWatchlist ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          {showWatchlist && (
            <div style={{ padding: '0 18px 16px' }}>
              <form onSubmit={handleAddAddress} style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <input value={newAddress} onChange={e => setNewAddress(e.target.value)} placeholder="0x... Hyperliquid address"
                  style={{ flex: 2, padding: '7px 10px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text-primary)', fontSize: 'var(--text-sm)', fontFamily: 'var(--mono)' }} />
                <input value={newLabel} onChange={e => setNewLabel(e.target.value)} placeholder="Label (optional)"
                  style={{ flex: 1, padding: '7px 10px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text-primary)', fontSize: 'var(--text-sm)' }} />
                <button type="submit" disabled={addAddress.isPending || !newAddress.trim()}
                  style={{ padding: '7px 12px', background: 'var(--accent)', border: 'none', borderRadius: 6, color: '#000', fontWeight: 700, cursor: 'pointer', fontSize: 'var(--text-sm)', display: 'flex', alignItems: 'center', gap: 4, opacity: (addAddress.isPending || !newAddress.trim()) ? 0.5 : 1 }}>
                  <Plus size={13} /> Add
                </button>
              </form>
              {addError && <p style={{ color: 'var(--red)', fontSize: 'var(--text-xs)', marginBottom: 8 }}>{addError}</p>}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {(watchlist as any[]).map((entry: any) => (
                  <div key={entry.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 6, opacity: entry.is_active ? 1 : 0.5 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {entry.label && <div style={{ color: 'var(--text-primary)', fontSize: 'var(--text-sm)', fontWeight: 500 }}>{entry.label}</div>}
                      <div style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entry.address}</div>
                    </div>
                    <button onClick={() => toggleAddress.mutate(entry.id)} title={entry.is_active ? 'Disable' : 'Enable'}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: entry.is_active ? 'var(--green)' : 'var(--text-dim)', display: 'flex' }}>
                      {entry.is_active ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                    </button>
                    <button onClick={() => deleteAddress.mutate(entry.id)} title="Remove"
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', display: 'flex' }}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
                {(watchlist as any[]).length === 0 && (
                  <p style={{ color: 'var(--text-dim)', fontSize: 'var(--text-xs)', textAlign: 'center', padding: '8px 0' }}>No addresses in watchlist. Add one above.</p>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
