import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { WsStatus } from '../types';

interface UiState {
  sidebarOpen: boolean;
  wsStatus: WsStatus;
}

const initialState: UiState = {
  sidebarOpen: true,
  wsStatus: 'disconnected',
};

const uiSlice = createSlice({
  name: 'ui',
  initialState,
  reducers: {
    setSidebarOpen(state, action: PayloadAction<boolean>) {
      state.sidebarOpen = action.payload;
    },
    setWsStatus(state, action: PayloadAction<WsStatus>) {
      state.wsStatus = action.payload;
    },
  },
});

export const { setSidebarOpen, setWsStatus } = uiSlice.actions;
export default uiSlice.reducer;
