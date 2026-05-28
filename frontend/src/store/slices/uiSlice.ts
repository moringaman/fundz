import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { WsStatus } from '../types';

interface UiState {
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;
  wsStatus: WsStatus;
}

const initialState: UiState = {
  sidebarOpen: true,
  sidebarCollapsed: false,
  wsStatus: 'disconnected',
};

const uiSlice = createSlice({
  name: 'ui',
  initialState,
  reducers: {
    setSidebarOpen(state, action: PayloadAction<boolean>) {
      state.sidebarOpen = action.payload;
    },
    toggleSidebarCollapsed(state) {
      state.sidebarCollapsed = !state.sidebarCollapsed;
    },
    setWsStatus(state, action: PayloadAction<WsStatus>) {
      state.wsStatus = action.payload;
    },
  },
});

export const { setSidebarOpen, toggleSidebarCollapsed, setWsStatus } = uiSlice.actions;
export default uiSlice.reducer;
