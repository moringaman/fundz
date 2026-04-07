import type React from 'react';

export function NavBadge({ children, variant = 'default' }: { children: React.ReactNode; variant?: 'default' | 'green' | 'red' | 'amber' }) {
  const colors: Record<string, React.CSSProperties> = {
    default: { background: 'var(--bg-hover)', color: 'var(--text-secondary)', border: '1px solid var(--border)' },
    green:   { background: 'var(--green-dim)', color: 'var(--green)', border: '1px solid rgba(0,230,118,.2)' },
    red:     { background: 'var(--red-dim)', color: 'var(--red)', border: '1px solid rgba(255,61,96,.2)' },
    amber:   { background: 'var(--amber-dim)', color: 'var(--amber)', border: '1px solid rgba(255,179,0,.2)' },
  };
  return (
    <span style={{
      marginLeft: 'auto',
      padding: '1px 6px',
      borderRadius: '4px',
      fontSize: '.65rem',
      fontFamily: 'var(--mono)',
      fontWeight: 600,
      lineHeight: '1.6',
      flexShrink: 0,
      ...colors[variant],
    }}>
      {children}
    </span>
  );
}
