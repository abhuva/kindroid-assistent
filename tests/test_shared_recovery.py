"""Recovery is an explicit local decision, never a guessed generation association."""
from dataclasses import replace
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from kindroid_relay.command_queue import CommandType, CommandStatus, validate_payload, CommandValidationError
from kindroid_relay.kindroid_api import MessagePage, KindroidAPIError
from kindroid_relay.shared import SharedConversation
from kindroid_relay.shared_recovery import recover_shared, recovery_status
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.turns import ReconciliationError
from kindroid_relay.worker import RelayWorker
from kindroid_relay.web.app import create_web_app
from test_relay import settings
from test_room_encounters import SharedRemote, SharedAgent, shared_app
from test_shared_interjections import append_remote


class RecoveryRemote(SharedRemote):
    reads = 0

    def get_messages_page(self, *args, **kwargs):
        self.reads += 1
        return super().get_messages_page(*args, **kwargs)


def interrupted(tmp_path, state):
    remote, agent = RecoveryRemote(empty=True), SharedAgent()
    app = shared_app(tmp_path, state, remote, agent)
    shared = SharedConversation(app)
    shared.start('session')
    append_remote(remote, 'I am joining.')
    append_remote(remote, 'Welcome.', sender='h', name='Hannah', role='ai')
    with pytest.raises(ReconciliationError):
        shared.step()
    payload = {'session_id': 'session', 'turn_id': shared.active()['pending_turn_id'], 'confirm': True}
    return app, shared, remote, agent, payload


@pytest.mark.parametrize('ended', [False, True])
def test_recovery_preserves_uncertainty_and_continues_only_in_new_session(tmp_path, ended):
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        if ended:
            shared.end()
        old_turn = tuple(state.connection.execute('SELECT * FROM generation_turns').fetchone())
        before = remote.reads
        assert recovery_status(state, app.settings)['eligible']
        result = recover_shared(app, 'recovery', payload)
        assert result['outcome'] == 'recovered' and result['paused']
        assert remote.reads == before + 1 and len(remote.calls) == 1 and not remote.posts and not agent.calls
        assert tuple(state.connection.execute('SELECT * FROM generation_turns').fetchone()) == old_turn
        assert not state.turns.has_uncertain() and state.is_paused() and shared.active() is None
        assert not state.pending_ai_messages()
        assert state.connection.execute('SELECT COUNT(*) FROM messages WHERE kindroid_id IS NULL').fetchone()[0] == 0
        recovery = state.connection.execute('SELECT * FROM shared_recoveries').fetchone()
        assert recovery['generation_id'] == payload['turn_id'] and recovery['actor'] == 'operator'
        assert recovery['anchor_id'] == remote.messages[-1].id
        assert not recovery_status(state, app.settings)['eligible']
        assert recover_shared(app, 'different-command', payload)['outcome'] == 'already_recovered'
        assert remote.reads == before + 1
    with RelayState(path) as state:
        app = shared_app(tmp_path, state, remote, agent)
        RelayWorker(app.settings)._recover(state)
        assert state.is_paused() and not state.turns.has_uncertain()
        assert app.sync_history('sync')['sync_outcome'] == 'synced'
        state.resume()
        shared = SharedConversation(app)
        assert shared.start('fresh')['outcome'] == 'started'
        remote.empty = False
        shared.step()
        assert remote.calls == [('g1', 'h'), ('g1', 'm')]
        assert not remote.posts and not agent.calls
        shared.relay.synchronize_context()
        assert state.connection.execute("SELECT status FROM generation_turns WHERE id=?", (payload['turn_id'],)).fetchone()[0] == 'uncertain'
        assert not state.turns.has_uncertain()
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('guard', ['unpaused', 'private', 'stale_session', 'stale_turn', 'captured', 'pending_send', 'sending', 'uncertain_send', 'other_turn', 'unconfirmed_post'])
def test_recovery_guards_do_not_fetch_or_clear_state(tmp_path, guard):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        if guard == 'unpaused': state.resume()
        elif guard == 'private': app.settings = replace(app.settings, room_mode='assistant')
        elif guard == 'stale_session': payload['session_id'] = 'other'
        elif guard == 'stale_turn': payload['turn_id'] = 'other'
        elif guard == 'captured':
            with state.connection:
                state.turns._insert_local(payload['turn_id'], 'g1', 'h', 'ai', 'Hannah', 'Captured', 1)
        elif guard in {'pending_send', 'sending', 'uncertain_send'}:
            outbox = state.create_outbox('[CODEX]: pending', request_kindroid_reply=False)
            if guard != 'pending_send': state.set_outbox_status(outbox, 'sending' if guard == 'sending' else 'uncertain')
        elif guard == 'unconfirmed_post':
            oid = state.create_outbox('[CODEX]: Existing unsynchronized post', request_kindroid_reply=False)
            state.turns.record_sent(oid, 'g1', 'Codex', 1)
        else:
            with state.connection:
                state.connection.execute("INSERT INTO generation_turns SELECT 'another',group_id,ai_id,display_name,NULL,anchor_message_id,status,after_timestamp,created_at FROM generation_turns LIMIT 1")
        before = remote.reads
        with pytest.raises(ValueError): recover_shared(app, 'recovery', payload)
        assert remote.reads == before
        assert state.connection.execute('SELECT COUNT(*) FROM shared_recoveries').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['budget', 'cooldown', 'http', 'empty', 'pagination', 'pause_changed', 'changed_text', 'commit', 'audit_commit'])
