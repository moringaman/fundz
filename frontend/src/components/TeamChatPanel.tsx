import { useEffect, useRef, useMemo } from 'react';
import { MessageCircle, Users } from 'lucide-react';
import { useFundConversations } from '../hooks/useQueries';

type ConversationMessage = {
  id: string;
  agent_name: string;
  agent_role?: string;
  avatar: string;
  message_type: string;
  timestamp: string;
  content: string;
  mentions?: string[];
};

const ROLE_COLORS: Record<string, string> = {
  cio_agent:         'var(--accent)',
  risk_manager:      'var(--amber)',
  research_analyst:  '#a78bfa',
  technical_analyst: '#38bdf8',
  fund_manager:      '#fb923c',
};

function getRoleColor(role?: string, name?: string): string {
  if (role && ROLE_COLORS[role]) return ROLE_COLORS[role];
  const n = name?.toLowerCase() ?? '';
  if (n.includes('cio'))       return 'var(--accent)';
  if (n.includes('risk'))      return 'var(--amber)';
  if (n.includes('research'))  return '#a78bfa';
  if (n.includes('technical')) return '#38bdf8';
  if (n.includes('fund'))      return '#fb923c';
  return 'var(--green)';
}

const ROLE_TITLES: Record<string, string> = {
  cio_agent:         'Chief Investment Officer',
  risk_manager:      'Risk Manager',
  research_analyst:  'Research Analyst',
  technical_analyst: 'Technical Analyst',
  fund_manager:      'Fund Manager',
};

function getRoleTitle(role?: string): string {
  if (role && ROLE_TITLES[role]) return ROLE_TITLES[role];
  return role?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) ?? 'Agent';
}

const TYPE_COLORS: Record<string, string> = {
  warning:       'var(--red)',
  analysis:      'var(--accent)',
  decision:      'var(--green)',
  recommendation:'var(--amber)',
  greeting:      'var(--text-secondary)',
  trade_intent:  'var(--amber)',
  risk_decision: 'var(--red)',
  trade_executed:'var(--green)',
  trade_blocked: 'var(--red)',
  allocation:    'var(--accent)',
  ta_confluence: '#a78bfa',
};

const TYPE_LABELS: Record<string, string> = {
  trade_intent:   'intent',
  risk_decision:  'risk',
  trade_executed: 'executed',
  trade_blocked:  'blocked',
  allocation:     'alloc',
  ta_confluence:  'ta signal',
  analysis:       'analysis',
  decision:       'decision',
  recommendation: 'rec',
  warning:        'warning',
  greeting:       'greeting',
};

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1)  return 'now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function formatContent(content: string) {
  return content.split(/\*\*(.*?)\*\*/g).map((part, i) =>
    i % 2 === 1
      ? <strong key={i} style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{part}</strong>
      : <span key={i}>{part}</span>
  );
}

