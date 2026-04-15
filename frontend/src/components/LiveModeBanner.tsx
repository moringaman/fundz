/**
 * LiveModeBanner
 * ==============
 * Shown at the top of the app when live trading mode is active.
 * Displays: mode badge, account balance, position count, sync status,
 * and an emergency stop button.
 */

import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

interface LiveStatus {
  mode: 'live' | 'paper';
  live_enabled: boolean;
  balance?: { available: number; total: number; currency: string };
  positions_count?: number;
  balance_error?: string;
}

export const LiveModeBanner: React.FC = () => {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get('/api/live/status');
      setStatus(res.data);
    } catch {
      // Silently fail — may be in paper mode or backend down
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 30_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await axios.post('/api/live/sync');
      setLastSync(new Date());
      await fetchStatus();
    } catch {
      //
    } finally {
      setSyncing(false);
    }
  };

  const handleEmergencyStop = async () => {
    setStopping(true);
    try {
      await axios.post('/api/live/emergency-stop');
      await fetchStatus();
    } catch {
      //
    } finally {
      setStopping(false);
      setShowStopConfirm(false);
    }
  };

  if (!status || !status.live_enabled) return null;

  return (
    <div className="live-mode-banner">
      <div className="live-mode-banner__left">
        <span className="live-mode-badge">
          <span className="live-mode-badge__dot" />
          LIVE
        </span>

        {status.balance && !status.balance_error && (
          <span className="live-mode-banner__balance">
            <span className="live-mode-banner__label">Balance</span>
            <span className="live-mode-banner__value">
              ${status.balance.available.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              <span className="live-mode-banner__currency">USDT</span>
            </span>
          </span>
        )}

        {status.positions_count !== undefined && (
          <span className="live-mode-banner__positions">
            <span className="live-mode-banner__label">Open</span>
            <span className="live-mode-banner__value">{status.positions_count} positions</span>
          </span>
        )}

        {lastSync && (
          <span className="live-mode-banner__sync-time">
            Synced {lastSync.toLocaleTimeString()}
          </span>
        )}
      </div>

      <div className="live-mode-banner__right">
        <button
          className="live-mode-banner__sync-btn"
          onClick={handleSync}
          disabled={syncing}
          title="Sync positions from Phemex"
        >
          {syncing ? (
            <span className="live-mode-banner__spinner" />
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          )}
          {syncing ? 'Syncing...' : 'Sync'}
        </button>

        {!showStopConfirm ? (
          <button
            className="live-mode-banner__stop-btn"
            onClick={() => setShowStopConfirm(true)}
            title="Emergency stop — close all live positions"
          >
            🛑 Emergency Stop
          </button>
        ) : (
          <div className="live-mode-banner__stop-confirm">
            <span>Close ALL live positions?</span>
            <button
              className="live-mode-banner__stop-confirm-yes"
              onClick={handleEmergencyStop}
              disabled={stopping}
            >
              {stopping ? 'Closing...' : 'Yes, close all'}
            </button>
            <button
              className="live-mode-banner__stop-confirm-no"
              onClick={() => setShowStopConfirm(false)}
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
