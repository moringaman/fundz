import { useBalance, usePaperBalance, useHyperliquidBalance } from '../hooks/useQueries';
import { SkeletonCard } from '../components/common/Skeleton';

function fmt(n: number, decimals = 4) {
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function VenueSection({
  title,
  badge,
  badgeColor,
  loading,
  children,
}: {
  title: string;
  badge?: string;
  badgeColor?: string;
  loading: boolean;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider">{title}</h2>
        {badge && (
          <span
            className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
            style={{ background: badgeColor ?? 'var(--border)', color: 'var(--text)' }}
          >
            {badge}
          </span>
        )}
      </div>
      {loading ? (
        <div className="wallet-grid">
          {Array.from({ length: 3 }, (_, i) => <SkeletonCard key={i} lines={2} height={90} />)}
        </div>
      ) : (
        children
      )}
    </section>
  );
}

export function WalletPage() {
  const { data: phemexRaw, isPending: phemexLoading, isError: phemexError } = useBalance();
  const { data: paperRaw, isPending: paperLoading } = usePaperBalance();
  const { data: hlData, isPending: hlLoading, isError: hlError } = useHyperliquidBalance();

  const phemexBalances: any[] = Array.isArray(phemexRaw?.data)
    ? phemexRaw.data
    : Array.isArray(phemexRaw)
      ? phemexRaw
      : [];

  const paperBalances: any[] = Array.isArray(paperRaw?.data)
    ? paperRaw.data
    : Array.isArray(paperRaw)
      ? paperRaw
      : [];

  return (
    <div className="space-y-8">
      <h1 className="page-title">Wallets</h1>

      {/* ── Paper Trading ─────────────────────────────────────────────── */}
      <VenueSection title="Paper Trading" badge="SIMULATED" badgeColor="#2a3a4a" loading={paperLoading}>
        {paperBalances.length === 0 ? (
          <p className="text-sm text-[var(--text-dim)]">No paper balances found.</p>
        ) : (
          <div className="wallet-grid">
            {paperBalances.map((b: any) => (
              <div key={b.asset} className="balance-card">
                <p className="balance-asset">{b.asset}</p>
                <p className="balance-amount">Available: {fmt(b.available)}</p>
                <p className="balance-amount">Locked: {fmt(b.locked)}</p>
              </div>
            ))}
          </div>
        )}
      </VenueSection>

      {/* ── Phemex ────────────────────────────────────────────────────── */}
      <VenueSection title="Phemex" badge="LIVE" badgeColor="rgba(16,185,129,.15)" loading={phemexLoading}>
        {phemexError ? (
          <p className="text-sm text-[var(--text-dim)]">Phemex API unavailable. Check your API keys in Settings.</p>
        ) : phemexBalances.length === 0 ? (
          <p className="text-sm text-[var(--text-dim)]">No Phemex balances found. Configure API keys in Settings.</p>
        ) : (
          <div className="wallet-grid">
            {phemexBalances.map((b: any) => (
              <div key={b.asset} className="balance-card">
                <p className="balance-asset">{b.asset}</p>
                <p className="balance-amount">Available: {fmt(b.available)}</p>
                <p className="balance-amount">Locked: {fmt(b.locked)}</p>
              </div>
            ))}
          </div>
        )}
      </VenueSection>

      {/* ── Hyperliquid ───────────────────────────────────────────────── */}
      <VenueSection title="Hyperliquid" badge="LIVE" badgeColor="rgba(139,92,246,.15)" loading={hlLoading}>
        {hlError ? (
          <p className="text-sm text-[var(--text-dim)]">Hyperliquid wallet not configured. Add your wallet address in Settings.</p>
        ) : !hlData ? null : (
          <div className="space-y-4">
            {/* Summary cards */}
            <div className="wallet-grid">
              <div className="balance-card">
                <p className="balance-asset">Account Value</p>
                <p className="balance-amount">${fmt(hlData.account_value, 2)}</p>
              </div>
              <div className="balance-card">
                <p className="balance-asset">Free Margin</p>
                <p className="balance-amount">${fmt(hlData.free_margin, 2)}</p>
              </div>
              <div className="balance-card">
                <p className="balance-asset">Margin Used</p>
                <p className="balance-amount">${fmt(hlData.margin_used, 2)}</p>
              </div>
              <div className="balance-card">
                <p className="balance-asset">Unrealised PnL</p>
                <p
                  className="balance-amount"
                  style={{ color: hlData.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}
                >
                  {hlData.unrealized_pnl >= 0 ? '+' : ''}${fmt(hlData.unrealized_pnl, 2)}
                </p>
              </div>
            </div>

            {/* Open positions */}
            {hlData.positions.length > 0 && (
              <div>
                <h3 className="text-xs text-[var(--text-dim)] uppercase tracking-wider mb-2">Open Positions</h3>
                <div className="wallet-grid">
                  {hlData.positions.map((p: any) => (
                    <div key={p.coin} className="balance-card">
                      <p className="balance-asset">{p.coin}</p>
                      <p className="balance-amount">Size: {p.size > 0 ? '+' : ''}{fmt(p.size, 4)}</p>
                      <p className="balance-amount">Entry: ${fmt(p.entry_price, 2)}</p>
                      <p
                        className="balance-amount"
                        style={{ color: p.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}
                      >
                        PnL: {p.unrealized_pnl >= 0 ? '+' : ''}${fmt(p.unrealized_pnl, 2)}
                      </p>
                      <p className="balance-amount">Leverage: {p.leverage}×</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </VenueSection>
    </div>
  );
}
