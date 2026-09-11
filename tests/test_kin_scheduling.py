"""Current global policy; legacy fixtures elsewhere preserve historical contracts."""
from contextlib import nullcontext
from dataclasses import replace
import json
import time
from types import SimpleNamespace

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.codex_agent import CodexAgent
from kindroid_relay.command_queue import CommandType, RETIRED_SCHEDULING_COMMANDS
from kindroid_relay.config import Settings
from kindroid_relay.encounters import Encounters
from kindroid_relay.models import ChatMessage
from kindroid_relay.retired_exploration import retire_safe_work
from kindroid_relay.rooms import configured_rooms
from kindroid_relay.schedule import DailyScheduler, effective_settings
from kindroid_relay.shared import SharedConversation
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from test_config import write_env
from test_room_encounters import chosen_settings, SharedRemote, SharedAgent, after
from test_web import dashboard, MUTATION_HEADERS


def test_loaded_policy_cannot_be_disabled_by_old_flags(tmp_path):
    env = tmp_path / 'fixture.env'
    write_env(env, 'RELAY_PARTICIPANT_SCHEDULING=false\nRELAY_EXPLORATION_ENABLED=true\n')
    config = Settings.load(env)
    assert config.participant_scheduling and not config.exploration_enabled
    rooms = tmp_path / 'rooms.json'
    rooms.write_text(json.dumps([
        {'key': 'private', 'group_id': config.group_id, 'participant_scheduling': False,
         'participants': [{'id': 'h', 'name': 'Hannah'}]},
        {'key': 'shared', 'group_id': 'shared', 'mode': 'shared', 'participant_scheduling': False,
         'participants': [{'id': 'h', 'name': 'Hannah'}, {'id': 'm', 'name': 'Miko'}]},
    ]))
    assert all(r.participant_scheduling and not r.exploration_enabled
               for r in configured_rooms(replace(config, rooms_file=str(rooms))))


def test_initial_fallback_survives_restart_and_ignores_disabled_daily_schedule(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        daily = DailyScheduler(state, config)
        daily.save({**daily.defaults(), 'enabled': False, 'dead_hours': list(range(24))}, 'old')
        effective = effective_settings(state, config)
        assert not effective.exploration_enabled and effective.initiative_quiet_hours is None
        encounters = Encounters(state, effective)
        assert not encounters.tick()
        first = dict(encounters.current())
        assert first['kind'] == 'retry' and first['due_at'] >= int(time.time()) + 21599
        assert not daily.tick(first['due_at'])
        assert encounters.policy_block(first['due_at']) is None
    with RelayState(config.state_path) as state:
        assert not Encounters(state, config).tick()
        assert dict(Encounters(state, config).current()) == first
        assert DailyScheduler(state, config).row()['revision'] == 'old'


@pytest.mark.parametrize('kind', sorted(RETIRED_SCHEDULING_COMMANDS, key=lambda k: k.value))
def test_old_queued_commands_have_no_external_effects(tmp_path, kind):
    config = chosen_settings(tmp_path)
    # Dispatch must return before accessing any transport or application method.
    app = SimpleNamespace(settings=config)
    command = SimpleNamespace(command_type=kind, payload={}, id='old-command')
    assert RelayWorker(config)._dispatch(app, command)['outcome'] == 'retired'


@pytest.mark.parametrize('path,payload', [
    ('/api/v1/settings/schedule', {}), ('/api/v1/settings/shared-session', {}),
    ('/api/v1/commands/start-exploration', None), ('/api/v1/exploration/interpret', {}),
])
def test_retired_http_mutations_rejected_without_queueing(tmp_path, path, payload):
    _, factory, _, client = dashboard(tmp_path, participant_scheduling=True, room_mode='shared')
    body = {'idempotency_key': 'retired'}
    if payload is not None:
        body['payload'] = payload
    response = client.post(path, headers=MUTATION_HEADERS, json=body)
    assert response.status_code == 409
    with factory.connect() as state:
        assert state.connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0] == 0


def current_shared(tmp_path, state, remote, agent):
    config = replace(chosen_settings(tmp_path), room_mode='shared',
                     participants=(('h', 'Hannah'), ('m', 'Miko')), shared_turn_seconds=0)
    return SharedConversation(RelayApplication(config, state, remote, lambda: nullcontext(agent)))


@pytest.mark.parametrize('speaker', ['h', 'm'])
def test_both_shared_companions_can_choose_and_end_without_extra_invitation(tmp_path, speaker):
    remote, agent = SharedRemote(), SharedAgent()
    path = chosen_settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = current_shared(tmp_path, state, remote, agent)
        shared.start('session')
        msg = ChatMessage('choice', speaker, 'ai', 'Hannah' if speaker == 'h' else 'Miko',
                          int(time.time()), 'Goodnight. Meet again in four hours.')
        state.add_message('g1', msg, processed=True)
        state.set_meta('context_synced_at', int(time.time()))
        agent.decision.update(end_session=True, end_reason='farewell_complete',
                              next_encounter=after(), scheduling_source_id=msg.id)
        with state.connection:
            state.connection.execute("INSERT INTO shared_attention VALUES('attention','session',?,'ending',NULL,NULL)",
                                     (msg.id,))
        shared._attention(shared.active(), 'attention')
        choice = Encounters(state, shared.settings).current()
        assert choice['actor'] == 'participant' and choice['source_message_id'] == msg.id
        assert choice['due_at'] == msg.timestamp + 14400
        assert shared.active() is None and not remote.posts and not remote.calls
    with RelayState(path) as state:
        shared = current_shared(tmp_path, state, remote, agent)
        assert not shared.tick()
        assert Encounters(state, shared.settings).current()['source_message_id'] == 'choice'


