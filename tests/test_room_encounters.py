from contextlib import nullcontext
from dataclasses import replace
import json
import time

import pytest
from fastapi.testclient import TestClient

from kindroid_relay.application import RelayApplication
from kindroid_relay.command_queue import CommandType
from kindroid_relay.encounters import Encounters
from kindroid_relay.kindroid_api import MessagePage
from kindroid_relay.models import ChatMessage, AgentReply
from kindroid_relay.rooms import configured_rooms
from kindroid_relay.config import ConfigurationError
from kindroid_relay.shared import SharedConversation
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.schedule import DailyScheduler
from kindroid_relay.curiosity import CuriosityScheduler
from kindroid_relay.turns import ReconciliationError
from kindroid_relay.web.app import create_web_app
from test_relay import settings


def source(state, text='Call me again in four hours.', sender='hannah'):
    message = ChatMessage('source', sender, 'ai', 'Hannah', int(time.time()), text)
    state.add_message('g1', message, processed=True)
    return message


def chosen_settings(tmp_path):
    return replace(settings(tmp_path), participant_scheduling=True, participants=(('hannah', 'Hannah'),))


def after():
    return {'kind': 'after', 'delay_seconds': 14400, 'at': None, 'evidence': 'in four hours'}


def test_choice_controls_both_starters_and_survives_restart(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        msg = source(state)
        service = Encounters(state, config)
        assert 'saved' in service.choose(msg, after())
        assert service.current()['due_at'] == msg.timestamp + 14400
        assert service.current()['source_message_id'] == msg.id
        assert DailyScheduler(state, config).gate(int(time.time()), 'exploration') == 'participant_schedule'
        assert not CuriosityScheduler(state, replace(config, exploration_enabled=True, curiosity_enabled=True)).enabled
        assert not service.tick()
        service.choose(msg, after())
        assert state.connection.execute('SELECT COUNT(*) FROM encounter_choices').fetchone()[0] == 1
    with RelayState(config.state_path) as state:
        assert Encounters(state, config).controls_schedule()


@pytest.mark.parametrize('change', [
    {'delay_seconds': True}, {'delay_seconds': 31 * 86400}, {'evidence': 'not in source'},
    {'kind': 'at', 'at': '2030-01-01T10:00:00', 'delay_seconds': None}, {'kind': 'execute'},
])
def test_invalid_choice_never_changes_schedule(tmp_path, change):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        with pytest.raises(ValueError):
            Encounters(state, config).choose(source(state), {**after(), **change})
        assert Encounters(state, config).current() is None


@pytest.mark.parametrize('sender', ['miko', 'user', 'ai'])
def test_unattributed_or_conflicting_source_cannot_schedule(tmp_path, sender):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        with pytest.raises(ValueError):
            Encounters(state, config).choose(source(state, sender=sender), after())


def test_decline_and_operator_override_never_restore_old_timers(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        service = Encounters(state, config)
        service.choose(source(state, 'No automatic next encounter.'),
            {'kind': 'none', 'delay_seconds': None, 'at': None, 'evidence': 'No automatic next encounter'})
        assert service.current()['status'] == 'waiting'
        assert not service.tick()
        service.override(int(time.time()) + 120)
        assert service.current()['actor'] == 'operator'
        service.override(None)
        assert service.controls_schedule()
        assert service.current()['due_at'] is None


def test_due_choice_queues_once_and_replacement_invalidates_old_command(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        service = Encounters(state, config)
        service.override(int(time.time()) + 60)
        with state.connection:
            state.connection.execute('UPDATE encounter_choices SET due_at=?', (int(time.time()) - 1,))
        assert service.tick()
        assert not service.tick()
        command = state.commands.claim_next()
        assert command.command_type == CommandType.CHOSEN_ENCOUNTER
        service.override(None)
        assert service.run(None, command.id)['outcome'] == 'superseded'


def test_missed_choice_queues_a_recovery_invitation(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        service = Encounters(state, config)
        service.override(int(time.time()) + 60)
        with state.connection:
            state.connection.execute('UPDATE encounter_choices SET due_at=?', (int(time.time()) - 7200,))
        original = service.current()['id']
        assert service.tick()
        assert service.current()['kind'] == 'missed'
        assert state.connection.execute('SELECT status FROM encounter_choices WHERE id=?', (original,)).fetchone()[0] == 'missed'
        assert not service.tick()
        assert service.controls_schedule()


def test_secondary_room_shares_account_budget_only(tmp_path):
    first, second = tmp_path / 'first.db', tmp_path / 'second.db'
    with RelayState(first) as primary, RelayState(second, account_path=first) as other:
        primary.record_history_request(2)
        other.record_history_request(2)
        assert primary.history_request_count() == other.history_request_count() == 2
        other.record_history_request(2)
        assert primary.history_request_count() == other.history_request_count() == 3
        other.set_meta('history_cooldown_until', 12345)
        assert primary.get_int('history_cooldown_until') == 12345
        other.set_meta('codex_thread_id', 'miko-thread')
        other.pause('only miko')
        source(other)
        assert primary.recent_messages() == []
        assert not primary.is_paused()
        assert primary.get_meta('codex_thread_id') is None


def test_rooms_preserve_primary_and_reject_duplicate_identity(tmp_path):
    path = tmp_path / 'rooms.json'
    rows = [
        {'key': 'hannah', 'group_id': 'g1', 'participants': [{'id': 'h', 'name': 'Hannah'}]},
        {'key': 'miko', 'group_id': 'g2', 'participants': [{'id': 'm', 'name': 'Miko'}]},
    ]
    path.write_text(json.dumps(rows))
    config = replace(settings(tmp_path), rooms_file=str(path))
    rooms = configured_rooms(config)
    assert rooms[0].state_path == config.state_path
    assert rooms[1].state_path != config.state_path
    assert rooms[1].account_state_path == config.state_path
    assert rooms[1].participant_scheduling
    rows[1]['group_id'] = 'g1'
    path.write_text(json.dumps(rows))
    with pytest.raises(ConfigurationError):
        configured_rooms(config)


class SharedRemote:
    def __init__(self, *, empty=False, fail=False):
        self.messages = [ChatMessage('initial', 'user', 'user', 'Marc', int(time.time()) - 10, 'Hello together.')]
        self.calls, self.posts = [], []
        self.empty, self.fail = empty, fail

    def get_messages_page(self, *args, **kwargs):
        return MessagePage(list(self.messages), False, self.messages[-1].timestamp)

    def generate_ai_response(self, group, ai):
        self.calls.append((group, ai))
        n = len(self.calls)
        text = f'Reply {n}'
        self.messages.append(ChatMessage(f'remote-{n}', ai, 'ai', 'Hannah' if ai == 'h' else 'Miko',
                                         self.messages[-1].timestamp + 1, text))
        if self.fail:
            raise TimeoutError('private provider body')
        return '' if self.empty else text

    def post_user_message(self, group, body):
        self.posts.append(body)
        self.messages.append(ChatMessage(f'post-{len(self.posts)}', 'user', 'user', 'Codex Test',
                                         self.messages[-1].timestamp + 1, body))

    def get_turn(self, *args, **kwargs):
        raise AssertionError('Shared sessions must select participants explicitly')

    def activate_user_persona(self, *args, **kwargs):
        pass


class SharedAgent:
    def __init__(self):
        self.calls = []
        self.decision = {'reply': '', 'end_session': False, 'end_reason': 'conversation_ongoing',
                         'await_reply': False, 'action': None}

    def shared_attention(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.decision)


def shared_app(tmp_path, state, remote, agent):
    config = replace(settings(tmp_path), room_mode='shared', participants=(('h', 'Hannah'), ('m', 'Miko')),
                     shared_turn_seconds=0, shared_max_messages=8, shared_check_every=4, poll_seconds=10000)
    return RelayApplication(config, state, remote, lambda: nullcontext(agent))


def test_shared_alternates_without_posts_and_checks_only_periodically(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        assert shared.start('session')['outcome'] == 'started'
        for _ in range(20):
            shared.step()
        assert [ai for _, ai in remote.calls] == ['h', 'm'] * 4
        assert remote.posts == []
        assert agent.calls == [{'ending_only': True, 'assistance_active': False}]
        assert shared.active() is None
        shared.relay.synchronize_context()
        assert state.connection.execute("SELECT COUNT(*) FROM generation_turns WHERE status='reconciled'").fetchone()[0] == 8


def test_shared_keyword_calls_assistance_once_and_supports_followup(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        row = shared.active()
        with state.connection:
            state.connection.execute('UPDATE messages SET body=? WHERE remote_id=?', ('CoDeX, help please.', row['anchor_id']))
        remote.messages[-1] = replace(remote.messages[-1], message='CoDeX, help please.')
        agent.decision.update(reply='What would you like to know?', await_reply=True)
        shared.step()  # reserve attention
        shared.step()  # decide/post
        assert len(agent.calls) == 1 and len(remote.posts) == 1
        assert shared.active()['next_speaker'] == 0  # requester gets clarification
        shared.step()  # reply without keyword
        shared.step()
        agent.decision.update(reply='', await_reply=False)
        shared.step()
        assert len(agent.calls) == 2
        assert len(remote.posts) == 1  # Codex's own prefix cannot trigger itself


def test_silent_ending_never_posts_model_commentary(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    agent.decision.update(reply='Unwanted narration', end_session=True, end_reason='farewell_complete')
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        for _ in range(6):
            shared.step()
        assert shared.active() is None
        assert remote.posts == []
        assert shared.snapshot()['session']['reason'] == 'natural_end'


def test_shared_empty_response_recovers_without_regeneration(tmp_path):
    remote, agent = SharedRemote(empty=True), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        shared.step()
        assert remote.calls == [('g1', 'h'), ('g1', 'm')]
        assert shared.active()['message_count'] == 2


def test_shared_uncertain_generation_recovers_after_restart_without_replay(tmp_path):
    remote, agent = SharedRemote(fail=True), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        with pytest.raises(ReconciliationError):
            shared.step()
        assert state.is_paused()
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.relay.synchronize_context()
        assert state.is_paused()
        state.resume()
        shared.step()
        assert shared.active()['message_count'] == 1
        assert len(remote.calls) == 1


def test_end_session_is_local_and_does_not_clear_uncertainty(tmp_path):
    remote, agent = SharedRemote(fail=True), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        with pytest.raises(ReconciliationError):
            shared.step()
        shared.end()
        assert state.is_paused() and state.turns.has_uncertain()
        shared.step()
        assert len(remote.calls) == 1


def test_encounter_dashboard_enqueues_and_keeps_csrf(tmp_path):
    import threading
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path):
        pass
    app = create_web_app(config, RelayStateFactory(config.state_path), threading.Event(), csrf_token='token', testing=True)
    with TestClient(app) as client:
        data = {'idempotency_key': 'choice', 'payload': {'due_at': None}}
        assert client.post('/api/v1/settings/encounter', json=data).status_code == 403
        response = client.post('/api/v1/settings/encounter', json=data, headers={'x-relay-csrf': 'token'})
        assert response.status_code == 202
        assert client.get('/api/v1/encounters').json()['choice'] is None
        duplicate = client.post('/api/v1/settings/encounter', json=data, headers={'x-relay-csrf': 'token'})
        assert duplicate.json()['id'] == response.json()['id']


def test_generation_migration_preserves_old_links(tmp_path):
    from kindroid_relay.migrations import open_database, apply_migrations, MIGRATIONS, migrate_database
    path = tmp_path / 'upgrade.db'
    db = open_database(path)
    apply_migrations(db, MIGRATIONS[:11])
    with db:
        db.execute("INSERT INTO messages VALUES('a','g1','user','user','Codex',1,'Hello',1,1,'a',NULL)")
        db.execute("INSERT INTO outbox VALUES('o','Hello','hash',NULL,1,'sent',NULL,1,1)")
        db.execute("INSERT INTO generation_turns VALUES('t','g1','h','Hannah','o','a','uncertain',1,2)")
    before = tuple(db.execute('SELECT * FROM generation_turns').fetchone())
    db.close()
    assert migrate_database(path) == [12, 13]
    with RelayState(path) as state:
        assert tuple(state.connection.execute('SELECT * FROM generation_turns').fetchone()) == before
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_shared_schedule_disable_and_resave_only_affect_future_starts(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.configure({'enabled': False, 'daily_time': '19:30'})
        assert not shared.tick()
        assert shared.active() is not None
        shared.configure({'enabled': True, 'daily_time': '19:30'})
        assert state.get_int('shared_due_at') > time.time()
        assert SharedConversation(shared.app).settings.shared_minute == 30


def test_one_worker_rotates_rooms_with_pending_work(monkeypatch, tmp_path):
    import threading
    from types import SimpleNamespace
    from kindroid_relay.room_worker import RoomWorker
    from kindroid_relay.worker import RelayWorker
    first = replace(settings(tmp_path), room_key='hannah', cooperative_turns=True)
    second = replace(first, room_key='miko', group_id='g2', state_path=tmp_path / 'miko.db', account_state_path=first.state_path)
    for config in (first, second):
        with RelayState(config.state_path) as state:
            state.add_message(config.group_id, ChatMessage('same-id', 'ai', 'ai', config.room_key, 1, config.room_key))
            state.set_meta('activity_session', 'active-' + config.room_key)
    worker = RoomWorker([first, second])
    calls, owners = [], []
    def application(self, state):
        def run():
            calls.append(self.settings.room_key)
            owners.append(threading.get_ident())
            if len(calls) == 2:
                worker.stop()
            return 1  # Deliberately leave work pending in the first room.
        return nullcontext(SimpleNamespace(state=state, settings=self.settings,
            relay=SimpleNamespace(settings=self.settings), run_once=run))
    monkeypatch.setattr(RelayWorker, '_application', application)
    worker.run()
    assert calls == ['hannah', 'miko']
    assert len(set(owners)) == 1


def test_room_routes_keep_commands_and_transcripts_isolated(tmp_path):
    import threading
    first = chosen_settings(tmp_path)
    second = replace(first, group_id='g2', state_path=tmp_path / 'miko.db', kindroid_display_name='Miko',
                     participants=(('m', 'Miko'),), room_key='miko')
    for config in (first, second):
        with RelayState(config.state_path) as state:
            state.add_message(config.group_id, ChatMessage('same', config.participants[0][0], 'ai', config.kindroid_display_name, 1, config.kindroid_display_name))
    wake = threading.Event()
    root = create_web_app(first, RelayStateFactory(first.state_path), wake, csrf_token='token', testing=True)
    child = create_web_app(second, RelayStateFactory(second.state_path), wake, csrf_token='token', testing=True)
    root.mount('/rooms/miko', child)
    with TestClient(root) as client:
        assert client.get('/api/v1/messages').json()['items'][0]['body'] == 'Hannah'
        message = client.get('/rooms/miko/api/v1/messages').json()['items'][0]
        assert message['body'] == 'Miko' and message['participant'] == 'companion'
        assert 'src="/rooms/miko/static/api-client.js"' in client.get('/rooms/miko/').text
        assert client.post('/rooms/miko/api/v1/settings/encounter', json={'idempotency_key': 'local', 'payload': {'due_at': None}},
                           headers={'x-relay-csrf': 'token'}).status_code == 202
    with RelayState(first.state_path) as a, RelayState(second.state_path) as b:
        assert a.connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0] == 0
        assert b.connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0] == 1


def test_private_reply_saves_choice_before_receipt_and_only_asks_once(tmp_path):
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, SharedRemote(), lambda: nullcontext(SharedAgent()))
        msg = source(state)
        result = app.relay._encounter_reply(msg, AgentReply('Talk later.', False, next_encounter=after()))
        assert result.reply.startswith('Next check-in saved')
        assert Encounters(state, config).current() is not None
        assert not result.request_kindroid_reply
        state.set_meta('encounter_choice_received', 0)
        result = app.relay._encounter_reply(msg, AgentReply('Talk later.', False))
        assert result.request_kindroid_reply and 'When would you like' in result.reply
        result = app.relay._encounter_reply(msg, AgentReply('Talk later.', False))
        assert not result.request_kindroid_reply


def test_exploration_choice_is_ignored_without_losing_next_time(tmp_path):
    config = replace(chosen_settings(tmp_path), exploration_enabled=True)
    with RelayState(config.state_path) as state:
        msg = source(state)
        app = RelayApplication(config, state, SharedRemote(), lambda: nullcontext(SharedAgent()))
        result = app.relay._encounter_reply(msg, AgentReply('Explore.', False, next_encounter=after(), explore=True))
        assert result.reply.startswith('Next check-in saved')
        service = Encounters(state, config)
        assert not service.queue_exploration()  # No outbound acknowledgement yet.
        app.relay._send_user_message(result.reply, source_message_id=msg.id, request_kindroid_reply=False)
        assert not service.queue_exploration()
        assert not service.queue_exploration()
        assert state.commands.claim_next() is None


def test_retired_exploration_interpretation_has_no_external_work(tmp_path):
    config = chosen_settings(tmp_path)
    remote = SharedRemote()
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(SharedAgent()))
        assert app.interpret_exploration('command', 'old-session')['outcome'] == 'retired'
        assert not remote.calls and not remote.posts



def test_shared_approved_result_does_not_request_auto_speaker(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    with RelayState(settings(tmp_path).state_path) as state:
        app = shared_app(tmp_path, state, remote, agent)
        shared = SharedConversation(app)
        shared.start('session')
        app.relay.post_execution_result('The approved task is complete.')
        assert not remote.calls
        shared.step()  # Link delivered result to the session.
        shared.step()  # Normal explicitly selected next speaker.
        assert remote.calls == [('g1', 'h')]


def test_private_unexpected_speaker_pauses_before_generation(tmp_path):
    remote = SharedRemote()
    remote.get_turn = lambda *args, **kwargs: 'wrong-person'
    config = chosen_settings(tmp_path)
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(SharedAgent()))
        outbox = app.relay._send_user_message('Hello.', request_kindroid_reply=True)
        with pytest.raises(ReconciliationError, match='unexpected participant'):
            app.relay._request_ai_turn(outbox_id=outbox, allow_user=True)
        assert state.is_paused()
        assert not remote.calls
        assert not state.turns.has_uncertain()


def test_chosen_exploration_closing_receipt_is_not_repeated(tmp_path):
    from kindroid_relay.command_queue import CommandStatus
    config = chosen_settings(tmp_path)
    remote = SharedRemote()
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(SharedAgent()))
        record, _ = state.commands.enqueue(CommandType.START_EXPLORATION, {}, idempotency_key='explore')
        state.commands.claim_next()
        state.commands.transition(record.id, CommandStatus.COMPLETED, result={})
        state.set_meta('encounter_exploration_command', record.id)
        state.set_meta('encounter_exploration_receipt', 'Next encounter saved for tomorrow.')
        state.set_meta('encounter_choice_received', 1)
        assert not Encounters(state, config).finish_exploration(app)
        assert not remote.posts and not remote.calls
        assert not Encounters(state, config).finish_exploration(app)
        assert not remote.posts


def test_shared_daily_resave_cannot_start_twice_on_same_date(tmp_path):
    with RelayState(settings(tmp_path).state_path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, SharedRemote(), SharedAgent()))
        now = int(time.time())
        key = 'shared-daily:' + time.strftime('%Y-%m-%d', time.localtime(now))
        state.commands.enqueue(CommandType.START_SHARED, {'scheduled_at': now - 3600}, idempotency_key=key)
        state.set_meta('shared_due_at', now)
        assert shared.tick()
        assert state.connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0] == 1
        assert state.get_int('shared_due_at') > now


def test_shared_attention_restart_does_not_repeat_decision_or_delivery(tmp_path):
    remote, agent = SharedRemote(), SharedAgent()
    path = settings(tmp_path).state_path
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.start('session')
        shared.step()
        with state.connection:
            state.connection.execute('UPDATE messages SET body=? WHERE remote_id=?', ('Codex, help.', shared.active()['anchor_id']))
        remote.messages[-1] = replace(remote.messages[-1], message='Codex, help.')
        agent.decision.update(reply='Here is the answer.')
        shared.step()
        send = shared.relay._send_user_message
        def crash_after_send(*args, **kwargs):
            send(*args, **kwargs)
            raise RuntimeError('simulated crash after durable delivery')
        shared.relay._send_user_message = crash_after_send
        with pytest.raises(RuntimeError, match='simulated crash'):
            shared.step()
    with RelayState(path) as state:
        shared = SharedConversation(shared_app(tmp_path, state, remote, agent))
        shared.step()
        assert len(remote.posts) == len(agent.calls) == 1
        assert shared.active()['attention_id'] is None
