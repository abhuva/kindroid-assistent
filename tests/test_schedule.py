import json
import threading
import time
from dataclasses import replace

import pytest

from kindroid_relay.command_queue import CommandType, CommandStatus, CommandValidationError, validate_payload
from kindroid_relay.curiosity import CuriosityScheduler
from kindroid_relay.migrations import MIGRATIONS, apply_migrations, migrate_database, open_database
from kindroid_relay.models import ChatMessage
from kindroid_relay.schedule import DailyScheduler, WINDOW, allocate, effective_settings, validate_schedule
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.worker import RelayWorker
from test_initiative import setup_application
from test_relay import settings
from test_web import dashboard, MUTATION_HEADERS


def ready(tmp_path, monkeypatch, activity='check_in', **overrides):
    now = [int(time.mktime((2026, 9, 7, 11, 59, 0, 0, 0, -1)))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    config = replace(settings(tmp_path), exploration_enabled=True, exploration_auto_interpret=False, **overrides)
    state = RelayState(config.state_path)
    app, remote, agent = setup_application(state, config)
    app.bootstrap()
    with state.connection:
        state.connection.execute('UPDATE messages SET received_at=?', (now[0] - 7200,))
    daily = DailyScheduler(state, config)
    value = daily.defaults()
    value['weights'] = {k: 100 if k == activity else 0 for k in ('check_in', 'exploration')}
    value.update(attempt_limit=24, exploration_limit=24, interpretation_limit=48)
    daily.save(value, 'fixture-save')
    app.settings = app.relay.settings = effective_settings(state, config)
    return config, state, app, remote, agent, daily, now


@pytest.mark.parametrize('dead,weights,expected', [
    ([], {'check_in': 40, 'exploration': 60}, (10, 14)),
    ([*range(6), 22, 23], {'check_in': 40, 'exploration': 60}, (6, 10)),
    (list(range(24)), {'check_in': 40, 'exploration': 60}, (0, 0)),
    ([], {'check_in': 0, 'exploration': 100}, (0, 24)),
])
def test_allocation_excludes_dead_hours_and_uses_whole_counts(dead, weights, expected):
    plan = allocate(weights, [h for h in range(24) if h not in dead])
    assert tuple(list(plan.values()).count(k) for k in weights) == expected
    assert not set(plan).intersection(dead)
    assert plan == allocate(weights, list(plan))


def test_future_types_and_even_spread():
    plan = allocate({'a': 25, 'b': 25, 'c': 50}, list(range(24)))
    assert [list(plan.values()).count(k) for k in ('a', 'b', 'c')] == [6, 6, 12]
    for h in range(0, 24, 4):
        assert sorted(plan[i] for i in range(h, h + 4)) == ['a', 'b', 'c', 'c']


@pytest.mark.parametrize('field,value', [('enabled', 1), ('weights', {'check_in': 60, 'exploration': 60}),
    ('weights', {'check_in': True, 'exploration': 99}), ('dead_hours', [24]), ('dead_hours', [1, 1]),
    ('dead_hours', [False]), ('attempt_limit', 0), ('exploration_limit', 1001), ('check_in_limit', '50')])
def test_strict_validation(tmp_path, field, value):
    with RelayState(settings(tmp_path).state_path) as state:
        payload = DailyScheduler(state, settings(tmp_path)).defaults()
        payload[field] = value
        with pytest.raises(CommandValidationError):
            validate_payload(CommandType.SET_SCHEDULE, payload)


def test_save_future_only_and_legacy_suppressed(tmp_path, monkeypatch):
    config, state, app, remote, agent, daily, now = ready(tmp_path, monkeypatch)
    with state:
        assert daily.row()['next_due_at'] > now[0]
        assert not daily.tick()
        state.set_meta('initiative_due_at', now[0] - 10)
        app.run_once()
        assert not remote.sent and agent.check_ins == 0
        assert not CuriosityScheduler(state, config).tick()
        value = daily.snapshot()['config']
        value['dead_hours'] = list(range(24))
        daily.save(value, 'all-dead')
        assert daily.row()['next_due_at'] is None
        assert not daily.tick()
        # Dead time does not block an explicit manual check-in.
        assert app.trigger_initiative()['check_in_sent']


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('interval', [15, 30, 60])
def test_tick_execution_single_sync_and_no_repeat(tmp_path, monkeypatch, activity, interval):
    config, state, app, remote, agent, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        value = daily.snapshot()['config']; value['tick_minutes'] = interval
        now[0] += 61  # Select a future fractional-hour boundary for shorter ticks.
        daily.save(value, 'interval-save')
        now[0] = daily.row()['next_due_at']
        assert daily.tick()
        assert not daily.tick()
        command = state.commands.claim_next()
        reads = remote.history_requests
        RelayWorker(config)._run_command(state, app, command)
        result = state.commands.get(command.id)
        assert result.status == CommandStatus.COMPLETED, result.error
        assert result.result['scheduled_outcome'] in ('check_in_answered', 'invitation_sent')
        assert remote.history_requests == reads + 1
        count = len(remote.sent)
        assert daily.guard(command.id) == 'already_attempted'
        assert not DailyScheduler(state, config).tick()
        assert len(remote.sent) == count
        assert daily.snapshot()['recent'][0]['outcome'] == result.result['scheduled_outcome']
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('interval,total,counts', [(15, 64, (26, 38)), (30, 32, (13, 19)), (60, 16, (6, 10))])
def test_tick_interval_allocates_remaining_day(tmp_path, interval, total, counts):
    with RelayState(settings(tmp_path).state_path) as state:
        daily = DailyScheduler(state, settings(tmp_path))
        value = daily.defaults()
        value.update(tick_minutes=interval, dead_hours=[0, 1, 2, 3, 4, 5, 22, 23])
        plan = daily.plan(validate_schedule(value))
        assert len(plan) == total
        assert tuple(list(plan.values()).count(k) for k in ('check_in', 'exploration')) == counts
        assert min(plan) == 360 and max(plan) == 1320 - interval
        assert all(m % interval == 0 for m in plan)


@pytest.mark.parametrize('interval', [0, 1, 45, 90, True, '15', 15.0])
def test_invalid_tick_interval_rejected(tmp_path, interval):
    with RelayState(settings(tmp_path).state_path) as state:
        value = DailyScheduler(state, settings(tmp_path)).defaults()
        value['tick_minutes'] = interval
        with pytest.raises(ValueError, match='Tick interval'):
            validate_schedule(value)


def test_legacy_config_and_hour_ids_survive_interval_switch(tmp_path, monkeypatch):
    config, state, _, _, _, daily, now = ready(tmp_path, monkeypatch)
    with state:
        value = daily.snapshot()['config']; del value['tick_minutes']
        # Emulate an existing hourly database without rewriting or migrating it.
        with state.connection:
            state.connection.execute('UPDATE daily_schedule SET config_json=?', (json.dumps(value),))
        assert daily.snapshot()['config']['tick_minutes'] == 60
        assert validate_schedule(value)['tick_minutes'] == 60
        state.pause('Fixture')
        now[0] = daily.row()['next_due_at']
        assert not daily.tick()
        hour_id = daily.snapshot()['recent'][0]['id']
        value['tick_minutes'] = 15
        daily.save(value, 'quarter-hour')
        assert daily.row()['next_due_at'] == now[0] + 900
        now[0] += 900
        assert not daily.tick()
        recent = DailyScheduler(state, config).snapshot()['recent']
        assert recent[0]['wall_slot'].endswith('12:15')
        assert recent[1]['id'] == hour_id
        assert len({s['id'] for s in recent}) == 2
        assert daily.snapshot()['timeline'][49]['actual']['wall_slot'].endswith('12:15')
        # Clock rollback/reconfiguration must still recognize the legacy hour ID.
        now[0] -= 901
        daily.save(value, 'rollback')
        now[0] += 1
        assert not daily.tick()
        assert len(daily.snapshot()['recent']) == 2


def test_old_schedule_submission_retains_retry_identity(tmp_path):
    with RelayState(settings(tmp_path).state_path) as state:
        value = DailyScheduler(state, settings(tmp_path)).defaults()
        del value['tick_minutes']
        command, created = state.commands.enqueue(CommandType.SET_SCHEDULE, value, idempotency_key='old-client')
        assert created and 'tick_minutes' not in command.payload
        repeated, created = state.commands.enqueue(CommandType.SET_SCHEDULE, value, idempotency_key='old-client')
        assert not created and repeated.id == command.id


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('reason', ['paused', 'not_bootstrapped', 'pending_message',
    'history_cooldown', 'delivery_unresolved', 'missed_window'])
def test_skips_no_external_calls_no_attempt_charge(tmp_path, monkeypatch, reason, activity):
    _, state, _, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        now[0] = daily.row()['next_due_at']
        if reason == 'paused': state.pause('Fixture')
        if reason == 'not_bootstrapped': state.set_meta('bootstrapped', '0')
        if reason == 'pending_message': state.add_message('g1', ChatMessage('pending', 'ai1', 'ai', 'Hannah', now[0], 'Fixture'), processed=False)
        if reason == 'history_cooldown': state.set_meta('history_cooldown_until', now[0] + 1000)
        if reason == 'delivery_unresolved': state.create_outbox('Fixture')
        if reason == 'missed_window': now[0] += 86400
        reads = remote.history_requests
        assert not daily.tick()
        assert daily.snapshot()['recent'][0]['outcome'] == reason
        assert state.initiative_check_count() == 0
        assert remote.history_requests == reads and not remote.sent
        assert daily.row()['next_due_at'] > now[0]


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
def test_pending_hannah_history_still_blocks_after_sync(tmp_path, monkeypatch, activity):
    config, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        now[0] = daily.row()['next_due_at']
        assert daily.tick()
        remote.messages.append(ChatMessage('new-hannah', 'ai1', 'ai', 'Hannah', now[0], 'New activity'))
        cmd = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, cmd)
        assert state.commands.get(cmd.id).result['scheduled_outcome'] == 'pending_message'
        assert not remote.sent


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('after_sync', [False, True])
def test_shared_ticks_ignore_recent_completed_conversation(tmp_path, monkeypatch, activity, after_sync):
    config, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        value = daily.snapshot()['config']; value['tick_minutes'] = 15
        daily.save(value, 'quarter-hour')
        now[0] = daily.row()['next_due_at']
        recent = ChatMessage('recent-marc', 'user', 'user', 'Marc', now[0] - 1, 'Recent fixture')
        remote.messages.append(recent)
        if not after_sync:
            state.add_message('g1', recent, processed=True)
        assert daily.tick()  # Pre-queue guard ignores recency too.
        command = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, command)
        result = state.commands.get(command.id)
        assert result.status == CommandStatus.COMPLETED, result.error
        assert result.result['scheduled_outcome'] in ('check_in_answered', 'invitation_sent')
        assert remote.sent
        assert not daily.tick()  # No duplicate or catch-up.
        assert app.settings.initiative_idle_seconds == app.settings.curiosity_idle_seconds == 0


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('status', ['generating', 'received', 'uncertain'])
def test_shared_ticks_keep_generation_guards_without_cooldown(tmp_path, monkeypatch, activity, status):
    _, state, _, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        outbox_id = state.create_outbox('Fixture already sent')
        state.set_outbox_status(outbox_id, 'sent')
        state.add_message('g1', ChatMessage('local-user:' + outbox_id, 'user', 'user',
            'Codex Test', now[0], 'Fixture already sent'), processed=True)
        turn_id = state.turns.begin('g1', 'ai1', 'Hannah', outbox_id, 0)
        with state.connection:
            state.connection.execute('UPDATE generation_turns SET status=? WHERE id=?', (status, turn_id))
        now[0] = daily.row()['next_due_at']
        assert not daily.tick()
        assert daily.snapshot()['recent'][0]['outcome'] == 'generation_unresolved'
        assert not remote.sent


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('response_status', ['needs_review', 'failed', 'complete', 'pending', 'ready',
    'awaiting_source', 'interpreting', 'awaiting_reply', 'sending', 'uncertain'])
