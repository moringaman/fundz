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
    // Register auto-unlock so sounds activate on the first click anywhere in
    // the app — the user never needs to visit Settings to re-enable after refresh.
    soundService.autoUnlockOnInteraction();

    const onTradeExecuted = () => {
      soundService.play('trade-open');
    };

    const onTeamChat = (msg: unknown) => {
      const payload = msg as { message_type?: string; content?: string; data?: { message_type?: string; content?: string } };
      const messageType = payload.data?.message_type ?? payload.message_type;
      if (messageType !== 'trade') return;

      const content = (payload.data?.content ?? payload.content ?? '').toUpperCase();
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
