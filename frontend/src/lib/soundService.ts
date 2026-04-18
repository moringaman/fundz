/**
 * Sound service for UI event audio.
 *
 * Design:
 * - Pre-loads Audio objects at first user gesture to satisfy browser autoplay policy
 * - Auto-unlocks on the first click/keydown anywhere in the page — no manual step needed
 * - Master volume + per-event toggles persisted to localStorage
 * - 500ms debounce per sound key to prevent stacking
 * - Silent when the browser tab is hidden (configurable)
 */

const STORAGE_KEY = 'px-sound-prefs';

export type SoundKey = 'trade-open' | 'profit-take' | 'stop-loss';

interface SoundPrefs {
  enabled: boolean;
  volume: number;           // 0.0 – 1.0
  events: Record<SoundKey, boolean>;
  muteWhenHidden: boolean;
}

const DEFAULT_PREFS: SoundPrefs = {
  enabled: true,
  volume: 0.45,
  events: {
    'trade-open':  true,
    'profit-take': true,
    'stop-loss':   true,
  },
  muteWhenHidden: true,
};

const SOUND_FILES: Record<SoundKey, string> = {
  'trade-open':  '/sounds/openPosition.mp3',
  'profit-take': '/sounds/profitTake.mp3',
  'stop-loss':   '/sounds/stopLossHit.mp3',
};

class SoundService {
  private buffers = new Map<SoundKey, AudioBuffer>();
  private ctx: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private prefs: SoundPrefs = DEFAULT_PREFS;
  private lastPlayed = new Map<SoundKey, number>();
  private unlocked = false;
  private unlockListeners: Array<() => void> = [];
  private readonly DEBOUNCE_MS = 500;

  constructor() {
    this._loadPrefs();
  }

  // ── Preferences ──────────────────────────────────────────────────────

  getPrefs(): SoundPrefs {
    return { ...this.prefs, events: { ...this.prefs.events } };
  }

  setPrefs(update: Partial<SoundPrefs>) {
    this.prefs = { ...this.prefs, ...update };
    if (update.events) {
      this.prefs.events = { ...this.prefs.events, ...update.events };
    }
    this._savePrefs();
    if (this.gainNode) {
      this.gainNode.gain.value = this.prefs.volume;
    }
  }

  // ── Unlock (must follow a user gesture) ─────────────────────────────

  async unlock(): Promise<void> {
    if (this.unlocked) {
      if (this.ctx?.state === 'suspended') {
        try {
          await this.ctx.resume();
        } catch {
          // Ignore resume failures; play() guards further usage.
        }
      }
      return;
    }
    try {
      this.ctx = new AudioContext();
      this.gainNode = this.ctx.createGain();
      this.gainNode.gain.value = this.prefs.volume;
      this.gainNode.connect(this.ctx.destination);
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume();
      }
      await this._preload();
      this.unlocked = true;
      this._notifyUnlockListeners();
    } catch (e) {
      console.warn('[SoundService] unlock failed', e);
    }
  }

  isUnlocked(): boolean {
    return this.unlocked;
  }

  /**
   * Subscribe to unlock events so components can react without polling.
   * Returns an unsubscribe function.
   */
  onUnlock(cb: () => void): () => void {
    this.unlockListeners.push(cb);
    return () => {
      this.unlockListeners = this.unlockListeners.filter(fn => fn !== cb);
    };
  }

  /**
   * Register one-time click/keydown listeners on the document so that the
   * AudioContext is unlocked automatically on the first user interaction with
   * the page — no need to go to Settings and click "Enable Sounds" every reload.
   *
   * Call this once from the app root (e.g. useEventSounds hook on mount).
   * Safe to call multiple times — the listeners are removed after first trigger.
   */
  autoUnlockOnInteraction(): void {
    if (this.unlocked) return;
    const handler = () => {
      this.unlock();
      document.removeEventListener('click', handler, { capture: true });
      document.removeEventListener('keydown', handler, { capture: true });
    };
    document.addEventListener('click', handler, { capture: true, once: true });
    document.addEventListener('keydown', handler, { capture: true, once: true });
  }

  // ── Play ─────────────────────────────────────────────────────────────

  play(key: SoundKey): void {
    if (!this.unlocked) return;
    if (!this.prefs.enabled) return;
    if (!this.prefs.events[key]) return;
    if (this.prefs.muteWhenHidden && document.visibilityState === 'hidden') return;
    if (this.ctx?.state === 'suspended') {
      this.ctx.resume().catch(() => {
        // If resume fails, no-op and wait for the next interaction.
      });
    }

    const now = Date.now();
    const last = this.lastPlayed.get(key) ?? 0;
    if (now - last < this.DEBOUNCE_MS) return;
    this.lastPlayed.set(key, now);

    const buffer = this.buffers.get(key);
    if (!buffer || !this.ctx || !this.gainNode) return;

    try {
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.gainNode);
      source.start(0);
    } catch (e) {
      console.warn('[SoundService] play failed', e);
    }
  }

  // ── Private ──────────────────────────────────────────────────────────

  private async _preload(): Promise<void> {
    if (!this.ctx) return;
    await Promise.all(
      (Object.entries(SOUND_FILES) as [SoundKey, string][]).map(async ([key, path]) => {
        try {
          const res = await fetch(path);
          const arrayBuffer = await res.arrayBuffer();
          const audioBuffer = await this.ctx!.decodeAudioData(arrayBuffer);
          this.buffers.set(key, audioBuffer);
        } catch (e) {
          console.warn(`[SoundService] failed to load ${path}`, e);
        }
      })
    );
  }

  private _loadPrefs(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as Partial<SoundPrefs>;
        this.prefs = {
          ...DEFAULT_PREFS,
          ...saved,
          events: { ...DEFAULT_PREFS.events, ...(saved.events ?? {}) },
        };
      }
    } catch { /* ignore */ }
  }

  private _savePrefs(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.prefs));
    } catch { /* ignore */ }
  }

  private _notifyUnlockListeners(): void {
    this.unlockListeners.forEach(fn => { try { fn(); } catch { /* ignore */ } });
  }
}

export const soundService = new SoundService();