def test_review_only_does_not_block_ticks_but_active_followthrough_does(tmp_path, monkeypatch, activity, response_status):
    from test_exploration_response import run_offer
    config, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        store = app.exploration.store
        store.enable(session, session)
        store.reconcile()
        store.set_status(store.row(session, 0), response_status)
        now[0] += 7200
        daily.save(daily.snapshot()['config'], 'next-fixture')
        now[0] = daily.row()['next_due_at']
        nonblocking = response_status in {'needs_review', 'failed', 'complete'}
        assert daily.tick() is nonblocking
        assert daily.snapshot()['recent'][0]['outcome'] == ('queued' if nonblocking else 'exploration_unresolved')
        if nonblocking:
            cmd = state.commands.claim_next()
            RelayWorker(config)._run_command(state, app, cmd)
            assert state.commands.get(cmd.id).result['scheduled_outcome'] in ('check_in_answered', 'invitation_sent')
            assert len(remote.sent) > 1
        else:
            assert len(remote.sent) == 1
            event = state.connection.execute("SELECT summary FROM activity_events WHERE kind='schedule_skipped' ORDER BY id DESC LIMIT 1").fetchone()
            assert 'exploration unresolved' in event['summary']


@pytest.mark.parametrize('activity', ['check_in', 'exploration'])
@pytest.mark.parametrize('delivery_status', ['pending', 'sending', 'uncertain'])
def test_review_only_never_bypasses_transport_blockers(tmp_path, monkeypatch, activity, delivery_status):
    from test_exploration_response import run_offer
    _, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch, activity)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        store = app.exploration.store
        store.enable(session, session)
        store.reconcile()
        store.set_status(store.row(session, 0), 'needs_review')
        oid = state.create_outbox('Unresolved fixture follow-up')
        with state.connection:
            state.connection.execute('UPDATE outbox SET status=? WHERE id=?', (delivery_status, oid))
        assert daily.gate(now[0] + 7200, activity) == 'delivery_unresolved'
        assert len(remote.sent) == 1


