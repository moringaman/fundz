import { useState, useMemo } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

export function usePagination<T>(items: T[], pageSize = 10) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, totalPages);

  const pageItems = useMemo(
    () => items.slice((safePage - 1) * pageSize, safePage * pageSize),
    [items, safePage, pageSize],
  );

  // Reset to page 1 when item list changes length significantly
  const reset = () => setPage(1);

  return { page: safePage, setPage, totalPages, pageItems, total: items.length, reset };
}

interface PaginatorProps {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPage: (p: number) => void;
  label?: string;
}

export function Paginator({ page, totalPages, total, pageSize, onPage, label = 'items' }: PaginatorProps) {
  if (totalPages <= 1) return null;

  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '.45rem .6rem',
      borderTop: '1px solid var(--border)',
      marginTop: '.25rem',
    }}>
      <span style={{ fontSize: '.68rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
        {from}–{to} of {total} {label}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: '.25rem' }}>
        <button
          type="button"
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 26, height: 26, borderRadius: 5, border: '1px solid var(--border)',
            background: 'var(--bg-elevated)', cursor: page <= 1 ? 'not-allowed' : 'pointer',
            opacity: page <= 1 ? 0.35 : 1, color: 'var(--text-secondary)',
          }}
        >
          <ChevronLeft size={13} />
        </button>

        {/* Page numbers — show up to 5 */}
        {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => {
          let p: number;
          if (totalPages <= 5) {
            p = i + 1;
          } else if (page <= 3) {
            p = i + 1;
          } else if (page >= totalPages - 2) {
            p = totalPages - 4 + i;
          } else {
            p = page - 2 + i;
          }
          return (
            <button
              key={p}
              type="button"
              onClick={() => onPage(p)}
              style={{
                width: 26, height: 26, borderRadius: 5, border: '1px solid var(--border)',
                background: p === page ? 'var(--accent)' : 'var(--bg-elevated)',
                color: p === page ? '#000' : 'var(--text-secondary)',
                fontSize: '.7rem', fontFamily: 'var(--mono)', fontWeight: p === page ? 700 : 400,
                cursor: 'pointer',
              }}
            >
              {p}
            </button>
          );
        })}

        <button
          type="button"
          onClick={() => onPage(page + 1)}
          disabled={page >= totalPages}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 26, height: 26, borderRadius: 5, border: '1px solid var(--border)',
            background: 'var(--bg-elevated)', cursor: page >= totalPages ? 'not-allowed' : 'pointer',
            opacity: page >= totalPages ? 0.35 : 1, color: 'var(--text-secondary)',
          }}
        >
          <ChevronRight size={13} />
        </button>
      </div>
    </div>
  );
}
