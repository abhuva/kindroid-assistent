from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from kindroid_relay.application import RelayApplication
from kindroid_relay.codex_agent import CodexAgent
from kindroid_relay.command_queue import CommandType, CommandStatus
from kindroid_relay.encounters import Encounters
from kindroid_relay.models import AgentReply, InvalidAgentReply
from kindroid_relay.quiet_hours import QuietHours, deferred_commands, validate_quiet_hours
from kindroid_relay.relay import Relay
from kindroid_relay.shared import SharedConversation
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.schedule import DailyScheduler
from kindroid_relay.web.app import create_web_app
from kindroid_relay.worker import RelayWorker
from test_relay import FakeKindroid, settings
from test_room_encounters import chosen_settings, source, after
from test_web import dashboard, MUTATION_HEADERS


def at(hour, minute=0, day=12):
    return int(datetime(2026, 9, day, hour, minute).timestamp())


def rest(service, start='22:00', end='06:00', participant=0):
    service.save({'participant': participant, 'enabled': True, 'start': start, 'end': end})


@pytest.mark.parametrize('hour,minute,blocked', [(21, 59, False), (22, 0, True), (0, 0, True), (5, 59, True), (6, 0, False)])
def test_quiet_boundaries(tmp_path, hour, minute, blocked):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        quiet = QuietHours(state, config)
        rest(quiet)
        assert quiet.blocked(at(hour, minute)) is blocked


@pytest.mark.parametrize('change', [{'start': '24:00'}, {'end': 'xx'}, {'enabled': 1}, {'participant': -1}, {'end': '22:00'}, {'extra': 'value'}])
def test_quiet_rejects_invalid_payload(change):
    with pytest.raises(ValueError):
        validate_quiet_hours({'participant': 0, 'enabled': True, 'start': '22:00', 'end': '06:00', **change})


def test_quiet_is_companion_specific_across_rooms_and_restart(tmp_path):
    config = chosen_settings(tmp_path)
    miko = replace(config, group_id='g2', participants=(('miko', 'Miko'),), state_path=tmp_path / 'miko.db')
    together = replace(config, participants=(('hannah', 'Hannah'), ('miko', 'Miko')), state_path=tmp_path / 'together.db')
    with RelayState(config.state_path) as state, RelayState(miko.state_path, account_path=config.state_path) as other:
        rest(QuietHours(state, config), '22:00', '06:00')
        rest(QuietHours(other, miko), '01:00', '09:00')
        assert not QuietHours(other, miko).blocked(at(23))
        assert not QuietHours(state, config).blocked(at(8))
    with RelayState(together.state_path, account_path=config.state_path) as shared:
        quiet = QuietHours(shared, together)
        assert quiet.blocked(at(23)) and quiet.blocked(at(8))
        assert not quiet.blocked(at(10))
        assert [p['start'] for p in quiet.snapshot()] == ['22:00', '01:00']


def test_retry_six_hours_shifts_to_next_open_and_does_not_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: at(19))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        rest(QuietHours(state, config))
        encounter = Encounters(state, config)
        first = encounter.retry_later()
        assert first['due_at'] == at(6, day=13)
        assert first['actor'] == 'system' and first['kind'] == 'retry'
        assert encounter.retry_later()['id'] == first['id']
    with RelayState(config.state_path) as state:
        assert Encounters(state, config).current()['due_at'] == at(6, day=13)


def test_retry_never_overrides_participant_choice_or_decline(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        encounter = Encounters(state, config)
        encounter.choose(source(state), after())
        first = encounter.current()['id']
        assert encounter.retry_later()['id'] == first
        encounter.override(None)
        assert encounter.retry_later()['kind'] == 'none'
        assert not encounter.tick()


def test_legacy_daily_command_does_not_become_a_new_check_in(tmp_path, monkeypatch):
    from test_schedule import ready
    config, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch, 'check_in')
    with state:
        app.settings = replace(app.settings, participant_scheduling=True)
        app.relay.settings = app.settings
        now[0] = daily.row()['next_due_at']
        assert daily.tick()  # Simulate an old queued command at upgrade.
        result = app.start_scheduled_check_in(state.commands.claim_next().id)
        assert result['scheduled_outcome'] == 'retired'
        assert not result['check_in_sent'] and not remote.sent



def test_private_loop_yields_when_quiet_starts_with_a_pending_reply(tmp_path, monkeypatch):
    now = [at(21, 59)]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        rest(QuietHours(state, config))
        remote = FakeKindroid()
        relay = Relay(config, state, remote, lambda: nullcontext(object()))
        relay.bootstrap()
        state.connection.execute('UPDATE messages SET processed=0')
        state.connection.commit()
        monkeypatch.setattr(relay, 'synchronize_context', lambda: None)
        monkeypatch.setattr(relay, '_maybe_offer_initiative', lambda **kwargs: False)
        calls = []
        def step(*args):
            calls.append(1)
            assert len(calls) == 1, 'Quiet hours must stop the outer pending-message loop'
            now[0] = at(22)
        monkeypatch.setattr(relay, '_handle_chain', step)
        relay.run_once()
        assert len(calls) == 1
        assert state.pending_ai_messages()


