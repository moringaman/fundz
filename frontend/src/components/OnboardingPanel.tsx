import { useState, useEffect, useCallback } from 'react';
import { CheckCircle, Circle, ArrowRight, Brain, Key, Zap, Shield, ExternalLink, AlertTriangle } from 'lucide-react';
import { settingsApi } from '../lib/api';

interface OnboardingState {
  hasLlm: boolean;
  hasExchange: boolean;
  loading: boolean;
}

export function useOnboarding() {
  const [state, setState] = useState<OnboardingState>({ hasLlm: false, hasExchange: false, loading: true });
  const [acceptedDisclaimer, setAcceptedDisclaimer] = useState(
    () => localStorage.getItem('disclaimer-accepted') === 'true'
  );

  const check = useCallback(async () => {
    try {
      const [llmResp, exchResp] = await Promise.allSettled([
        settingsApi.listLlmCredentials(),
        settingsApi.listExchangeCredentials(),
      ]);
      // On 401 (not authenticated yet) we assume credentials may exist
      // so the onboarding doesn't lock the user out.
      const llm = llmResp.status === 'fulfilled' ? llmResp.value as Record<string, any> : {};
      const exch = exchResp.status === 'fulfilled' ? exchResp.value as Record<string, any> : {};
      const hadApiError = llmResp.status === 'rejected' || exchResp.status === 'rejected';
      const hasLlm = hadApiError || Object.values(llm || {}).some(
        (v: any) => v?.has_key || v?.has_endpoint
      );
      const hasExchange = hadApiError || (Object.keys(exch || {}).length > 0 && Object.values(exch as Record<string, any[]>).some(
        arr => Array.isArray(arr) && arr.some((e: any) => e?.has_value)
      ));
      setState({ hasLlm, hasExchange, loading: false });
    } catch {
      setState(s => ({ ...s, loading: false }));
    }
  }, []);

  useEffect(() => { check(); }, [check]);

  const acceptDisclaimer = () => {
    localStorage.setItem('disclaimer-accepted', 'true');
    setAcceptedDisclaimer(true);
  };

  return { ...state, acceptedDisclaimer, acceptDisclaimer, refresh: check };
}

const STEPS = [
  {
    id: 'llm' as const,
    label: 'Connect an AI provider',
    title: 'What is an LLM?',
    body: `AI trading agents need a large language model (LLM) to analyze markets and make decisions — think of it as the "brain" behind each agent.

You don't need to run anything locally. Services like OpenRouter give you one API key that works with 200+ models. Setting up takes about 60 seconds.`,
    links: [
      { label: 'Get an OpenRouter key (free tier available)', url: 'https://openrouter.ai/keys' },
      { label: 'Or get an OpenAI API key', url: 'https://platform.openai.com/api-keys' },
      { label: 'Or get an Anthropic API key', url: 'https://console.anthropic.com/' },
    ],
    action: 'Go to Settings',
    navigateTo: '/settings',
  },
  {
    id: 'exchange' as const,
    label: 'Connect an exchange',
    title: 'What is a crypto exchange?',
    body: `To trade real funds, you need API access to a cryptocurrency exchange — the marketplace where buying and selling happens.

Paper trading (simulated) only needs step 1 above. Live trading requires exchange credentials. Your keys are encrypted and never stored in plaintext.`,
    links: [
      { label: 'Create a Phemex account & API keys', url: 'https://phemex.com/' },
      { label: 'Get started with Hyperliquid', url: 'https://app.hyperliquid.xyz/' },
      { label: 'About paper trading (no real money)', url: 'https://www.investopedia.com/terms/p/papertrade.asp' },
    ],
    action: 'Go to Settings',
    navigateTo: '/settings',
  },
];

const DISCLAIMER_TEXT = `IMPORTANT RISK DISCLOSURE

By using this platform, you acknowledge and agree that:

1. Cryptocurrency trading involves substantial risk of loss and is not suitable for all investors. You may lose some or all of your invested capital.

2. This software is provided "as is" for educational and informational purposes only. It does not constitute financial advice, investment recommendations, or solicitation to trade.

3. Past performance of trading strategies, backtests, or AI agents does not guarantee future results. Market conditions change and models can fail.

4. You are solely responsible for all trading decisions and their financial outcomes. The platform operators bear no liability for any losses incurred.

5. API keys and credentials are your responsibility. Keep them secure. The platform encrypts stored credentials but cannot guarantee absolute security.

6. You should never trade with funds you cannot afford to lose. Consider consulting a qualified financial advisor before engaging in live trading.

By clicking "I Understand and Accept", you confirm you have read, understood, and agree to these terms.`;

interface OnboardingOverlayProps {
  hasLlm: boolean;
  hasExchange: boolean;
  acceptedDisclaimer: boolean;
  onAcceptDisclaimer: () => void;
  onRefresh: () => void;
  onNavigate: (path: string) => void;
  showLiveGate?: boolean;
}

