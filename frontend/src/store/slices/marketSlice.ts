import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { Kline, Ticker, Indicators, Signal } from '../types';

interface MarketState {
  selectedSymbol: string;
  klines: Kline[];
  ticker: Ticker | null;
  tickers: Record<string, Ticker>;
  indicators: Indicators | null;
  signal: Signal | null;
}

const initialState: MarketState = {
  selectedSymbol: 'BTCUSDT',
  klines: [],
  ticker: null,
  tickers: {},
  indicators: null,
  signal: null,
};

const marketSlice = createSlice({
  name: 'market',
  initialState,
  reducers: {
    setSelectedSymbol(state, action: PayloadAction<string>) {
      if (state.selectedSymbol !== action.payload) {
        state.selectedSymbol = action.payload;
        // Clear stale data from previous symbol so charts don't show wrong candles
        state.klines = [];
        state.indicators = null;
        state.signal = null;
      }
    },
    setKlines(state, action: PayloadAction<Kline[]>) {
      const incoming = action.payload;
      const current = state.klines;
      // Skip update if data is identical (same length, same last bar time & close)
      // to avoid triggering unnecessary chart redraws.
      if (
        current.length > 0 &&
        incoming.length === current.length &&
        incoming[incoming.length - 1]?.time === current[current.length - 1]?.time &&
        incoming[incoming.length - 1]?.close === current[current.length - 1]?.close
      ) {
        return;
      }
      state.klines = incoming;
    },
    upsertKline(state, action: PayloadAction<Kline>) {
      const kline = action.payload;
      const len = state.klines.length;
      if (!len) {
        state.klines = [kline];
        return;
      }
      const last = state.klines[len - 1];
      if (last.time === kline.time) {
        state.klines[len - 1] = kline;
      } else if (kline.time > last.time) {
        state.klines.push(kline);
      }
    },
    setTicker(state, action: PayloadAction<Ticker | null>) {
      state.ticker = action.payload;
    },
    setTickerForSymbol(state, action: PayloadAction<{ symbol: string; ticker: Ticker }>) {
      state.tickers[action.payload.symbol] = action.payload.ticker;
    },
    setIndicators(state, action: PayloadAction<Indicators | null>) {
      state.indicators = action.payload;
    },
    setSignal(state, action: PayloadAction<Signal | null>) {
      state.signal = action.payload;
    },
  },
});

export const {
  setSelectedSymbol,
  setKlines,
  upsertKline,
  setTicker,
  setTickerForSymbol,
  setIndicators,
  setSignal,
} = marketSlice.actions;

export default marketSlice.reducer;
