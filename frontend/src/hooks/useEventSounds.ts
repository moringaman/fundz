/**
 * useEventSounds — mount once near the app root.
 * Listens to WebSocket events and triggers the appropriate sound.
 *
 * Event → Sound mapping:
 *   trade_executed                    → trade-open
 *   team_chat (message_type: "trade", content includes "TAKE-PROFIT") → profit-take
 *   team_chat (message_type: "trade", content includes "STOP-LOSS")   → stop-loss
 */

import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';
import { soundService } from '../lib/soundService';

export function useEventSounds() {
  useEffect(() => {
    const onTradeExecuted = () => {
      soundService.play('trade-open');
    };

    const onTeamChat = (msg: unknown) => {
      const m = msg as { message_type?: string; content?: string };
      if (m.message_type !== 'trade') return;
      const content = (m.content ?? '').toUpperCase();
      if (content.includes('TAKE-PROFIT')) {
        soundService.play('profit-take');
      } else if (content.includes('STOP-LOSS') || content.includes('TRAILING-STOP')) {
        soundService.play('stop-loss');
      }
    };

    wsClient.on('trade_executed', onTradeExecuted);
    wsClient.on('team_chat', onTeamChat);

    return () => {
      wsClient.off('trade_executed', onTradeExecuted);
      wsClient.off('team_chat', onTeamChat);
    };
  }, []);
}
