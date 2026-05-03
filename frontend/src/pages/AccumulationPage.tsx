import { useEffect, useState } from 'react';
import { Plus, Send, TrendingUp, Wallet, RefreshCw, DollarSign, PiggyBank, ArrowUpFromLine, Globe } from 'lucide-react';
import api from '../lib/api';

export default function AccumulationPage() {
  const [portfolio, setPortfolio] = useState<any>(null);
  const [configs, setConfigs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [depositAmount, setDepositAmount] = useState('');
  const [transferAmount, setTransferAmount] = useState('');
  const [editingAsset, setEditingAsset] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState(false);

  const fetch = async () => {
    try {
      const [pRes, cRes, sRes] = await Promise.all([
        api.get('accumulation/portfolio'),
        api.get('accumulation/configs'),
        api.get('settings'),
      ]);
      setPortfolio(pRes.data);
      setConfigs(cRes.data);
      setLiveMode(sRes.data?.trading?.accumulation_live_enabled ?? false);
    } catch {
      setPortfolio(null);
      setConfigs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetch(); }, []);

  const deposit = async () => {
    const amt = parseFloat(depositAmount);
    if (!amt || amt <= 0) return;
    await api.post('accumulation/deposit', { amount: amt });
    setDepositAmount('');
    fetch();
  };

  const transfer = async () => {
    const amt = parseFloat(transferAmount);
    if (!amt || amt <= 0) return;
    try {
      await api.post('accumulation/transfer-to-trading', { amount: amt });
      setTransferAmount('');
      fetch();
    } catch (e: any) {
      alert(e.response?.data?.detail || 'Transfer failed');
    }
  };

  const saveConfig = async (asset: string, data: any) => {
    await api.put('accumulation/configs', { asset, ...data });
    setEditingAsset(null);
    fetch();
  };

  if (loading) return <div className="page-inner"><p style={{ color: 'var(--text-dim)' }}>Loading...</p></div>;

  return (
    <div className="page-inner">
      <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
        <PiggyBank size={22} /> Accumulation Fund
      </h1>

      {/* Portfolio summary */}
      {portfolio && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '.65rem', marginBottom: '1.25rem' }}>
          <div className="stat-card">
            <div className="stat-label">Total Invested</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>${portfolio.total_invested?.toFixed(2)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Current Value</div>
            <div className={`stat-value ${(portfolio.total_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '1rem' }}>
              ${portfolio.current_value?.toFixed(2)}
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Total P&L</div>
            <div className={`stat-value ${(portfolio.total_pnl ?? 0) >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '1rem' }}>
              {portfolio.total_pnl >= 0 ? '+' : ''}${portfolio.total_pnl?.toFixed(2)} ({portfolio.total_pnl_pct?.toFixed(1)}%)
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-label">USDT Available</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>${portfolio.usdt_balance?.toFixed(2)}</div>
          </div>
        </div>
      )}

      {/* Capital actions */}
      <div className="panel" style={{ marginBottom: '1rem' }}>
        <div className="panel-header">
          <span className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
            <DollarSign size={13} /> Capital Management
          </span>
          <label style={{ display: 'flex', alignItems: 'center', gap: '.4rem', cursor: 'pointer', fontSize: '.78rem' }}>
            <Globe size={14} />
            <span style={{ color: liveMode ? 'var(--red)' : 'var(--text-secondary)', fontWeight: 600 }}>
              {liveMode ? 'LIVE' : 'PAPER'}
            </span>
            <input type="checkbox" checked={liveMode}
              onChange={async (e) => {
                const v = e.target.checked;
                setLiveMode(v);
                await api.put('settings/trading', { accumulation_live_enabled: v });
              }}
              style={{ accentColor: 'var(--accent)' }} />
          </label>
        </div>
        <div className="panel-body" style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '.5rem' }}>
            <div>
              <div className="form-label">Deposit USDT</div>
              <input type="number" step="10" min="0" className="settings-input" style={{ width: 140, marginBottom: 0 }}
                value={depositAmount} onChange={e => setDepositAmount(e.target.value)}
                placeholder="Amount" />
            </div>
            <button className="agent-btn" onClick={deposit} style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
              <Plus size={14} /> Deposit
            </button>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '.5rem' }}>
            <div>
              <div className="form-label">Transfer to Trading Fund</div>
              <input type="number" step="10" min="0" className="settings-input" style={{ width: 140, marginBottom: 0 }}
                value={transferAmount} onChange={e => setTransferAmount(e.target.value)}
                placeholder="Amount" />
            </div>
            <button className="agent-btn" onClick={transfer} style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
              <Send size={14} /> Transfer
            </button>
          </div>
          <button className="agent-btn" onClick={fetch} style={{ display: 'flex', alignItems: 'center', gap: '.3rem', alignSelf: 'flex-end' }}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Strategy configs */}
      <div className="panel">
        <div className="panel-header">
          <span className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
            <TrendingUp size={13} /> Accumulation Strategies
          </span>
          <button className="header-btn" onClick={() => setEditingAsset('__new__')} title="Add asset">
            <Plus size={16} />
          </button>
        </div>
        <div className="panel-body" style={{ padding: 0 }}>
          {configs.length === 0 && !editingAsset && (
            <p style={{ padding: '1.25rem 1.5rem', color: 'var(--text-dim)', fontSize: '.8rem' }}>
              No accumulation strategies configured. Add an asset to start DCA or value averaging.
            </p>
          )}
          {configs.map((cfg) => (
            <div key={cfg.id} style={{
              padding: '1rem 1.5rem', borderBottom: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem',
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.3rem' }}>
                  <span style={{ fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--accent)' }}>{cfg.asset}</span>
                  {cfg.dca_enabled && <span className="strategy-tag">DCA ${cfg.dca_amount_usd}/{(cfg.dca_interval_hours ?? 168) / 24}d</span>}
                  {cfg.va_enabled && <span className="strategy-tag">VA {cfg.va_target_growth_rate}%</span>}
                  {cfg.dip_enabled && <span className="strategy-tag">Dip Buy</span>}
                  {cfg.scale_out_enabled && <span className="strategy-tag">Scale-out +{cfg.scale_out_target_pct}%</span>}
                </div>
                {cfg.dca_next_at && <div style={{ fontSize: '.72rem', color: 'var(--text-dim)' }}>Next DCA: {new Date(cfg.dca_next_at).toLocaleString()}</div>}
                {cfg.scale_out_count > 0 && <div style={{ fontSize: '.72rem', color: 'var(--text-dim)' }}>Scale-outs: {cfg.scale_out_count}/{cfg.scale_out_max_transfers}</div>}
              </div>
              <button className="run-now-btn" onClick={() => setEditingAsset(cfg.asset)}>Edit</button>
            </div>
          ))}
          {editingAsset && (
            <ConfigForm
              asset={editingAsset === '__new__' ? '' : editingAsset}
              existing={configs.find(c => c.asset === editingAsset) || {}}
              onSave={saveConfig}
              onCancel={() => setEditingAsset(null)}
            />
          )}
        </div>
      </div>

      {/* Positions */}
      {portfolio?.positions?.length > 0 && (
        <div className="panel" style={{ marginTop: '1rem' }}>
          <div className="panel-header">
            <span className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
              <Wallet size={13} /> Holdings
            </span>
          </div>
          <div className="panel-body" style={{ padding: 0 }}>
            <div className="trades-table">
              <div className="trades-header" style={{ gridTemplateColumns: '1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr' }}>
                <span>Asset</span><span>Qty</span><span>Avg Cost</span><span>Price</span><span>Value</span><span>P&L</span><span>P&L%</span>
              </div>
              {portfolio.positions.map((p: any) => (
                <div key={p.asset} className="trades-row" style={{ gridTemplateColumns: '1fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr 0.8fr' }}>
                  <span style={{ fontFamily: 'var(--mono)', fontWeight: 600 }}>{p.asset}</span>
                  <span>{p.quantity.toFixed(4)}</span>
                  <span style={{ fontFamily: 'var(--mono)' }}>${p.avg_cost.toFixed(4)}</span>
                  <span style={{ fontFamily: 'var(--mono)' }}>${p.current_price.toFixed(4)}</span>
                  <span style={{ fontFamily: 'var(--mono)' }}>${p.value.toFixed(2)}</span>
                  <span className={p.unrealized_pnl >= 0 ? 'positive' : 'negative'} style={{ fontWeight: 600 }}>
                    {p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl.toFixed(2)}
                  </span>
                  <span className={p.unrealized_pnl >= 0 ? 'positive' : 'negative'}>
                    {p.unrealized_pnl_pct >= 0 ? '+' : ''}{p.unrealized_pnl_pct.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Manual actions */}
      <div style={{ display: 'flex', gap: '.5rem', marginTop: '1rem' }}>
        <button className="agent-btn" onClick={async () => { await api.post('accumulation/run-dca'); fetch(); }}
          style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          <RefreshCw size={14} /> Run DCA Now
        </button>
        <button className="agent-btn" onClick={async () => { await api.post('accumulation/run-scaleout'); fetch(); }}
          style={{ display: 'flex', alignItems: 'center', gap: '.3rem' }}>
          <ArrowUpFromLine size={14} /> Check Scale-outs
        </button>
      </div>
    </div>
  );
}

function ConfigForm({ asset, existing, onSave, onCancel }: {
  asset: string;
  existing: any;
  onSave: (asset: string, data: any) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    asset: asset,
    enabled: existing.enabled ?? true,
    dca_enabled: existing.dca_enabled ?? false,
    dca_amount_usd: existing.dca_amount_usd ?? 50,
    dca_interval_hours: existing.dca_interval_hours ?? 168,
    va_enabled: existing.va_enabled ?? false,
    va_target_growth_rate: existing.va_target_growth_rate ?? 1.0,
    va_period_hours: existing.va_period_hours ?? 168,
    dip_enabled: existing.dip_enabled ?? false,
    dip_levels: existing.dip_levels ?? [],
    scale_out_enabled: existing.scale_out_enabled ?? false,
    scale_out_target_pct: existing.scale_out_target_pct ?? 30.0,
    scale_out_tranche_pct: existing.scale_out_tranche_pct ?? 10.0,
    scale_out_max_transfers: existing.scale_out_max_transfers ?? 4,
  });

  return (
    <div style={{ padding: '1.25rem 1.5rem', borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '.75rem' }}>
        <div>
          <label className="form-label">Asset</label>
          <input className="settings-input" value={form.asset}
            onChange={e => setForm(f => ({ ...f, asset: e.target.value.toUpperCase() }))}
            placeholder="e.g. BTCUSDT" />
        </div>
        <div>
          <label className="form-label">Enabled</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginTop: '.5rem' }}>
            <input type="checkbox" checked={form.enabled}
              onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
              style={{ accentColor: 'var(--accent)' }} />
            <span>{form.enabled ? 'Active' : 'Inactive'}</span>
          </label>
        </div>
      </div>

      <p style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '1rem', marginBottom: '.5rem' }}>Dollar Cost Average</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '.75rem' }}>
        <div>
          <label className="form-label">Enable DCA</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginTop: '.3rem' }}>
            <input type="checkbox" checked={form.dca_enabled}
              onChange={e => setForm(f => ({ ...f, dca_enabled: e.target.checked }))}
              style={{ accentColor: 'var(--accent)' }} />
            <span>{form.dca_enabled ? 'ON' : 'OFF'}</span>
          </label>
        </div>
        <div>
          <label className="form-label">Amount per Buy (USDT)</label>
          <input type="number" step="10" min="10" className="settings-input"
            value={form.dca_amount_usd}
            onChange={e => setForm(f => ({ ...f, dca_amount_usd: parseFloat(e.target.value) }))} />
        </div>
        <div>
          <label className="form-label">Interval (hours)</label>
          <input type="number" step="24" min="24" className="settings-input"
            value={form.dca_interval_hours}
            onChange={e => setForm(f => ({ ...f, dca_interval_hours: parseInt(e.target.value) }))} />
        </div>
      </div>

      <p style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '1rem', marginBottom: '.5rem' }}>Value Averaging</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.75rem' }}>
        <div>
          <label className="form-label">Enable VA</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginTop: '.3rem' }}>
            <input type="checkbox" checked={form.va_enabled}
              onChange={e => setForm(f => ({ ...f, va_enabled: e.target.checked }))}
              style={{ accentColor: 'var(--accent)' }} />
            <span>{form.va_enabled ? 'ON' : 'OFF'}</span>
          </label>
        </div>
        <div>
          <label className="form-label">Target Growth % per Period</label>
          <input type="number" step="0.25" min="0.25" className="settings-input"
            value={form.va_target_growth_rate}
            onChange={e => setForm(f => ({ ...f, va_target_growth_rate: parseFloat(e.target.value) }))} />
        </div>
      </div>

      <p style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--text-secondary)', marginTop: '1rem', marginBottom: '.5rem' }}>Scale-out (Bull Market Profit-Taking)</p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '.75rem' }}>
        <div>
          <label className="form-label">Enable Scale-out</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginTop: '.3rem' }}>
            <input type="checkbox" checked={form.scale_out_enabled}
              onChange={e => setForm(f => ({ ...f, scale_out_enabled: e.target.checked }))}
              style={{ accentColor: 'var(--accent)' }} />
            <span>{form.scale_out_enabled ? 'ON' : 'OFF'}</span>
          </label>
        </div>
        <div>
          <label className="form-label">Gain % to Trigger</label>
          <input type="number" step="5" min="5" className="settings-input"
            value={form.scale_out_target_pct}
            onChange={e => setForm(f => ({ ...f, scale_out_target_pct: parseFloat(e.target.value) }))} />
        </div>
        <div>
          <label className="form-label">Sell % per Tranche</label>
          <input type="number" step="5" min="5" max="50" className="settings-input"
            value={form.scale_out_tranche_pct}
            onChange={e => setForm(f => ({ ...f, scale_out_tranche_pct: parseFloat(e.target.value) }))} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: '.5rem', marginTop: '1rem' }}>
        <button className="save-btn" onClick={() => onSave(form.asset || existing.asset, form)}>Save</button>
        <button className="cancel-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
