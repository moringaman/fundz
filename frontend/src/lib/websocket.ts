/**
 * WebSocket singleton with:
 *  - Automatic reconnection with exponential backoff (1s → 30s cap)
 *  - Heartbeat ping/pong every 30s (closes + reconnects if no pong within 10s)
 *  - Typed message handler registry
 *  - Symbol subscription tracking for re-subscribe on reconnect
 *  - Page-visibility-aware reconnection
 */

import { store } from '../store';
import { setWsStatus } from '../store/slices/uiSlice';

type MessageHandler = (data: unknown) => void;

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000];
const HEARTBEAT_INTERVAL_MS = 30_000;
const PONG_TIMEOUT_MS = 10_000;

function getWsUrl(): string {
  const apiUrl = import.meta.env.VITE_API_URL || '';
  if (apiUrl.startsWith('http')) {
    return apiUrl.replace(/^http/, 'ws') + '/api/ws/market';
  }
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/api/ws/market`;
}

class WsClient {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<MessageHandler>>();
  private subscribedSymbols = new Set<string>();
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    this.intentionalClose = false;
    const url = getWsUrl();
    store.dispatch(setWsStatus('connecting'));

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      store.dispatch(setWsStatus('connected'));
      this.reconnectAttempt = 0;

      // Re-subscribe to all tracked symbols
      if (this.subscribedSymbols.size > 0) {
        this.send({ type: 'subscribe', symbols: Array.from(this.subscribedSymbols) });
      }

      this._startHeartbeat();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string) as { type?: string; [key: string]: unknown };
        const type = msg.type as string | undefined;

        if (type === 'pong') {
          if (this.pongTimer) {
            clearTimeout(this.pongTimer);
            this.pongTimer = null;
          }
          return;
        }

        if (type) {
          const set = this.handlers.get(type);
          if (set) {
            set.forEach((fn) => fn(msg));
          }
          // Wildcard handlers
          const wildcard = this.handlers.get('*');
          if (wildcard) {
            wildcard.forEach((fn) => fn(msg));
          }
        }
      } catch {
        // Non-JSON frame — ignore
      }
    };

    this.ws.onclose = () => {
      this._stopHeartbeat();
      if (!this.intentionalClose) {
        store.dispatch(setWsStatus('disconnected'));
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      store.dispatch(setWsStatus('error'));
      // onclose fires after onerror; reconnect is handled there
    };
  }

  disconnect() {
    this.intentionalClose = true;
    this._stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    store.dispatch(setWsStatus('disconnected'));
  }

  send(msg: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  subscribe(symbols: string[]) {
    symbols.forEach((s) => this.subscribedSymbols.add(s));
    this.send({ type: 'subscribe', symbols });
  }

  unsubscribe(symbols: string[]) {
    symbols.forEach((s) => this.subscribedSymbols.delete(s));
    this.send({ type: 'unsubscribe', symbols });
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);
  }

  off(type: string, handler: MessageHandler) {
    this.handlers.get(type)?.delete(handler);
  }

  get status() {
    return store.getState().ui.wsStatus;
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = BACKOFF_DELAYS[Math.min(this.reconnectAttempt, BACKOFF_DELAYS.length - 1)];
    this.reconnectAttempt++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' });
      this.pongTimer = setTimeout(() => {
        // No pong received — connection is stale, force reconnect
        this.ws?.close();
      }, PONG_TIMEOUT_MS);
    }, HEARTBEAT_INTERVAL_MS);
  }

  private _stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.pongTimer) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }
}

export const wsClient = new WsClient();

// Re-connect when the page becomes visible after being hidden
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    const status = store.getState().ui.wsStatus;
    if (status === 'disconnected' || status === 'error') {
      wsClient.connect();
    }
  }
});