def test_configuration_change_invalidates_queued_tick(tmp_path, monkeypatch):
    _, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch)
    with state:
        now[0] = daily.row()['next_due_at']
        assert daily.tick()
        cmd = state.commands.claim_next()
        payload = daily.snapshot()['config']; payload['enabled'] = False
        daily.save(payload, 'changed')
        assert app.start_scheduled_check_in(cmd.id)['scheduled_outcome'] == 'configuration_changed'
        assert not remote.sent
        assert daily.row()['next_due_at'] is None


def test_check_in_expiring_during_codex_keeps_charge_without_send(tmp_path, monkeypatch):
    _, state, app, remote, agent, daily, now = ready(tmp_path, monkeypatch)
    with state:
        now[0] = daily.row()['next_due_at']
        assert daily.tick()
        def slow(_latest):
            now[0] += WINDOW + 1
            return 'Fixture check-in'
        agent.scheduled_check_in = slow
        result = app.start_scheduled_check_in(state.commands.claim_next().id)
        assert result['scheduled_outcome'] == 'missed_window'
        assert state.initiative_check_count() == 1
        assert not remote.sent


def test_concurrent_workers_reserve_once_and_recovery_pauses(tmp_path, monkeypatch):
    config, state, _, _, _, daily, now = ready(tmp_path, monkeypatch)
    now[0] = daily.row()['next_due_at']
    barrier, results, errors = threading.Barrier(2), [], []
    def run():
        try:
            with RelayStateFactory(config.state_path).connect() as other:
                barrier.wait(timeout=5)
                results.append(DailyScheduler(other, config).tick())
        except Exception as error: errors.append(error)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    with state:
        assert not errors and sorted(results) == [False, True]
        cmd = state.commands.claim_next()
        RelayWorker(config)._recover(state)
        assert state.is_paused()
        assert state.commands.get(cmd.id).status == CommandStatus.INTERRUPTED
        assert daily.snapshot()['recent'][0]['outcome'] == 'interrupted'


