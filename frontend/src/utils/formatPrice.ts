/**
 * Smart price formatter that adapts decimal places based on value.
 *
 *   >= $1000   → 2 decimals   ($70,319.40)
 *   >= $1      → 4 decimals   ($1.3342)
 *   >= $0.01   → 6 decimals   ($0.271546)
 *   < $0.01    → 8 decimals   ($0.00001234)
 */
export function formatPrice(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toFixed(2);
  if (abs >= 1) return v.toFixed(4);
  if (abs >= 0.01) return v.toFixed(6);
  return v.toFixed(8);
}

/**
 * Smart P&L formatter — preserves sign and uses enough precision to avoid
 * showing "$-0.00" for small-value losses (e.g. high-qty low-price assets).
 *
 *   |pnl| >= $1    → 2 decimals  (+$12.34)
 *   |pnl| >= $0.01 → 4 decimals  (+$0.0342)
 *   otherwise      → 6 decimals  (+$0.000012)
 */
export function formatPnl(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  const sign = v >= 0 ? '+' : '-';
  if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
  if (abs >= 0.01) return `${sign}$${abs.toFixed(4)}`;
  return `${sign}$${abs.toFixed(6)}`;
}

/**
 * Smart P&L percentage formatter — preserves sign with enough decimal places
 * to avoid showing "+0.00%" for small moves on low-price assets.
 *
 *   |pct| >= 0.1   → 2 decimals  (+1.23%)
 *   otherwise      → 4 decimals  (+0.0123%)
 */
export function formatPnlPct(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  const sign = v >= 0 ? '+' : '-';
  if (abs >= 0.1) return `${sign}${abs.toFixed(2)}%`;
  return `${sign}${abs.toFixed(4)}%`;
}
