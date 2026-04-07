import { takeEvery, delay, put } from 'redux-saga/effects';
import { addToast, dismissToast } from '../slices/teamChatSlice';

const AUTO_DISMISS_MS = 8000;

function* autoDismissToast(action: ReturnType<typeof addToast>) {
  yield delay(AUTO_DISMISS_MS);
  yield put(dismissToast(action.payload.id));
}

export function* watchTeamChat() {
  yield takeEvery(addToast.type, autoDismissToast);
}
