"""Startup catch-up is a one-shot read, never passive monitoring or a reply."""
from dataclasses import replace
import time

import pytest

from kindroid_relay.kindroid_api import KindroidAPIError, MessagePage
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState
from test_no_monitoring import Agent, config_for, run_cycles
from test_relay import FakeKindroid, settings
from test_shared_recovery import interrupted


@pytest.mark.parametrize('multi', [False, True])
@pytest.mark.parametrize('case', ['paused', 'cooldown', 'quiet', '429', 'http_error', 'unexpected', 'pagination'])
def test_startup_guards_failures_and_pagination(tmp_path, monkeypatch, multi, case):
    config = config_for(tmp_path)
    remote, agent = FakeKindroid(), Agent()
    now = int(time.time())
    with RelayState(config.state_path) as state:
        if case == 'paused':
            state.pause('Operator fixture pause')
        if case == 'cooldown':
            state.set_meta('history_cooldown_until', now + 1200)
    if case == 'quiet':
        monkeypatch.setattr('kindroid_relay.quiet_hours.QuietHours.blocked', lambda self, *a: time.time() < now + 1200)
    if case in {'429', 'http_error', 'unexpected'}:
        def failing(*a, **kw):
            remote.history_requests += 1
            if case == 'unexpected':
                raise RuntimeError('PRIVATE_STARTUP_SENTINEL')
            raise KindroidAPIError('history', 429 if case == '429' else 503,
                                   'PRIVATE_STARTUP_SENTINEL', retry_after_seconds=60)
        remote.get_messages_page = failing
    if case == 'pagination':
        def pages(*a, **kw):
            remote.history_requests += 1
            if remote.history_requests == 1:
                assert kw['start_after_timestamp'] is None
                return MessagePage(remote.messages, True, 1)
            assert kw['start_after_timestamp'] == 1
            return MessagePage([ChatMessage('page2', 'ai1', 'ai', 'Hannah', 2, 'More history')], False, 2)
        remote.get_messages_page = pages
    def waiting(index):
        if case in {'quiet', 'cooldown'} and index <= 2:
            assert remote.history_requests == 0
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent},
               multi=multi, on_wait=waiting)
    assert remote.history_requests == (2 if case == 'pagination' else 1)
    assert agent.calls == 0 and not remote.sent and not remote.turn_requests
    with RelayState(config.state_path) as state:
        assert state.history_request_count() == remote.history_requests
        events = [e for e in state.activity.after() if e.kind == 'history_sync' and e.details.get('reason') == 'startup']
        assert len(events) == 1 and 'command_id' not in events[0].details
        assert 'PRIVATE_STARTUP_SENTINEL' not in repr(state.activity.after())
        if case == 'paused':
            assert state.is_paused() and state.get_meta('pause_reason') == 'Operator fixture pause'
        if case == '429':
            assert state.is_paused() and state.get_int('history_rate_limit_streak') == 1
        if case not in {'429', 'http_error', 'unexpected'}:
            assert state.get_meta('bootstrapped') == '1'
            assert not state.pending_ai_messages()  # First import does not replay old chat.


def test_shared_room_syncs_at_each_start_without_starting_a_shared_session(tmp_path, monkeypatch):
    config = replace(config_for(tmp_path), room_mode='shared', participants=(('ai1', 'Hannah'), ('ai2', 'Miko')))
    remote, agent = FakeKindroid(), Agent()
    with RelayState(config.state_path):
        pass  # The host applies migrations before starting its worker.
    for launch in range(2):
        run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=True)
        assert remote.history_requests == launch + 1 and not remote.sent and not remote.turn_requests
        with RelayState(config.state_path) as state:
            assert state.connection.execute('SELECT COUNT(*) FROM shared_sessions').fetchone()[0] == 0
    assert agent.calls == 0


def test_startup_of_paused_shared_recovery_does_not_replay_uncertain_generation(tmp_path, monkeypatch):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, agent, _ = interrupted(tmp_path, state)
        config = replace(app.settings, participant_scheduling=True, memory_enabled=False)
        session_id = shared.active()['id']
        turns = [tuple(row) for row in state.connection.execute('SELECT * FROM generation_turns')]
        reads = remote.reads
    run_cycles(monkeypatch, [config], {config.room_key: remote}, {config.room_key: agent}, multi=True)
    assert remote.reads == reads + 1 and len(remote.calls) == 1 and not remote.posts
    with RelayState(config.state_path) as state:
        assert state.is_paused()
        assert [tuple(row) for row in state.connection.execute('SELECT * FROM generation_turns')] == turns
        assert state.connection.execute('SELECT id FROM shared_sessions WHERE status=\'active\'').fetchone()[0] == session_id
