import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';
import { useAppStore } from '../lib/store';
import type { TeamChatMessage } from '../lib/store';

/**
 * Listens for `team_chat` WebSocket events and pushes them
 * into the Zustand toast queue for display.
 */
export function useTeamChatStream() {
  const addTeamChatToast = useAppStore((s) => s.addTeamChatToast);

  useEffect(() => {
    const onTeamChat = (msg: unknown) => {
      const { data } = msg as { data: TeamChatMessage };
      if (data) addTeamChatToast(data);
    };

    wsClient.on('team_chat', onTeamChat);
    return () => {
      wsClient.off('team_chat', onTeamChat);
    };
  }, [addTeamChatToast]);
}