export function TeamChatPanel() {
  const { data: conversations = [], isLoading } = useFundConversations(100);
  const feedRef = useRef<HTMLDivElement>(null);

  const msgs: ConversationMessage[] = Array.isArray(conversations)
    ? (conversations as ConversationMessage[])
    : [];

  const participants = useMemo(() => {
    const map = new Map<string, { agent: ConversationMessage; count: number }>();
    msgs.forEach(m => {
      const existing = map.get(m.agent_name);
      if (!existing) {
        map.set(m.agent_name, { agent: m, count: 1 });
      } else {
        existing.count++;
        // keep the latest message as representative
        existing.agent = m;
      }
    });
    return Array.from(map.values());
  }, [msgs]);

  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [msgs.length]);

  return (
    <div style={{
      background: 'var(--bg-panel)',
      border: '1px solid var(--border)',
      borderRadius: '12px',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '.5rem',
        padding: '.85rem 1.25rem',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        <MessageCircle size={14} style={{ color: 'var(--accent)' }} />
        <span style={{ fontSize: '.8rem', fontWeight: 700, color: 'var(--text-primary)' }}>Team Discussion</span>
        <span style={{
          marginLeft: 'auto',
          background: 'var(--accent-dim)',
          color: 'var(--accent)',
          fontSize: '.62rem',
          fontWeight: 700,
          padding: '.12rem .5rem',
          borderRadius: '10px',
          fontFamily: 'var(--mono)',
        }}>
          {msgs.length}
        </span>
      </div>

      {/* Body */}
      <div style={{ display: 'flex', height: '540px', minHeight: 0 }}>

        {/* Participants sidebar */}
        <div style={{
          width: '200px',
          flexShrink: 0,
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          <div style={{
            padding: '.55rem .85rem',
            fontSize: '.58rem',
            fontFamily: 'var(--mono)',
            fontWeight: 600,
            color: 'var(--text-dim)',
            textTransform: 'uppercase',
            letterSpacing: '.08em',
            borderBottom: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: '.35rem',
          }}>
            <Users size={10} />
            Participants · {participants.length}
          </div>
          <div style={{ overflowY: 'auto', flex: 1, padding: '.5rem 0' }}>
            {participants.map(({ agent, count }) => {
              const color = getRoleColor(agent.agent_role, agent.agent_name);
              return (
                <div key={agent.agent_name} style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '.6rem',
                  padding: '.5rem .85rem',
                }}>
                  <div style={{
                    width: '30px',
                    height: '30px',
                    borderRadius: '50%',
                    background: 'var(--bg-elevated)',
                    border: `2px solid ${color}50`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '.95rem',
                    flexShrink: 0,
                  }}>
                    {agent.avatar}
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{
                      fontSize: '.7rem',
                      fontWeight: 700,
                      color,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}>
                      {agent.agent_name}
                    </div>
                    <div style={{
                      fontSize: '.58rem',
                      color: 'var(--text-secondary)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      marginBottom: '.1rem',
                    }}>
                      {getRoleTitle(agent.agent_role)}
                    </div>
                    <div style={{
                      fontSize: '.55rem',
                      fontFamily: 'var(--mono)',
                      color: 'var(--text-dim)',
                    }}>
                      {count} msg{count !== 1 ? 's' : ''}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Message feed */}
        <div
          ref={feedRef}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '.5rem .6rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
            scrollbarWidth: 'thin',
            scrollbarColor: 'var(--border-mid) transparent',
          }}
        >
          {isLoading && (
            <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
              Loading conversations…
            </div>
          )}
          {!isLoading && msgs.length === 0 && (
            <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
              <MessageCircle size={28} style={{ opacity: .25, display: 'block', margin: '0 auto .75rem' }} />
              No conversations yet. Team discussions appear when the scheduler runs.
            </div>
          )}
          {msgs.map((msg, i) => {
            const prev = msgs[i - 1];
            const isContinuation = prev?.agent_name === msg.agent_name;
            const color = getRoleColor(msg.agent_role, msg.agent_name);
            const typeColor = TYPE_COLORS[msg.message_type] || 'var(--text-secondary)';

            return (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  gap: '.55rem',
                  padding: isContinuation ? '.15rem .4rem .15rem 2.55rem' : '.4rem .4rem',
                  borderRadius: '6px',
                  transition: 'background .12s',
                  cursor: 'default',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                {!isContinuation && (
                  <div style={{
                    width: '26px',
                    height: '26px',
                    borderRadius: '50%',
                    background: 'var(--bg-elevated)',
                    border: `2px solid ${color}35`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '.85rem',
                    flexShrink: 0,
                    marginTop: '2px',
                  }}>
                    {msg.avatar}
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  {!isContinuation && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem', marginBottom: '.2rem' }}>
                      <span style={{ fontSize: '.74rem', fontWeight: 700, color }}>{msg.agent_name}</span>
                      <span style={{
                        fontSize: '.54rem',
                        fontFamily: 'var(--mono)',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        letterSpacing: '.05em',
                        color: typeColor,
                        padding: '.1rem .35rem',
                        background: `${typeColor}18`,
                        borderRadius: '3px',
                      }}>
                        {TYPE_LABELS[msg.message_type] || msg.message_type}
                      </span>
                      <span style={{
                        fontSize: '.58rem',
                        fontFamily: 'var(--mono)',
                        color: 'var(--text-dim)',
                        marginLeft: 'auto',
                      }}>
                        {relTime(msg.timestamp)}
                      </span>
                    </div>
                  )}
                  <div style={{
                    fontSize: '.74rem',
                    color: 'var(--text-secondary)',
                    lineHeight: 1.55,
                    wordBreak: 'break-word',
                  }}>
                    {formatContent(msg.content)}
                  </div>
                  {msg.mentions && msg.mentions.length > 0 && (
                    <div style={{ display: 'flex', gap: '.25rem', marginTop: '.3rem', flexWrap: 'wrap' }}>
                      {msg.mentions.map((m: string) => (
                        <span key={m} style={{
                          fontSize: '.58rem',
                          padding: '.1rem .35rem',
                          borderRadius: '3px',
                          background: 'var(--accent-dim)',
                          color: 'var(--accent)',
                          fontFamily: 'var(--mono)',
                          fontWeight: 600,
                        }}>
                          @{m.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
