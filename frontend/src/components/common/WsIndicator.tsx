import { useAppSelector } from '../../store/hooks';

export function WsIndicator() {
  const wsStatus = useAppSelector((s) => s.ui.wsStatus);
  const dotClass = `live-dot ${wsStatus !== 'connected' ? wsStatus : ''}`;
  const label = wsStatus === 'connected' ? 'LIVE' : wsStatus === 'connecting' ? 'CONN' : 'OFF';
  return (
    <span className="live-badge">
      <span className={dotClass} />
      {label}
    </span>
  );
}
