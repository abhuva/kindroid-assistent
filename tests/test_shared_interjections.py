"""Offline regression cases for app messages interleaved with shared generations."""
import pytest

from kindroid_relay.models import ChatMessage
from kindroid_relay.shared import SharedConversation
from kindroid_relay.state import RelayState
from kindroid_relay.turns import ReconciliationError
from test_relay import settings
from test_room_encounters import SharedRemote, SharedAgent, shared_app


def append_remote(remote, text, *, sender='user', name='Marc', role='user'):
    message = ChatMessage('external-' + str(len(remote.messages)), sender, role, name,
                          remote.messages[-1].timestamp + 1, text)
    remote.messages.append(message)
    return message


@pytest.mark.parametrize('manual_response', [False, True])
def test_captured_shared_reply_reconciles_across_manual_input(tmp_path, manual_response):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        manual = append_remote(remote, 'I would like to join in.')
        if manual_response:
            append_remote(remote, 'Welcome to our conversation.', sender='h', name='Hannah', role='ai')
        shared.step()
        anchor = shared.active()['anchor_id']
        shared.relay.synchronize_context()
        assert not state.is_paused()
        assert state.turns.message(anchor).message == 'Reply 2'
        assert state.connection.execute('SELECT status FROM generation_turns WHERE id=?', (anchor,)).fetchone()[0] == 'reconciled'
        assert state.connection.execute('SELECT 1 FROM messages WHERE kindroid_id=?', (manual.id,)).fetchone()
        assert len(remote.calls) == 2
        shared.relay.synchronize_context()
        assert len(remote.calls) == 2


def test_empty_shared_reply_recovers_across_manual_input_once(tmp_path):
    remote, agent = SharedRemote(empty=True), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'Another thought before you answer.')
        shared.step()
        assert not state.is_paused()
        assert not state.turns.has_uncertain()
        assert shared.active()['message_count'] == 1
        assert len(remote.calls) == 1


def test_unknown_reply_with_two_possible_answers_stays_blocked(tmp_path):
    remote, agent = SharedRemote(empty=True), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'I have a question.')
        append_remote(remote, 'An independently generated answer.', sender='h', name='Hannah', role='ai')
        with pytest.raises(ReconciliationError):
            shared.step()
        assert state.is_paused() and state.turns.has_uncertain()
        assert len(remote.calls) == 1
        state.resume()
        with pytest.raises(ReconciliationError):
            shared.step()
        assert len(remote.calls) == 1


def test_empty_reply_does_not_confuse_another_companions_app_response(tmp_path):
    remote, agent = SharedRemote(empty=True), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'Hello both.')
        append_remote(remote, 'Hello Marc.', sender='m', name='Miko', role='ai')
        shared.step()  # Only the requested Hannah response can match.
        assert not state.is_paused() and not state.turns.has_uncertain()
        assert state.turns.message(shared.active()['anchor_id']).message == 'Reply 1'
        assert len(remote.calls) == 1


def test_identical_external_answer_cannot_steal_captured_reply_identity(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'I have a question.')
        append_remote(remote, 'Reply 1', sender='h', name='Hannah', role='ai')
        shared.step()
        with pytest.raises(ReconciliationError):
            shared.relay.synchronize_context()
        assert state.is_paused()
        assert state.connection.execute("SELECT COUNT(*) FROM generation_turns WHERE status='reconciled'").fetchone()[0] == 0
        assert len(remote.calls) == 1


@pytest.mark.parametrize('manual_response', [False, True])
def test_sync_resume_uses_new_tail_without_resetting_session(tmp_path, manual_response):
    remote, agent = SharedRemote(), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        app = shared_app(tmp_path, state, remote, agent)
        shared = SharedConversation(app)
        shared.start('session')
        shared.step()  # Hannah. Miko would be next.
        started = shared.active()['started_at']
        tail = append_remote(remote, 'I am joining your conversation.')
        if manual_response:
            tail = append_remote(remote, 'Welcome!', sender='m', name='Miko', role='ai')
        state.pause('simulate the old reconciliation failure')
        assert app.sync_history('sync')['sync_outcome'] == 'synced'
        assert state.is_paused() and len(remote.calls) == 1
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        state.resume()
        shared.step()  # Adopt the fully synchronized app context without effects.
        assert shared.active()['anchor_id'] == tail.id
        assert shared.active()['started_at'] == started
        assert shared.active()['message_count'] == 1 + int(manual_response)
        assert not remote.posts and len(remote.calls) == 1
        shared.step()
        assert remote.calls[-1] == ('g1', 'h' if manual_response else 'm')
        assert len(remote.calls) == 2 and not state.is_paused()
        assert len(agent.calls) == 0