def test_api_local_queue_validation_and_persistence(tmp_path):
    config, factory, wake, client = dashboard(tmp_path)
    snapshot = client.get('/api/v1/schedule').json()
    assert not snapshot['configured']
    body = {'idempotency_key': 'schedule-fixture', 'payload': snapshot['config']}
    assert client.post('/api/v1/settings/schedule', json=body).status_code == 403
    response = client.post('/api/v1/settings/schedule', json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 202
    assert client.post('/api/v1/settings/schedule', json=body, headers=MUTATION_HEADERS).json()['id'] == response.json()['id']
    assert wake.is_set()
    with factory.connect() as state:
        assert not DailyScheduler(state, config).configured
        cmd = state.commands.claim_next()
        # The setting operation itself needs no external clients.
        from types import SimpleNamespace
        RelayWorker(config)._run_command(state, SimpleNamespace(state=state, settings=config), cmd)
        assert state.commands.get(cmd.id).status == CommandStatus.COMPLETED
    assert client.get('/api/v1/schedule').json()['configured']
    assert client.get('/api/v1/snapshot').json()['schedule']['daily']['configured']
    body['idempotency_key'] = 'invalid'
    body['payload']['weights']['check_in'] = 120
    assert client.post('/api/v1/settings/schedule', json=body, headers=MUTATION_HEADERS).status_code == 422


def test_migration_preserves_previous_schedule_and_metadata(tmp_path):
    path = tmp_path / 'old.sqlite3'
    connection = open_database(path)
    apply_migrations(connection, MIGRATIONS[:9])
    connection.execute("INSERT INTO metadata VALUES ('kept','yes')")
    connection.execute("INSERT INTO curiosity_schedule VALUES ('g1','old-basis',123,100)")
    connection.commit(); connection.close()
    assert migrate_database(path) == [10, 11]
    with RelayState(path) as state:
        assert state.get_meta('kept') == 'yes'
        assert state.connection.execute('SELECT next_due_at FROM curiosity_schedule').fetchone()[0] == 123
        assert state.connection.execute('SELECT COUNT(*) FROM daily_schedule').fetchone()[0] == 0


def test_slot_reservation_and_advance_roll_back_together(tmp_path, monkeypatch):
    _, state, _, _, _, daily, now = ready(tmp_path, monkeypatch, 'exploration')
    with state:
        now[0] = daily.row()['next_due_at']
        with state.connection:
            state.connection.execute("CREATE TRIGGER fail_daily BEFORE INSERT ON daily_slots BEGIN SELECT RAISE(ABORT,'fixture'); END")
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError): daily.tick()
        assert daily.row()['next_due_at'] == now[0]
        assert state.commands.recent() == []
        assert CuriosityScheduler(state, daily.settings)._budget(now[0]) == {'attempts': 0, 'interpretations': 0}