def test_failed_recovery_never_partially_imports_or_abandons(tmp_path, failure):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        before_messages = [tuple(r) for r in state.connection.execute('SELECT * FROM messages')]
        old = remote.get_messages_page
        if failure == 'budget':
            app.relay.settings = replace(app.settings, max_history_requests_24h=state.history_request_count())
        elif failure == 'cooldown': state.set_meta('history_cooldown_until', int(time.time()) + 300)
        elif failure == 'http':
            def failed(*a, **kw): raise KindroidAPIError('history', 503, 'PRIVATE_EXCEPTION')
            remote.get_messages_page = failed
        elif failure == 'empty': remote.get_messages_page = lambda *a, **kw: MessagePage([], False, None)
        elif failure == 'pagination':
            remote.get_messages_page = lambda *a, **kw: MessagePage(remote.messages, True, 1)
        elif failure == 'pause_changed':
            def changed(*a, **kw):
                state.resume()
                return old(*a, **kw)
            remote.get_messages_page = changed
        elif failure == 'changed_text': remote.messages[0] = replace(remote.messages[0], message='Changed original')
        elif failure == 'commit':
            state.connection.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON shared_recoveries BEGIN SELECT RAISE(ABORT,'fixture'); END")
        elif failure == 'audit_commit':
            state.connection.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON activity_events WHEN NEW.kind='shared_session_recovered' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        if failure in {'pagination', 'pause_changed', 'changed_text', 'commit', 'audit_commit'}:
            with pytest.raises((RuntimeError, ValueError, sqlite3.IntegrityError)):
                recover_shared(app, 'recovery', payload)
        else:
            assert recover_shared(app, 'recovery', payload)['outcome'] == 'recovery_blocked'
        assert [tuple(r) for r in state.connection.execute('SELECT * FROM messages')] == before_messages
        assert state.connection.execute('SELECT COUNT(*) FROM shared_recoveries').fetchone()[0] == 0
        assert state.turns.has_uncertain() and shared.active() is not None
        assert len(remote.calls) == 1 and not remote.posts and not agent.calls


def test_migration_13_preserves_generation_session_and_links(tmp_path):
    from kindroid_relay.migrations import open_database, apply_migrations, MIGRATIONS, migrate_database
    path = tmp_path / 'upgrade.sqlite3'
    db = open_database(path)
    apply_migrations(db, MIGRATIONS[:12])
    with db:
        db.execute("INSERT INTO messages VALUES('anchor','g1','user','user','Marc',1,'Initial',1,1,'anchor',NULL)")
        db.execute("INSERT INTO generation_turns VALUES('turn','g1','h','Hannah',NULL,'anchor','uncertain',1,2)")
        db.execute("INSERT INTO shared_sessions(id,status,started_at,next_turn_at,anchor_id,pending_turn_id) VALUES('session','active',1,1,'anchor','turn')")
    before = [tuple(db.execute('SELECT * FROM ' + table).fetchone()) for table in ('messages', 'generation_turns', 'shared_sessions')]
    db.close()
    assert migrate_database(path) == [13]
    with RelayState(path) as state:
        assert [tuple(state.connection.execute('SELECT * FROM ' + table).fetchone()) for table in ('messages', 'generation_turns', 'shared_sessions')] == before
        assert state.turns.has_uncertain()
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_recovery_endpoint_requires_confirmation_and_worker_owns_read(tmp_path):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        web = create_web_app(app.settings, RelayStateFactory(state.path), threading.Event(), csrf_token='token', testing=True)
        path = '/api/v1/commands/recover-shared'
        with TestClient(web) as client:
            body = {'idempotency_key': 'recover', 'payload': payload}
            assert client.post(path, json=body).status_code == 403
            headers = {'x-relay-csrf': 'token'}
            assert client.post(path, json={**body, 'payload': {**payload, 'confirm': False}}, headers=headers).status_code == 422
            reads = remote.reads
            command = client.post(path, json=body, headers=headers)
            assert command.status_code == 202 and remote.reads == reads
            assert client.post(path, json=body, headers=headers).json()['id'] == command.json()['id']
            assert client.post(path, json={**body, 'idempotency_key': 'other'}, headers=headers).status_code == 409
            RelayWorker(app.settings)._run_command(state, app, state.commands.claim_next())
            saved = state.commands.get(command.json()['id'])
            assert saved.status == CommandStatus.COMPLETED and saved.result['outcome'] == 'recovered'
            assert remote.reads == reads + 1 and state.is_paused()


