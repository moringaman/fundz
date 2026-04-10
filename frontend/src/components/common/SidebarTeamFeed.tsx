import { useRef, useEffect } from 'react';
import { useFundConversations } from '../../hooks/useQueries';

const TYPE_COLORS: Record<string, string> = {
  warning:  'var(--amber)',
  error:    'var(--red)',
  analysis: 'var(--accent)',
  info:     'var(--text-secondary)',
  success:  'var(--green)',
};

const ROLE_COLORS: Record<string, string> = {
  cio:              'var(--accent)',
  risk_manager:     'var(--amber)',
  portfolio_manager:'var(--purple, #b988ff)',
};

function roleColor(role: string): string {
  if (ROLE_COLORS[role]) return ROLE_COLORS[role];
  if (role.startsWith('trader_')) return 'var(--green)';
  return 'var(--text-secondary)';
}

function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/\n+/g, ' ')
    .trim();
}

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1)  return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

interface Props {
  onNavigate: (page: string) => void;
}

export function SidebarTeamFeed({ onNavigate }: Props) {
  const { data } = useFundConversations(12);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = Array.isArray(data) ? data : [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  return (
    <div className="stf-root">
      <button
        type="button"
        className="stf-header"
        onClick={() => onNavigate('fundteam')}
        title="Open Fund Team"
      >
        <span className="stf-header-label">
          <span className="stf-dot" />
          TEAM
        </span>
        <span className="stf-header-count">{messages.length}</span>
      </button>

      <div className="stf-feed">
        {messages.length === 0 && (
          <div className="stf-empty">No messages yet…</div>
        )}
        {messages.map((msg: any) => (
          <div key={msg.id} className="stf-row">
            <span className="stf-avatar">{msg.avatar ?? '🤖'}</span>
            <div className="stf-body">
              <div className="stf-meta">
                <span className="stf-name" style={{ color: roleColor(msg.agent_role) }}>
                  {msg.agent_name?.split(' ')[0] ?? msg.agent_id}
                </span>
                <span
                  className="stf-type"
                  style={{ color: TYPE_COLORS[msg.message_type] ?? 'var(--text-dim)' }}
                >
                  {msg.message_type}
                </span>
                <span className="stf-time">{relTime(msg.timestamp)}</span>
              </div>
              <p className="stf-content">{stripMarkdown(msg.content)}</p>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
