import { useBalance } from '../hooks/useQueries';
import { SkeletonCard } from '../components/common/Skeleton';

export function WalletPage() {
  const { data: balancesRaw, isPending: balanceLoading } = useBalance();
  const balances: any[] = Array.isArray(balancesRaw?.data)
    ? balancesRaw.data
    : Array.isArray(balancesRaw)
      ? balancesRaw
      : [];

  return (
    <div className="space-y-6">
      <h1 className="page-title">Wallet</h1>
      {balanceLoading ? (
        <div className="wallet-grid">
          {Array.from({ length: 4 }, (_, i) => (
            <SkeletonCard key={i} lines={2} height={100} />
          ))}
        </div>
      ) : (
      <div className="wallet-grid">
        {balances.length === 0 ? (
          <p className="text-gray-400">No balances found. Configure API key to sync.</p>
        ) : (
          balances.map((balance) => (
            <div key={balance.asset} className="balance-card">
              <p className="balance-asset">{balance.asset}</p>
              <p className="balance-amount">Available: {balance.available.toFixed(4)}</p>
              <p className="balance-amount">Locked: {balance.locked.toFixed(4)}</p>
            </div>
          ))
        )}
      </div>
      )}
    </div>
  );
}
