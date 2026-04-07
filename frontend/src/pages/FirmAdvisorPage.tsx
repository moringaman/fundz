import { useState, useRef, useEffect } from 'react';
import { useAdvisorHistory, useAskAdvisor, useClearAdvisorHistory } from '../hooks/useQueries';
import { Send, Trash2, MessageCircle, Loader2 } from 'lucide-react';

export function FirmAdvisorPage() {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: history = [] } = useAdvisorHistory();
  const askMutation = useAskAdvisor();
  const clearMutation = useClearAdvisorHistory();

  const messages: any[] = Array.isArray(history) ? history : [];

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, askMutation.isPending]);

  const handleSend = () => {
    const msg = input.trim();
    if (!msg || askMutation.isPending) return;
    setInput('');
    askMutation.mutate(msg);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const suggestedQuestions = [
    'What would happen if BTC dropped 20% overnight?',
    'Should we increase our ETH exposure right now?',
    'How would you handle a sudden altcoin pump?',
    'What is our current risk profile and how could we improve it?',
    'Which agents are performing best and why?',
    'If interest rates were cut tomorrow, how would we reposition?',
  ];

  return (
    <div className="space-y-4" style={{ height: 'calc(100vh - 6rem)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div>
          <h1 className="page-title" style={{ marginBottom: '0.25rem' }}>Firm Advisor</h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', margin: 0 }}>
            Ask the fund management team about strategy, risk, and market scenarios
          </p>
        </div>
        <button
          type="button"
          onClick={() => clearMutation.mutate()}
          disabled={clearMutation.isPending || messages.length === 0}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            padding: '0.4rem 0.8rem',
            borderRadius: '6px',
            fontSize: '0.75rem',
            cursor: messages.length === 0 ? 'not-allowed' : 'pointer',
            border: '1px solid var(--border)',
            background: 'var(--surface-2, #2a2d35)',
            color: 'var(--text-secondary)',
            opacity: messages.length === 0 ? 0.4 : 1,
          }}
        >
          <Trash2 size={13} />
          Clear
        </button>
      </div>

      {/* Chat Messages Area */}
      <div
        className="card"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '1rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          minHeight: 0,
        }}
      >
        {messages.length === 0 && !askMutation.isPending && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '1.5rem' }}>
            <div style={{ textAlign: 'center' }}>
              <MessageCircle size={40} style={{ color: 'var(--accent)', marginBottom: '0.5rem' }} />
              <h3 style={{ margin: '0 0 0.25rem', color: 'var(--text)' }}>Ask the Fund Team</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', maxWidth: '400px', margin: '0 auto' }}>
                Ask hypothetical questions about market scenarios, strategy adjustments, or risk management.
                The team responds based on your live portfolio, positions, and current market conditions.
              </p>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', justifyContent: 'center', maxWidth: '600px' }}>
              {suggestedQuestions.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => { setInput(q); inputRef.current?.focus(); }}
                  style={{
                    padding: '0.4rem 0.75rem',
                    borderRadius: '16px',
                    fontSize: '0.72rem',
                    cursor: 'pointer',
                    border: '1px solid var(--border)',
                    background: 'var(--surface-2, #2a2d35)',
                    color: 'var(--text-secondary)',
                    transition: 'all 0.15s',
                  }}
                  onMouseOver={(e) => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--text)'; }}
                  onMouseOut={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg: any, i: number) => (
          <div
            key={`${msg.role}-${i}`}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '85%',
              alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            }}
          >
            <span style={{
              fontSize: '0.65rem',
              color: 'var(--text-dim)',
              marginBottom: '0.2rem',
              paddingLeft: msg.role === 'user' ? 0 : '0.5rem',
              paddingRight: msg.role === 'user' ? '0.5rem' : 0,
            }}>
              {msg.role === 'user' ? 'You' : '🏢 Fund Team'}
            </span>
            <div style={{
              padding: '0.75rem 1rem',
              borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
              background: msg.role === 'user' ? 'var(--accent)' : 'var(--surface-2, #2a2d35)',
              color: msg.role === 'user' ? '#fff' : 'var(--text)',
              fontSize: '0.82rem',
              lineHeight: '1.5',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}>
              {msg.role === 'advisor' ? (
                <AdvisorContent content={msg.content} />
              ) : (
                msg.content
              )}
            </div>
          </div>
        ))}

        {/* Loading indicator */}
        {askMutation.isPending && (
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', alignSelf: 'flex-start', maxWidth: '85%' }}>
            <div style={{
              padding: '0.75rem 1rem',
              borderRadius: '16px 16px 16px 4px',
              background: 'var(--surface-2, #2a2d35)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              color: 'var(--text-secondary)',
              fontSize: '0.82rem',
            }}>
              <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
              The team is discussing...
            </div>
          </div>
        )}

        {/* Error */}
        {askMutation.isError && (
          <div style={{
            padding: '0.5rem 0.75rem',
            borderRadius: '8px',
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid var(--red)',
            color: 'var(--red)',
            fontSize: '0.78rem',
            alignSelf: 'center',
          }}>
            Failed to get response. Please try again.
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input Area */}
      <div className="card" style={{ flexShrink: 0, padding: '0.75rem', display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask the fund team a question..."
          rows={1}
          style={{
            flex: 1,
            resize: 'none',
            border: '1px solid var(--border)',
            borderRadius: '12px',
            padding: '0.6rem 0.9rem',
            fontSize: '0.85rem',
            fontFamily: 'inherit',
            background: 'var(--surface, #1a1d24)',
            color: 'var(--text)',
            outline: 'none',
            minHeight: '2.4rem',
            maxHeight: '6rem',
            overflowY: 'auto',
          }}
          onInput={(e) => {
            const el = e.currentTarget;
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 96) + 'px';
          }}
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={!input.trim() || askMutation.isPending}
          style={{
            padding: '0.6rem',
            borderRadius: '10px',
            border: 'none',
            background: input.trim() && !askMutation.isPending ? 'var(--accent)' : 'var(--surface-2, #2a2d35)',
            color: input.trim() && !askMutation.isPending ? '#fff' : 'var(--text-dim)',
            cursor: input.trim() && !askMutation.isPending ? 'pointer' : 'not-allowed',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'all 0.15s',
          }}
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}

/** Render advisor markdown-like content with basic formatting */
function AdvisorContent({ content }: { content: string }) {
  // Simple markdown: **bold**, bullet points, line breaks
  const lines = content.split('\n');
  return (
    <>
      {lines.map((line, i) => {
        if (!line.trim()) return <div key={i} style={{ height: '0.4rem' }} />;
        // Bold
        const formatted = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Bullet points
        const isBullet = /^\s*[-•]\s/.test(line);
        return (
          <div
            key={i}
            style={{
              paddingLeft: isBullet ? '1rem' : 0,
              textIndent: isBullet ? '-0.6rem' : 0,
            }}
            dangerouslySetInnerHTML={{ __html: formatted }}
          />
        );
      })}
    </>
  );
}
