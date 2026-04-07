import { configureStore } from '@reduxjs/toolkit';
import createSagaMiddleware from 'redux-saga';
import marketReducer from './slices/marketSlice';
import uiReducer from './slices/uiSlice';
import teamChatReducer from './slices/teamChatSlice';
import rootSaga from './rootSaga';

const sagaMiddleware = createSagaMiddleware();

export const store = configureStore({
  reducer: {
    market: marketReducer,
    ui: uiReducer,
    teamChat: teamChatReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({ thunk: false }).concat(sagaMiddleware),
});

sagaMiddleware.run(rootSaga);

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
