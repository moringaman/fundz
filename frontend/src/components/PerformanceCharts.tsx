import { useRef, useEffect, useState } from 'react';
import { createChart, ColorType, LineSeries, HistogramSeries, AreaSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { usePerformanceChart } from '../hooks/useQueries';

interface ChartRow {
  date: string;
  cumulative_pnl: number;
  win_rate: number;
  daily_trades: number;
  avg_win: number;
  avg_loss: number;
}

type MetricKey = 'cumulative_pnl' | 'win_rate' | 'daily_trades' | 'avg_win_loss';

const BG     = '#0d1220';
const GRID   = '#1c2b42';
const BORDER = '#243650';
const TEXT   = '#8ba3c7';

const METRICS: { key: MetricKey; label: string; description: string }[] = [
  { key: 'cumulative_pnl',  label: 'Cumulative P&L',  description: 'Running total net P&L across all closed trades' },
  { key: 'win_rate',        label: 'Win Rate %',       description: 'Rolling win rate: % of all trades closed in profit' },
  { key: 'daily_trades',    label: 'Daily Executions', description: 'Number of round-trip trades closed each day' },
  { key: 'avg_win_loss',    label: 'Avg Win / Loss',   description: 'Average winning vs average losing trade (absolute value)' },
];

// Single effect: create chart + load data + return cleanup.
// This avoids the StrictMode race where the data effect doesn't
// re-run after the chart creation cleanup/re-run cycle.
function MetricChart({ rows, metric }: { rows: ChartRow[]; metric: MetricKey }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !rows.length) return;

    // autoSize lets LW-charts own the ResizeObserver internally,
    // eliminating our manual resize loop.
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: BG },
        textColor: TEXT,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: BORDER },
      timeScale: { borderColor: BORDER, timeVisible: true, secondsVisible: false },
    });

    let main: ISeriesApi<'Area'> | ISeriesApi<'Line'> | ISeriesApi<'Histogram'> | null = null;
    let aux: ISeriesApi<'Line'> | null = null;

    if (metric === 'cumulative_pnl') {
      main = chart.addSeries(AreaSeries, {
        lineColor: '#00c2ff',
        topColor: 'rgba(0,194,255,.22)',
        bottomColor: 'rgba(0,194,255,.02)',
        lineWidth: 2,
      });
      main.setData(rows.map(r => ({ time: r.date as Time, value: r.cumulative_pnl })));
    } else if (metric === 'win_rate') {
      main = chart.addSeries(LineSeries, { color: '#ffd060', lineWidth: 2 });
      main.setData(rows.map(r => ({ time: r.date as Time, value: r.win_rate })));
    } else if (metric === 'daily_trades') {
      main = chart.addSeries(HistogramSeries, { color: 'rgba(0,194,255,.7)' });
      main.setData(rows.map(r => ({ time: r.date as Time, value: r.daily_trades })));
    } else {
      // avg_win_loss: two lines
      main = chart.addSeries(LineSeries, { color: '#00e676', lineWidth: 2 });
      aux  = chart.addSeries(LineSeries, { color: '#ff5370', lineWidth: 2 });
      const wins  = rows.filter(r => r.avg_win  > 0).map(r => ({ time: r.date as Time, value: r.avg_win }));
      const losses = rows.filter(r => r.avg_loss < 0).map(r => ({ time: r.date as Time, value: Math.abs(r.avg_loss) }));
      main.setData(wins);
      aux.setData(losses);
    }

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
    };
  }, [rows, metric]); // recreate when data or metric changes

  return <div ref={containerRef} style={{ width: '100%', height: 180 }} />;
}

