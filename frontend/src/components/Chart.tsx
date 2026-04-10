import { useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, CandlestickData, Time } from 'lightweight-charts';

interface ChartProps {
  data: Array<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume?: number;
  }>;
  symbol: string;
  timeframe?: string;
  onTimeframeChange?: (tf: string) => void;
}

export function Chart({ data, symbol, timeframe = '1h', onTimeframeChange }: ChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  // Track the length of data last passed to setData so we can decide
  // between a full reload vs a single-bar update.
  const prevLengthRef = useRef<number>(0);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0d1220' },
        textColor: '#8ba3c7',
      },
      grid: {
        vertLines: { color: '#1c2b42' },
        horzLines: { color: '#1c2b42' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#243650' },
      timeScale: {
        borderColor: '#243650',
        timeVisible: true,
        secondsVisible: false,
      },
      width: chartContainerRef.current.clientWidth,
      height: 400,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#00e676',
      downColor: '#ff5370',
      borderUpColor: '#00e676',
      borderDownColor: '#ff5370',
      wickUpColor: '#00e676',
      wickDownColor: '#ff5370',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#00c2ff',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries as ISeriesApi<'Candlestick'>;
    volumeSeriesRef.current = volumeSeries as ISeriesApi<'Histogram'>;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      prevLengthRef.current = 0;
    };
  }, []);

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !data.length) return;

    const candleData: CandlestickData<Time>[] = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const volumeData = data.map((d) => ({
      time: d.time as Time,
      value: d.volume || 0,
      color: d.close >= d.open ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)',
    }));

    const prevLen = prevLengthRef.current;
    const newLen = data.length;

    if (prevLen > 0 && newLen === prevLen) {
      // Same length — only the last bar changed (live tick). Use update() for
      // smooth animation without re-rendering the whole chart.
      const last = candleData[candleData.length - 1];
      const lastVol = volumeData[volumeData.length - 1];
      candleSeriesRef.current.update(last);
      volumeSeriesRef.current.update(lastVol);
    } else if (prevLen > 0 && newLen === prevLen + 1) {
      // One new bar appended — update the previous last bar then add the new one.
      const prev = candleData[candleData.length - 2];
      const last = candleData[candleData.length - 1];
      const prevVol = volumeData[volumeData.length - 2];
      const lastVol = volumeData[volumeData.length - 1];
      candleSeriesRef.current.update(prev);
      candleSeriesRef.current.update(last);
      volumeSeriesRef.current.update(prevVol);
      volumeSeriesRef.current.update(lastVol);
    } else {
      // New dataset or major length change — full reload.
      candleSeriesRef.current.setData(candleData);
      volumeSeriesRef.current.setData(volumeData);
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent();
      }
    }

    prevLengthRef.current = newLen;
  }, [data]);

  return (
    <div className="chart-wrapper">
      <div className="chart-header">
        <span className="chart-symbol">{symbol}</span>
        <div className="timeframe-selector">
          {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
            <button
              key={tf}
              type="button"
              className={`timeframe-btn ${timeframe === tf ? 'active' : ''}`}
              onClick={() => onTimeframeChange?.(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div ref={chartContainerRef} className="chart-container" />
    </div>
  );
}
