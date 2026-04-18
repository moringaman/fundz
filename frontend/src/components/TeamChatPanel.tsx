import { useEffect, useRef } from 'react';
import { MessageCircle } from 'lucide-react';
import { useFundConversations } from '../hooks/useQueries';

type ConversationMessage = {
  id: string;
  agent_name: string;
  avatar: string;
  message_type: string;
  timestamp: string;
  content: string;
  mentions?: string[];
};

export function TeamChatPanel() {
  const { data: conversations = [], isLoading } = useFundConversations(100);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const msgs: ConversationMessage[] = Array.isArray(conversations)
    ? (conversations as ConversationMessage[])
    : [];

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [msgs.length]);

  const typeColors: Record<string, string> = {
    warning: 'var(--red)',
    analysis: 'var(--accent)',
    decision: 'var(--green)',
    recommendation: 'var(--amber)',
    greeting: 'var(--text-secondary)',
    trade_intent: 'var(--amber)',
    risk_decision: 'var(--red)',
    trade_executed: 'var(--green)',
    trade_blocked: 'var(--red)',
    allocation: 'var(--accent)',
    ta_confluence: '#a78bfa',
  };

  const typeLabels: Record<string, string> = {
    trade_intent: 'trade intent',
    risk_decision: 'risk decision',
    trade_executed: 'executed',
    trade_blocked: 'blocked',
    allocation: 'allocation',
    ta_confluence: 'TA confluence',
    analysis: 'analysis',
    decision: 'decision',
    recommendation: 'recommendation',
    warning: 'warning',
    greeting: 'greeting',
  };

  const formatContent = (content: string) => {
    return content.split(/\*\*(.*?)\*\*/g).map((part, i) =>
      i % 2 === 1
        ? <strong key={i} style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{part}</strong>
        : <span key={i}>{part}</span>
    );
  };

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        timeZoneName: 'short',
      });
    } catch { return ''; }
  };

  return (
    <div className="team-chat-panel">
      <div className="team-chat-header">
        <MessageCircle size={15} style={{ color: 'var(--accent)' }} />
        <span>Team Discussion</span>
        <span className="team-chat-badge">{msgs.length}</span>
      </div>
      <div className="team-chat-messages">
        {isLoading && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
            Loading conversations…
          </div>
        )}
        {!isLoading && msgs.length === 0 && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
            <MessageCircle size={24} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
            No conversations yet. Team discussions appear when the scheduler runs.
          </div>
        )}
        {msgs.map((msg) => (
          <div key={msg.id} className="team-chat-msg">
            <div className="team-chat-msg-avatar">{msg.avatar}</div>
            <div className="team-chat-msg-body">
              <div className="team-chat-msg-meta">
                <span className="team-chat-msg-name">{msg.agent_name}</span>
                <span
                  className="team-chat-msg-type-badge"
                  style={{ color: typeColors[msg.message_type] || 'var(--text-secondary)' }}
                >
                  {typeLabels[msg.message_type] || msg.message_type}
                </span>
                <span className="team-chat-msg-time">{formatTime(msg.timestamp)}</span>
              </div>
              <div className="team-chat-msg-content">
                {formatContent(msg.content)}
              </div>
              {msg.mentions && msg.mentions.length > 0 && (
                <div className="team-chat-msg-mentions">
                  {msg.mentions.map((m: string) => (
                    <span key={m} className="team-chat-mention">@{m.replace('_', ' ')}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>
    </div>
  );
}