@pytest.mark.parametrize('dead', [[1, 2, 3, 4, 5, 6], list(range(24))])
def test_retry_ignores_retired_daily_dead_hours(tmp_path, monkeypatch, dead):
    monkeypatch.setattr(time, 'time', lambda: at(19))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        schedule = DailyScheduler(state, config)
        schedule.save({**schedule.defaults(), 'dead_hours': dead}, 'fixture')
        encounters = Encounters(state, config)
        assert encounters.retry_later()['due_at'] == at(1, day=13)
        monkeypatch.setattr(time, 'time', lambda: at(6, day=13))
        assert encounters.tick()


def test_pre_upgrade_missed_choice_recovers_without_replaying_original(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: at(12))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        encounters = Encounters(state, config)
        encounters.override(at(13))
        with state.connection:
            state.connection.execute("UPDATE encounter_choices SET status='missed'")
        monkeypatch.setattr(time, 'time', lambda: at(16))
        assert encounters.tick()
        assert encounters.current()['actor'] == 'system'
        assert encounters.current()['kind'] == 'missed'
        assert not encounters.tick()


def test_missed_waits_for_open_time_and_queues_once_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: at(20))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        rest(QuietHours(state, config))
        encounters = Encounters(state, config)
        encounters.override(at(21))
        monkeypatch.setattr(time, 'time', lambda: at(23))
        assert not encounters.tick()
        assert encounters.current()['kind'] == 'missed'
        assert not state.commands.recent()
    with RelayState(config.state_path) as state:
        monkeypatch.setattr(time, 'time', lambda: at(6, day=13))
        encounters = Encounters(state, config)
        assert encounters.tick()
        assert not encounters.tick()
        assert len(state.commands.recent()) == 1


@pytest.mark.parametrize('gate', ['paused', 'uncertain', 'history', 'budget', 'disabled'])
def test_missed_recovery_preserves_gates(tmp_path, monkeypatch, gate):
    monkeypatch.setattr(time, 'time', lambda: at(12))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        encounters = Encounters(state, config)
        encounters.override(at(13))
        monkeypatch.setattr(time, 'time', lambda: at(16))
        if gate == 'paused': state.pause('fixture')
        if gate == 'uncertain': monkeypatch.setattr(state.turns, 'has_uncertain', lambda: True)
        if gate == 'history': state.set_meta('history_cooldown_until', at(18))
        if gate == 'budget': monkeypatch.setattr(state, 'initiative_check_count', lambda: 10000)
        if gate == 'disabled': encounters.settings = replace(config, initiative_enabled=False)
        assert not encounters.tick()
        assert not state.commands.recent()


def test_recovery_uses_explicit_opener_and_never_replays_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: at(12))
    config = chosen_settings(tmp_path)
    remote = FakeKindroid()
    remote.get_turn = lambda *args, **kwargs: None
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: pytest.fail('Recovery opener does not need a model'))
        app.bootstrap()
        encounters = Encounters(state, config)
        encounters.override(at(13))
        monkeypatch.setattr(time, 'time', lambda: at(16))
        assert encounters.tick()
        command = state.commands.claim_next()
        result = encounters.run(app, command.id)
        assert result['check_in_sent']
        assert len(remote.sent) == 1
        assert 'missed our planned encounter' in remote.sent[0]
        assert 'now, or choose another time?' in remote.sent[0]
        assert encounters.current()['kind'] == 'retry'
        assert encounters.run(app, command.id)['outcome'] == 'superseded'
        assert len(remote.sent) == 1


def test_quiet_defers_external_commands_but_allows_editing_hours(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        external, _ = state.commands.enqueue(CommandType.START_CONVERSATION, {}, idempotency_key='start')
        local, _ = state.commands.enqueue(CommandType.SET_QUIET_HOURS,
            {'participant': 0, 'enabled': True, 'start': '22:00', 'end': '06:00'}, idempotency_key='quiet')
        assert state.commands.claim_next(exclude=deferred_commands()).id == local.id
        assert state.commands.get(external.id).status == CommandStatus.QUEUED
        assert state.commands.claim_next().id == external.id


def test_quiet_prevents_private_and_shared_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: at(23))
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        rest(QuietHours(state, config))
        remote = FakeKindroid()
        app = RelayApplication(config, state, remote, lambda: pytest.fail('No model during quiet hours'))
        assert app.run_once() == 0
        assert app.start_conversation() == 0
        assert app.start_exploration('fixture')['scheduled_outcome'] == 'retired'
        assert remote.history_requests == 0 and remote.sent == []
        shared_config = replace(config, room_mode='shared', participants=(('hannah', 'Hannah'), ('miko', 'Miko')))
        shared = SharedConversation(RelayApplication(shared_config, state, remote, lambda: None))
        assert not shared.tick() and not shared.step()
        assert shared.start('fixture')['outcome'] == 'quiet_hours'
        assert remote.history_requests == 0


