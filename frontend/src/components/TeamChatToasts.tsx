import { useAppSelector, useAppDispatch } from '../store/hooks';
import { dismissToast } from '../store/slices/teamChatSlice';

export function TeamChatToasts() {
  const toasts = useAppSelector((s) => s.teamChat.toasts);
  const dispatch = useAppDispatch();

  // Auto-dismiss is handled by teamChatSaga

  if (toasts.length === 0) return null;

  const typeColors: Record<string, string> = {
    warning: 'var(--red)',
    analysis: 'var(--accent)',
    decision: 'var(--green)',
    recommendation: 'var(--amber)',
    greeting: 'var(--text-secondary)',
  };
  const typeBgColors: Record<string, string> = {
    warning: 'var(--red-dim)',
    analysis: 'var(--accent-dim)',
    decision: 'var(--green-dim)',
    recommendation: 'var(--amber-dim)',
    greeting: 'var(--bg-elevated)',
  };

  return (
    <div className="team-chat-toasts">
      {toasts.map((msg) => (
        <div
          key={msg.id}
          className="team-chat-toast"
          style={{
            borderLeft: `3px solid ${typeColors[msg.message_type] || 'var(--accent)'}`,
            background: typeBgColors[msg.message_type] || 'var(--bg-panel)',
          }}
          onClick={() => dispatch(dismissToast(msg.id))}
        >
          <div className="team-chat-toast-header">
            <span className="team-chat-toast-avatar">{msg.avatar}</span>
            <span className="team-chat-toast-name">{msg.agent_name}</span>
            <span className="team-chat-toast-type">{msg.message_type}</span>
          </div>
          <div className="team-chat-toast-content">
            {msg.content.replace(/\*\*/g, '').slice(0, 200)}
            {msg.content.length > 200 ? '…' : ''}
          </div>
        </div>
      ))}
    </div>
  );
}
