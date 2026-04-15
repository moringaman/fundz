import { useState, useCallback, useEffect } from 'react';
import { Volume2, VolumeX, Play } from 'lucide-react';
import { soundService, type SoundKey } from '../../lib/soundService';

const EVENT_LABELS: { key: SoundKey; label: string; description: string }[] = [
  { key: 'trade-open',  label: 'Trade Opened',    description: 'Plays when a new position is entered' },
  { key: 'profit-take', label: 'Take Profit Hit',  description: 'Plays when a TP target is reached' },
  { key: 'stop-loss',   label: 'Stop Loss Hit',    description: 'Plays when a SL is triggered' },
];

export function SoundSettings() {
  const [prefs, setPrefs] = useState(() => soundService.getPrefs());
  const [unlocked, setUnlocked] = useState(() => soundService.isUnlocked());
  const [testingAll, setTestingAll] = useState(false);

  // Stay in sync with soundService — auto-unlock fires externally (e.g. from
  // useEventSounds on first page click) so we subscribe rather than only reading
  // state at mount time.
  useEffect(() => {
    const unsub = soundService.onUnlock(() => setUnlocked(true));
    // Also re-read prefs on mount in case they changed while this component was unmounted
    setPrefs(soundService.getPrefs());
    return unsub;
  }, []);

  const save = useCallback((update: Parameters<typeof soundService.setPrefs>[0]) => {
    soundService.setPrefs(update);
    setPrefs(soundService.getPrefs());
  }, []);

  const handleUnlock = async () => {
    await soundService.unlock();
    setUnlocked(soundService.isUnlocked());
  };

  const handleTest = async (key: SoundKey) => {
    if (!soundService.isUnlocked()) {
      await soundService.unlock();
      setUnlocked(soundService.isUnlocked());
    }
    soundService.play(key);
  };

  const handleTestAll = async () => {
    if (testingAll) return;
    setTestingAll(true);
    try {
      if (!soundService.isUnlocked()) {
        await soundService.unlock();
        setUnlocked(soundService.isUnlocked());
      }

      const sequence: SoundKey[] = ['trade-open', 'profit-take', 'stop-loss'];
      sequence.forEach((key, idx) => {
        window.setTimeout(() => soundService.play(key), idx * 650);
      });
    } finally {
      window.setTimeout(() => setTestingAll(false), 2100);
    }
  };

  return (
    <div className="settings-card space-y-4">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.25rem' }}>
        <Volume2 size={15} style={{ color: 'var(--accent)' }} />
        <h2 className="settings-title" style={{ marginBottom: 0 }}>Sound Effects</h2>
      </div>
      <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', marginTop: 0 }}>
        Subtle audio cues for key trading events. Sounds play in-browser when events arrive via WebSocket.
      </p>

      {/* Autoplay unlock banner — only shown until first interaction unlocks audio */}
      {!unlocked && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '.6rem .85rem',
          background: 'rgba(0,194,255,.08)',
          border: '1px solid rgba(0,194,255,.2)',
          borderRadius: '6px',
          gap: '1rem',
        }}>
          <div>
            <div style={{ fontSize: '.75rem', fontWeight: 600, color: 'var(--accent)' }}>Sounds not yet active</div>
            <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)' }}>
              Will activate automatically on your next click — or tap below to enable now.
            </div>
          </div>
          <button type="button" className="settings-btn settings-btn-primary" onClick={handleUnlock}
            style={{ whiteSpace: 'nowrap', fontSize: '.72rem' }}>
            Enable Now
          </button>
        </div>
      )}

      {/* Master toggle + volume */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: '.8rem', fontWeight: 600, color: 'var(--text-primary)' }}>Master Volume</div>
          <div style={{ fontSize: '.68rem', color: 'var(--text-secondary)' }}>
            {prefs.enabled ? 'Sounds enabled' : 'All sounds muted'}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '.75rem' }}>
          <button
            type="button"
            onClick={() => save({ enabled: !prefs.enabled })}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: prefs.enabled ? 'var(--accent)' : 'var(--text-dim)',
              display: 'flex', alignItems: 'center',
            }}
            title={prefs.enabled ? 'Mute all' : 'Unmute all'}
          >
            {prefs.enabled ? <Volume2 size={18} /> : <VolumeX size={18} />}
          </button>
          <input
            type="range" min={0} max={1} step={0.05}
            value={prefs.volume}
            onChange={e => save({ volume: parseFloat(e.target.value) })}
            style={{ width: '110px', accentColor: 'var(--accent)', cursor: 'pointer' }}
            disabled={!prefs.enabled}
          />
          <span style={{ fontSize: '.72rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)', width: '2.5rem' }}>
            {Math.round(prefs.volume * 100)}%
          </span>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '.75rem' }}>
        <div style={{ fontSize: '.67rem', color: 'var(--text-secondary)' }}>
          Run a quick three-tone test to verify browser audio output.
        </div>
        <button
          type="button"
          onClick={handleTestAll}
          className="settings-btn"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '.4rem',
            whiteSpace: 'nowrap',
            opacity: testingAll ? 0.8 : 1,
          }}
          disabled={testingAll}
        >
          <Play size={13} />
          {testingAll ? 'Testing...' : 'Test All Sounds'}
        </button>
      </div>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '.5rem 0' }} />

      {/* Per-event toggles */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '.6rem' }}>
        {EVENT_LABELS.map(({ key, label, description }) => (
          <div key={key} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '.5rem .75rem',
            background: 'var(--bg-elevated)',
            borderRadius: '6px',
            border: '1px solid var(--border)',
          }}>
            <div>
              <div style={{ fontSize: '.78rem', fontWeight: 500, color: 'var(--text-primary)' }}>{label}</div>
              <div style={{ fontSize: '.67rem', color: 'var(--text-secondary)' }}>{description}</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '.6rem' }}>
              <button
                type="button"
                onClick={() => handleTest(key)}
                title="Preview sound"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-dim)', display: 'flex', alignItems: 'center',
                  padding: '.25rem',
                }}
              >
                <Play size={12} />
              </button>
              <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', gap: '.4rem' }}>
                <input
                  type="checkbox"
                  checked={prefs.events[key]}
                  onChange={e => save({ events: { ...prefs.events, [key]: e.target.checked } })}
                  style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
                />
                <span style={{ fontSize: '.72rem', color: 'var(--text-secondary)' }}>
                  {prefs.events[key] ? 'On' : 'Off'}
                </span>
              </label>
            </div>
          </div>
        ))}
      </div>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '.5rem 0' }} />

      {/* Mute when hidden */}
      <label style={{ display: 'flex', alignItems: 'center', gap: '.6rem', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={prefs.muteWhenHidden}
          onChange={e => save({ muteWhenHidden: e.target.checked })}
          style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
        />
        <div>
          <div style={{ fontSize: '.78rem', fontWeight: 500, color: 'var(--text-primary)' }}>
            Mute when tab is in background
          </div>
          <div style={{ fontSize: '.67rem', color: 'var(--text-secondary)' }}>
            Silences sounds when you switch away from the app
          </div>
        </div>
      </label>
    </div>
  );
}
