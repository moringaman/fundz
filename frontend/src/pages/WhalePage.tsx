import { WhaleIntelligencePanel } from '../components/WhaleIntelligencePanel';

export function WhalePage() {
  return (
    <div className="page-content" style={{ paddingTop: '2rem', paddingBottom: '2rem' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--sans)', margin: 0, color: 'var(--text)', letterSpacing: '-0.02em' }}>
          Whale Intelligence
        </h1>
        <p style={{ fontSize: '.8rem', color: 'var(--text-dim)', margin: '.4rem 0 0', fontFamily: 'var(--mono)' }}>
          Hyperliquid on-chain smart-money positioning — updated every 60 seconds
        </p>
      </div>
      <WhaleIntelligencePanel />
    </div>
  );
}
