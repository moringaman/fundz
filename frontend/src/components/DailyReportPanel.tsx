import { useState } from 'react';
import { FileText, Calendar, RefreshCw } from 'lucide-react';
import { useDailyReport, useDailyReports } from '../hooks/useQueries';

export function DailyReportPanel() {
  const { data: todayReport, isLoading: reportLoading } = useDailyReport();
  const { data: pastReports = [] } = useDailyReports(7);
  const [generating, setGenerating] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const { data: selectedReport } = useDailyReport(selectedDate || undefined);

  const report: any = selectedDate ? selectedReport : todayReport;
  const reports: any[] = Array.isArray(pastReports) ? pastReports : [];
  const hasReport = report && !report.message;

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const { fundApi: fApi } = await import('../lib/api');
      await fApi.generateDailyReport(undefined, true);
      window.location.reload();
    } catch { /* ignore */ }
    finally { setGenerating(false); }
  };

  const pnlColor = (val: number) => val > 0 ? 'var(--green)' : val < 0 ? 'var(--red)' : 'var(--text-secondary)';

  return (
    <div className="daily-report-panel">
      <div className="daily-report-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
          <FileText size={15} style={{ color: 'var(--accent)' }} />
          <span>Daily Report</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
          {/* Past report date pills */}
          {reports.slice(0, 5).map((r: any) => (
            <button
              key={r.report_date}
              type="button"
              onClick={() => setSelectedDate(r.report_date === selectedDate ? null : r.report_date)}
              className="daily-report-date-pill"
              style={{
                background: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                color: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent)' : 'var(--text-secondary)',
                borderColor: (selectedDate || new Date().toISOString().slice(0, 10)) === r.report_date
                  ? 'var(--accent)' : 'var(--border-mid)',
              }}
            >
              {r.report_date?.slice(5)}
            </button>
          ))}
          <button
            type="button"
            className="settings-btn"
            onClick={handleGenerate}
            disabled={generating}
            style={{ fontSize: '.68rem', padding: '.3rem .6rem' }}
          >
            <RefreshCw size={11} /> {generating ? 'Generating…' : 'Generate'}
          </button>
        </div>
      </div>

      {reportLoading && (
        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
          Loading report…
        </div>
      )}

      {!reportLoading && !hasReport && (
        <div style={{ textAlign: 'center', padding: '2.5rem 1rem', color: 'var(--text-dim)', fontSize: '.75rem' }}>
          <Calendar size={28} style={{ opacity: .3, display: 'block', margin: '0 auto .5rem' }} />
          No report available yet. Reports are generated automatically every hour when the scheduler is running.
        </div>
      )}

      {hasReport && (
        <div className="daily-report-body">
          {/* Key Metrics Row */}
          <div className="daily-report-metrics-grid">
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Total P&L</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.total_pnl || 0) }}>
                ${(report.total_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Realized</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.realized_pnl || 0) }}>
                ${(report.realized_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Unrealized</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.unrealized_pnl || 0) }}>
                ${(report.unrealized_pnl || 0).toFixed(2)}
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Daily Return</span>
              <span className="daily-report-metric-value" style={{ color: pnlColor(report.daily_return_pct || 0) }}>
                {(report.daily_return_pct || 0).toFixed(3)}%
              </span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Trades Opened</span>
              <span className="daily-report-metric-value">{report.trades_opened || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Trades Closed</span>
              <span className="daily-report-metric-value">{report.trades_closed || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Open Positions</span>
              <span className="daily-report-metric-value">{report.open_positions_count || 0}</span>
            </div>
            <div className="daily-report-metric">
              <span className="daily-report-metric-label">Portfolio Value</span>
              <span className="daily-report-metric-value">${(report.portfolio_value || 0).toFixed(0)}</span>
            </div>
          </div>

          {/* Market Conditions + Risk */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.75rem', marginTop: '.75rem' }}>
            {report.market_conditions && Object.keys(report.market_conditions).length > 0 && (
              <div className="daily-report-section">
                <div className="daily-report-section-title">Market Conditions</div>
                <div className="daily-report-kv">
                  {report.market_conditions.regime && <div><span>Regime</span><span>{report.market_conditions.regime}</span></div>}
                  {report.market_conditions.sentiment && <div><span>Sentiment</span><span>{report.market_conditions.sentiment}</span></div>}
                  {report.market_conditions.volatility && <div><span>Volatility</span><span>{report.market_conditions.volatility}</span></div>}
                  {report.market_conditions.analyst_recommendation && <div><span>Recommendation</span><span>{report.market_conditions.analyst_recommendation}</span></div>}
                </div>
              </div>
            )}

            {report.risk_summary && Object.keys(report.risk_summary).length > 0 && (
              <div className="daily-report-section">
                <div className="daily-report-section-title">Risk Summary</div>
                <div className="daily-report-kv">
                  {report.risk_summary.risk_level && (
                    <div>
                      <span>Level</span>
                      <span style={{
                        color: report.risk_summary.risk_level === 'safe' ? 'var(--green)' :
                               report.risk_summary.risk_level === 'danger' ? 'var(--red)' : 'var(--amber)',
                        fontWeight: 700,
                      }}>{report.risk_summary.risk_level.toUpperCase()}</span>
                    </div>
                  )}
                  {report.risk_summary.exposure_pct != null && <div><span>Exposure</span><span>{report.risk_summary.exposure_pct?.toFixed(1)}%</span></div>}
                  {report.risk_summary.concentration_risk && <div><span>Concentration</span><span>{report.risk_summary.concentration_risk}</span></div>}
                </div>
              </div>
            )}
          </div>
          {/* CIO Summary */}
          {report.cio_summary && (
            <div className="daily-report-section" style={{ marginTop: '.75rem' }}>
              <div className="daily-report-section-title">
                CIO Commentary
                {report.cio_sentiment && (
                  <span style={{
                    marginLeft: '.5rem', fontSize: '.6rem', padding: '.1rem .4rem', borderRadius: 4,
                    background: 'var(--accent-dim)', color: 'var(--accent)',
                  }}>{report.cio_sentiment.replace('_', ' ')}</span>
                )}
              </div>
              <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0 }}>
                {report.cio_summary}
              </p>
            </div>
          )}

          {/* Team Discussion Summary */}
          {report.team_discussion_summary && (
            <div className="daily-report-section" style={{ marginTop: '.75rem' }}>
              <div className="daily-report-section-title">
                Team Discussions
                <span style={{
                  marginLeft: '.5rem', fontSize: '.6rem', padding: '.1rem .4rem', borderRadius: 4,
                  background: 'var(--bg-elevated)', color: 'var(--text-dim)',
                }}>{report.team_message_count || 0} messages</span>
              </div>
              <p style={{ fontSize: '.75rem', color: 'var(--text-secondary)', lineHeight: 1.6, margin: 0 }}>
                {report.team_discussion_summary}
              </p>
            </div>
          )}

          {/* Generated timestamp */}
          {report.generated_at && (
            <div style={{ marginTop: '.75rem', fontSize: '.62rem', color: 'var(--text-dim)', textAlign: 'right', fontFamily: 'var(--mono)' }}>
              Generated: {new Date(report.generated_at).toLocaleString()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
