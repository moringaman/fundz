import { useState } from 'react';
import { ArrowUpRight, ArrowDownRight, Minus, ChevronDown } from 'lucide-react';
import { useAppSelector, useAppDispatch } from '../store/hooks';
import { setSelectedSymbol } from '../store/slices/marketSlice';
import { Chart } from '../components/Chart';
import { WsIndicator } from '../components/common/WsIndicator';
import { useTradingPairs } from '../hooks/useQueries';

export function TradingPage({ timeframe, onTimeframeChange }: { timeframe: string; onTimeframeChange: (tf: string) => void }) {
  const selectedSymbol = useAppSelector((s) => s.market.selectedSymbol);
  const dispatch = useAppDispatch();
  const { data: tradingPairs = [] } = useTradingPairs();
  const ticker = useAppSelector((s) => s.market.ticker);
  const klines = useAppSelector((s) => s.market.klines);
  const indicators = useAppSelector((s) => s.market.indicators);
  const signal = useAppSelector((s) => s.market.signal);

  const [orderSide, setOrderSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [showPairSelector, setShowPairSelector] = useState(false);

  const sigAction = signal?.action ?? 'hold';
  const sigConf   = signal?.confidence ?? 0;
  const price     = ticker?.lastPrice;
  const upChange  = (ticker?.priceChangePercent ?? 0) >= 0;

  const getRsiColor = (rsi: number | null) => {
    if (rsi == null) return '';
    if (rsi < 30) return 'positive';
    if (rsi > 70) return 'negative';
    if (rsi > 55) return 'amber';
    return '';
  };

  return (
    <div style={{ padding: '1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '.75rem', height: 'calc(100vh - 48px)' }}>

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap', flexShrink: 0 }}>
        {/* Pair Selector Dropdown */}
        <div style={{ position: 'relative' }}>
          <button
            type="button"
            onClick={() => setShowPairSelector(!showPairSelector)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '.4rem',
              padding: '.5rem .75rem',
              borderRadius: '6px',
              border: '1px solid var(--border)',
              background: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
              fontSize: '.85rem',
              fontFamily: 'var(--mono)',
              fontWeight: 600,
              transition: 'all .2s',
            }}
          >
            <span style={{ minWidth: '70px', textAlign: 'left' }}>{selectedSymbol}</span>
            <ChevronDown size={14} style={{ opacity: 0.6 }} />
          </button>
          {showPairSelector && (
            <div
              style={{
                position: 'absolute',
                top: '100%',
                left: 0,
                marginTop: '.4rem',
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                borderRadius: '6px',
                minWidth: '160px',
                maxHeight: '320px',
                overflowY: 'auto',
                zIndex: 1000,
                boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
              }}
            >
              {tradingPairs.map((pair) => (
                <button
                  key={pair}
                  type="button"
                  onClick={() => {
                    dispatch(setSelectedSymbol(pair));
                    setShowPairSelector(false);
                  }}
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '.5rem .75rem',
                    border: 'none',
                    background: selectedSymbol === pair ? 'var(--accent-dim)' : 'transparent',
                    color: selectedSymbol === pair ? 'var(--accent)' : 'var(--text-secondary)',
                    cursor: 'pointer',
                    fontSize: '.8rem',
                    fontFamily: 'var(--mono)',
                    textAlign: 'left',
                    transition: 'all .15s',
                    borderLeft: selectedSymbol === pair ? '2px solid var(--accent)' : 'none',
                    paddingLeft: selectedSymbol === pair ? '0.65rem' : '.75rem',
                  }}
                  onMouseEnter={(e) => {
                    if (selectedSymbol !== pair) {
                      e.currentTarget.style.background = 'var(--bg-hover)';
                    }
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = selectedSymbol === pair ? 'var(--accent-dim)' : 'transparent';
                  }}
                >
                  {pair}
                </button>
              ))}
              {tradingPairs.length === 0 && (
                <div style={{ padding: '.5rem .75rem', fontSize: '.72rem', color: 'var(--text-dim)', textAlign: 'center' }}>
                  No pairs configured
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: '.75rem' }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '1rem', color: 'var(--accent)', letterSpacing: '.06em' }}>{selectedSymbol}</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '1.4rem', color: 'var(--text-primary)' }}>
            ${price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '—'}
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '.82rem', padding: '.2rem .5rem', borderRadius: '5px', background: upChange ? 'var(--green-dim)' : 'var(--red-dim)', color: upChange ? 'var(--green)' : 'var(--red)' }}>
            {upChange ? '+' : ''}{ticker?.priceChangePercent?.toFixed(2) ?? '0.00'}%
          </span>
        </div>
        <WsIndicator />
      </div>

      {/* Main layout */}
      <div className="trading-layout" style={{ flex: 1, minHeight: 0 }}>

        {/* Chart */}
        <div className="trading-chart-area">
          <div className="chart-wrapper" style={{ flex: 1, minHeight: 0 }}>
            <div className="chart-header">
              <span className="chart-symbol">{selectedSymbol}</span>
              <div className="timeframe-selector">
                {['1m','5m','15m','1h','4h','1d'].map((tf) => (
                  <button key={tf} type="button" className={`timeframe-btn ${timeframe === tf ? 'active' : ''}`} onClick={() => onTimeframeChange(tf)}>{tf}</button>
                ))}
              </div>
            </div>
            <div className="chart-container">
              {klines.length > 0
                ? <Chart data={klines} symbol={selectedSymbol} timeframe={timeframe} onTimeframeChange={onTimeframeChange} />
                : <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-dim)', fontFamily: 'var(--mono)', fontSize: '.8rem' }}>LOADING MARKET DATA...</div>
              }
            </div>
          </div>
        </div>

        {/* Right sidebar */}
        <div className="trading-sidebar">

          {/* Signal */}
          <div className="panel">
            <div className="panel-header">
              <span className="panel-title">AI Signal</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: '.65rem', color: 'var(--text-dim)' }}>{timeframe.toUpperCase()}</span>
            </div>
            <div className="panel-body">
              <div className={`signal-badge ${sigAction}`} style={{ marginBottom: '.6rem' }}>
                <span>
                  {sigAction === 'buy' ? <ArrowUpRight size={24} /> : sigAction === 'sell' ? <ArrowDownRight size={24} /> : <Minus size={20} />}
                </span>
                <div className="signal-meta">
                  <span className="signal-action">{sigAction.toUpperCase()}</span>
                  <span className="signal-conf">{(sigConf * 100).toFixed(0)}% confidence</span>
                </div>
                <div style={{ flex: 1 }}>
                  <div className="confidence-bar">
                    <div className="confidence-fill" style={{ width: `${sigConf * 100}%` }} />
                  </div>
                </div>
              </div>
              {signal?.reasoning && (
                <p style={{ fontSize: '.72rem', color: 'var(--text-secondary)', lineHeight: 1.5, borderTop: '1px solid var(--border)', paddingTop: '.5rem' }}>
                  {signal.reasoning}
                </p>
              )}
            </div>
          </div>

          {/* Indicators */}
          {indicators && (
            <div className="indicators-compact panel">
              <div className="panel-header">
                <span className="panel-title">Indicators</span>
              </div>
              {[
                { label: 'RSI (14)', val: indicators.rsi?.toFixed(1), color: getRsiColor(indicators.rsi) },
                { label: 'MACD', val: indicators.macd?.toFixed(3), color: (indicators.macd ?? 0) > (indicators.macd_signal ?? 0) ? 'positive' : 'negative' },
                { label: 'MACD Sig', val: indicators.macd_signal?.toFixed(3), color: '' },
                { label: 'BB Upper', val: `$${indicators.bb_upper?.toFixed(0)}`, color: '' },
                { label: 'BB Mid', val: `$${indicators.bb_middle?.toFixed(0)}`, color: '' },
                { label: 'BB Lower', val: `$${indicators.bb_lower?.toFixed(0)}`, color: '' },
                { label: 'SMA 20', val: `$${indicators.sma_20?.toFixed(0)}`, color: '' },
                { label: 'SMA 50', val: `$${indicators.sma_50?.toFixed(0)}`, color: '' },
                { label: 'ATR', val: indicators.atr?.toFixed(2), color: 'amber' },
              ].map(({ label, val, color }) => val && (
                <div key={label} className="indicator-row">
                  <span className="indicator-label">{label}</span>
                  <span className={`indicator-value ${color}`}>{val}</span>
                </div>
              ))}
            </div>
          )}

          {/* Order form */}
          <div className="order-form">
            <div className="order-tabs">
              <button type="button" className={`order-tab buy ${orderSide === 'buy' ? 'active' : ''}`} onClick={() => setOrderSide('buy')}>
                BUY
              </button>
              <button type="button" className={`order-tab sell ${orderSide === 'sell' ? 'active' : ''}`} onClick={() => setOrderSide('sell')}>
                SELL
              </button>
            </div>
            <div className="order-form-body">
              <div className="order-price-display">
                <span className="order-price-label">Market Price</span>
                <span className="order-price-val">${price?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? '—'}</span>
              </div>
              <div className="order-field">
                <label className="order-field-label">Quantity</label>
                <input
                  type="number"
                  placeholder="0.000"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  className="order-input"
                />
              </div>
              {quantity && price && (
                <div className="order-price-display">
                  <span className="order-price-label">Total Value</span>
                  <span className="order-price-val">${(parseFloat(quantity) * price).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                </div>
              )}
              <button type="button" className={`execute-btn ${orderSide}`}>
                {orderSide === 'buy' ? '↑' : '↓'} {orderSide.toUpperCase()} {selectedSymbol}
              </button>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
