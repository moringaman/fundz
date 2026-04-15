import { useState } from 'react';
import { useStrategies, useUpdateStrategy, useResetStrategy } from '../hooks/useQueries';

// ── market condition colours ──────────────────────────────────────────────────
const CONDITION_STYLE: Record<string, string> = {
  trending_up:    'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
  trending_down:  'bg-rose-500/15    text-rose-400    border border-rose-500/30',
  ranging:        'bg-amber-500/15   text-amber-400   border border-amber-500/30',
  volatile:       'bg-orange-500/15  text-orange-400  border border-orange-500/30',
  consolidating:  'bg-sky-500/15     text-sky-400     border border-sky-500/30',
  pre_breakout:   'bg-violet-500/15  text-violet-400  border border-violet-500/30',
  low_volatility: 'bg-slate-500/15   text-slate-400   border border-slate-500/30',
};

const TIMEFRAME_OPTIONS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

// ── Types ─────────────────────────────────────────────────────────────────────
interface Strategy {
  value: string;
  label: string;
  description: string;
  timeframes: string[];
  defaultTf: string;
  risk: { stop_loss_pct?: number; take_profit_pct?: number; trailing_stop_pct?: number };
  yaml_risk: { stop_loss_pct?: number; take_profit_pct?: number; trailing_stop_pct?: number };
  market_conditions: string[];
  avoid_conditions: string[];
  enabled: boolean;
  has_overrides: boolean;
  require_marina: boolean;
  notes?: string;
}

// ── StrategyCard ──────────────────────────────────────────────────────────────
function StrategyCard({ strategy }: { strategy: Strategy }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    stop_loss_pct:        strategy.risk.stop_loss_pct ?? '',
    take_profit_pct:      strategy.risk.take_profit_pct ?? '',
    trailing_stop_pct:    strategy.risk.trailing_stop_pct ?? '',
    default_timeframe:    strategy.defaultTf,
    notes:                strategy.notes ?? '',
  });
  const [saving, setSaving] = useState(false);

  const updateMutation = useUpdateStrategy();
  const resetMutation  = useResetStrategy();

  const handleToggleEnabled = async () => {
    await updateMutation.mutateAsync({
      id: strategy.value,
      data: { enabled: !strategy.enabled },
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (form.stop_loss_pct     !== '') payload.default_stop_loss_pct     = Number(form.stop_loss_pct);
      if (form.take_profit_pct   !== '') payload.default_take_profit_pct   = Number(form.take_profit_pct);
      if (form.trailing_stop_pct !== '') payload.default_trailing_stop_pct = Number(form.trailing_stop_pct);
      if (form.default_timeframe)        payload.default_timeframe          = form.default_timeframe;
      if (form.notes !== undefined)      payload.notes                      = form.notes;
      await updateMutation.mutateAsync({ id: strategy.value, data: payload });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!confirm(`Reset "${strategy.label}" to YAML defaults?`)) return;
    await resetMutation.mutateAsync(strategy.value);
    setEditing(false);
  };

  return (
    <div className={`strategy-card ${!strategy.enabled ? 'strategy-card--disabled' : ''}`}>
      {/* Header row */}
      <div className="strategy-card__header">
        <div className="strategy-card__title-group">
          <span className="strategy-card__label">{strategy.label}</span>
          <code className="strategy-card__key">{strategy.value}</code>
          {strategy.require_marina && (
            <span className="strategy-card__badge strategy-card__badge--marina">Marina only</span>
          )}
          {strategy.has_overrides && (
            <span className="strategy-card__badge strategy-card__badge--override">overridden</span>
          )}
        </div>

        <div className="strategy-card__actions">
          {strategy.has_overrides && !editing && (
            <button
              className="strategy-card__btn strategy-card__btn--ghost"
              onClick={handleReset}
              title="Revert to YAML defaults"
            >
              ↺ reset
            </button>
          )}
          <button
            className="strategy-card__btn strategy-card__btn--edit"
            onClick={() => setEditing((e) => !e)}
          >
            {editing ? 'cancel' : 'edit'}
          </button>
          {/* Enable / Disable toggle */}
          <button
            className={`strategy-card__toggle ${strategy.enabled ? 'strategy-card__toggle--on' : 'strategy-card__toggle--off'}`}
            onClick={handleToggleEnabled}
            title={strategy.enabled ? 'Disable strategy' : 'Enable strategy'}
          >
            <span className="strategy-card__toggle-knob" />
          </button>
        </div>
      </div>

      {/* Description */}
      <p className="strategy-card__desc">{strategy.description}</p>

      {/* Condition pills */}
      <div className="strategy-card__pills">
        {strategy.market_conditions.map((c) => (
          <span key={c} className={`strategy-card__pill ${CONDITION_STYLE[c] ?? 'bg-slate-700 text-slate-300'}`}>
            ✓ {c.replace('_', ' ')}
          </span>
        ))}
        {strategy.avoid_conditions.map((c) => (
          <span key={c} className={`strategy-card__pill strategy-card__pill--avoid`}>
            ✗ {c.replace('_', ' ')}
          </span>
        ))}
      </div>

      {/* Risk params row (read-only when not editing) */}
      {!editing ? (
        <div className="strategy-card__params">
          <span className="strategy-card__param">
            <span className="strategy-card__param-label">SL</span>
            {strategy.risk.stop_loss_pct ?? '—'}%
          </span>
          <span className="strategy-card__param">
            <span className="strategy-card__param-label">TP</span>
            {strategy.risk.take_profit_pct ?? '—'}%
          </span>
          {strategy.risk.trailing_stop_pct != null && (
            <span className="strategy-card__param">
              <span className="strategy-card__param-label">Trail</span>
              {strategy.risk.trailing_stop_pct}%
            </span>
          )}
          <span className="strategy-card__param">
            <span className="strategy-card__param-label">TF</span>
            {strategy.defaultTf}
          </span>
        </div>
      ) : (
        /* Edit form */
        <div className="strategy-card__edit-form">
          <div className="strategy-card__edit-row">
            <label className="strategy-card__edit-label">
              Stop Loss %
              <span className="strategy-card__edit-hint">(YAML: {strategy.yaml_risk.stop_loss_pct}%)</span>
            </label>
            <input
              type="number"
              step="0.1"
              min="0.1"
              className="strategy-card__edit-input"
              value={form.stop_loss_pct}
              onChange={(e) => setForm((f) => ({ ...f, stop_loss_pct: e.target.value }))}
              placeholder={String(strategy.yaml_risk.stop_loss_pct ?? '')}
            />
          </div>
          <div className="strategy-card__edit-row">
            <label className="strategy-card__edit-label">
              Take Profit %
              <span className="strategy-card__edit-hint">(YAML: {strategy.yaml_risk.take_profit_pct}%)</span>
            </label>
            <input
              type="number"
              step="0.1"
              min="0.1"
              className="strategy-card__edit-input"
              value={form.take_profit_pct}
              onChange={(e) => setForm((f) => ({ ...f, take_profit_pct: e.target.value }))}
              placeholder={String(strategy.yaml_risk.take_profit_pct ?? '')}
            />
          </div>
          <div className="strategy-card__edit-row">
            <label className="strategy-card__edit-label">
              Trailing Stop %
              <span className="strategy-card__edit-hint">(YAML: {strategy.yaml_risk.trailing_stop_pct ?? 'none'})</span>
            </label>
            <input
              type="number"
              step="0.1"
              min="0"
              className="strategy-card__edit-input"
              value={form.trailing_stop_pct}
              onChange={(e) => setForm((f) => ({ ...f, trailing_stop_pct: e.target.value }))}
              placeholder={String(strategy.yaml_risk.trailing_stop_pct ?? '')}
            />
          </div>
          <div className="strategy-card__edit-row">
            <label className="strategy-card__edit-label">Default Timeframe</label>
            <select
              className="strategy-card__edit-input"
              value={form.default_timeframe}
              onChange={(e) => setForm((f) => ({ ...f, default_timeframe: e.target.value }))}
            >
              {strategy.timeframes.map((tf) => (
                <option key={tf} value={tf}>{tf}</option>
              ))}
              {/* include all timeframes as fallback */}
              {TIMEFRAME_OPTIONS.filter((tf) => !strategy.timeframes.includes(tf)).map((tf) => (
                <option key={tf} value={tf} disabled>{tf} (not in allowed list)</option>
              ))}
            </select>
          </div>
          <div className="strategy-card__edit-row strategy-card__edit-row--full">
            <label className="strategy-card__edit-label">Notes</label>
            <textarea
              className="strategy-card__edit-input strategy-card__edit-textarea"
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              placeholder="Optional admin notes for this override…"
              rows={2}
            />
          </div>
          <div className="strategy-card__edit-actions">
            <button
              className="strategy-card__btn strategy-card__btn--save"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? 'saving…' : 'save overrides'}
            </button>
            <button
              className="strategy-card__btn strategy-card__btn--ghost"
              onClick={handleReset}
            >
              ↺ revert to defaults
            </button>
          </div>
        </div>
      )}

      {/* Notes display */}
      {!editing && strategy.notes && (
        <p className="strategy-card__notes">📝 {strategy.notes}</p>
      )}
    </div>
  );
}

