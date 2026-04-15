import React from 'react';

/* ── Skeleton shimmer primitives ──────────────────────────────────────
 *  Drop-in loading placeholders that match the app's dark theme.
 *  Uses a single CSS shimmer animation defined in index.css.
 *
 *  Usage:
 *    <Skeleton width="60%" height={14} />           — inline text line
 *    <Skeleton width={120} height={32} rounded />    — pill/badge
 *    <Skeleton height={180} />                       — full-width block
 *    <SkeletonRows rows={5} />                       — table-like rows
 *    <SkeletonCard />                                — panel card
 * ────────────────────────────────────────────────────────────────────*/

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  rounded?: boolean;
  circle?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

export const Skeleton: React.FC<SkeletonProps> = ({
  width = '100%',
  height = 14,
  rounded = false,
  circle = false,
  className = '',
  style,
}) => (
  <div
    className={`skeleton-shimmer ${className}`}
    style={{
      width: typeof width === 'number' ? `${width}px` : width,
      height: typeof height === 'number' ? `${height}px` : height,
      borderRadius: circle ? '50%' : rounded ? '999px' : '4px',
      ...style,
    }}
  />
);

/* ── Composite: rows of varying width (table / list placeholder) ── */
interface SkeletonRowsProps {
  rows?: number;
  gap?: number;
  lineHeight?: number;
}

export const SkeletonRows: React.FC<SkeletonRowsProps> = ({
  rows = 4,
  gap = 10,
  lineHeight = 14,
}) => {
  const widths = ['92%', '78%', '85%', '65%', '90%', '72%', '80%', '60%'];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} width={widths[i % widths.length]} height={lineHeight} />
      ))}
    </div>
  );
};

/* ── Composite: card placeholder ─────────────────────────────────── */
interface SkeletonCardProps {
  lines?: number;
  height?: number;
}

export const SkeletonCard: React.FC<SkeletonCardProps> = ({ lines = 3, height }) => (
  <div
    style={{
      background: 'var(--bg-panel)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: '16px 18px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      ...(height ? { minHeight: height } : {}),
    }}
  >
    <Skeleton width="45%" height={12} />
    <Skeleton width="70%" height={20} />
    <SkeletonRows rows={lines} gap={8} lineHeight={12} />
  </div>
);

/* ── Composite: table skeleton ────────────────────────────────────── */
interface SkeletonTableProps {
  rows?: number;
  cols?: number;
}

export const SkeletonTable: React.FC<SkeletonTableProps> = ({ rows = 5, cols = 6 }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
    {/* Header */}
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gap: 12,
        padding: '10px 14px',
        borderBottom: '1px solid var(--border)',
      }}
    >
      {Array.from({ length: cols }, (_, i) => (
        <Skeleton key={i} width={`${55 + (i * 7) % 30}%`} height={10} />
      ))}
    </div>
    {/* Rows */}
    {Array.from({ length: rows }, (_, r) => (
      <div
        key={r}
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 12,
          padding: '12px 14px',
          borderBottom: '1px solid var(--border)',
          opacity: 1 - r * 0.12,
        }}
      >
        {Array.from({ length: cols }, (_, c) => (
          <Skeleton key={c} width={`${60 + ((r + c) * 13) % 30}%`} height={13} />
        ))}
      </div>
    ))}
  </div>
);

/* ── Composite: stat cards row ────────────────────────────────────── */
interface SkeletonStatsProps {
  count?: number;
}

export const SkeletonStats: React.FC<SkeletonStatsProps> = ({ count = 4 }) => (
  <div style={{ display: 'grid', gridTemplateColumns: `repeat(${count}, 1fr)`, gap: 12 }}>
    {Array.from({ length: count }, (_, i) => (
      <div
        key={i}
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: '14px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <Skeleton width="50%" height={10} />
        <Skeleton width="70%" height={22} />
      </div>
    ))}
  </div>
);

/* ── Composite: chart placeholder ─────────────────────────────────── */
interface SkeletonChartProps {
  height?: number;
}

export const SkeletonChart: React.FC<SkeletonChartProps> = ({ height = 180 }) => (
  <div
    style={{
      background: 'var(--bg-panel)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      height,
      display: 'flex',
      alignItems: 'flex-end',
      justifyContent: 'center',
      padding: '16px 20px',
      gap: 6,
      overflow: 'hidden',
    }}
  >
    {Array.from({ length: 20 }, (_, i) => (
      <div
        key={i}
        className="skeleton-shimmer"
        style={{
          width: '4%',
          height: `${20 + ((i * 17 + 7) % 60)}%`,
          borderRadius: '3px 3px 0 0',
        }}
      />
    ))}
  </div>
);

export default Skeleton;