def test_checkpoint_discards_stale_attention_after_app_input(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        for _ in range(5):
            shared.step()  # Four generations, then reserve the ending check.
        attention_id = shared.active()['attention_id']
        assert attention_id
        tail = append_remote(remote, 'Let me add something to that.')
        shared.step()  # Existing attention sync discovers the interjection.
        assert shared.active()['anchor_id'] == tail.id
        assert not agent.calls and not remote.posts
        assert shared.active()['attention_id'] is None
        assert state.connection.execute('SELECT status FROM shared_attention WHERE id=?', (attention_id,)).fetchone()[0] == 'superseded'
        shared.step()
        assert len(remote.calls) == 5 and not state.is_paused()


def test_old_history_snapshot_cannot_rewind_new_direct_reply(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.relay.synchronize_context()
        shared.step()  # Consume snapshot and generate Miko's newer direct reply.
        newer = shared.active()['anchor_id']
        shared.step()
        assert shared.active()['anchor_id'] != newer
        assert [ai for _, ai in remote.calls] == ['h', 'm', 'h']


def test_external_companion_replies_count_once_towards_session_limit(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'A thought.')
        append_remote(remote, 'An app-driven reply.', sender='m', name='Miko', role='ai')
        shared.step()
        shared.relay.synchronize_context()
        shared.step()
        assert shared.active()['message_count'] == 2
        shared.relay.synchronize_context()
        shared.step()
        assert shared.active()['message_count'] == 3  # Only the newly generated turn.
        assert len(remote.calls) == 2


def test_old_uncertain_generation_recovers_after_restart_and_manual_input(tmp_path):
    remote, agent = SharedRemote(fail=True), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        append_remote(remote, 'I interrupted during your turn.')
        with pytest.raises(ReconciliationError):
            shared.step()
        assert state.is_paused()
    with RelayState(path) as state:
        app = shared_app(tmp_path, state, remote, agent)
        assert app.sync_history('recover')['sync_outcome'] == 'synced'
        assert state.is_paused()
        state.resume()
        shared = SharedConversation(app)
        shared.step()
        assert not state.turns.has_uncertain()
        assert shared.active()['message_count'] == 1
        assert len(remote.calls) == 1


@pytest.mark.parametrize('delivery', ['pending', 'sent', 'uncertain'])
def test_new_context_preserves_delivered_effect_and_cancels_only_unsent_answer(tmp_path, delivery):
    import json
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        source_id = shared.active()['anchor_id']
        decision = {'reply': 'The old answer.', 'end_session': False, 'await_reply': False, 'action': None}
        if delivery == 'sent':
            outbox = shared.relay._send_user_message(decision['reply'], source_message_id=source_id, request_kindroid_reply=False)
        else:
            outbox = state.create_outbox('[CODEX]: The old answer.', source_message_id=source_id, request_kindroid_reply=False)
            if delivery == 'uncertain':
                state.set_outbox_status(outbox, delivery)
        with state.connection:
            state.connection.execute("INSERT INTO shared_attention VALUES('attention','session',?,'decided',?,?)", (source_id, json.dumps(decision), outbox))
            state.connection.execute("UPDATE shared_sessions SET attention_id='attention' WHERE id='session'")
        tail = append_remote(remote, 'Change of subject.')
        shared.relay.synchronize_context()
        posts_before = len(remote.posts)
        if delivery == 'uncertain':
            with pytest.raises(ReconciliationError, match='uncertain outbound'):
                shared.step()
            assert shared.active()['attention_id'] == 'attention'
        else:
            shared.step()
            assert shared.active()['anchor_id'] == tail.id
            assert shared.active()['attention_id'] is None
        assert len(remote.posts) == posts_before and not agent.calls
        status = state.connection.execute('SELECT status FROM outbox WHERE id=?', (outbox,)).fetchone()[0]
        assert status == ('cancelled' if delivery == 'pending' else delivery)


def test_shared_mapping_does_not_cross_a_later_relay_post(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        # Captured reply is missing in remote history, but an identical later
        # reply belongs after a new Codex post. It cannot repair the old turn.
        remote.messages.pop()
        shared.relay._send_user_message('A different exchange.', request_kindroid_reply=False)
        append_remote(remote, 'Reply 1', sender='h', name='Hannah', role='ai')
        result = shared.app.sync_history('sync')
        assert result['sync_outcome'] == 'history_pending'
        assert state.connection.execute("SELECT COUNT(*) FROM generation_turns WHERE status='reconciled'").fetchone()[0] == 0
        assert len(remote.calls) == 1


@pytest.mark.parametrize('specific_reason', [None, 'Kindroid history reconciliation is unresolved; inspect conversation'])
def test_room_failure_preserves_specific_pause_reason(monkeypatch, tmp_path, specific_reason):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from kindroid_relay.room_worker import RoomWorker
    from kindroid_relay.worker import RelayWorker

    config = settings(tmp_path)
    with RelayState(config.state_path):
        pass
    worker = RoomWorker([config])

    def application(self, state):
        def run():
            if specific_reason:
                state.pause(specific_reason)
            worker.stop()
            raise ReconciliationError('Untrusted exception text must not become the pause reason')
        return nullcontext(SimpleNamespace(state=state, settings=self.settings,
            relay=SimpleNamespace(settings=self.settings), run_once=run))

    monkeypatch.setattr(RelayWorker, '_application', application)
    worker.run()
    with RelayState(config.state_path) as state:
        assert state.is_paused()
        assert state.get_meta('pause_reason') == (specific_reason or
            'Room work failed; inspect history, delivery and activity before resuming')


@pytest.mark.parametrize('change,reason', [
    ('text', 'response_text_changed'),
    ('speaker', 'requested_reply_missing'),
    ('duplicate', 'multiple_matching_replies'),
    ('anchor', 'anchor_missing'),
    ('reply', 'reply_missing'),
])
def test_sync_reports_specific_safe_blocker_without_importing_or_retrying(tmp_path, change, reason):
    from dataclasses import replace
    from kindroid_relay.command_queue import CommandType, CommandStatus
    from kindroid_relay.worker import RelayWorker

    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        app = shared_app(tmp_path, state, remote, agent)
        shared = SharedConversation(app)
        shared.start('session')
        shared.step()
        if change == 'text':
            remote.messages[-1] = replace(remote.messages[-1], message='PRIVATE_CHANGED_TEXT')
        elif change == 'speaker':
            remote.messages[-1] = replace(remote.messages[-1], sender='m', display_name='Miko')
        elif change == 'duplicate':
            append_remote(remote, 'Reply 1', sender='h', name='Hannah', role='ai')
        elif change == 'anchor':
            remote.messages.pop(0)
        else:
            remote.messages.pop()
        state.pause('fixture')
        command, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='diagnose')
        RelayWorker(app.settings)._run_command(state, app, state.commands.claim_next())
        result = state.commands.get(command.id)
        assert result.status == CommandStatus.COMPLETED  # Execution ended, not a successful sync.
        assert result.result['sync_outcome'] in {'history_pending', 'reconciliation_unresolved'}
        assert result.result['reason_code'] == reason
        assert result.result['messages_added'] == 0 and state.is_paused()
        assert 'PRIVATE_CHANGED_TEXT' not in repr(result) + repr(state.activity.after())
        event = [e for e in state.activity.after() if e.kind == 'command_completed'][-1]
        assert event.severity.value == 'warning' and 'sync blocked' in event.summary
        assert len(remote.calls) == 1 and not remote.posts and not agent.calls


def test_reconciliation_diagnostic_never_publishes_exception_or_unknown_code():
    from kindroid_relay.turns import reconciliation_diagnostic
    exc = ReconciliationError('PRIVATE_EXCEPTION', reason='PRIVATE_REASON')
    assert reconciliation_diagnostic(exc) == {
        'reason_code': 'unresolved',
        'reason_detail': 'History could not be safely associated with local effects.',
    }
