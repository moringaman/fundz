/**
 * Browser Notification Service
 *
 * Uses a Service Worker to display native desktop notifications reliably
 * across all modern browsers (Chrome, Edge, Firefox, Safari).
 *
 * Preferences are stored in localStorage so they persist across sessions.
 */

export interface NotificationPreferences {
  enabled: boolean;
  tradeExecuted: boolean;
  positionClosed: boolean;
  riskAlert: boolean;
  allocation: boolean;
  dailyReport: boolean;
  agentError: boolean;
}

const PREFS_KEY = 'px_notification_prefs';

const DEFAULT_PREFS: NotificationPreferences = {
  enabled: true,
  tradeExecuted: true,
  positionClosed: true,
  riskAlert: true,
  allocation: false,
  dailyReport: true,
  agentError: true,
};

class NotificationService {
  private _permission: NotificationPermission = 'default';
  private _swRegistration: ServiceWorkerRegistration | null = null;
  private _swReady: Promise<void>;
  private _swResolve!: () => void;

  constructor() {
    if ('Notification' in window) {
      this._permission = Notification.permission;
    }
    this._swReady = new Promise((resolve) => {
      this._swResolve = resolve;
    });
    this._registerServiceWorker();
  }

  /** Register the notification service worker */
  private async _registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      console.warn('Service Workers not supported — notifications will use fallback');
      this._swResolve();
      return;
    }
    try {
      const reg = await navigator.serviceWorker.register('/sw-notifications.js');
      this._swRegistration = reg;
      // Wait for the SW to be active
      if (reg.active) {
        this._swResolve();
      } else {
        const sw = reg.installing || reg.waiting;
        if (sw) {
          sw.addEventListener('statechange', () => {
            if (sw.state === 'activated') this._swResolve();
          });
        } else {
          this._swResolve();
        }
      }
    } catch (e) {
      console.warn('SW registration failed:', e);
      this._swResolve();
    }
  }

  /** Current browser permission state */
  get permission(): NotificationPermission {
    return this._permission;
  }

  /** Whether the browser supports notifications */
  get supported(): boolean {
    return 'Notification' in window;
  }

  /** Request permission from the user */
  async requestPermission(): Promise<NotificationPermission> {
    if (!this.supported) return 'denied';
    this._permission = await Notification.requestPermission();
    return this._permission;
  }

  /** Load preferences from localStorage */
  getPreferences(): NotificationPreferences {
    try {
      const stored = localStorage.getItem(PREFS_KEY);
      if (stored) return { ...DEFAULT_PREFS, ...JSON.parse(stored) };
    } catch { /* ignore */ }
    return { ...DEFAULT_PREFS };
  }

  /** Save preferences to localStorage */
  savePreferences(prefs: NotificationPreferences): void {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  }

  /** Send a browser notification via the Service Worker (with fallback) */
  async notify(
    title: string,
    options?: {
      body?: string;
      icon?: string;
      tag?: string;
      requireInteraction?: boolean;
    },
  ): Promise<void> {
    if (!this.supported || this._permission !== 'granted') return;

    const prefs = this.getPreferences();
    if (!prefs.enabled) return;

    const notifOptions = {
      icon: options?.icon || '/favicon.svg',
      body: options?.body,
      tag: options?.tag,
      requireInteraction: options?.requireInteraction || false,
      silent: false,
      badge: '/favicon.svg',
    };

    // Prefer Service Worker showNotification (works in background)
    await this._swReady;
    if (this._swRegistration?.active) {
      try {
        await this._swRegistration.showNotification(title, notifOptions);
        return;
      } catch {
        // Fall through to fallback
      }
    }

    // Fallback: direct Notification constructor (works in some browsers)
    try {
      new Notification(title, notifOptions);
    } catch (e) {
      console.warn('Notification failed:', e);
    }
  }

  /** Classify a team_chat message and send appropriate notification */
  handleTeamChatMessage(msg: {
    agent_name?: string;
    agent_role?: string;
    content?: string;
    message_type?: string;
  }): void {
    const prefs = this.getPreferences();
    if (!prefs.enabled) return;

    const type = msg.message_type || '';
    const agent = msg.agent_name || 'System';
    const content = msg.content || '';
    const contentPreview = content.replace(/\*\*/g, '').slice(0, 120);

    // Trade executed or position closed
    if (type === 'trade') {
      const isClosed = /stop.?loss|take.?profit|trailing.?stop|exit/i.test(content);
      if (isClosed && prefs.positionClosed) {
        this.notify('Position Closed', {
          body: contentPreview,
          tag: 'position-closed',
          requireInteraction: true,
        });
      } else if (!isClosed && prefs.tradeExecuted) {
        this.notify('Trade Executed', {
          body: contentPreview,
          tag: 'trade-executed',
        });
      }
      return;
    }

    // Risk alerts (from risk manager)
    if (type === 'warning' || (msg.agent_role === 'risk_manager' && /danger|caution|exceeded/i.test(content))) {
      if (prefs.riskAlert) {
        this.notify('⚠️ Risk Alert', {
          body: `${agent}: ${contentPreview}`,
          tag: 'risk-alert',
          requireInteraction: true,
        });
      }
      return;
    }

    // Allocation changes
    if (type === 'allocation' || (type === 'decision' && msg.agent_role === 'portfolio_manager')) {
      if (prefs.allocation) {
        this.notify('Portfolio Rebalanced', {
          body: contentPreview,
          tag: 'allocation',
        });
      }
      return;
    }

    // Daily report
    if (type === 'report' || (msg.agent_role === 'cio_agent' && /daily|report|summary/i.test(content))) {
      if (prefs.dailyReport) {
        this.notify('📊 Daily Report Ready', {
          body: contentPreview,
          tag: 'daily-report',
        });
      }
      return;
    }

    // Agent errors
    if (type === 'error' || /failed|error|offline/i.test(content)) {
      if (prefs.agentError) {
        this.notify('⚠️ Agent Issue', {
          body: `${agent}: ${contentPreview}`,
          tag: 'agent-error',
        });
      }
    }
  }
}

export const notificationService = new NotificationService();