@pytest.mark.parametrize('disabled', [False, True])
def test_exploration_capability_and_limits_still_apply(tmp_path, monkeypatch, disabled):
    config, state, _, remote, _, daily, now = ready(tmp_path, monkeypatch, 'exploration')
    with state:
        if disabled:
            config = replace(config, exploration_enabled=False)
        else:
            config = replace(config, exploration_auto_interpret=True)
            value = daily.snapshot()['config']; value['interpretation_limit'] = 1
            daily.save(value, 'restricted')
        daily = DailyScheduler(state, config)
        now[0] = daily.row()['next_due_at']
        assert not daily.tick()
        assert daily.snapshot()['recent'][0]['outcome'] == ('disabled' if disabled else 'attention_budget')
        assert not remote.sent


def test_worker_loop_executes_shared_tick(tmp_path, monkeypatch):
    from contextlib import contextmanager
    config, state, app, remote, _, daily, now = ready(tmp_path, monkeypatch)
    now[0] = daily.row()['next_due_at']
    with state:
        worker = RelayWorker(config)
        original = worker._run_command
        def complete(active_state, application, command):
            original(active_state, application, command)
            worker.stop()
        worker._run_command = complete
        @contextmanager
        def factory(active_state):
            # Use the worker-owned connection for all application/store operations.
            from kindroid_relay.application import RelayApplication
            yield RelayApplication(config, active_state, remote, app.agent_factory)
        worker.application_factory = factory
        worker.run()
        assert len(remote.sent) == 2
        assert daily.snapshot()['recent'][0]['outcome'] == 'check_in_answered'