def test_shared_due_check_in_uses_encounter_and_reserves_budget(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(chosen_settings(tmp_path).state_path) as state:
        shared = current_shared(tmp_path, state, remote, agent)
        encounters = Encounters(state, shared.settings)
        encounters.retry_later()
        with state.connection:
            state.connection.execute('UPDATE encounter_choices SET due_at=?', (int(time.time()) - 1,))
        assert encounters.tick()
        record = state.commands.claim_next()
        assert record.command_type == CommandType.CHOSEN_ENCOUNTER
        assert encounters.run(shared.app, record.id)['outcome'] == 'started'
        assert len(remote.posts) == 1 and 'follow-up check-in' in remote.posts[0]
        assert state.initiative_check_count() == 1
        assert shared.active()['anchor_id'].startswith('local-user:')
        assert not encounters.tick() and not shared.tick()


def test_shared_structured_contract_has_nullable_evidence_link(tmp_path):
    config = replace(chosen_settings(tmp_path), room_mode='shared', participants=(('h', 'Hannah'), ('m', 'Miko')))
    config.prompt_path.write_text('Fixture prompt', encoding='utf-8')
    with RelayState(config.state_path) as state:
        calls = []
        class Thread:
            id = 'fixture'
            def run(self, prompt, **kwargs):
                calls.append((prompt, kwargs))
                return SimpleNamespace(final_response='{}')
        agent = CodexAgent(state, cwd=tmp_path, prompt_path=config.prompt_path,
                           model=None, approved_sandbox='workspace-write', memory_settings=config)
        agent._thread = Thread()
        agent.shared_attention(ending_only=True, assistance_active=False)
        prompt, options = calls[0]
        schema = options['output_schema']
        assert {'next_encounter', 'scheduling_source_id'} <= set(schema['required'])
        assert schema['properties']['scheduling_source_id']['type'] == ['string', 'null']
        assert 'Do not prolong a goodbye' in prompt
        assert 'Do not offer scripted exploration invitations' in agent.prompt


@pytest.mark.parametrize('case', ['missing', 'bad_source', 'bad_time', 'none'])
def test_shared_timing_fallback_and_refusal_do_not_prolong_goodbye(tmp_path, case):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(chosen_settings(tmp_path).state_path) as state:
        shared = current_shared(tmp_path, state, remote, agent)
        shared.start('session')
        msg = ChatMessage('last', 'm', 'ai', 'Miko', int(time.time()), 'Goodnight. No follow-up. In four hours.')
        state.add_message('g1', msg, processed=True)
        state.set_meta('context_synced_at', int(time.time()))
        choice = None if case == 'missing' else after()
        if case == 'none':
            choice = {'kind': 'none', 'delay_seconds': None, 'at': None, 'evidence': 'No follow-up'}
        if case == 'bad_time':
            choice['delay_seconds'] = -1
        agent.decision.update(end_session=True, end_reason='farewell_complete',
            next_encounter=choice, scheduling_source_id='unknown' if case == 'bad_source' else msg.id)
        with state.connection:
            state.connection.execute("INSERT INTO shared_attention VALUES('attention','session',?,'ending',NULL,NULL)", (msg.id,))
        shared._attention(shared.active(), 'attention')
        current = Encounters(state, shared.settings).current()
        assert current['kind'] == ('none' if case == 'none' else 'retry')
        assert shared.active() is None and not remote.posts and not remote.calls


def test_retirement_preserves_uncertain_transport_and_history(tmp_path):
    from test_agency import setup, command
    _, state, remote, app = setup(tmp_path)
    with state:
        record = command(state)
        # An old never-sent invitation can be closed without erasing its record.
        with state.connection:
            state.connection.execute("""INSERT INTO exploration_sessions
                (id,group_id,mode,version,status,budget,seed,inputs_json,created_at)
                VALUES(?,'g1','explorer','fixture','prepared',1,'fixture','{}',?)""",
                (record.id, int(time.time())))
        config = replace(app.settings, participant_scheduling=True)
        outbox = state.create_outbox('Uncertain fixture', request_kindroid_reply=False)
        state.set_outbox_status(outbox, 'uncertain')
        retire_safe_work(state, config)
        assert state.connection.execute('SELECT status FROM exploration_sessions WHERE id=?', (record.id,)).fetchone()[0] == 'prepared'
        assert state.connection.execute('SELECT status FROM outbox WHERE id=?', (outbox,)).fetchone()[0] == 'uncertain'
        # Fixture simulates independent confirmation; retirement never does this.
        state.set_outbox_status(outbox, 'sent')
        retire_safe_work(state, config)
        assert state.connection.execute('SELECT status FROM exploration_sessions WHERE id=?', (record.id,)).fetchone()[0] == 'closed'
        assert not remote.sent
