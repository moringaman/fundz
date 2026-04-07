import { all, fork } from 'redux-saga/effects';
import { watchTeamChat } from './sagas/teamChatSaga';

export default function* rootSaga() {
  yield all([
    fork(watchTeamChat),
  ]);
}
