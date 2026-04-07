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
