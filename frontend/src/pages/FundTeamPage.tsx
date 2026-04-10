import { useState } from 'react';
import { AlertTriangle, BarChart3, ChevronUp, ChevronDown, Users, GitBranch } from 'lucide-react';
import {
  useFundMarketAnalysis,
  useFundRiskAssessment,
  useFundCIOReport,
  useFundPerformanceAttribution,
  useFundTeamRoster,
  useFundTechnicalAnalysisBatch,
  useAgents as _useAgents,
  useStrategyActions,
  useTradeRetrospective,
  useTraderLeaderboard,
  useTraderAllocation,
} from '../hooks/useQueries';
import { DailyReportPanel } from '../components/DailyReportPanel';
import { TeamChatPanel } from '../components/TeamChatPanel';
import { usePagination, Paginator } from '../components/common/Paginator';

export function FundTeamPage() {
  const { data: marketAnalysis, isLoading: marketLoading } = useFundMarketAnalysis();
  const { data: technicalBatch, isLoading: technicalLoading } = useFundTechnicalAnalysisBatch();
  const { data: riskData, isLoading: riskLoading } = useFundRiskAssessment();
  const { data: cioReport, isLoading: cioLoading } = useFundCIOReport();
  const { data: attribution, isLoading: attrLoading } = useFundPerformanceAttribution();
  const { data: traderAllocation, isLoading: allocLoading } = useTraderAllocation();
  const { data: teamRoster } = useFundTeamRoster();
  // agents data available via _useAgents() if needed for future agent-name lookups
  const { data: strategyActions } = useStrategyActions();
  const { data: retroData } = useTradeRetrospective();
  const { data: traderLeaderboard = [] } = useTraderLeaderboard();
  const [selectedTASymbol, setSelectedTASymbol] = useState<string | null>(null);

  const taReports: any[] = Array.isArray(technicalBatch) ? technicalBatch : [];
  const activeSymbol = selectedTASymbol || (taReports.length > 0 ? taReports[0].symbol : null);
  const technicalAnalysis = taReports.find((r: any) => r.symbol === activeSymbol) || null;

  const leaderboardEntries: any[] = (cioReport as any)?.agent_leaderboard ?? [];
  const leaderboardPager = usePagination(leaderboardEntries, 8);
  const strategyActionsList: any[] = Array.isArray(strategyActions) ? strategyActions : [];
  const actionsPager = usePagination(strategyActionsList, 8);

  const riskLevel: string = (riskData as any)?.risk_level ?? 'unknown';
  const riskColors: Record<string, string> = {
    safe: 'var(--green)',
    caution: 'var(--amber)',
    danger: 'var(--red)',
    unknown: 'var(--text-dim)',
  };
  const riskColor = riskColors[riskLevel] ?? 'var(--text-dim)';

  const sentiment: string = (cioReport as any)?.cio_sentiment ?? 'neutral';
  const sentimentColors: Record<string, string> = {
    very_bullish: 'var(--green)',
    bullish: 'var(--green)',
    neutral: 'var(--text-secondary)',
    bearish: 'var(--red)',
    very_bearish: 'var(--red)',
  };
  const sentimentColor = sentimentColors[sentiment] ?? 'var(--text-secondary)';

  const getTeamMember = (role: string) => {
    const roster = (teamRoster as any) || [];
    return roster.find((m: any) => m.role === role) || {
      name: 'Unknown Agent',
      avatar: '🤖',
      title: 'Agent',
      bio: 'Loading...'
    };
  };

  return (
    <div className="page-content" style={{ paddingTop: '2rem', paddingBottom: '2rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2.5rem', flexWrap: 'wrap', gap: '1.25rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--sans)', margin: 0, color: 'var(--text)', letterSpacing: '-0.02em' }}>
            Fund Management Team
          </h1>
          <p style={{ fontSize: '.8rem', color: 'var(--text-dim)', margin: '.5rem 0 0', fontFamily: 'var(--mono)', lineHeight: 1.5 }}>
            AI-driven multi-agent fund coordination — team decisions update every 5 minutes
          </p>
        </div>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{
            padding: '6px 14px',
            borderRadius: '8px',
            fontSize: '.75rem',
            fontFamily: 'var(--mono)',
            fontWeight: 700,
            background: riskLevel === 'safe' ? 'var(--green-dim)' : riskLevel === 'danger' ? 'var(--red-dim)' : 'var(--amber-dim)',
            color: riskColor,
            border: `1px solid ${riskColor}40`,
          }}>
            {riskLevel.toUpperCase()} RISK
          </span>
          <span style={{
            padding: '6px 14px',
            borderRadius: '8px',
            fontSize: '.75rem',
            fontFamily: 'var(--mono)',
            fontWeight: 700,
            background: 'var(--bg-card)',
            color: sentimentColor,
            border: `1px solid ${sentimentColor}40`,
          }}>
            CIO: {sentiment.replace('_', ' ').toUpperCase()}
          </span>
        </div>
      </div>
      <div style={{  marginBottom: '2rem' }}>
      <TeamChatPanel />
      </div>

      {/* Main Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.5rem' }}>

        {/* === Trader Leaderboard === */}
        <div className="panel" style={{ gridColumn: 'span 2' }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <span style={{ fontSize: '1.2rem' }}>🏆</span>
              <span className="panel-title">Competing Traders</span>
            </div>
            <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-dim)' }}>
              {(traderLeaderboard as any[]).filter((t: any) => t.is_enabled).length} active
            </span>
          </div>
          <div className="panel-body">
            {(traderLeaderboard as any[]).length === 0 ? (
              <p style={{ fontSize: '.78rem', color: 'var(--text-dim)', textAlign: 'center', padding: '1rem 0' }}>
                No traders configured. Start the scheduler to seed defaults.
              </p>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '.75rem' }}>
                {(traderLeaderboard as any[]).map((t: any, i: number) => (
                  <div key={t.id} style={{
                    padding: '.75rem',
                    background: 'var(--bg-elevated)',
                    borderRadius: '8px',
                    border: `1px solid ${i === 0 ? 'rgba(0,230,118,.3)' : 'var(--border)'}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                        <span style={{ fontSize: '1.1rem' }}>{t.config?.avatar || ['🥇','🥈','🥉'][i] || '🏅'}</span>
                        <div>
                          <div style={{ fontSize: '.85rem', fontWeight: 700, color: 'var(--text-primary)' }}>{t.name}</div>
                          <div style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                            {t.llm_model?.split('/').pop() || 'unknown'}
                          </div>
                        </div>
                      </div>
                      <span style={{
                        fontSize: '.6rem', fontFamily: 'var(--mono)', padding: '.15rem .35rem',
                        borderRadius: '4px',
                        background: t.is_enabled ? 'var(--green-dim)' : 'var(--bg-hover)',
                        color: t.is_enabled ? 'var(--green)' : 'var(--text-dim)',
                      }}>
                        {t.is_enabled ? 'ACTIVE' : 'OFF'}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.35rem' }}>
                      <div className="stat-card">
                        <div className="stat-label">P&L</div>
                        <div className={`stat-value ${t.total_pnl >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: '.82rem' }}>
                          {t.total_pnl >= 0 ? '+' : ''}${t.total_pnl?.toFixed(2)}
                        </div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-label">Win Rate</div>
                        <div className="stat-value" style={{ fontSize: '.82rem' }}>
                          {t.win_rate != null ? `${(t.win_rate * 100).toFixed(0)}%` : '—'}
                        </div>
                      </div>
                      <div className="stat-card">
                        <div className="stat-label">Allocation</div>
                        <div className="stat-value" style={{ fontSize: '.82rem', color: 'var(--accent)' }}>
                          {t.allocation_pct?.toFixed(1)}%
                        </div>
                        {t.allocation_dollars > 0 && (
                          <div style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)', marginTop: '.1rem' }}>
                            ${t.allocation_dollars?.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                          </div>
                        )}
                      </div>
                      <div className="stat-card">
                        <div className="stat-label">Strategies</div>
                        <div className="stat-value" style={{ fontSize: '.82rem' }}>
                          {t.agent_count}
                        </div>
                      </div>
                    </div>
                    <div style={{ marginTop: '.35rem', fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                      {t.total_trades} trades
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* === Research Analyst === */}
        <div className="panel" style={{ gridColumn: 'span 2' }}>
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('research_analyst').avatar} alt={getTeamMember('research_analyst').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('research_analyst').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('research_analyst').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
              MARKET ANALYSIS
            </span>
          </div>

          {marketLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem', color: 'var(--text-dim)' }}>Analyzing markets...</div>
          ) : marketAnalysis ? (
            <div>
              {/* Analyst Summary */}
              <div style={{ fontSize: '.75rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.7, padding: '.75rem 1rem', background: 'var(--bg-hover)', borderRadius: '6px', marginBottom: '1rem', borderLeft: '3px solid var(--accent)' }}>
                <strong style={{ color: 'var(--text)', display: 'block', marginBottom: '.5rem' }}>Market Analysis Summary:</strong>
                {(marketAnalysis as any).reasoning || 'Analyzing market conditions across major symbols...'}
              </div>

              {/* Regime & Sentiment */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '.75rem', marginBottom: '1rem' }}>
                {[
                  { label: 'Regime', value: (marketAnalysis as any).market_regime?.regime?.replace('_', ' ') ?? 'N/A' },
                  { label: 'Sentiment', value: (marketAnalysis as any).market_regime?.sentiment?.replace('_', ' ') ?? 'N/A' },
                  { label: 'Volatility', value: (marketAnalysis as any).market_regime?.volatility_regime ?? 'N/A' },
                  { label: 'Correlation', value: (marketAnalysis as any).market_regime?.correlation_status?.replace('_', ' ') ?? 'N/A' },
                ].map(item => (
                  <div key={item.label} style={{ textAlign: 'center', padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '6px' }}>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.25rem' }}>{item.label}</div>
                    <div style={{ fontSize: '.8rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text)', textTransform: 'capitalize' }}>{item.value}</div>
                  </div>
                ))}
              </div>

              {/* Opportunities */}
              {((marketAnalysis as any).opportunities?.length > 0) && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginBottom: '.5rem', textTransform: 'uppercase', letterSpacing: '.06em' }}>
                    Opportunities Identified
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                    {((marketAnalysis as any).opportunities as any[])?.slice(0, 4).map((opp: any, i: number) => (
                      <div key={i} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '.4rem .6rem', background: 'var(--bg-hover)', borderRadius: '4px',
                        borderLeft: `2px solid ${opp.recommended_action === 'buy' ? 'var(--green)' : opp.recommended_action === 'sell' ? 'var(--red)' : 'var(--border)'}`,
                      }}>
                        <div>
                          <span style={{ fontFamily: 'var(--mono)', fontSize: '.75rem', fontWeight: 700, color: 'var(--text)' }}>{opp.symbol}</span>
                          <span style={{ marginLeft: '.5rem', fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{opp.opportunity_type?.replace(/_/g, ' ')}</span>
                        </div>
                        <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
                          <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{(opp.confidence * 100).toFixed(0)}%</span>
                          <span style={{
                            padding: '1px 6px',
                            borderRadius: '3px',
                            fontSize: '.6rem',
                            fontFamily: 'var(--mono)',
                            fontWeight: 700,
                            background: opp.recommended_action === 'buy' ? 'var(--green-dim)' : opp.recommended_action === 'sell' ? 'var(--red-dim)' : 'var(--bg-card)',
                            color: opp.recommended_action === 'buy' ? 'var(--green)' : opp.recommended_action === 'sell' ? 'var(--red)' : 'var(--text-dim)',
                          }}>
                            {opp.recommended_action?.toUpperCase()}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Macro Context */}
              {(marketAnalysis as any).market_regime?.macro_context && (
                <div style={{ fontSize: '.7rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.6, padding: '.5rem .75rem', background: 'var(--bg-hover)', borderRadius: '6px', borderLeft: '2px solid var(--accent)' }}>
                  {(marketAnalysis as any).market_regime.macro_context}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No market analysis available</div>
          )}
        </div>

        {/* === Technical Analyst === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('technical_analyst').avatar} alt={getTeamMember('technical_analyst').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('technical_analyst').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('technical_analyst').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>
              CHART ANALYSIS
            </span>
          </div>

          {technicalLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Analyzing charts...</div>
          ) : taReports.length > 0 ? (
            <div>
              {/* Symbol Tabs */}
              {taReports.length > 1 && (
                <div style={{ display: 'flex', gap: '.35rem', marginBottom: '.75rem', flexWrap: 'wrap' }}>
                  {taReports.map((report: any) => (
                    <button
                      key={report.symbol}
                      onClick={() => setSelectedTASymbol(report.symbol)}
                      className="ta-symbol-tab"
                      style={{
                        padding: '3px 10px',
                        borderRadius: '4px',
                        border: 'none',
                        cursor: 'pointer',
                        fontSize: '.65rem',
                        fontFamily: 'var(--mono)',
                        fontWeight: 600,
                        background: report.symbol === activeSymbol ? 'var(--accent)' : 'var(--bg-hover)',
                        color: report.symbol === activeSymbol ? '#000' : 'var(--text-dim)',
                        transition: 'all .15s ease',
                      }}
                    >
                      {report.symbol.replace('USDT', '')}
                      <span style={{
                        marginLeft: '4px',
                        fontSize: '.55rem',
                        color: report.overall_signal === 'bullish' ? 'var(--green)' : report.overall_signal === 'bearish' ? 'var(--red)' : 'var(--text-dim)',
                      }}>
                        {report.overall_signal === 'bullish' ? '▲' : report.overall_signal === 'bearish' ? '▼' : '—'}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {technicalAnalysis && (
              <div>
              {/* Current Price & Signal */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <div>
                  <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text)' }}>
                    ${((technicalAnalysis as any).current_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </div>
                  <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{(technicalAnalysis as any).symbol || activeSymbol}</div>
                </div>
                <div style={{
                  padding: '4px 12px',
                  borderRadius: '6px',
                  fontSize: '.75rem',
                  fontFamily: 'var(--mono)',
                  fontWeight: 700,
                  background: (technicalAnalysis as any).overall_signal === 'bullish' ? 'var(--green-dim)' : (technicalAnalysis as any).overall_signal === 'bearish' ? 'var(--red-dim)' : 'var(--bg-hover)',
                  color: (technicalAnalysis as any).overall_signal === 'bullish' ? 'var(--green)' : (technicalAnalysis as any).overall_signal === 'bearish' ? 'var(--red)' : 'var(--text-dim)',
                }}>
                  {(technicalAnalysis as any).overall_signal?.toUpperCase() || 'HOLD'} ({((technicalAnalysis as any).confidence || 0) * 100}%)
                </div>
              </div>

              {/* Price Levels */}
              {(technicalAnalysis as any).price_levels && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Key Levels</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
                    {(technicalAnalysis as any).price_levels.resistance?.slice(0, 2).map((r: number, i: number) => (
                      <div key={`r${i}`} style={{ display: 'flex', justifyContent: 'space-between', padding: '.3rem .5rem', background: 'var(--red-dim)', borderRadius: '4px' }}>
                        <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>R{i + 1}</span>
                        <span style={{ fontSize: '.7rem', color: 'var(--red)', fontFamily: 'var(--mono)', fontWeight: 600 }}>${r.toLocaleString()}</span>
                      </div>
                    ))}
                    {(technicalAnalysis as any).price_levels.support?.slice(0, 2).map((s: number, i: number) => (
                      <div key={`s${i}`} style={{ display: 'flex', justifyContent: 'space-between', padding: '.3rem .5rem', background: 'var(--green-dim)', borderRadius: '4px' }}>
                        <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>S{i + 1}</span>
                        <span style={{ fontSize: '.7rem', color: 'var(--green)', fontFamily: 'var(--mono)', fontWeight: 600 }}>${s.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Fibonacci */}
              {(technicalAnalysis as any).price_levels?.fibonacci_retracements && (
                <div style={{ marginBottom: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Fibonacci Retracements</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '.25rem' }}>
                    {Object.entries((technicalAnalysis as any).price_levels.fibonacci_retracements).slice(0, 3).map(([level, price]: [string, any]) => (
                      <div key={level} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.65rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>
                        <span>{level}</span>
                        <span>${(price || 0).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Patterns */}
              {((technicalAnalysis as any).patterns?.length > 0) && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.4rem' }}>Pattern Signals</div>
                  {((technicalAnalysis as any).patterns as any[]).slice(0, 2).map((pattern: any, i: number) => (
                    <div key={i} style={{
                      padding: '.4rem .5rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '4px',
                      marginBottom: '.3rem',
                      borderLeft: `2px solid ${pattern.direction === 'bullish' ? 'var(--green)' : 'var(--red)'}`,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.2rem' }}>
                        <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', fontWeight: 600, color: 'var(--text)' }}>{pattern.pattern_type?.replace(/_/g, ' ')}</span>
                        <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: pattern.direction === 'bullish' ? 'var(--green)' : 'var(--red)' }}>{pattern.direction?.toUpperCase()} {(pattern.confidence * 100).toFixed(0)}%</span>
                      </div>
                      <div style={{ display: 'flex', gap: '.5rem', fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                        <span>SL: ${pattern.stop_loss?.toFixed(0)}</span>
                        <span>TP1: ${pattern.take_profit_1?.toFixed(0)}</span>
                        <span>RR: {pattern.risk_reward?.toFixed(1)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Multi-Timeframe */}
              {(technicalAnalysis as any).multi_timeframe && (() => {
                const mtf = (technicalAnalysis as any).multi_timeframe;
                const tiers = [
                  { label: mtf.tf_primary || '1h', data: mtf.timeframe_1h },
                  { label: mtf.tf_mid     || '4h', data: mtf.timeframe_4h },
                  { label: mtf.tf_high    || '1d', data: mtf.timeframe_1d },
                ];
                const trendColor = (t: string) =>
                  t === 'bullish' ? 'var(--green)' : t === 'bearish' ? 'var(--red)' : 'var(--text-dim)';
                const alignColor = mtf.alignment === 'bullish' ? 'var(--green)' : mtf.alignment === 'bearish' ? 'var(--red)' : 'var(--amber)';
                return (
                  <div style={{ marginTop: '.75rem', padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '6px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.4rem' }}>
                      <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Multi-Timeframe</span>
                      <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: alignColor, fontWeight: 700 }}>
                        {mtf.alignment?.toUpperCase()} · {Math.round((mtf.confluence_score || 0) * 100)}%
                      </span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '.3rem' }}>
                      {tiers.map(({ label, data }) => {
                        const trend = data?.trend || 'neutral';
                        return (
                          <div key={label} style={{ background: 'var(--bg-card)', borderRadius: '4px', padding: '.3rem .4rem', textAlign: 'center' }}>
                            <div style={{ fontSize: '.55rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', marginBottom: '.15rem' }}>{label}</div>
                            <div style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', fontWeight: 700, color: trendColor(trend) }}>
                              {trend === 'bullish' ? '▲' : trend === 'bearish' ? '▼' : '—'} {trend.toUpperCase()}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {mtf.trend_confirmation && (
                      <div style={{ fontSize: '.55rem', color: 'var(--green)', fontFamily: 'var(--mono)', textAlign: 'center', marginTop: '.35rem' }}>
                        ✓ Trend confirmed across timeframes
                      </div>
                    )}
                  </div>
                );
              })()}
              </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No technical analysis available</div>
          )}
        </div>

        {/* === Risk Manager === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('risk_manager').avatar} alt={getTeamMember('risk_manager').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('risk_manager').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('risk_manager').title}</div>
              </div>
            </div>
            <span style={{
              fontSize: '.65rem',
              fontFamily: 'var(--mono)',
              fontWeight: 700,
              color: riskColor,
              padding: '2px 8px',
              background: `${riskColor}18`,
              borderRadius: '4px',
            }}>
              {riskLevel.toUpperCase()}
            </span>
          </div>

          {riskLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Assessing risk...</div>
          ) : riskData ? (
            <div>
              {/* Risk Level Gauge */}
              <div style={{ marginBottom: '1rem', textAlign: 'center' }}>
                <div style={{
                  fontSize: '1.75rem',
                  fontWeight: 900,
                  fontFamily: 'var(--mono)',
                  color: riskColor,
                  lineHeight: 1,
                }}>
                  {riskLevel.toUpperCase()}
                </div>
                <div style={{ fontSize: '.7rem', color: 'var(--text-dim)', marginTop: '.25rem', fontFamily: 'var(--mono)' }}>Portfolio Risk Level</div>
              </div>

              {/* Risk Explanation */}
              <div style={{ fontSize: '.72rem', color: 'var(--text-secondary)', fontFamily: 'var(--sans)', lineHeight: 1.6, padding: '.6rem .8rem', background: 'var(--bg-hover)', borderRadius: '6px', marginBottom: '.75rem', borderLeft: '3px solid ' + riskColor }}>
                {(riskData as any).reasoning || 'Monitoring portfolio risk exposure and limits.'}
              </div>

              {/* Risk Metrics */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem', marginBottom: '.75rem' }}>
                {[
                  { label: 'Daily P&L', value: `$${((riskData as any).daily_pnl ?? 0).toFixed(2)}`, color: ((riskData as any).daily_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' },
                  { label: 'Exposure', value: `${((riskData as any).exposure_pct_of_capital ?? 0).toFixed(1)}%`, color: 'var(--text)' },
                  { label: 'Concentration', value: ((riskData as any).concentration_risk ?? 'N/A').toUpperCase(), color: 'var(--text)' },
                ].map(item => (
                  <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '.7rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{item.label}</span>
                    <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: item.color }}>{item.value}</span>
                  </div>
                ))}
              </div>

              {/* Recommendations */}
              {((riskData as any).recommendations as string[])?.length > 0 && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>Recommendations</div>
                  {((riskData as any).recommendations as string[]).map((rec: string, i: number) => (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'flex-start', gap: '.4rem',
                      fontSize: '.7rem', color: 'var(--text-secondary)',
                      padding: '.3rem .5rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '4px',
                      marginBottom: '.3rem',
                      lineHeight: 1.5,
                    }}>
                      <AlertTriangle size={10} style={{ color: 'var(--amber)', flexShrink: 0, marginTop: '2px' }} />
                      {rec}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No risk data available</div>
          )}
        </div>

        {/* === CIO Report === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('cio_agent').avatar} alt={getTeamMember('cio_agent').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('cio_agent').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('cio_agent').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>OVERSIGHT</span>
          </div>

          {cioLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Generating report...</div>
          ) : cioReport ? (
            <div>
              {/* Sentiment */}
              <div style={{ textAlign: 'center', marginBottom: '1rem' }}>
                <div style={{ fontSize: '1rem', fontWeight: 800, fontFamily: 'var(--mono)', color: sentimentColor, textTransform: 'uppercase' }}>
                  {sentiment.replace('_', ' ')}
                </div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>CIO Sentiment</div>
              </div>

              {/* Executive Summary */}
              {(cioReport as any).executive_summary && (
                <div style={{ marginBottom: '1rem', fontSize: '.72rem', color: 'var(--text-secondary)', lineHeight: 1.6, padding: '.5rem .75rem', background: 'var(--bg-hover)', borderRadius: '6px' }}>
                  {(cioReport as any).executive_summary}
                </div>
              )}

              {/* Strategic Recommendations */}
              {((cioReport as any).strategic_recommendations as any[])?.length > 0 && (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.5rem' }}>Strategic Actions</div>
                  {((cioReport as any).strategic_recommendations as any[]).slice(0, 3).map((rec: any, i: number) => (
                    <div key={i} style={{
                      padding: '.5rem .75rem',
                      background: 'var(--bg-hover)',
                      borderRadius: '6px',
                      marginBottom: '.4rem',
                      borderLeft: `2px solid ${rec.confidence > 0.7 ? 'var(--accent)' : 'var(--border)'}`,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.2rem' }}>
                        <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--text)', textTransform: 'capitalize' }}>
                          {rec.recommendation?.replace(/_/g, ' ')}
                        </span>
                        <span style={{ fontSize: '.6rem', fontFamily: 'var(--mono)', color: 'var(--text-dim)' }}>
                          {(rec.confidence * 100).toFixed(0)}% conf
                        </span>
                      </div>
                      <div style={{ fontSize: '.65rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{rec.rationale}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No CIO report available</div>
          )}
        </div>

        {/* === Performance Attribution === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <BarChart3 size={14} />
              Performance Attribution
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>BY AGENT</span>
          </div>

          {attrLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Computing attribution...</div>
          ) : attribution ? (
            <div>
              {/* Total P&L */}
              <div style={{ textAlign: 'center', marginBottom: '1rem' }}>
                <div style={{
                  fontSize: '1.5rem',
                  fontWeight: 900,
                  fontFamily: 'var(--mono)',
                  color: ((attribution as any).total_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)',
                }}>
                  {((attribution as any).total_pnl ?? 0) >= 0 ? '+' : ''}{((attribution as any).total_pnl ?? 0).toFixed(2)}
                </div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>Total Fund P&L</div>
              </div>

              {/* Agent contributions */}
              {Object.keys((attribution as any).agent_contributions ?? {}).length > 0 ? (
                <div>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>Agent Contributions</div>
                  {Object.entries((attribution as any).agent_contributions as Record<string, number>)
                    .sort(([, a], [, b]) => b - a)
                    .map(([agentId, pnl]: [string, number]) => (
                      <div key={agentId} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.35rem' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '.4rem' }}>
                          {pnl >= 0 ? <ChevronUp size={12} style={{ color: 'var(--green)' }} /> : <ChevronDown size={12} style={{ color: 'var(--red)' }} />}
                          <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', color: 'var(--text-secondary)' }}>{agentId.substring(0, 12)}...</span>
                        </div>
                        <span style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                        </span>
                      </div>
                    ))
                  }
                </div>
              ) : (
                <div style={{ color: 'var(--text-dim)', fontSize: '.7rem', textAlign: 'center', padding: '1rem', fontFamily: 'var(--mono)' }}>No agent data yet</div>
              )}

              {/* Strategy breakdown */}
              {Object.keys((attribution as any).strategy_contributions ?? {}).length > 0 && (
                <div style={{ marginTop: '.75rem' }}>
                  <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '.4rem' }}>By Strategy</div>
                  {Object.entries((attribution as any).strategy_contributions as Record<string, number>).map(([strat, pnl]: [string, number]) => (
                    <div key={strat} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.3rem' }}>
                      <span style={{ fontSize: '.7rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', textTransform: 'capitalize' }}>{strat}</span>
                      <span style={{ fontSize: '.7rem', fontFamily: 'var(--mono)', color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No attribution data</div>
          )}
        </div>

        {/* === Capital Allocation === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <img src={getTeamMember('portfolio_manager').avatar} alt={getTeamMember('portfolio_manager').name} style={{ width: '36px', height: '36px', borderRadius: '50%', objectFit: 'cover' }} />
              <div style={{ marginLeft: '.75rem' }}>
                <div style={{ fontSize: '.9rem', fontWeight: 700 }}>{getTeamMember('portfolio_manager').name}</div>
                <div style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginTop: '.125rem' }}>{getTeamMember('portfolio_manager').title}</div>
              </div>
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>ALLOCATION</span>
          </div>

          {allocLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Calculating allocation...</div>
          ) : traderAllocation?.traders?.length > 0 ? (
            <div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.75rem' }}>
                {(traderAllocation.traders as any[]).map((t: any) => (
                  <div key={t.id}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.35rem' }}>
                      <span style={{ fontSize: '.75rem', fontFamily: 'var(--sans)', color: 'var(--text)', fontWeight: 500 }}>
                        {t.avatar} {t.name}
                        <span style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)', marginLeft: '.35rem' }}>{t.llm_model}</span>
                      </span>
                      <span style={{ fontSize: '.8rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--accent)', flexShrink: 0, marginLeft: '.5rem' }}>{(t.allocation_pct as number).toFixed(1)}%</span>
                    </div>
                    <div style={{ height: '14px', background: 'var(--bg-hover)', borderRadius: '4px', overflow: 'hidden', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.2)' }}>
                      <div style={{ height: '100%', width: `${Math.min(t.allocation_pct, 100)}%`, background: 'linear-gradient(90deg, var(--accent), var(--accent-bright))', borderRadius: '4px', transition: 'width 0.3s ease' }} />
                    </div>
                  </div>
                ))}
              </div>

              {traderAllocation.reasoning && (
                <div style={{ marginTop: '.75rem', fontSize: '.68rem', color: 'var(--text-secondary)', lineHeight: 1.5, padding: '.5rem', background: 'var(--bg-hover)', borderRadius: '4px', borderLeft: '2px solid var(--accent)' }}>
                  {traderAllocation.reasoning}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1rem' }}>No allocation data</div>
          )}
        </div>

        {/* === Strategy Leaderboard (from CIO) === */}
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">
              <Users size={14} />
              Strategy Leaderboard
            </div>
            <span style={{ fontSize: '.65rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>RANKED BY P&L</span>
          </div>

          {cioLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '1.5rem', color: 'var(--text-dim)' }}>Ranking strategies...</div>
          ) : leaderboardEntries.length > 0 ? (
            <>
              <div>
                {leaderboardPager.pageItems.map((entry: any, i: number) => {
                  const rank = (leaderboardPager.page - 1) * 8 + i;
                  return (
                <div key={entry.agent_id} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '.45rem .6rem',
                  background: rank === 0 ? 'rgba(0, 194, 255, .05)' : 'var(--bg-hover)',
                  borderRadius: '5px',
                  marginBottom: '.35rem',
                  borderLeft: rank === 0 ? '2px solid var(--accent)' : '2px solid transparent',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
                    <span style={{ fontSize: '.65rem', fontFamily: 'var(--mono)', color: rank === 0 ? 'var(--accent)' : 'var(--text-dim)', minWidth: '14px' }}>#{rank + 1}</span>
                    <div>
                      <div style={{ fontSize: '.75rem', fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--text)' }}>{entry.agent_name && !entry.agent_name.includes('-') ? entry.agent_name : (entry.name ?? entry.agent_name)}</div>
                      <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{(entry.win_rate * 100).toFixed(0)}% WR · {entry.total_runs} runs</div>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '.8rem', fontFamily: 'var(--mono)', fontWeight: 700, color: entry.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {entry.total_pnl >= 0 ? '+' : ''}{entry.total_pnl.toFixed(2)}
                    </div>
                    <div style={{ fontSize: '.6rem', color: 'var(--text-dim)', fontFamily: 'var(--mono)' }}>{entry.contribution_pct.toFixed(1)}% of fund</div>
                  </div>
                </div>
                  );
                })}
              </div>
              <Paginator page={leaderboardPager.page} totalPages={leaderboardPager.totalPages} total={leaderboardPager.total} pageSize={8} onPage={leaderboardPager.setPage} label="strategies" />
            </>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1.5rem' }}>
              <Users size={28} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
              No strategy data yet. Strategies need to run first.
            </div>
          )}
        </div>

      </div>

      {/* Strategy Actions — FM + TA Cooperation Log */}
      <div className="card" style={{ marginTop: '1rem' }}>
        <div className="card-header">
          <h3 className="card-title"><GitBranch size={16} /> Strategy Actions</h3>
          <span style={{ fontSize: '.65rem', color: 'var(--text-dim)' }}>Fund Manager ↔ Technical Analyst Cooperation</span>
        </div>
        {strategyActionsList.length > 0 ? (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem', padding: '.5rem' }}>
              {actionsPager.pageItems.map((action: any) => {
                const actionColors: Record<string, string> = {
                  create_agent: '#22c55e',
                  disable_agent: '#ef4444',
                  enable_agent: '#3b82f6',
                  adjust_params: '#f59e0b',
                };
                const actionIcons: Record<string, string> = {
                  create_agent: '➕',
                  disable_agent: '⛔',
                  enable_agent: '✅',
                  adjust_params: '⚙️',
                };
                return (
                  <div key={action.id} style={{
                    padding: '.6rem .8rem',
                    borderRadius: '8px',
                    background: 'var(--surface)',
                    borderLeft: `3px solid ${actionColors[action.action] || 'var(--border)'}`,
                    fontSize: '.75rem',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.3rem' }}>
                      <span style={{ fontWeight: 600, color: actionColors[action.action] || 'var(--text)' }}>
                        {actionIcons[action.action] || '📋'} {action.action?.replace('_', ' ')}
                      </span>
                      <span style={{ color: 'var(--text-dim)', fontSize: '.65rem' }}>
                        {action.created_at ? new Date(action.created_at).toLocaleString() : ''}
                      </span>
                    </div>
                    <div style={{ color: 'var(--text)', marginBottom: '.2rem' }}>
                      <strong>{action.target_agent_name || 'New Agent'}</strong>
                      {action.strategy_type && <span style={{ marginLeft: '.4rem', color: 'var(--text-dim)' }}>({action.strategy_type})</span>}
                    </div>
                    <div style={{ color: 'var(--text-dim)', fontSize: '.7rem', lineHeight: 1.4 }}>
                      {action.rationale?.slice(0, 150)}{action.rationale?.length > 150 ? '…' : ''}
                    </div>
                    <div style={{ display: 'flex', gap: '.6rem', marginTop: '.3rem', fontSize: '.65rem', color: 'var(--text-dim)' }}>
                      {action.confluence_score != null && <span>Confluence: {(action.confluence_score * 100).toFixed(0)}%</span>}
                      {action.backtest_net_pnl != null && <span>Backtest PnL: ${action.backtest_net_pnl.toFixed(2)}</span>}
                      <span style={{ color: action.executed ? 'var(--green)' : 'var(--red)' }}>
                        {action.executed ? '✓ Executed' : '✗ Not executed'}
                      </span>
                      <span>By: {action.initiated_by}</span>
                    </div>
                  </div>
                );
              })}
            </div>
            <Paginator page={actionsPager.page} totalPages={actionsPager.totalPages} total={actionsPager.total} pageSize={8} onPage={actionsPager.setPage} label="actions" />
          </>
        ) : (
          <div style={{ color: 'var(--text-dim)', fontSize: '.75rem', textAlign: 'center', padding: '1.5rem' }}>
            <GitBranch size={28} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
            No strategy actions yet. Actions appear when the fund manager and technical analyst cooperate on strategy changes.
          </div>
        )}
      </div>

      {/* Daily Report Panel */}
      <DailyReportPanel />

      {/* Trade Retrospective Panel */}
      {retroData && retroData.trade_count > 0 && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '.75rem', border: '1px solid var(--border)', padding: '1.25rem' }}>
          <h3 style={{ margin: '0 0 1rem', display: 'flex', alignItems: 'center', gap: '.5rem' }}>
            <BarChart3 size={18} /> Trade Retrospective
            <span style={{ fontSize: '.7rem', color: 'var(--text-dim)', fontWeight: 400 }}>
              {retroData.trade_count} trades analysed
            </span>
          </h3>

          {retroData.summary && (
            <p style={{ fontSize: '.8rem', color: 'var(--text-secondary)', margin: '0 0 1rem', lineHeight: 1.5 }}>
              {retroData.summary}
            </p>
          )}

          {/* Per-agent insights */}
          {retroData.agent_insights && Object.keys(retroData.agent_insights).length > 0 && (
            <div style={{ display: 'grid', gap: '.75rem' }}>
              {Object.entries(retroData.agent_insights).map(([agentId, insight]: [string, any]) => (
                <div
                  key={agentId}
                  style={{
                    background: 'var(--bg-primary)',
                    borderRadius: '.5rem',
                    padding: '.75rem 1rem',
                    border: '1px solid var(--border)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '.5rem' }}>
                    <span style={{ fontWeight: 600, fontSize: '.8rem' }}>{insight.agent_name}</span>
                    <span style={{
                      fontSize: '.7rem',
                      color: insight.win_rate >= 0.5 ? '#4ade80' : '#f87171',
                      fontWeight: 600,
                    }}>
                      {(insight.win_rate * 100).toFixed(0)}% WR ({insight.total_trades} trades)
                    </span>
                  </div>

                  {insight.strengths?.length > 0 && (
                    <div style={{ fontSize: '.75rem', color: '#4ade80', marginBottom: '.25rem' }}>
                      ✅ {insight.strengths.join(' • ')}
                    </div>
                  )}
                  {insight.weaknesses?.length > 0 && (
                    <div style={{ fontSize: '.75rem', color: '#fbbf24', marginBottom: '.25rem' }}>
                      ⚠️ {insight.weaknesses.join(' • ')}
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: '1rem', fontSize: '.7rem', color: 'var(--text-dim)', marginTop: '.5rem' }}>
                    {insight.avg_exit_efficiency !== null && (
                      <span>Exit Efficiency: {(insight.avg_exit_efficiency * 100).toFixed(0)}%</span>
                    )}
                    {insight.best_pattern && <span>Best: {insight.best_pattern}</span>}
                    {insight.worst_pattern && <span>Worst: {insight.worst_pattern}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Parameter adjustments */}
          {retroData.parameter_adjustments?.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <h4 style={{ fontSize: '.75rem', margin: '0 0 .5rem', color: 'var(--text-secondary)' }}>
                🔧 Recommended Adjustments
              </h4>
              {retroData.parameter_adjustments.map((adj: any, i: number) => (
                <div
                  key={i}
                  style={{
                    fontSize: '.75rem',
                    background: 'rgba(251, 191, 36, .08)',
                    border: '1px solid rgba(251, 191, 36, .2)',
                    borderRadius: '.4rem',
                    padding: '.5rem .75rem',
                    marginBottom: '.4rem',
                  }}
                >
                  <strong>{adj.agent_name}</strong>: {adj.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
