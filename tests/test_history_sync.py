from contextlib import nullcontext
from dataclasses import replace
import threading
import time

import pytest

from kindroid_relay.command_queue import CommandType, CommandStatus, CommandValidationError, validate_payload
from kindroid_relay.kindroid_api import KindroidAPIError, MessagePage
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.turns import ReconciliationError
from kindroid_relay.worker import RelayWorker
from test_initiative import setup_application
from test_relay import settings
from test_web import dashboard, MUTATION_HEADERS
from test_worker import FakeApplication, wait_until, command_status


@pytest.mark.parametrize('paused', [False, True])
def test_sync_reads_new_history_without_reply_or_resume(tmp_path, paused):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        if paused:
            state.pause('Fixture pause')
        remote.messages.append(ChatMessage('new-hannah', 'ai1', 'ai', 'Hannah', int(time.time()), 'New fixture chat'))
        before = remote.history_requests
        command, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='sync')
        RelayWorker(config)._run_command(state, app, state.commands.claim_next())
        result = state.commands.get(command.id)
        assert result.status == CommandStatus.COMPLETED
        assert result.result['sync_outcome'] == 'synced'
        assert result.result['messages_added'] == 1 and result.result['history_requests'] == 1
        assert result.result['paused'] is paused and state.is_paused() is paused
        assert remote.history_requests == before + 1
        assert [m.id for m in state.pending_ai_messages()] == ['new-hannah']
        assert not remote.sent and not remote.turn_requests and agent.check_ins == 0
        assert app.sync_history('second')['messages_added'] == 0
        assert not remote.sent


def test_first_sync_bootstraps_without_old_chat_replay(tmp_path):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        remote.messages.append(ChatMessage('old-hannah', 'ai1', 'ai', 'Hannah', 2, 'Old fixture'))
        state.pause('Fixture')
        result = app.sync_history('initial')
        assert result['sync_outcome'] == 'bootstrapped' and result['messages_added'] == 2
        assert state.get_meta('bootstrapped') == '1' and state.is_paused()
        assert not state.pending_ai_messages() and not remote.sent and not remote.turn_requests


@pytest.mark.parametrize('block', ['cooldown', 'budget', '429', 'http_error', 'pagination_budget'])
def test_sync_limits_and_failures_are_explicit_without_retries(tmp_path, block):
    config = replace(settings(tmp_path), max_history_requests_24h=2)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap()
        state.pause('Fixture')
        if block == 'cooldown':
            state.set_meta('history_cooldown_until', int(time.time()) + 300)
        if block == 'budget':
            state.record_history_request(2)
        if block in {'429', 'http_error'}:
            def failing(*a, **kw):
                raise KindroidAPIError('history', 429 if block == '429' else 503,
                                       'PRIVATE_HTTP_SENTINEL', retry_after_seconds=123)
            remote.get_messages_page = failing
        if block == 'pagination_budget':
            remote.get_messages_page = lambda *a, **kw: MessagePage(
                [ChatMessage('later', 'ai1', 'ai', 'Hannah', int(time.time()), 'Fixture')], True, int(time.time()))
        before = state.history_request_count()
        result = app.sync_history('blocked')
        expected = {'cooldown': 'history_cooldown', 'budget': 'history_budget', '429': 'history_cooldown',
                    'http_error': 'history_error', 'pagination_budget': 'history_budget'}[block]
        assert result['sync_outcome'] == expected
        assert result['history_requests'] == (0 if block in {'cooldown', 'budget'} else 1)
        assert state.history_request_count() - before == result['history_requests']
        assert result['messages_added'] == 0 and state.is_paused()
        assert not remote.sent and not remote.turn_requests
        assert 'PRIVATE_HTTP_SENTINEL' not in repr(result) + repr(state.activity.after())


