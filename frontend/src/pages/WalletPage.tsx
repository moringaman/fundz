import { Wallet, TrendingUp, TrendingDown, Activity } from 'lucide-react';
import { useBalance, usePaperBalance, useHyperliquidBalance } from '../hooks/useQueries';
import { SkeletonCard } from '../components/common/Skeleton';

function fmt(n: number, decimals = 4) {
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function VenueBadge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      fontSize: '.6rem', fontWeight: 700, letterSpacing: '.07em',
      padding: '.2rem .55rem', borderRadius: 20,
      background: color, color: 'var(--text-secondary)',
      textTransform: 'uppercase',
      border: '1px solid var(--border)',
    }}>
      {label}
    </span>
  );
}

function VenuePanel({
  title, badge, badgeColor, icon, loading, empty, children,
}: {
  title: string;
  badge?: string;
  badgeColor?: string;
  icon: React.ReactNode;
  loading: boolean;
  empty?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 18px', borderBottom: '1px solid var(--border)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {icon}
          <span style={{ fontWeight: 600, fontSize: 'var(--text-md)', color: 'var(--text-primary)' }}>{title}</span>
        </div>
        {badge && <VenueBadge label={badge} color={badgeColor ?? 'var(--border)'} />}
      </div>
      <div style={{ padding: '1rem 1.25rem' }}>
        {loading ? (
          <div className="wallet-grid">
            {Array.from({ length: 3 }, (_, i) => <SkeletonCard key={i} lines={2} height={88} />)}
          </div>
        ) : empty ? (
          <p style={{ color: 'var(--text-dim)', fontSize: '.78rem', padding: '.25rem 0' }}>{children}</p>
        ) : children}
      </div>
    </div>
  );
}

