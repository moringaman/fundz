import { useRef, useEffect } from 'react';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';

export function MiniChart({ data }: { data: any[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const prevLenRef = useRef<number>(0);

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#5a7394' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { mode: 0 },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      width: containerRef.current.clientWidth,
      height: 100,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#00e676',
      downColor: '#ff3d60',
      borderUpColor: '#00e676',
      borderDownColor: '#ff3d60',
      wickUpColor: '#00e676',
      wickDownColor: '#ff3d60',
    });

    chartRef.current = chart;
    seriesRef.current = series as ISeriesApi<'Candlestick'>;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      prevLenRef.current = 0;
    };
  }, []);

  // Update data without recreating chart
  useEffect(() => {
    if (!seriesRef.current || !data.length) return;

    const candleData = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const prevLen = prevLenRef.current;
    const newLen = data.length;

    if (prevLen > 0 && newLen === prevLen) {
      seriesRef.current.update(candleData[candleData.length - 1]);
    } else {
      seriesRef.current.setData(candleData);
      chartRef.current?.timeScale().fitContent();
    }
    prevLenRef.current = newLen;
  }, [data]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
