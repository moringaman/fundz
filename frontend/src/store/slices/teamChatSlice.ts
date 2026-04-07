import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { TeamChatMessage } from '../types';

interface TeamChatState {
  toasts: TeamChatMessage[];
}

const initialState: TeamChatState = {
  toasts: [],
};

const teamChatSlice = createSlice({
  name: 'teamChat',
  initialState,
  reducers: {
    addToast(state, action: PayloadAction<TeamChatMessage>) {
      state.toasts = [...state.toasts.slice(-4), action.payload]; // keep last 5
    },
    dismissToast(state, action: PayloadAction<string>) {
      state.toasts = state.toasts.filter((m) => m.id !== action.payload);
    },
  },
});

export const { addToast, dismissToast } = teamChatSlice.actions;
export default teamChatSlice.reducer;
