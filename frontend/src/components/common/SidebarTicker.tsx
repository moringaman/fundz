import { useAppSelector } from '../../store/hooks';
import { WsIndicator } from './WsIndicator';

export function SidebarTicker() {
  const ticker = useAppSelector((s) => s.market.ticker);
  if (!ticker) return null;
  const up = ticker.priceChangePercent >= 0;
  return (
    <div className="sidebar-footer">
      <div className="sidebar-ticker">
        <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
          {ticker.symbol}
        </span>
        <WsIndicator />
      </div>
      <div className="sidebar-ticker">
        <span className="sidebar-ticker-price">${ticker.lastPrice?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
        <span className={`sidebar-ticker-change ${up ? 'up' : 'down'}`}>
          {up ? '+' : ''}{ticker.priceChangePercent?.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}