def test_invalid_timing_closes_with_durable_retry_not_clarification_loop(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, FakeKindroid(), lambda: None)
        reply = relay._encounter_reply(source(state), AgentReply('Unsupported promise', False, next_encounter={'kind': 'bad'}))
        assert not reply.request_kindroid_reply
        assert 'Unsupported promise' not in reply.reply
        assert Encounters(state, config).current()['actor'] == 'system'


def test_timing_question_without_choice_closes_instead_of_asking_forever(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        state.set_meta('encounter_asked', 1)
        relay = Relay(config, state, FakeKindroid(), lambda: None)
        reply = relay._encounter_reply(source(state, 'I am not sure.'), AgentReply('What time exactly?', True))
        assert not reply.request_kindroid_reply
        assert '?' not in reply.reply
        assert Encounters(state, config).current()['kind'] == 'retry'


@pytest.mark.parametrize('payload', ['', 'not-json', '[]', '{}', '{"reply":"hello","request_kindroid_reply":"false"}'])
def test_malformed_agent_response_is_typed_and_sanitized(tmp_path, payload):
    with RelayState(tmp_path / 'state.db') as state:
        message = source(state)
        prompt = tmp_path / 'prompt.md'; prompt.write_text('Fixture', encoding='utf-8')
        agent = CodexAgent(state, cwd=tmp_path, prompt_path=prompt, model=None, approved_sandbox='workspace-write')
        agent._thread = SimpleNamespace(id='test', run=lambda *a, **k: SimpleNamespace(final_response=payload))
        with pytest.raises(InvalidAgentReply): agent.reply_to(message)


def test_malformed_reply_schedules_fallback_without_external_send_or_pause(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        message = source(state)
        class Agent:
            def reply_to(self, _): raise InvalidAgentReply('Fixture invalid structure')
        remote = FakeKindroid()
        relay = Relay(config, state, remote, lambda: None)
        relay._context_synced_at = time.monotonic()
        relay._handle_chain(Agent(), message)
        assert not state.is_paused()
        assert not state.pending_ai_messages()
        assert not remote.sent and not remote.generated
        assert Encounters(state, config).current()['kind'] == 'retry'
        records = state.activity.after(limit=100)
        assert records[-1].details['outcome'] == 'invalid_agent_reply'
        assert len({r.details['session_id'] for r in records if 'session_id' in r.details}) == 1


def test_quiet_api_is_validated_queued_and_worker_owned(tmp_path):
    config, factory, wake, client = dashboard(tmp_path, participants=(('hannah', 'Hannah'),))
    body = {'idempotency_key': 'hours', 'payload': {'participant': 0, 'enabled': True, 'start': '22:00', 'end': '06:00'}}
    assert client.post('/api/v1/settings/quiet-hours', json=body).status_code == 403
    response = client.post('/api/v1/settings/quiet-hours', json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 202
    assert client.get('/api/v1/encounters').json()['quiet_hours'][0]['enabled'] is False
    with factory.connect() as state:
        worker = RelayWorker(config)
        app = RelayApplication(config, state, FakeKindroid(), lambda: None)
        worker._run_command(state, app, state.commands.claim_next())
    assert client.get('/api/v1/encounters').json()['quiet_hours'][0]['enabled'] is True
    assert client.post('/api/v1/settings/quiet-hours', json=body, headers=MUTATION_HEADERS).json()['id'] == response.json()['id']


@pytest.mark.parametrize('prefix,key', [('', 'hannah'), ('/rooms/miko', 'miko'), ('/rooms/together', 'together')])
def test_room_buttons_have_exactly_one_active_room(tmp_path, prefix, key):
    config = replace(settings(tmp_path), room_key=key)
    with RelayState(config.state_path): pass
    links = [(name, '/rooms/' + name + '/') for name in ('hannah', 'miko', 'together')]
    app = create_web_app(config, RelayStateFactory(config.state_path), threading.Event(), room_links=links, testing=True)
    html = TestClient(app, root_path=prefix).get('/').text
    assert html.count('class="room-button"') == 3
    assert f'data-room-url="/rooms/{key}/" aria-pressed="true"' in html
    assert html.count('class="room-button" data-room-url=') == 3
    assert html.count('aria-pressed="true"') == 1  # selected room only; ticks retired
