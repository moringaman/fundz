import { WhaleIntelligencePanel } from '../components/WhaleIntelligencePanel';

export function WhalePage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h1 className="page-title">Whale Intelligence</h1>
        <p className="page-subtitle">
          Hyperliquid on-chain smart-money positioning — updated every 60 seconds
        </p>
      </div>
      <WhaleIntelligencePanel />
    </div>
  );
}