export function PerformanceCharts() {
  const { data = [], isLoading, isError } = usePerformanceChart();
  const rows = data as ChartRow[];
  const [activeMetric, setActiveMetric] = useState<MetricKey>('cumulative_pnl');

  const latest      = rows[rows.length - 1];
  const totalTrades = rows.reduce((s, r) => s + r.daily_trades, 0);

  function pillValue(key: MetricKey): string {
    if (!latest) return '\u2014';
    if (key === 'cumulative_pnl') {
      const v = latest.cumulative_pnl;
      return `${v >= 0 ? '+' : ''}$${v.toFixed(2)}`;
    }
    if (key === 'win_rate')     return `${latest.win_rate.toFixed(1)}%`;
    if (key === 'daily_trades') return String(totalTrades);
    return `$${latest.avg_win.toFixed(2)} / $${Math.abs(latest.avg_loss).toFixed(2)}`;
  }

  function pillColor(key: MetricKey): string {
    if (!latest) return 'var(--text-primary)';
    if (key === 'cumulative_pnl')
      return latest.cumulative_pnl >= 0 ? 'var(--green)' : 'var(--red)';
    if (key === 'win_rate')
      return latest.win_rate >= 50 ? 'var(--green)' : 'var(--amber)';
    return 'var(--text-primary)';
  }

  const hasData = !isLoading && !isError && rows.length >= 2;

  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column' }}>
      <div className="panel-header">
        <span className="panel-title">Performance Charts</span>
        <span style={{ fontFamily: 'var(--mono)', fontSize: '.7rem', color: 'var(--text-secondary)' }}>
          {rows.length} days &middot; {totalTrades} trades
        </span>
      </div>

      {/* Metric selector pills */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '.5rem',
        padding: '.75rem 1.1rem',
        borderBottom: '1px solid var(--border)',
      }}>
        {METRICS.map(m => {
          const active = activeMetric === m.key;
          return (
            <button
              key={m.key}
              type="button"
              title={m.description}
              onClick={() => setActiveMetric(m.key)}
              style={{
                all: 'unset',
                cursor: 'pointer',
                padding: '.55rem .75rem',
                borderRadius: '7px',
                border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                background: active ? 'var(--accent-dim)' : 'var(--bg-elevated)',
                transition: 'border-color .15s, background .15s',
                boxSizing: 'border-box',
              }}
            >
              <div style={{
                fontSize: '0.68rem',
                color: 'var(--text-secondary)',
                textTransform: 'uppercase',
                letterSpacing: '.06em',
                marginBottom: '.25rem',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {m.label}
              </div>
              <div style={{
                fontFamily: 'var(--mono)',
                fontSize: '.88rem',
                fontWeight: 700,
                color: active ? 'var(--accent)' : pillColor(m.key),
              }}>
                {pillValue(m.key)}
              </div>
            </button>
          );
        })}
      </div>

      {/* Chart area */}
      <div style={{ padding: '.75rem 1rem .5rem' }}>
        {isLoading && (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', fontSize: '.82rem' }}>
            Loading chart data\u2026
          </div>
        )}
        {isError && (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--red)', fontSize: '.82rem' }}>
            Failed to load performance data.
          </div>
        )}
        {!isLoading && !isError && rows.length < 2 && (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', fontSize: '.82rem', fontStyle: 'italic', textAlign: 'center', padding: '0 2rem' }}>
            Not enough trade history yet. Complete more trades to see performance trends.
          </div>
        )}
        {hasData && (
          <>
            {activeMetric === 'avg_win_loss' && (
              <div style={{ display: 'flex', gap: '1rem', marginBottom: '.4rem', fontSize: '0.7rem', fontFamily: 'var(--mono)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '.3rem', color: 'var(--green)' }}>
                  <span style={{ display: 'inline-block', width: 16, height: 2, background: 'var(--green)', borderRadius: 1 }} />
                  Avg Win
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '.3rem', color: 'var(--red)' }}>
                  <span style={{ display: 'inline-block', width: 16, height: 2, background: 'var(--red)', borderRadius: 1 }} />
                  Avg Loss (abs)
                </span>
              </div>
            )}
            <MetricChart rows={rows} metric={activeMetric} />
            <div style={{ marginTop: '.3rem', fontSize: '0.68rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
              {METRICS.find(m => m.key === activeMetric)?.description}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
