"""No provider reads merely because time passes or saved chat is pending."""
from contextlib import nullcontext
from dataclasses import replace
import threading
import time

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.command_queue import CommandType, CommandStatus
from kindroid_relay.encounters import Encounters
from kindroid_relay.models import AgentReply, ChatMessage
from kindroid_relay.room_worker import RoomWorker
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from test_relay import FakeKindroid, settings


class Agent:
    def __init__(self):
        self.calls = 0

    def manual_conversation_opener(self, latest):
        return 'Hello'

    def scheduled_check_in(self, latest):
        return 'Hello'

    def reply_to(self, message):
        self.calls += 1
        if self.calls == 1:
            return AgentReply('Go on', True)
        return AgentReply('Closing', False, next_encounter={
            'kind': 'none', 'delay_seconds': None, 'at': None, 'evidence': message.message})


def run_cycles(monkeypatch, configs, remotes, agents, *, multi, cycles=8, on_wait=None):
    stop = threading.Event()
    now = [int(time.time())]
    monkeypatch.setattr(time, 'time', lambda: now[0])

    class Wake:
        waits = 0
        def wait(self, timeout):
            self.waits += 1
            if on_wait:
                on_wait(self.waits)
            now[0] += 600  # Cross old polling deadlines without real sleeps.
            if self.waits >= cycles:
                stop.set()
        def set(self):
            pass
        def clear(self):
            pass

    def application(worker, state):
        key = worker.settings.room_key
        return nullcontext(RelayApplication(worker.settings, state, remotes[key], lambda: nullcontext(agents[key])))
    monkeypatch.setattr(RelayWorker, '_application', application)
    worker = (RoomWorker(configs, wake_event=Wake(), stop_event=stop) if multi
              else RelayWorker(configs[0], wake_event=Wake(), stop_event=stop))
    worker.run()


def config_for(tmp_path, key='hannah'):
    return replace(settings(tmp_path), participant_scheduling=True, cooperative_turns=True,
                   room_key=key, memory_enabled=False)


@pytest.mark.parametrize('multi', [False, True])
@pytest.mark.parametrize('saved_pending', [False, True])
def test_startup_and_restart_sync_once_then_idle_even_with_pending_chat(tmp_path, monkeypatch, multi, saved_pending):
    config = config_for(tmp_path)
    configs = [config]
    if multi:
        configs.append(replace(config, room_key='miko', group_id='g2',
                               state_path=tmp_path / 'miko.sqlite3', account_state_path=config.state_path))
    remotes = {c.room_key: FakeKindroid() for c in configs}
    agents = {c.room_key: Agent() for c in configs}
    for c in configs:
        with RelayState(c.state_path) as state:
            if saved_pending:
                state.set_meta('bootstrapped', 1)
                state.add_message(c.group_id, remotes[c.room_key].messages[0])
            # Even a stale activity marker without a pending reply is not a timer.
            else:
                state.set_meta('activity_session', 'stale-session')
    for launch in range(2):
        if launch:
            for remote in remotes.values():
                remote.messages.append(ChatMessage('during-break', 'ai1', 'ai', 'Hannah', 100, 'Added while offline'))
        run_cycles(monkeypatch, configs, remotes, agents, multi=multi)
        assert all(r.history_requests == launch + 1 and not r.sent for r in remotes.values())
        assert all(a.calls == 0 for a in agents.values())
    for c in configs:
        with RelayState(c.state_path, account_path=c.account_state_path) as state:
            assert state.history_request_count() == 2 * len(configs)
            assert any(m.id == 'during-break' for m in state.pending_ai_messages())


@pytest.mark.parametrize('multi', [False, True])
@pytest.mark.parametrize('start', ['manual', 'scheduled'])
def test_manual_sync_does_not_start_reply_but_next_start_does(tmp_path, monkeypatch, multi, start):
    config = config_for(tmp_path)
    remote, agent = FakeKindroid(), Agent()
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(agent))
        app.bootstrap()
        remote.messages.append(ChatMessage('manual-message', 'ai1', 'ai', 'Hannah', 2, 'Bye'))
        sync, _ = state.commands.enqueue(CommandType.SYNC_HISTORY, {}, idempotency_key='sync')
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=multi)
    assert remote.history_requests == 3 and not remote.sent and agent.calls == 0
    with RelayState(config.state_path) as state:
        assert state.commands.get(sync.id).status == CommandStatus.COMPLETED
        assert len(state.pending_ai_messages()) == 1
        if start == 'manual':
            trigger, _ = state.commands.enqueue(CommandType.TRIGGER_INITIATIVE, {}, idempotency_key='trigger')
        else:
            with state.connection:
                state.connection.execute('UPDATE encounter_choices SET due_at=?', (int(time.time()),))
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=multi)
    with RelayState(config.state_path) as state:
        if start == 'manual':
            assert state.commands.get(trigger.id).status == CommandStatus.COMPLETED
        assert not state.has_active_conversation_work()
        assert not state.pending_ai_messages() and not state.is_paused()
    assert agent.calls == 2 and len(remote.sent) == 2
    reads = remote.history_requests
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=multi)
    assert remote.history_requests == reads + 1  # One restart sync, no idle polling.


@pytest.mark.parametrize('multi', [False, True])
@pytest.mark.parametrize('kind', ['scheduled', 'manual'])
@pytest.mark.parametrize('fresh', [False, True])
def test_due_and_manual_starts_continue_cooperatively_then_stop_reading(tmp_path, monkeypatch, multi, kind, fresh):
    config = config_for(tmp_path)
    remote, agent = FakeKindroid(), Agent()
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(agent))
        if not fresh:
            app.bootstrap()
        if kind == 'manual':
            state.commands.enqueue(CommandType.START_CONVERSATION, {}, idempotency_key='manual')
        else:
            encounters = Encounters(state, config)
            encounters.retry_later()
            with state.connection:
                state.connection.execute('UPDATE encounter_choices SET due_at=?', (int(time.time()),))
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=multi)
    assert agent.calls == 2 and len(remote.sent) == 3
    with RelayState(config.state_path) as state:
        assert not state.is_paused() and not state.has_active_conversation_work()
    reads = remote.history_requests
    run_cycles(monkeypatch, [config], {'hannah': remote}, {'hannah': agent}, multi=multi)
    assert remote.history_requests == reads + 1