@pytest.mark.parametrize('visible', [False, True])
def test_sync_reconciles_uncertain_generation_without_repeating_it(tmp_path, visible):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap()
        oid = app.relay._send_user_message('Fixture question')
        original = remote.generate_ai_response
        def lost_response(*args):
            if visible:
                original(*args)
            raise RuntimeError('PRIVATE_GENERATION_SENTINEL')
        remote.generate_ai_response = lost_response
        with pytest.raises(ReconciliationError):
            app.relay._request_ai_turn(outbox_id=oid, allow_user=False)
        before = len(remote.turn_requests)
        result = app.sync_history('recover')
        assert result['sync_outcome'] == ('synced' if visible else 'reconciliation_unresolved')
        assert state.is_paused()  # Even successful reconciliation never resumes.
        assert state.turns.has_uncertain() is not visible
        assert len(remote.turn_requests) == before and len(remote.sent) == 1


def test_sync_does_not_claim_completion_for_missing_local_message(tmp_path):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap()
        app.relay._send_user_message('Fixture not yet visible')
        remote.messages.pop()
        assert app.sync_history('pending')['sync_outcome'] == 'history_pending'
        assert not remote.turn_requests and len(remote.sent) == 1


def test_sync_endpoint_is_protected_queued_and_idempotent(tmp_path):
    config, factory, wake, client = dashboard(tmp_path)
    path = '/api/v1/commands/sync-history'
    body = {'idempotency_key': 'sync-fixture'}
    assert client.post(path, json=body).status_code == 403
    assert client.post(path, json={**body, 'force': True}, headers=MUTATION_HEADERS).status_code == 422
    with pytest.raises(CommandValidationError):
        validate_payload(CommandType.SYNC_HISTORY, {'force': True})
    with factory.connect() as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap(); state.pause('Fixture')
        before = remote.history_requests
        first = client.post(path, json=body, headers=MUTATION_HEADERS)
        assert first.status_code == 202 and wake.is_set()
        assert client.post(path, json=body, headers=MUTATION_HEADERS).json()['id'] == first.json()['id']
        assert client.post(path, json={'idempotency_key': 'different'}, headers=MUTATION_HEADERS).status_code == 409
        client.get('/api/v1/messages')
        assert remote.history_requests == before  # Request handlers never pull history.
        RelayWorker(config)._run_command(state, app, state.commands.claim_next())
        assert remote.history_requests == before + 1
        assert client.get('/api/v1/commands/' + first.json()['id']).json()['result']['sync_outcome'] == 'synced'


def test_unexpected_sync_error_is_sanitized_and_restart_does_not_replay(tmp_path, monkeypatch, caplog):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, _, _ = setup_application(state, config)
        app.bootstrap()
        def failed():
            raise RuntimeError('PRIVATE_FAILURE_SENTINEL')
        monkeypatch.setattr(app.relay, 'synchronize_context', failed)
        command, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='failed')
        RelayWorker(config)._run_command(state, app, state.commands.claim_next())
        result = state.commands.get(command.id)
        assert result.status == CommandStatus.FAILED
        assert 'PRIVATE_FAILURE_SENTINEL' not in result.error + caplog.text + repr(state.activity.after())
        other, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='interrupted')
        state.commands.claim_next()
        RelayWorker(config)._recover(state)
        assert state.commands.get(other.id).status == CommandStatus.INTERRUPTED
        assert not state.is_paused()  # Interrupted reads alone do not require a safety pause.


def test_worker_does_not_force_a_second_poll_after_manual_sync(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False, poll_seconds=300)
    with RelayState(config.state_path) as state:
        command, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='sync')
    calls = []
    class App(FakeApplication):
        def sync_history(self, command_id):
            calls.append('sync')
            return {'sync_outcome': 'synced'}
    worker = RelayWorker(config, application_factory=lambda state: nullcontext(App(state, calls)))
    factory = RelayStateFactory(config.state_path)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        wait_until(lambda: command_status(factory, command.id) == CommandStatus.COMPLETED)
        time.sleep(0.05)  # Let the loop reach its next safe wait, never signal the console.
    finally:
        worker.stop(); thread.join(timeout=2)
    assert not thread.is_alive() and calls == ['sync']