@pytest.mark.parametrize('payload', [{}, {'session_id': 's', 'turn_id': 't', 'confirm': 1},
    {'session_id': 's', 'turn_id': 't', 'confirm': True, 'force': True},
    {'session_id': 's', 'turn_id': '', 'confirm': True}])
def test_recovery_payload_cannot_omit_or_expand_confirmation(payload):
    with pytest.raises(CommandValidationError): validate_payload(CommandType.RECOVER_SHARED, payload)


@pytest.mark.parametrize('exhausted', [False, True])
def test_recovery_fetches_all_pages_before_adopting_any_history(tmp_path, exhausted):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        requests = []
        def pages(*args, **kwargs):
            requests.append(kwargs['start_after_timestamp'])
            assert state.connection.execute('SELECT COUNT(*) FROM shared_recoveries').fetchone()[0] == 0
            assert state.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 1
            if len(requests) == 1:
                return MessagePage(remote.messages[:2], True, remote.messages[1].timestamp)
            return MessagePage(remote.messages[2:], False, remote.messages[-1].timestamp)
        remote.get_messages_page = pages
        if exhausted:
            app.relay.settings = replace(app.settings, max_history_requests_24h=state.history_request_count() + 1)
        result = recover_shared(app, 'recovery', payload)
        assert requests[0] is None
        if exhausted:
            assert result['outcome'] == 'recovery_blocked' and len(requests) == 1
            assert state.turns.has_uncertain() and shared.active() is not None
        else:
            assert result['outcome'] == 'recovered' and len(requests) == 2
            assert state.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == len(remote.messages)
        assert state.is_paused() and len(remote.calls) == 1


def test_interrupted_command_after_recovery_commit_cannot_replay_or_end_new_session(tmp_path):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, payload = interrupted(tmp_path, state)
        command, _ = state.commands.enqueue(CommandType.RECOVER_SHARED, payload, idempotency_key='recover')
        state.commands.claim_next()
        recover_shared(app, command.id, payload)  # Crash before command completed.
        reads = remote.reads
        RelayWorker(app.settings)._recover(state)
        assert state.commands.get(command.id).status == CommandStatus.INTERRUPTED
        assert state.is_paused() and not state.turns.has_uncertain()
        assert recover_shared(app, 'retry', payload)['outcome'] == 'already_recovered'
        assert remote.reads == reads
        state.resume()
        shared.start('new-session')
        reads = remote.reads
        assert recover_shared(app, 'old-click', payload)['outcome'] == 'already_recovered'
        assert shared.active()['id'] == 'new-session' and not state.is_paused()
        assert remote.reads == reads and len(remote.calls) == 1


def test_recovery_uses_shared_allowance_without_touching_other_room_uncertainty(tmp_path):
    from kindroid_relay.models import ChatMessage
    primary_path = tmp_path / 'primary.sqlite3'
    with RelayState(primary_path) as primary:
        primary.add_message('private', ChatMessage('private-anchor', 'user', 'user', 'Marc', 1, 'Private fixture'), processed=True)
        with primary.connection:
            private_turn = primary.turns.begin_after('private', 'h', 'Hannah', 'private-anchor', 1)
        primary.turns.mark_uncertain(private_turn)
        with RelayState(settings(tmp_path).state_path, account_path=primary_path) as state:
            app, shared, remote, agent, payload = interrupted(tmp_path, state)
            before = primary.history_request_count()
            assert recover_shared(app, 'recovery', payload)['outcome'] == 'recovered'
            assert primary.history_request_count() == before + 1
            assert state.connection.execute('SELECT COUNT(*) FROM history_requests').fetchone()[0] == 0
            assert primary.turns.has_uncertain() and not state.turns.has_uncertain()
            assert primary.connection.execute('SELECT COUNT(*) FROM shared_recoveries').fetchone()[0] == 0
            assert state.is_paused() and len(remote.calls) == 1
