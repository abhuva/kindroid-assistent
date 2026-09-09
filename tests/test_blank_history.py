from contextlib import nullcontext
from dataclasses import replace

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.kindroid_api import MessagePage
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState
from test_relay import FakeKindroid, settings


def scenario(tmp_path, captured=False):
    config = replace(settings(tmp_path), initiative_enabled=False)
    state = RelayState(config.state_path)
    origin = ChatMessage('origin', 'user', 'user', 'Marc', 1000, 'Earlier fixture')
    state.add_message('g1', origin, processed=True)
    state.set_meta('bootstrapped', True)
    state.set_meta('cursor_timestamp', 1000)
    oid = state.create_outbox('[CODEX]: Fixture invitation')
    state.turns.record_sent(oid, 'g1', 'Codex Test', 1000)
    turn = state.turns.begin('g1', 'ai1', 'Hannah', oid, 1000)
    if captured:
        state.turns.complete(turn, 'Actual fixture answer')
    else:
        state.turns.mark_uncertain(turn)
    state.pause('Fixture pause')
    remote = FakeKindroid()
    remote.messages = [origin,
        ChatMessage('anchor', 'user', 'user', 'Codex Test', 2000, '[CODEX]: Fixture invitation'),
        ChatMessage('answer', 'ai', 'ai', 'Hannah', 3000, 'Actual fixture answer')]
    class ForbiddenAgent:
        def reply_to(self, message):
            raise AssertionError('History sync must not run Codex')
    app = RelayApplication(config, state, remote, lambda: nullcontext(ForbiddenAgent()))
    return state, app, remote, turn, oid


@pytest.mark.parametrize('captured', [False, True])
@pytest.mark.parametrize('text,before', [('', False), ('', True), (' \t\n', False), (' \t\n', True)])
def test_blank_ai_does_not_compete_with_real_answer_or_trigger_a_reply(tmp_path, captured, text, before):
    state, app, remote, turn, oid = scenario(tmp_path, captured)
    with state:
        remote.messages.append(ChatMessage('blank', 'ai', 'ai', 'Hannah', 2500 if before else 4000, text))
        result = app.sync_history('recover')
        assert result['sync_outcome'] == 'synced'
        assert state.is_paused() and not state.turns.has_uncertain()
        assert remote.history_requests == 1 and not remote.turn_requests and not remote.sent
        assert [m.id for m in state.pending_ai_messages()] == [turn]
        answer = state.connection.execute('SELECT * FROM messages WHERE remote_id=?', (turn,)).fetchone()
        assert answer['kindroid_id'] == 'answer' and answer['body'] == 'Actual fixture answer'
        stored_turn = state.turns.for_outbox(oid)
        assert stored_turn['status'] == 'reconciled' and stored_turn['id'] == turn
        blank = state.connection.execute("SELECT * FROM messages WHERE remote_id='blank'").fetchone()
        assert blank['processed'] == 1 and blank['body'] == text and blank['kindroid_id'] == 'blank'
        state.mark_message_processed(turn)
        assert app.sync_history('repeat')['sync_outcome'] == 'synced'
        assert not state.pending_ai_messages()
        assert state.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 4
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('captured', [False, True])
@pytest.mark.parametrize('problem', ['only_blank', 'second_answer', 'same_answer_twice',
                                   'conflicting_blank', 'unknown_role_blank', 'user_boundary', 'wrong_answer_speaker'])
def test_blank_exception_preserves_missing_reply_and_ambiguity_guards(tmp_path, captured, problem):
    state, app, remote, turn, oid = scenario(tmp_path, captured)
    with state:
        blank = ChatMessage('blank', 'ai', 'ai', 'Hannah', 4000, '')
        remote.messages.append(blank)
        if problem == 'only_blank':
            remote.messages = [m for m in remote.messages if m.id != 'answer']
        elif problem in {'second_answer', 'same_answer_twice'}:
            remote.messages.append(replace(remote.messages[2], id='another', timestamp=5000,
                message='Actual fixture answer' if problem == 'same_answer_twice' else 'Different real answer'))
        elif problem == 'conflicting_blank':
            remote.messages[-1] = replace(blank, sender='other-ai', display_name='Other companion')
        elif problem == 'unknown_role_blank':
            remote.messages[-1] = replace(blank, sender_type='system')
        elif problem == 'user_boundary':
            remote.messages.append(ChatMessage('interjection', 'user', 'user', 'Marc', 2500, ''))
        else:
            remote.messages[2] = replace(remote.messages[2], sender='other-ai', display_name='Other companion')
        result = app.sync_history('blocked')
        assert result['sync_outcome'] in {'history_pending', 'reconciliation_unresolved'}
        assert state.is_paused() and not remote.turn_requests and not remote.sent
        assert state.get_int('cursor_timestamp') == 1000
        assert state.turns.for_outbox(oid)['status'] == ('received' if captured else 'uncertain')
        # The complete window must succeed before any aliases are committed.
        assert state.connection.execute('SELECT kindroid_id FROM messages WHERE remote_id=?',
            ('local-user:' + oid,)).fetchone()[0] is None
        assert state.connection.execute("SELECT 1 FROM messages WHERE kindroid_id='blank'").fetchone() is None


def test_later_page_with_real_competing_answer_still_prevents_partial_binding(tmp_path):
    state, app, remote, _, oid = scenario(tmp_path)
    with state:
        remote.messages.extend([
            ChatMessage('blank', 'ai', 'ai', 'Hannah', 4000, ''),
            ChatMessage('other-answer', 'ai', 'ai', 'Hannah', 5000, 'Another real reply')])
        cursors = []
        def history(*args, start_after_timestamp=None, **kwargs):
            cursors.append(start_after_timestamp)
            if len(cursors) == 1:
                return MessagePage(remote.messages[:4], True, 4000)
            return MessagePage(remote.messages[4:], False, 5000)
        remote.get_messages_page = history
        assert app.sync_history('paged')['sync_outcome'] == 'reconciliation_unresolved'
        assert cursors == [999, 4000]
        assert state.turns.for_outbox(oid)['status'] == 'uncertain'
        assert state.get_int('cursor_timestamp') == 1000
        assert not remote.turn_requests and not remote.sent
