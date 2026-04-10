import { useState, useEffect, useCallback } from 'react';
import { Key, Brain, Save, Eye, EyeOff, Check, RefreshCw, Info, AlertTriangle, TrendingUp, Shield, Mail, Send, Bell, Wallet, Plus, Minus, MessageCircle, Volume2 } from 'lucide-react';
import { useSettings } from '../hooks/useQueries';
import { settingsApi, paperApi } from '../lib/api';
import { notificationService, type NotificationPreferences } from '../lib/notifications';
import { SoundSettings } from '../components/settings/SoundSettings';

type SettingsTab = 'api' | 'risk' | 'trading' | 'llm' | 'email' | 'notifications' | 'telegram' | 'sounds';

export function SettingsPage() {
  const { data: settingsData, refetch: refetchSettings } = useSettings();
  const [activeTab, setActiveTab] = useState<SettingsTab>('api');
  const [saving, setSaving] = useState(false);
  const [sendingEmail, setSendingEmail] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  // ── API Keys state ──
  const [showSecret, setShowSecret] = useState(false);
  const [apiForm, setApiForm] = useState({
    phemex_api_key: '',
    phemex_api_secret: '',
    phemex_testnet: true,
  });

  // ── Risk Limits state ──
  const [riskForm, setRiskForm] = useState({
    max_position_size_pct: 5,
    max_daily_loss_pct: 5,
    max_open_positions: 5,
    default_stop_loss_pct: 2,
    default_take_profit_pct: 4,
    max_leverage: 1,
    exposure_threshold_pct: 80,
  });

  // ── Trading Preferences state ──
  const [tradingForm, setTradingForm] = useState({
    default_symbol: 'BTCUSDT',
    default_timeframe: '1h',
    paper_trading_default: true,
    auto_confirm_orders: false,
    default_order_type: 'limit',
    trading_pairs: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT'] as string[],
  });
  const [newPairInput, setNewPairInput] = useState('');

  // ── LLM Config state ──
  const [llmForm, setLlmForm] = useState({
    provider: 'openrouter',
    model: 'openai/gpt-4o-mini',
    temperature: 0.7,
    max_tokens: 1000,
    openai_api_key: '',
    anthropic_api_key: '',
    openrouter_api_key: '',
  });

  // Hydrate forms from server data
  useEffect(() => {
    if (!settingsData) return;
    setApiForm(prev => ({ ...prev, phemex_testnet: settingsData.api_keys?.phemex_testnet ?? true }));
    if (settingsData.risk_limits) setRiskForm(settingsData.risk_limits);
    if (settingsData.trading) setTradingForm(settingsData.trading);
    if (settingsData.llm) {
      setLlmForm(prev => ({
        ...prev,
        provider: settingsData.llm.provider,
        model: settingsData.llm.model,
        temperature: settingsData.llm.temperature,
        max_tokens: settingsData.llm.max_tokens,
      }));
    }
  }, [settingsData]);

  // ── Telegram state ──
  const [telegramForm, setTelegramForm] = useState({
    bot_token: '',
    chat_id: '',
    enabled: false,
    trade_executed: true,
    trade_rejected: true,
    ta_veto: true,
    daily_loss_limit: true,
    position_closed: true,
    take_profit_hit: true,
    automation_start_stop: true,
    agent_error: true,
    api_error: true,
    daily_report: true,
    rebalance: false,
  });
  const [telegramLoaded, setTelegramLoaded] = useState(false);
  const [testingTelegram, setTestingTelegram] = useState(false);

  useEffect(() => {
    if (activeTab === 'telegram' && !telegramLoaded) {
      settingsApi.getTelegramSettings().then((data: unknown) => {
        setTelegramForm(data as typeof telegramForm);
        setTelegramLoaded(true);
      }).catch(console.warn);
    }
  }, [activeTab, telegramLoaded]);

  const handleSaveTelegram = async () => {
    setSaving(true);
    try {
      await settingsApi.updateTelegramSettings(telegramForm);
      showToast('Telegram settings saved', 'success');
      setTelegramLoaded(false); // force reload on next visit
    } catch {
      showToast('Failed to save Telegram settings', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleTestTelegram = async () => {
    setTestingTelegram(true);
    try {
      await settingsApi.testTelegram(telegramForm);
      showToast('Test message sent! Check your Telegram.', 'success');
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Test failed — check token and chat ID';
      showToast(msg, 'error');
    } finally {
      setTestingTelegram(false);
    }
  };

  // ── Notification Preferences state ──
  const [notifPrefs, setNotifPrefs] = useState<NotificationPreferences>(
    notificationService.getPreferences()
  );
  const [notifPermission, setNotifPermission] = useState<NotificationPermission>(
    notificationService.permission
  );

  const updateNotifPref = (key: keyof NotificationPreferences, value: boolean) => {
    const updated = { ...notifPrefs, [key]: value };
    setNotifPrefs(updated);
    notificationService.savePreferences(updated);
  };

  // ── Paper Wallet state ──
  const [walletBalances, setWalletBalances] = useState<{ asset: string; available: number; locked: number }[]>([]);
  const [walletLoading, setWalletLoading] = useState(false);
  const [depositAsset, setDepositAsset] = useState('USDT');
  const [depositAmount, setDepositAmount] = useState('');

  const fetchWalletBalances = useCallback(async () => {
    try {
      setWalletLoading(true);
      const res = await paperApi.getBalance();
      const data = Array.isArray(res.data) ? res.data : (Array.isArray(res) ? res : []);
      setWalletBalances(data);
    } catch (e) {
      console.warn('Failed to fetch wallet balances:', e);
    }
    finally { setWalletLoading(false); }
  }, []);

  useEffect(() => {
    if (activeTab === 'trading') fetchWalletBalances();
  }, [activeTab, fetchWalletBalances]);

  const handleAdjustBalance = async (amount: number) => {
    if (!depositAmount && amount === 0) return;
    const finalAmount = amount || parseFloat(depositAmount);
    if (isNaN(finalAmount) || finalAmount === 0) {
      showToast('Enter a valid amount', 'error');
      return;
    }
    try {
      await paperApi.adjustBalance(depositAsset, finalAmount);
      showToast(`${finalAmount > 0 ? 'Deposited' : 'Withdrew'} ${Math.abs(finalAmount)} ${depositAsset}`, 'success');
      setDepositAmount('');
      fetchWalletBalances();
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to adjust balance';
      showToast(msg, 'error');
    }
  };

  const requestNotifPermission = async () => {
    const perm = await notificationService.requestPermission();
    setNotifPermission(perm);
    if (perm === 'granted') {
      showToast('Notifications enabled!', 'success');
      // Send a test notification
      notificationService.notify('PX·AI Notifications Enabled', {
        body: 'You will now receive alerts for trades, risk events, and more.',
        tag: 'test',
      });
    } else {
      showToast('Notification permission denied', 'error');
    }
  };

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const handleSaveApiKeys = async () => {
    if (!apiForm.phemex_api_key || !apiForm.phemex_api_secret) {
      showToast('Both API key and secret are required', 'error');
      return;
    }
    setSaving(true);
    try {
      await settingsApi.updateApiKeys(apiForm);
      showToast('API keys saved successfully', 'success');
      setApiForm(prev => ({ ...prev, phemex_api_key: '', phemex_api_secret: '' }));
      refetchSettings();
    } catch (err) {
      showToast('Failed to save API keys', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveRiskLimits = async () => {
    setSaving(true);
    try {
      await settingsApi.updateRiskLimits(riskForm);
      showToast('Risk limits updated', 'success');
      refetchSettings();
    } catch (err) {
      showToast('Failed to update risk limits', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveTradingPrefs = async () => {
    setSaving(true);
    try {
      await settingsApi.updateTradingPrefs(tradingForm);
      showToast('Trading preferences updated', 'success');
      refetchSettings();
    } catch (err) {
      showToast('Failed to update trading preferences', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveLlmConfig = async () => {
    setSaving(true);
    try {
      const payload: Record<string, any> = {
        provider: llmForm.provider,
        model: llmForm.model,
        temperature: llmForm.temperature,
        max_tokens: llmForm.max_tokens,
      };
      if (llmForm.openai_api_key) payload.openai_api_key = llmForm.openai_api_key;
      if (llmForm.anthropic_api_key) payload.anthropic_api_key = llmForm.anthropic_api_key;
      if (llmForm.openrouter_api_key) payload.openrouter_api_key = llmForm.openrouter_api_key;
      await settingsApi.updateLlmConfig(payload);
      showToast('LLM configuration updated', 'success');
      setLlmForm(prev => ({ ...prev, openai_api_key: '', anthropic_api_key: '', openrouter_api_key: '' }));
      refetchSettings();
    } catch (err) {
      showToast('Failed to update LLM config', 'error');
    } finally {
      setSaving(false);
    }
  };

  const tabs: { id: SettingsTab; label: string; icon: React.ReactNode }[] = [
    { id: 'api', label: 'API Keys', icon: <Key size={14} /> },
    { id: 'risk', label: 'Risk Limits', icon: <Shield size={14} /> },
    { id: 'trading', label: 'Trading', icon: <TrendingUp size={14} /> },
    { id: 'llm', label: 'AI / LLM', icon: <Brain size={14} /> },
    { id: 'email', label: 'Email', icon: <Mail size={14} /> },
    { id: 'notifications', label: 'Notifications', icon: <Bell size={14} /> },
    { id: 'telegram', label: 'Telegram', icon: <MessageCircle size={14} /> },
    { id: 'sounds', label: 'Sounds', icon: <Volume2 size={14} /> },
  ];

  const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT'];
  const timeframes = ['1m', '5m', '15m', '1h', '4h', '1d'];
  const orderTypes = ['limit', 'market'];
  const llmProviders = ['openrouter', 'openai', 'anthropic', 'azure'];

  return (
    <div className="space-y-6">
      {/* Toast notification */}
      {toast && (
        <div
          style={{
            position: 'fixed', top: '1.5rem', right: '1.5rem', zIndex: 9999,
            padding: '.75rem 1.25rem', borderRadius: 8,
            background: toast.type === 'success' ? 'var(--green-dim)' : 'var(--red-dim)',
            border: `1px solid ${toast.type === 'success' ? 'rgba(0,230,118,.3)' : 'rgba(255,61,96,.3)'}`,
            color: toast.type === 'success' ? 'var(--green)' : 'var(--red)',
            fontSize: '.82rem', fontWeight: 600,
            display: 'flex', alignItems: 'center', gap: '.5rem',
            animation: 'fadeIn .2s ease-out',
          }}
        >
          {toast.type === 'success' ? <Check size={14} /> : <AlertTriangle size={14} />}
          {toast.message}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h1 className="page-title" style={{ marginBottom: 0 }}>Settings</h1>
        <button
          type="button"
          className="settings-btn"
          onClick={() => refetchSettings()}
          style={{ display: 'flex', alignItems: 'center', gap: '.35rem' }}
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {/* Tab navigation */}
      <div style={{
        display: 'flex', gap: '.35rem', padding: '.25rem',
        background: 'var(--bg-panel)', borderRadius: 10,
        border: '1px solid var(--border)',
      }}>
        {tabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            style={{
              flex: 1, padding: '.55rem .75rem', borderRadius: 7,
              border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '.4rem',
              fontSize: '.78rem', fontWeight: 600,
              fontFamily: 'var(--sans)',
              background: activeTab === tab.id ? 'var(--accent-dim)' : 'transparent',
              color: activeTab === tab.id ? 'var(--accent)' : 'var(--text-secondary)',
              transition: 'all .15s',
            }}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* ── API Keys Tab ── */}
      {activeTab === 'api' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Key size={16} style={{ color: 'var(--accent)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Phemex API Configuration</h2>
          </div>

          {/* Current key status */}
          {settingsData?.api_keys && (
            <div style={{
              padding: '.75rem 1rem', borderRadius: 8,
              background: settingsData.api_keys.has_phemex_key ? 'var(--green-dim)' : 'var(--amber-dim)',
              border: `1px solid ${settingsData.api_keys.has_phemex_key ? 'rgba(0,230,118,.2)' : 'rgba(255,179,0,.2)'}`,
              display: 'flex', alignItems: 'center', gap: '.6rem',
              fontSize: '.78rem',
            }}>
              <Info size={14} style={{ color: settingsData.api_keys.has_phemex_key ? 'var(--green)' : 'var(--amber)', flexShrink: 0 }} />
              <span style={{ color: settingsData.api_keys.has_phemex_key ? 'var(--green)' : 'var(--amber)' }}>
                {settingsData.api_keys.has_phemex_key
                  ? `API key configured (${settingsData.api_keys.key_hint}) — ${settingsData.api_keys.phemex_testnet ? 'Testnet' : 'Mainnet'}`
                  : 'No API key configured — set your Phemex credentials below'}
              </span>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">API Key</label>
            <input
              type="text"
              className="settings-input"
              placeholder="Enter your Phemex API key"
              value={apiForm.phemex_api_key}
              onChange={e => setApiForm({ ...apiForm, phemex_api_key: e.target.value })}
              autoComplete="off"
            />
          </div>

          <div className="form-group">
            <label className="form-label">API Secret</label>
            <div style={{ position: 'relative' }}>
              <input
                type={showSecret ? 'text' : 'password'}
                className="settings-input"
                style={{ paddingRight: '2.5rem' }}
                placeholder="Enter your Phemex API secret"
                value={apiForm.phemex_api_secret}
                onChange={e => setApiForm({ ...apiForm, phemex_api_secret: e.target.value })}
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                style={{
                  position: 'absolute', right: '.6rem', top: '.55rem',
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-secondary)', padding: 0,
                }}
              >
                {showSecret ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Network</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: '.75rem', marginTop: '.25rem' }}>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={apiForm.phemex_testnet}
                  onChange={e => setApiForm({ ...apiForm, phemex_testnet: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
              <span style={{ fontSize: '.8rem', color: apiForm.phemex_testnet ? 'var(--amber)' : 'var(--green)' }}>
                {apiForm.phemex_testnet ? '⚠ Testnet Mode' : '● Live / Mainnet'}
              </span>
            </div>
          </div>

          {!apiForm.phemex_testnet && (
            <div className="warning-banner" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <AlertTriangle size={14} />
              <span>Mainnet mode uses real funds. Ensure your credentials are correct before trading.</span>
            </div>
          )}

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveApiKeys}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save API Keys'}
          </button>
        </div>
      )}

      {/* ── Risk Limits Tab ── */}
      {activeTab === 'risk' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Shield size={16} style={{ color: 'var(--red)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Risk Management</h2>
          </div>

          <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', marginBottom: '.5rem' }}>
            Set guardrails to protect your capital. These limits apply across all agents and manual trades.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Max Position Size: {riskForm.max_position_size_pct}%</label>
              <input
                type="range" min="0.5" max="50" step="0.5"
                className="slider"
                value={riskForm.max_position_size_pct}
                onChange={e => setRiskForm({ ...riskForm, max_position_size_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>50%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Daily Loss: {riskForm.max_daily_loss_pct}%</label>
              <input
                type="range" min="0.5" max="25" step="0.5"
                className="slider"
                value={riskForm.max_daily_loss_pct}
                onChange={e => setRiskForm({ ...riskForm, max_daily_loss_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>25%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Stop Loss: {riskForm.default_stop_loss_pct}%</label>
              <input
                type="range" min="0.5" max="20" step="0.5"
                className="slider"
                value={riskForm.default_stop_loss_pct}
                onChange={e => setRiskForm({ ...riskForm, default_stop_loss_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>20%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Take Profit: {riskForm.default_take_profit_pct}%</label>
              <input
                type="range" min="0.5" max="50" step="0.5"
                className="slider"
                value={riskForm.default_take_profit_pct}
                onChange={e => setRiskForm({ ...riskForm, default_take_profit_pct: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>0.5%</span><span>50%</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Open Positions</label>
              <input
                type="number" min="1" max="50"
                className="settings-input"
                style={{ marginBottom: 0 }}
                value={riskForm.max_open_positions}
                onChange={e => setRiskForm({ ...riskForm, max_open_positions: parseInt(e.target.value) || 1 })}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Max Leverage: {riskForm.max_leverage}x</label>
              <input
                type="range" min="1" max="50" step="1"
                className="slider"
                value={riskForm.max_leverage}
                onChange={e => setRiskForm({ ...riskForm, max_leverage: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>1x</span><span>50x</span></div>
            </div>
          </div>

          {/* Exposure Threshold */}
          <div style={{ marginTop: '.25rem' }}>
            <label className="settings-label">
              Exposure Threshold — <span style={{ fontWeight: 700, color: riskForm.exposure_threshold_pct > 90 ? 'var(--red)' : riskForm.exposure_threshold_pct > 75 ? 'var(--amber)' : 'var(--green)' }}>{riskForm.exposure_threshold_pct}%</span>
            </label>
            <p style={{ fontSize: '.68rem', color: 'var(--text-secondary)', margin: '0 0 .35rem' }}>
              Elena flags "caution" when portfolio exposure exceeds this level
            </p>
            <input
              type="range" min="20" max="100" step="5"
              className="slider"
              value={riskForm.exposure_threshold_pct}
              onChange={e => setRiskForm({ ...riskForm, exposure_threshold_pct: parseFloat(e.target.value) })}
            />
            <div className="slider-labels"><span>20%</span><span>100%</span></div>
          </div>

          {/* Risk summary */}
          <div style={{
            marginTop: '.5rem', padding: '.75rem 1rem', borderRadius: 8,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '.75rem',
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Risk Profile</div>
              <div style={{
                fontSize: '.9rem', fontWeight: 700, marginTop: '.2rem',
                color: riskForm.max_leverage > 10 || riskForm.max_daily_loss_pct > 10 ? 'var(--red)' :
                       riskForm.max_leverage > 3 || riskForm.max_daily_loss_pct > 5 ? 'var(--amber)' : 'var(--green)',
              }}>
                {riskForm.max_leverage > 10 || riskForm.max_daily_loss_pct > 10 ? 'Aggressive' :
                 riskForm.max_leverage > 3 || riskForm.max_daily_loss_pct > 5 ? 'Moderate' : 'Conservative'}
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Risk:Reward</div>
              <div style={{ fontSize: '.9rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: '.2rem' }}>
                1:{(riskForm.default_take_profit_pct / riskForm.default_stop_loss_pct).toFixed(1)}
              </div>
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em' }}>Max Exposure</div>
              <div style={{ fontSize: '.9rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: '.2rem' }}>
                {(riskForm.max_position_size_pct * riskForm.max_open_positions).toFixed(0)}%
              </div>
            </div>
          </div>

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveRiskLimits}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save Risk Limits'}
          </button>
        </div>
      )}

      {/* ── Trading Preferences Tab ── */}
      {activeTab === 'trading' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <TrendingUp size={16} style={{ color: 'var(--green)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Trading Preferences</h2>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Default Symbol</label>
              <select
                className="settings-input"
                style={{ marginBottom: 0, cursor: 'pointer' }}
                value={tradingForm.default_symbol}
                onChange={e => setTradingForm({ ...tradingForm, default_symbol: e.target.value })}
              >
                {symbols.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Default Timeframe</label>
              <div style={{ display: 'flex', gap: '.3rem' }}>
                {timeframes.map(tf => (
                  <button
                    key={tf}
                    type="button"
                    onClick={() => setTradingForm({ ...tradingForm, default_timeframe: tf })}
                    style={{
                      flex: 1, padding: '.45rem .25rem', borderRadius: 6,
                      border: '1px solid',
                      borderColor: tradingForm.default_timeframe === tf ? 'var(--accent)' : 'var(--border-mid)',
                      background: tradingForm.default_timeframe === tf ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                      color: tradingForm.default_timeframe === tf ? 'var(--accent)' : 'var(--text-secondary)',
                      fontSize: '.72rem', fontWeight: 600, cursor: 'pointer',
                      fontFamily: 'var(--mono)',
                      transition: 'all .15s',
                    }}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Default Order Type</label>
              <div style={{ display: 'flex', gap: '.4rem' }}>
                {orderTypes.map(ot => (
                  <button
                    key={ot}
                    type="button"
                    onClick={() => setTradingForm({ ...tradingForm, default_order_type: ot })}
                    style={{
                      flex: 1, padding: '.5rem .75rem', borderRadius: 7,
                      border: '1px solid',
                      borderColor: tradingForm.default_order_type === ot ? 'var(--accent)' : 'var(--border-mid)',
                      background: tradingForm.default_order_type === ot ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                      color: tradingForm.default_order_type === ot ? 'var(--accent)' : 'var(--text-secondary)',
                      fontSize: '.78rem', fontWeight: 600, cursor: 'pointer',
                      fontFamily: 'var(--sans)', textTransform: 'capitalize',
                      transition: 'all .15s',
                    }}
                  >
                    {ot}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '.85rem', marginTop: '.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Paper Trading by Default</div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)' }}>New agents and manual trades use paper mode</div>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={tradingForm.paper_trading_default}
                  onChange={e => setTradingForm({ ...tradingForm, paper_trading_default: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Auto-Confirm Orders</div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)' }}>Execute agent signals without manual confirmation</div>
              </div>
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={tradingForm.auto_confirm_orders}
                  onChange={e => setTradingForm({ ...tradingForm, auto_confirm_orders: e.target.checked })}
                />
                <span className="toggle-slider" />
              </label>
            </div>
          </div>

          {tradingForm.auto_confirm_orders && (
            <div className="warning-banner" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <AlertTriangle size={14} />
              <span>Auto-confirm is enabled. Agent signals will be executed automatically without review.</span>
            </div>
          )}

          {/* Paper Wallet */}
          <div style={{ marginTop: '.75rem', borderTop: '1px solid var(--border)', paddingTop: '.75rem' }}>
            <label className="settings-label" style={{ marginBottom: '.5rem', display: 'flex', alignItems: 'center', gap: '.35rem' }}>
              <Wallet size={14} /> Paper Trading Wallet
            </label>
            <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)', marginBottom: '.5rem' }}>
              Add or withdraw funds to test how the fund behaves at different capital levels. Live account funds are managed on Phemex.
            </div>

            {walletLoading ? (
              <div style={{ fontSize: '.75rem', color: 'var(--text-secondary)' }}>Loading balances…</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.5rem', marginBottom: '.5rem' }}>
                {walletBalances.map(b => (
                  <div key={b.asset} style={{
                    background: 'var(--bg-secondary)',
                    borderRadius: 6,
                    padding: '.4rem .65rem',
                    fontSize: '.75rem',
                    minWidth: 100,
                  }}>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '.65rem' }}>{b.asset}</div>
                    <div style={{ fontWeight: 600, fontFamily: 'monospace', color: 'var(--text-primary)' }}>
                      {b.asset === 'USDT' ? `$${b.available.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : b.available.toLocaleString(undefined, { maximumFractionDigits: 8 })}
                    </div>
                    {b.locked > 0 && (
                      <div style={{ color: 'var(--text-tertiary)', fontSize: '.6rem' }}>Locked: {b.locked}</div>
                    )}
                  </div>
                ))}
                {walletBalances.length === 0 && (
                  <div style={{ fontSize: '.75rem', color: 'var(--text-secondary)' }}>No balances yet — deposit to get started.</div>
                )}
              </div>
            )}

            <div style={{ display: 'flex', gap: '.35rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                value={depositAsset}
                onChange={e => setDepositAsset(e.target.value)}
                style={{
                  padding: '.35rem .5rem',
                  borderRadius: 6,
                  border: '1px solid var(--border)',
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  fontSize: '.75rem',
                }}
              >
                <option value="USDT">USDT</option>
                {Array.from(new Set(
                  tradingForm.trading_pairs.map(p => p.replace('USDT', ''))
                )).sort().map(asset => (
                  <option key={asset} value={asset}>{asset}</option>
                ))}
              </select>
              <input
                type="number"
                placeholder="Amount"
                value={depositAmount}
                onChange={e => setDepositAmount(e.target.value)}
                style={{
                  padding: '.35rem .5rem',
                  borderRadius: 6,
                  border: '1px solid var(--border)',
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  fontSize: '.75rem',
                  width: 120,
                }}
                onKeyDown={e => { if (e.key === 'Enter') handleAdjustBalance(parseFloat(depositAmount)); }}
              />
              <button
                type="button"
                className="settings-btn"
                style={{ display: 'flex', alignItems: 'center', gap: '.25rem', fontSize: '.72rem' }}
                onClick={() => handleAdjustBalance(parseFloat(depositAmount))}
                disabled={!depositAmount || parseFloat(depositAmount) <= 0}
              >
                <Plus size={12} /> Deposit
              </button>
              <button
                type="button"
                className="settings-btn"
                style={{ display: 'flex', alignItems: 'center', gap: '.25rem', fontSize: '.72rem' }}
                onClick={() => handleAdjustBalance(-Math.abs(parseFloat(depositAmount)))}
                disabled={!depositAmount || parseFloat(depositAmount) <= 0}
              >
                <Minus size={12} /> Withdraw
              </button>
              <button
                type="button"
                className="settings-btn"
                style={{ display: 'flex', alignItems: 'center', gap: '.25rem', fontSize: '.72rem', marginLeft: 'auto' }}
                onClick={fetchWalletBalances}
              >
                <RefreshCw size={12} /> Refresh
              </button>
            </div>
          </div>

          {/* Trading Pairs Config */}
          <div style={{ marginTop: '.75rem', borderTop: '1px solid var(--border)', paddingTop: '.75rem' }}>
            <label className="settings-label" style={{ marginBottom: '.35rem', display: 'block' }}>
              Trading Pairs — <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>{tradingForm.trading_pairs.length} pairs</span>
            </label>
            <p style={{ fontSize: '.68rem', color: 'var(--text-secondary)', margin: '0 0 .5rem' }}>
              Marcus runs technical analysis on all pairs. Agents and the ticker strip also use this list.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.35rem', marginBottom: '.5rem' }}>
              {tradingForm.trading_pairs.map((pair) => (
                <span key={pair} style={{
                  display: 'inline-flex', alignItems: 'center', gap: '.3rem',
                  padding: '.25rem .55rem', borderRadius: '14px', fontSize: '.72rem', fontWeight: 600,
                  background: 'var(--accent-dim)', color: 'var(--accent)',
                  border: '1px solid rgba(99,102,241,.25)',
                }}>
                  {pair}
                  <button
                    type="button"
                    onClick={() => setTradingForm({
                      ...tradingForm,
                      trading_pairs: tradingForm.trading_pairs.filter(p => p !== pair)
                    })}
                    style={{
                      background: 'none', border: 'none', color: 'var(--text-dim)',
                      cursor: 'pointer', fontSize: '.85rem', lineHeight: 1, padding: 0,
                    }}
                    title={`Remove ${pair}`}
                  >×</button>
                </span>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '.35rem' }}>
              <input
                type="text"
                className="settings-input"
                placeholder="e.g. DOGEUSDT"
                value={newPairInput}
                onChange={(e) => setNewPairInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    const p = newPairInput.trim();
                    if (p && !tradingForm.trading_pairs.includes(p)) {
                      setTradingForm({ ...tradingForm, trading_pairs: [...tradingForm.trading_pairs, p] });
                      setNewPairInput('');
                    }
                  }
                }}
                style={{ flex: 1, marginBottom: 0 }}
              />
              <button
                type="button"
                className="settings-btn"
                onClick={() => {
                  const p = newPairInput.trim();
                  if (p && !tradingForm.trading_pairs.includes(p)) {
                    setTradingForm({ ...tradingForm, trading_pairs: [...tradingForm.trading_pairs, p] });
                    setNewPairInput('');
                  }
                }}
                style={{ padding: '.4rem .75rem', fontSize: '.75rem' }}
              >
                Add
              </button>
            </div>
          </div>

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveTradingPrefs}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save Preferences'}
          </button>
        </div>
      )}

      {/* ── LLM / AI Tab ── */}
      {activeTab === 'llm' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Brain size={16} style={{ color: 'var(--accent)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>AI / LLM Configuration</h2>
          </div>

          <div className="form-group">
            <label className="form-label">Provider</label>
            <div style={{ display: 'flex', gap: '.35rem' }}>
              {llmProviders.map(p => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setLlmForm({ ...llmForm, provider: p })}
                  style={{
                    flex: 1, padding: '.5rem .5rem', borderRadius: 7,
                    border: '1px solid',
                    borderColor: llmForm.provider === p ? 'var(--accent)' : 'var(--border-mid)',
                    background: llmForm.provider === p ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                    color: llmForm.provider === p ? 'var(--accent)' : 'var(--text-secondary)',
                    fontSize: '.75rem', fontWeight: 600, cursor: 'pointer',
                    fontFamily: 'var(--sans)', textTransform: 'capitalize',
                    transition: 'all .15s',
                  }}
                >
                  {p === 'openrouter' ? 'OpenRouter' : p === 'openai' ? 'OpenAI' : p === 'anthropic' ? 'Anthropic' : 'Azure'}
                </button>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Model</label>
            <input
              type="text"
              className="settings-input"
              value={llmForm.model}
              onChange={e => setLlmForm({ ...llmForm, model: e.target.value })}
              placeholder="e.g. openai/gpt-4o-mini"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div className="form-group">
              <label className="form-label">Temperature: {llmForm.temperature.toFixed(1)}</label>
              <input
                type="range" min="0" max="2" step="0.1"
                className="slider"
                value={llmForm.temperature}
                onChange={e => setLlmForm({ ...llmForm, temperature: parseFloat(e.target.value) })}
              />
              <div className="slider-labels"><span>Precise (0)</span><span>Creative (2)</span></div>
            </div>

            <div className="form-group">
              <label className="form-label">Max Tokens</label>
              <input
                type="number" min="100" max="32000" step="100"
                className="settings-input"
                style={{ marginBottom: 0 }}
                value={llmForm.max_tokens}
                onChange={e => setLlmForm({ ...llmForm, max_tokens: parseInt(e.target.value) || 1000 })}
              />
            </div>
          </div>

          {/* Provider API key status indicators */}
          <div style={{
            padding: '.75rem 1rem', borderRadius: 8,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          }}>
            <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.07em', marginBottom: '.6rem' }}>
              Provider Key Status
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '.4rem' }}>
              {[
                { label: 'OpenAI', has: settingsData?.llm?.has_openai_key },
                { label: 'Anthropic', has: settingsData?.llm?.has_anthropic_key },
                { label: 'OpenRouter', has: settingsData?.llm?.has_openrouter_key },
                { label: 'Azure', has: settingsData?.llm?.has_azure_key },
              ].map(({ label, has }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '.4rem', fontSize: '.75rem' }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: has ? 'var(--green)' : 'var(--text-dim)',
                    flexShrink: 0,
                  }} />
                  <span style={{ color: has ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{label}</span>
                  <span style={{ color: has ? 'var(--green)' : 'var(--text-dim)', fontSize: '.7rem' }}>
                    {has ? 'Configured' : 'Not set'}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Conditional API key input based on selected provider */}
          {llmForm.provider === 'openai' && (
            <div className="form-group">
              <label className="form-label">OpenAI API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_openai_key ? 'Key already set — enter new value to update' : 'sk-...'}
                value={llmForm.openai_api_key}
                onChange={e => setLlmForm({ ...llmForm, openai_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}
          {llmForm.provider === 'anthropic' && (
            <div className="form-group">
              <label className="form-label">Anthropic API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_anthropic_key ? 'Key already set — enter new value to update' : 'sk-ant-...'}
                value={llmForm.anthropic_api_key}
                onChange={e => setLlmForm({ ...llmForm, anthropic_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}
          {llmForm.provider === 'openrouter' && (
            <div className="form-group">
              <label className="form-label">OpenRouter API Key</label>
              <input
                type="password"
                className="settings-input"
                placeholder={settingsData?.llm?.has_openrouter_key ? 'Key already set — enter new value to update' : 'sk-or-...'}
                value={llmForm.openrouter_api_key}
                onChange={e => setLlmForm({ ...llmForm, openrouter_api_key: e.target.value })}
                autoComplete="off"
              />
            </div>
          )}

          <button
            type="button"
            className="settings-btn"
            onClick={handleSaveLlmConfig}
            disabled={saving}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: saving ? 0.6 : 1 }}
          >
            <Save size={13} /> {saving ? 'Saving…' : 'Save LLM Config'}
          </button>
        </div>
      )}

      {/* ── Email Tab ── */}
      {activeTab === 'email' && (
        <div className="settings-card space-y-4">
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
            <Mail size={16} style={{ color: 'var(--accent)' }} />
            <h2 className="settings-title" style={{ marginBottom: 0 }}>Daily Email Summary</h2>
          </div>

          <p style={{ fontSize: '.8rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '1rem' }}>
            A daily trading summary email is sent automatically at <strong>5:00 PM</strong> to&nbsp;
            <span style={{ color: 'var(--accent)', fontFamily: 'var(--mono)' }}>trading@webnostix.co.uk</span>.
            The email is composed by Victoria (CIO) using AI, covering P&L, agent performance, risk levels, and market conditions.
          </p>

          <div style={{ padding: '.75rem', background: 'var(--bg-hover)', borderRadius: '6px', fontSize: '.75rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)', display: 'flex', flexDirection: 'column', gap: '.35rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Mail Server</span>
              <span style={{ color: 'var(--text-secondary)' }}>wx-microservice-email.herokuapp.com</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Recipient</span>
              <span style={{ color: 'var(--text-secondary)' }}>trading@webnostix.co.uk</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Schedule</span>
              <span style={{ color: 'var(--text-secondary)' }}>Daily at 5:00 PM</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>API Key</span>
              <span style={{ color: 'var(--green)' }}>Configured ✓</span>
            </div>
          </div>

          <button
            type="button"
            className="settings-btn"
            onClick={async () => {
              setSendingEmail(true);
              try {
                const resp = await fetch('/api/settings/test-email', { method: 'POST' });
                const data = await resp.json();
                if (resp.ok) {
                  showToast(data.message || 'Test email sent!', 'success');
                } else {
                  showToast(data.detail || 'Email failed', 'error');
                }
              } catch {
                showToast('Failed to send test email', 'error');
              } finally {
                setSendingEmail(false);
              }
            }}
            disabled={sendingEmail}
            style={{ display: 'flex', alignItems: 'center', gap: '.35rem', opacity: sendingEmail ? 0.6 : 1, marginTop: '.5rem' }}
          >
            <Send size={13} /> {sendingEmail ? 'Sending…' : 'Send Test Email'}
          </button>
        </div>
      )}

      {/* ── Notifications Tab ── */}
      {activeTab === 'notifications' && (
        <div className="settings-panel" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <h3 className="settings-section-title"><Bell size={15} /> Browser Notifications</h3>

          {/* Permission status */}
          <div style={{
            padding: '.75rem 1rem', borderRadius: 8,
            background: notifPermission === 'granted' ? 'rgba(0,230,118,.08)' : 'rgba(255,193,7,.08)',
            border: `1px solid ${notifPermission === 'granted' ? 'rgba(0,230,118,.25)' : 'rgba(255,193,7,.25)'}`,
            display: 'flex', alignItems: 'center', gap: '.75rem',
          }}>
            <Bell size={18} style={{ color: notifPermission === 'granted' ? 'var(--green)' : 'var(--amber)' }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: '.82rem' }}>
                {notifPermission === 'granted' ? 'Notifications Enabled' :
                 notifPermission === 'denied' ? 'Notifications Blocked' :
                 'Notifications Not Yet Enabled'}
              </div>
              <div style={{ fontSize: '.72rem', color: 'var(--text-secondary)' }}>
                {notifPermission === 'granted'
                  ? 'You will receive desktop notifications when the tab is in the background.'
                  : notifPermission === 'denied'
                  ? 'Please enable notifications in your browser settings for this site.'
                  : 'Click the button to enable desktop notifications for trade alerts and risk events.'}
              </div>
            </div>
            {notifPermission !== 'granted' && notifPermission !== 'denied' && (
              <button
                type="button"
                className="settings-btn"
                onClick={requestNotifPermission}
                style={{ display: 'flex', alignItems: 'center', gap: '.35rem', whiteSpace: 'nowrap' }}
              >
                <Bell size={13} /> Enable Notifications
              </button>
            )}
          </div>

          {/* Master toggle */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '.6rem 0', borderBottom: '1px solid var(--border)',
          }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: '.82rem' }}>Enable All Notifications</div>
              <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)' }}>
                Master switch for all browser notification types
              </div>
            </div>
            <label style={{ position: 'relative', display: 'inline-block', width: 44, height: 24, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={notifPrefs.enabled}
                onChange={(e) => updateNotifPref('enabled', e.target.checked)}
                style={{ opacity: 0, width: 0, height: 0 }}
              />
              <span style={{
                position: 'absolute', inset: 0, borderRadius: 12,
                background: notifPrefs.enabled ? 'var(--accent)' : 'var(--surface-2, #3a3d45)',
                transition: 'background .2s',
              }}>
                <span style={{
                  position: 'absolute', top: 2, left: notifPrefs.enabled ? 22 : 2,
                  width: 20, height: 20, borderRadius: '50%',
                  background: '#fff', transition: 'left .2s',
                }} />
              </span>
            </label>
          </div>

          {/* Individual toggles */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '.1rem', opacity: notifPrefs.enabled ? 1 : 0.4, pointerEvents: notifPrefs.enabled ? 'auto' : 'none' }}>
            {([
              { key: 'tradeExecuted' as const, label: 'Trade Executed', desc: 'When an agent opens a new position', icon: '📈' },
              { key: 'positionClosed' as const, label: 'Position Closed (SL/TP)', desc: 'When stop-loss, take-profit, or trailing stop triggers', icon: '🔔' },
              { key: 'riskAlert' as const, label: 'Risk Alerts', desc: 'When Elena flags danger or high exposure', icon: '⚠️' },
              { key: 'allocation' as const, label: 'Portfolio Rebalance', desc: 'When James reallocates between agents', icon: '⚖️' },
              { key: 'dailyReport' as const, label: 'Daily Report', desc: 'When the daily fund summary is ready', icon: '📊' },
              { key: 'agentError' as const, label: 'Agent Errors', desc: 'When an agent fails or goes offline', icon: '🚨' },
            ]).map(item => (
              <div
                key={item.key}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '.6rem .25rem', borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
                  <span style={{ fontSize: '1.1rem' }}>{item.icon}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '.8rem' }}>{item.label}</div>
                    <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)' }}>{item.desc}</div>
                  </div>
                </div>
                <label style={{ position: 'relative', display: 'inline-block', width: 40, height: 22, cursor: 'pointer', flexShrink: 0 }}>
                  <input
                    type="checkbox"
                    checked={notifPrefs[item.key]}
                    onChange={(e) => updateNotifPref(item.key, e.target.checked)}
                    style={{ opacity: 0, width: 0, height: 0 }}
                  />
                  <span style={{
                    position: 'absolute', inset: 0, borderRadius: 11,
                    background: notifPrefs[item.key] ? 'var(--accent)' : 'var(--surface-2, #3a3d45)',
                    transition: 'background .2s',
                  }}>
                    <span style={{
                      position: 'absolute', top: 2, left: notifPrefs[item.key] ? 20 : 2,
                      width: 18, height: 18, borderRadius: '50%',
                      background: '#fff', transition: 'left .2s',
                    }} />
                  </span>
                </label>
              </div>
            ))}
          </div>

          {/* Test notification */}
          {notifPermission === 'granted' && (
            <button
              type="button"
              className="settings-btn"
              onClick={() => {
                notificationService.notify('🧪 Test Notification', {
                  body: 'If you see this, browser notifications are working correctly!',
                  tag: 'test',
                });
                showToast('Test notification sent!', 'success');
              }}
              style={{ display: 'flex', alignItems: 'center', gap: '.35rem', marginTop: '.25rem', alignSelf: 'flex-start' }}
            >
              <Send size={13} /> Send Test Notification
            </button>
          )}
        </div>
      )}

      {/* ── Telegram Tab ── */}
      {activeTab === 'telegram' && (
        <div className="settings-panel" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <h3 className="settings-section-title"><MessageCircle size={15} /> Telegram Alerts</h3>

          {/* Setup instructions */}
          <div style={{
            padding: '.75rem 1rem', borderRadius: 8,
            background: 'rgba(0,149,255,.06)', border: '1px solid rgba(0,149,255,.2)',
            fontSize: '.75rem', color: 'var(--text-secondary)', lineHeight: 1.6,
          }}>
            <span style={{ fontWeight: 600, color: 'var(--accent)' }}>Setup:</span>
            {' '}1. Message <code style={{ background: 'var(--surface-2)', padding: '1px 4px', borderRadius: 3 }}>@BotFather</code> on Telegram → /newbot → copy the token.
            {' '}2. Add the bot to a chat or channel.
            {' '}3. Message <code style={{ background: 'var(--surface-2)', padding: '1px 4px', borderRadius: 3 }}>@userinfobot</code> to get your chat ID.
          </div>

          {/* Credentials */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
            <div>
              <label className="settings-label">Bot Token</label>
              <input
                className="settings-input"
                type="password"
                placeholder="123456789:ABCdefGHI..."
                value={telegramForm.bot_token}
                onChange={e => setTelegramForm(f => ({ ...f, bot_token: e.target.value }))}
              />
            </div>
            <div>
              <label className="settings-label">Chat ID</label>
              <input
                className="settings-input"
                type="text"
                placeholder="-1001234567890"
                value={telegramForm.chat_id}
                onChange={e => setTelegramForm(f => ({ ...f, chat_id: e.target.value }))}
              />
              <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)', marginTop: '.25rem' }}>
                Use a negative number for groups/channels, positive for direct messages.
              </div>
            </div>
          </div>

          {/* Master enable toggle */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '.75rem 1rem', borderRadius: 8,
            background: telegramForm.enabled ? 'rgba(0,230,118,.06)' : 'var(--surface-2, #2a2d35)',
            border: `1px solid ${telegramForm.enabled ? 'rgba(0,230,118,.2)' : 'var(--border)'}`,
            transition: 'all .2s',
          }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: '.85rem' }}>
                {telegramForm.enabled ? '✅ Alerts Enabled' : '⏸ Alerts Disabled'}
              </div>
              <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)', marginTop: '.15rem' }}>
                Master switch — disables all Telegram notifications when off
              </div>
            </div>
            <label style={{ position: 'relative', display: 'inline-block', width: 44, height: 24, cursor: 'pointer' }}>
              <input type="checkbox" checked={telegramForm.enabled}
                onChange={e => setTelegramForm(f => ({ ...f, enabled: e.target.checked }))}
                style={{ opacity: 0, width: 0, height: 0 }} />
              <span style={{
                position: 'absolute', inset: 0, borderRadius: 12,
                background: telegramForm.enabled ? 'var(--green, #00e676)' : 'var(--surface-2, #3a3d45)',
                transition: 'background .2s',
              }}>
                <span style={{
                  position: 'absolute', top: 2, left: telegramForm.enabled ? 22 : 2,
                  width: 20, height: 20, borderRadius: '50%', background: '#fff', transition: 'left .2s',
                }} />
              </span>
            </label>
          </div>

          {/* Per-event toggles */}
          <div style={{ opacity: telegramForm.enabled ? 1 : 0.45, pointerEvents: telegramForm.enabled ? 'auto' : 'none', transition: 'opacity .2s' }}>
            <div style={{ fontSize: '.72rem', fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.6rem' }}>
              Alert Types
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '.05rem' }}>
              {([
                { key: 'trade_executed', label: 'Trade Executed', desc: 'Position opened (paper or live)', icon: '📈' },
                { key: 'trade_rejected', label: 'Trade Rejected', desc: 'Risk Manager blocks a trade', icon: '🚫' },
                { key: 'ta_veto', label: 'TA Veto', desc: "Marcus's opposing signal blocks a trade", icon: '⚠️' },
                { key: 'daily_loss_limit', label: 'Daily Loss Limit', desc: 'Daily loss cap reached — trading halted', icon: '🚨' },
                { key: 'position_closed', label: 'Position Closed (SL)', desc: 'Stop-loss or trailing stop triggered', icon: '🔴' },
                { key: 'take_profit_hit', label: 'Take Profit Hit', desc: 'Position closed at target price', icon: '💰' },
                { key: 'automation_start_stop', label: 'Automation On/Off', desc: 'Trading engine started or stopped', icon: '🟢' },
                { key: 'agent_error', label: 'Agent Errors', desc: 'Strategy crashes or encounters errors', icon: '❌' },
                { key: 'api_error', label: 'API Errors', desc: 'Exchange connection issues', icon: '🔌' },
                { key: 'daily_report', label: 'Daily Report', desc: 'End-of-day P&L summary', icon: '📊' },
                { key: 'rebalance', label: 'Portfolio Rebalance', desc: "James's allocation changes (can be noisy)", icon: '⚖️' },
              ] as { key: keyof typeof telegramForm; label: string; desc: string; icon: string }[]).map(item => (
                <div key={item.key} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '.55rem .25rem', borderBottom: '1px solid var(--border)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
                    <span style={{ fontSize: '1rem', width: 22, textAlign: 'center' }}>{item.icon}</span>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '.8rem' }}>{item.label}</div>
                      <div style={{ fontSize: '.67rem', color: 'var(--text-secondary)' }}>{item.desc}</div>
                    </div>
                  </div>
                  <label style={{ position: 'relative', display: 'inline-block', width: 40, height: 22, cursor: 'pointer', flexShrink: 0 }}>
                    <input type="checkbox"
                      checked={telegramForm[item.key] as boolean}
                      onChange={e => setTelegramForm(f => ({ ...f, [item.key]: e.target.checked }))}
                      style={{ opacity: 0, width: 0, height: 0 }} />
                    <span style={{
                      position: 'absolute', inset: 0, borderRadius: 11,
                      background: (telegramForm[item.key] as boolean) ? 'var(--accent)' : 'var(--surface-2, #3a3d45)',
                      transition: 'background .2s',
                    }}>
                      <span style={{
                        position: 'absolute', top: 2, left: (telegramForm[item.key] as boolean) ? 20 : 2,
                        width: 18, height: 18, borderRadius: '50%', background: '#fff', transition: 'left .2s',
                      }} />
                    </span>
                  </label>
                </div>
              ))}
            </div>
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '.75rem', marginTop: '.25rem' }}>
            <button
              type="button"
              className="settings-btn"
              onClick={handleTestTelegram}
              disabled={testingTelegram || !telegramForm.chat_id}
              style={{ display: 'flex', alignItems: 'center', gap: '.35rem' }}
            >
              {testingTelegram ? <RefreshCw size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Send size={13} />}
              Send Test Message
            </button>
            <button
              type="button"
              className="settings-btn settings-btn-primary"
              onClick={handleSaveTelegram}
              disabled={saving}
              style={{ display: 'flex', alignItems: 'center', gap: '.35rem' }}
            >
              {saving ? <RefreshCw size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
              Save Settings
            </button>
          </div>
        </div>
      )}

      {activeTab === 'sounds' && (
        <SoundSettings />
      )}
    </div>
  );
}