function BalanceCard({ asset, available, locked, highlight }: {
  asset: string;
  available: number;
  locked?: number;
  highlight?: boolean;
}) {
  return (
    <div style={{
      background: 'var(--bg-elevated)', border: `1px solid ${highlight ? 'rgba(99,102,241,.3)' : 'var(--border)'}`,
      borderRadius: 8, padding: '1rem',
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <span style={{ fontFamily: 'var(--mono)', fontSize: '.62rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
        {asset}
      </span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>
        {asset === 'USDT' ? `$${fmt(available, 2)}` : fmt(available, 4)}
      </span>
      {(locked ?? 0) > 0 && (
        <span style={{ fontFamily: 'var(--mono)', fontSize: '.68rem', color: 'var(--text-dim)' }}>
          Locked: {fmt(locked!, 4)}
        </span>
      )}
    </div>
  );
}

function StatTile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: 'var(--bg-elevated)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '.85rem 1rem',
    }}>
      <div style={{ fontSize: '.58rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontFamily: 'var(--mono)', fontSize: '.95rem', fontWeight: 700, color: color ?? 'var(--text-primary)' }}>
        {value}
      </div>
    </div>
  );
}

export function WalletPage() {
  const { data: phemexRaw, isPending: phemexLoading, isError: phemexError } = useBalance();
  const { data: paperRaw, isPending: paperLoading } = usePaperBalance();
  const { data: hlData, isPending: hlLoading, isError: hlError } = useHyperliquidBalance();

  const phemexBalances: any[] = Array.isArray(phemexRaw?.data)
    ? phemexRaw.data
    : Array.isArray(phemexRaw) ? phemexRaw : [];

  const paperBalances: any[] = Array.isArray(paperRaw?.data)
    ? paperRaw.data
    : Array.isArray(paperRaw) ? paperRaw : [];

  const hlPnlPositive = (hlData?.unrealized_pnl ?? 0) >= 0;

  return (
    <div className="page-content" style={{ paddingTop: '2rem', paddingBottom: '2rem' }}>

      {/* Header */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--sans)', margin: 0, color: 'var(--text)', letterSpacing: '-0.02em' }}>
          Wallets
        </h1>
        <p style={{ fontSize: '.8rem', color: 'var(--text-dim)', margin: '.4rem 0 0', fontFamily: 'var(--mono)' }}>
          Balances across paper trading, Phemex, and Hyperliquid
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

        {/* Paper Trading */}
        <VenuePanel
          title="Paper Trading"
          badge="SIMULATED"
          badgeColor="rgba(75,107,150,.2)"
          icon={<Wallet size={14} color="var(--text-secondary)" />}
          loading={paperLoading}
          empty={paperBalances.length === 0}
        >
          {paperBalances.length === 0
            ? 'No paper balances found.'
            : (
              <div className="wallet-grid">
                {paperBalances.map((b: any) => (
                  <BalanceCard key={b.asset} asset={b.asset} available={b.available} locked={b.locked} />
                ))}
              </div>
            )}
        </VenuePanel>

        {/* Phemex */}
        <VenuePanel
          title="Phemex"
          badge="LIVE"
          badgeColor="rgba(16,185,129,.12)"
          icon={<Activity size={14} color="var(--green)" />}
          loading={phemexLoading}
          empty={phemexError || phemexBalances.length === 0}
        >
          {phemexError
            ? 'Phemex API unavailable — check your API keys in Settings.'
            : phemexBalances.length === 0
              ? 'No Phemex balances found. Configure API keys in Settings.'
              : (
                <div className="wallet-grid">
                  {phemexBalances.map((b: any) => (
                    <BalanceCard key={b.asset} asset={b.asset} available={b.available} locked={b.locked} />
                  ))}
                </div>
              )}
        </VenuePanel>

        {/* Hyperliquid */}
        <VenuePanel
          title="Hyperliquid"
          badge="LIVE"
          badgeColor="rgba(139,92,246,.15)"
          icon={<TrendingUp size={14} color="var(--accent)" />}
          loading={hlLoading}
          empty={hlError || !hlData}
        >
          {hlError
            ? 'Hyperliquid wallet not configured — add your wallet address in Settings.'
            : !hlData
              ? 'No Hyperliquid data available.'
              : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                  {/* Summary stats */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '.65rem' }}>
                    <StatTile label="Account Value" value={`$${fmt(hlData.account_value, 2)}`} />
                    <StatTile label="Free Margin" value={`$${fmt(hlData.free_margin, 2)}`} />
                    <StatTile label="Margin Used" value={`$${fmt(hlData.margin_used, 2)}`} />
                    <StatTile
                      label="Unrealised PnL"
                      value={`${hlPnlPositive ? '+' : ''}$${fmt(hlData.unrealized_pnl, 2)}`}
                      color={hlPnlPositive ? 'var(--green)' : 'var(--red)'}
                    />
                  </div>

                  {/* Open positions */}
                  {hlData.positions.length > 0 && (
                    <div>
                      <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: '.6rem', fontFamily: 'var(--mono)' }}>
                        Open Positions — {hlData.positions.length}
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
                        {hlData.positions.map((p: any) => {
                          const isLong = p.size > 0;
                          const pnlPos = p.unrealized_pnl >= 0;
                          return (
                            <div key={p.coin} style={{
                              display: 'grid',
                              gridTemplateColumns: '80px 1fr 80px 80px 60px',
                              gap: '0 12px',
                              padding: '.7rem 1rem',
                              background: 'var(--bg-elevated)',
                              border: '1px solid var(--border)',
                              borderRadius: 8,
                              alignItems: 'center',
                            }}>
                              <div>
                                <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: '.82rem', color: 'var(--text-primary)' }}>{p.coin}</div>
                                <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', marginTop: 2 }}>Entry: ${fmt(p.entry_price, 2)}</div>
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                {isLong
                                  ? <TrendingUp size={13} color="var(--green)" />
                                  : <TrendingDown size={13} color="var(--red)" />}
                                <span style={{ fontSize: '.72rem', fontFamily: 'var(--mono)', color: isLong ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                                  {isLong ? 'LONG' : 'SHORT'}
                                </span>
                              </div>
                              <div style={{ textAlign: 'right', fontFamily: 'var(--mono)', fontSize: '.75rem', color: 'var(--text-secondary)' }}>
                                {isLong ? '+' : ''}{fmt(p.size, 4)}
                              </div>
                              <div style={{ textAlign: 'right', fontFamily: 'var(--mono)', fontSize: '.78rem', fontWeight: 600, color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                                {pnlPos ? '+' : ''}${fmt(p.unrealized_pnl, 2)}
                              </div>
                              <div style={{ textAlign: 'right', fontFamily: 'var(--mono)', fontSize: '.7rem', color: 'var(--text-dim)' }}>
                                {p.leverage}×
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
        </VenuePanel>

      </div>
    </div>
  );
}
