import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';
import { notificationService } from '../lib/notifications';
import type { TeamChatMessage } from '../store/types';

/**
 * Listens for WebSocket events and triggers browser push notifications
 * based on user preferences. Risk alerts always fire; routine events
 * only fire when the tab is not focused.
 */
export function useBrowserNotifications() {
  useEffect(() => {
    const onTeamChat = (msg: unknown) => {
      const { data } = msg as { data: TeamChatMessage };
      if (!data) return;

      const type = data.message_type || '';
      const content = data.content || '';
      const isCritical =
        type === 'warning' ||
        type === 'error' ||
        /stop.?loss|take.?profit|trailing.?stop|exit/i.test(content) ||
        /danger|exceeded|liquidat/i.test(content);

      // Critical alerts always fire; routine events only when tab hidden
      if (isCritical || document.hidden) {
        notificationService.handleTeamChatMessage(data);
      }
    };

    wsClient.on('team_chat', onTeamChat);
    return () => {
      wsClient.off('team_chat', onTeamChat);
    };
  }, []);
}