export function OnboardingOverlay({
  hasLlm, hasExchange, acceptedDisclaimer, onAcceptDisclaimer, onRefresh, onNavigate, showLiveGate,
}: OnboardingOverlayProps) {
  const [step, setStep] = useState(0);
  const currentStep = STEPS[step];

  // Once disclaimer accepted + step 1 done, overlay hides
  const showOverlay = !acceptedDisclaimer || !hasLlm;

  // Live trading gate
  if (showLiveGate && !hasExchange && acceptedDisclaimer) {
    return (
      <div style={overlayStyle}>
        <div style={modalStyle}>
          <AlertTriangle size={32} style={{ color: 'var(--amber)', marginBottom: '.5rem' }} />
          <h2 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0 0 .5rem', color: 'var(--text)' }}>
            Live Trading Unavailable
          </h2>
          <p style={{ fontSize: '.78rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '1rem' }}>
            You need exchange credentials before you can trade with real funds.
            Paper trading (simulated) is available now with just an LLM provider.
          </p>
          <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
            <button onClick={() => onNavigate('/settings')} className="settings-btn" style={{ fontSize: '.78rem', padding: '.5rem 1rem' }}>
              Add Exchange Keys
            </button>
            <button onClick={() => window.dispatchEvent(new CustomEvent('live-gate-dismiss'))}
              style={{ fontSize: '.78rem', padding: '.5rem 1rem', background: 'none', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-secondary)', cursor: 'pointer' }}>
              Stay in Paper Mode
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!showOverlay) return null;

  // Disclaimer step
  if (!acceptedDisclaimer) {
    return (
      <div style={overlayStyle}>
        <div style={{ ...modalStyle, maxWidth: 560 }}>
          <Shield size={32} style={{ color: 'var(--accent)', marginBottom: '.5rem' }} />
          <h2 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0 0 .75rem', color: 'var(--text)' }}>
            Before you begin
          </h2>
          <div style={{
            fontSize: '.7rem', color: 'var(--text-secondary)', lineHeight: 1.6,
            whiteSpace: 'pre-wrap', marginBottom: '1rem',
            maxHeight: 300, overflow: 'auto', padding: '.75rem',
            background: 'var(--bg-elevated)', borderRadius: 8, border: '1px solid var(--border)',
          }}>
            {DISCLAIMER_TEXT}
          </div>
          <button
            onClick={onAcceptDisclaimer}
            className="settings-btn"
            style={{ fontSize: '.82rem', padding: '.6rem 1.5rem', fontWeight: 600 }}
          >
            I Understand and Accept
          </button>
          <p style={{ fontSize: '.65rem', color: 'var(--text-dim)', marginTop: '.5rem' }}>
            You can review this disclaimer again at any time in Settings.
          </p>
        </div>
      </div>
    );
  }

  // Step 1: Configure LLM (shown until completed)
  if (!hasLlm) {
    const done = currentStep.id === 'llm' ? false : true;
    return (
      <div style={overlayStyle}>
        <div style={{ ...modalStyle, maxWidth: 560 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.75rem' }}>
            <Brain size={24} style={{ color: 'var(--accent)' }} />
            <h2 style={{ fontSize: '1.1rem', fontWeight: 700, margin: 0, color: 'var(--text)' }}>
              {currentStep.title}
            </h2>
          </div>

          <p style={{ fontSize: '.78rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '.75rem' }}>
            {currentStep.body}
          </p>

          <div style={{ marginBottom: '.85rem', display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
            {currentStep.links.map(link => (
              <a key={link.url} href={link.url} target="_blank" rel="noopener noreferrer"
                style={{
                  fontSize: '.74rem', color: 'var(--accent)',
                  textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '.4rem',
                  padding: '.35rem 0',
                }}>
                <ExternalLink size={12} /> {link.label}
              </a>
            ))}
          </div>

          <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
            <button
              onClick={() => onNavigate(currentStep.navigateTo || '/settings')}
              className="settings-btn"
              style={{ fontSize: '.8rem', padding: '.55rem 1.2rem', fontWeight: 600 }}
            >
              {currentStep.action} <ArrowRight size={14} style={{ marginLeft: '.3rem' }} />
            </button>
            <button
              onClick={onRefresh}
              style={{ fontSize: '.72rem', color: 'var(--text-dim)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              I've added a key — check again
            </button>
          </div>

          <div style={{ marginTop: '1.25rem', display: 'flex', gap: '1.5rem' }}>
            {STEPS.map((s, i) => (
              <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: '.35rem', fontSize: '.68rem', color: i === 0 ? 'var(--accent)' : 'var(--text-dim)' }}>
                <Circle size={12} style={{ color: i === 0 ? 'var(--accent)' : 'var(--text-dim)' }} />
                {s.label}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return null;
}

const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 10000,
  background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const modalStyle: React.CSSProperties = {
  background: 'var(--bg-panel)', border: '1px solid var(--border)',
  borderRadius: 16, padding: '1.75rem 2rem',
  maxWidth: 500, width: '90%',
  boxShadow: '0 25px 60px rgba(0,0,0,0.5)',
};