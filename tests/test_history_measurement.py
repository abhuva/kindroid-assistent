"""Local counts advise; only actual provider rejection stops history access."""
from dataclasses import replace
import time

import pytest

from kindroid_relay.command_queue import CommandStatus, CommandType
from kindroid_relay.encounters import Encounters
from kindroid_relay.kindroid_api import KindroidAPIError, MessagePage
from kindroid_relay.models import ChatMessage
from kindroid_relay.relay import HistoryCooldownError
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from test_initiative import setup_application
from test_relay import settings
from test_room_encounters import chosen_settings, source, after
from test_shared_recovery import interrupted


def test_daily_count_resets_exactly_at_utc_midnight_without_a_request(tmp_path, monkeypatch):
    start = 20000 * 86400
    now = [start + 86399]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        for _ in range(3):
            state.record_history_request(1)
        assert state.history_request_count() == 3
        assert state.status()['history_window_reset_at'] == start + 86400
    with RelayState(path) as state:
        assert state.history_request_count() == 3
        now[0] += 1
        assert state.history_measurement() == {'used': 0, 'starts_at': now[0], 'resets_at': now[0] + 86400}
        assert state.connection.execute('SELECT COUNT(*) FROM history_requests').fetchone()[0] == 3
        state.record_history_request(1)
        assert state.history_request_count() == 1
        now[0] += 3 * 86400  # Downtime spanning several days needs no reset job.
        assert state.history_request_count() == 0
        assert not state.is_paused()


@pytest.mark.parametrize('cross_midnight', [False, True])
def test_pagination_exceeds_threshold_and_counts_attempts_across_reset(tmp_path, monkeypatch, cross_midnight):
    now = [20000 * 86400 + 86399]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    config = replace(settings(tmp_path), max_history_requests_24h=1)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap()
        stamp = now[0] + 10
        calls = []
        def pages(*a, **kw):
            calls.append(kw['start_after_timestamp'])
            if len(calls) == 1:
                if cross_midnight:
                    now[0] += 1
                return MessagePage([ChatMessage('p1', 'ai1', 'ai', 'Hannah', stamp, 'First')], True, stamp)
            return MessagePage([ChatMessage('p2', 'ai1', 'ai', 'Hannah', stamp + 1, 'Second')], False, stamp + 1)
        remote.get_messages_page = pages
        result = app.sync_history('pagination')
        assert result['sync_outcome'] == 'synced' and result['history_requests'] == 2
        assert result['messages_added'] == 2 and len(calls) == 2
        assert state.history_request_count() == (1 if cross_midnight else 3)
        assert not state.is_paused() and not remote.sent and not remote.turn_requests


def test_over_threshold_does_not_block_chosen_encounter(tmp_path, monkeypatch):
    config = replace(chosen_settings(tmp_path), max_history_requests_24h=1)
    with RelayState(config.state_path) as state:
        state.set_meta('bootstrapped', 1)
        encounters = Encounters(state, config)
        encounters.choose(source(state), after())
        due = encounters.current()['due_at']
        monkeypatch.setattr(time, 'time', lambda: due)
        for _ in range(3):
            state.record_history_request(1)
        assert state.history_request_count() == 3
        assert encounters.tick()
        assert state.commands.claim_next().command_type == CommandType.CHOSEN_ENCOUNTER


def test_actual_429_fails_command_and_session_without_retry_or_reset_bypass(tmp_path, monkeypatch):
    now = [20000 * 86400 + 86399]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, _ = setup_application(state, config)
        app.bootstrap()
        app.relay._start_activity_session('Fixture session')
        calls = []
        def limited(*a, **kw):
            calls.append(1)
            raise KindroidAPIError('history', 429, 'PRIVATE_RESPONSE', retry_after_seconds=300)
        remote.get_messages_page = limited
        command, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='limited')
        RelayWorker(config)._run_command(state, app, state.commands.claim_next())
        saved = state.commands.get(command.id)
        assert saved.status == CommandStatus.FAILED and state.is_paused()
        assert saved.result['history_requests'] == 1 and len(calls) == 1
        assert any(e.kind == 'session_ended' and e.details['outcome'] == 'failed_history_rate_limit'
                   for e in state.activity.after())
        assert 'PRIVATE_RESPONSE' not in repr(state.activity.after()) + str(saved)
        now[0] += 1
        assert state.history_request_count() == 0
        with pytest.raises(HistoryCooldownError):
            app.relay.fetch_history(None)
        assert len(calls) == 1 and state.is_paused()


def test_shared_429_ends_session_but_preserves_uncertain_generation(tmp_path):
    with RelayState(settings(tmp_path).state_path) as state:
        app, shared, remote, _, payload = interrupted(tmp_path, state)
        evidence = tuple(state.connection.execute('SELECT * FROM generation_turns').fetchone())
        session = dict(shared.active())
        state.resume()  # Fixture models a rejection during an active session.
        def limited(*a, **kw):
            raise KindroidAPIError('history', 429, 'Fixture', retry_after_seconds=60)
        remote.get_messages_page = limited
        with pytest.raises(HistoryCooldownError):
            app.relay.fetch_history(None)
        assert state.is_paused() and shared.active() is None
        ended = state.connection.execute('SELECT * FROM shared_sessions WHERE id=?', (session['id'],)).fetchone()
        assert ended['reason'] == 'history_rate_limit'
        assert ended['pending_turn_id'] == session['pending_turn_id']
        assert tuple(state.connection.execute('SELECT * FROM generation_turns').fetchone()) == evidence
        assert state.turns.has_uncertain() and len(remote.calls) == 1
