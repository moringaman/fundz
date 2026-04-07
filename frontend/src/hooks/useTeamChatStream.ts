import { useEffect } from 'react';
import { wsClient } from '../lib/websocket';
import { useAppDispatch } from '../store/hooks';
import { addToast } from '../store/slices/teamChatSlice';
import type { TeamChatMessage } from '../store/types';

/**
 * Listens for `team_chat` WebSocket events and pushes them
 * into the Redux toast queue for display.
 */
export function useTeamChatStream() {
  const dispatch = useAppDispatch();

  useEffect(() => {
    const onTeamChat = (msg: unknown) => {
      const { data } = msg as { data: TeamChatMessage };
      if (data) dispatch(addToast(data));
    };

    wsClient.on('team_chat', onTeamChat);
    return () => {
      wsClient.off('team_chat', onTeamChat);
    };
  }, [dispatch]);
}