// ── StrategyManager ───────────────────────────────────────────────────────────
export function StrategyManager() {
  const { data: strategiesRaw = [], isLoading } = useStrategies();
  const strategies: Strategy[] = Array.isArray(strategiesRaw) ? strategiesRaw : [];
  const [filter, setFilter] = useState<'all' | 'enabled' | 'disabled'>('all');

  const visible = strategies.filter((s) => {
    if (filter === 'enabled')  return s.enabled;
    if (filter === 'disabled') return !s.enabled;
    return true;
  });

  const enabledCount  = strategies.filter((s) => s.enabled).length;
  const overrideCount = strategies.filter((s) => s.has_overrides).length;

  return (
    <div className="strategy-manager">
      <div className="strategy-manager__header">
        <div>
          <h2 className="strategy-manager__title">Strategy Registry</h2>
          <p className="strategy-manager__subtitle">
            Base definitions are in <code>registry.yaml</code> — overrides below are stored in the DB and merged at runtime.
          </p>
        </div>
        <div className="strategy-manager__stats">
          <span className="strategy-manager__stat">
            <span className="strategy-manager__stat-value strategy-manager__stat-value--green">{enabledCount}</span>
            enabled
          </span>
          <span className="strategy-manager__stat">
            <span className="strategy-manager__stat-value">{strategies.length - enabledCount}</span>
            disabled
          </span>
          {overrideCount > 0 && (
            <span className="strategy-manager__stat">
              <span className="strategy-manager__stat-value strategy-manager__stat-value--amber">{overrideCount}</span>
              overridden
            </span>
          )}
        </div>
      </div>

      <div className="strategy-manager__filter-bar">
        {(['all', 'enabled', 'disabled'] as const).map((f) => (
          <button
            key={f}
            className={`strategy-manager__filter-btn ${filter === f ? 'strategy-manager__filter-btn--active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="strategy-manager__loading">Loading strategies…</div>
      ) : (
        <div className="strategy-manager__grid">
          {visible.map((s) => (
            <StrategyCard key={s.value} strategy={s} />
          ))}
        </div>
      )}
    </div>
  );
}
