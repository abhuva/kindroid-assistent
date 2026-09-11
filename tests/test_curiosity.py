import calendar
import json
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from kindroid_relay.curiosity import CuriosityScheduler, inside_quiet, next_fixed_due
from kindroid_relay.command_queue import CommandType, CommandStatus, SIDE_EFFECTING_COMMANDS
from kindroid_relay.config import Settings, ConfigurationError
from kindroid_relay.models import ChatMessage
from kindroid_relay.migrations import MIGRATIONS, apply_migrations, open_database, migrate_database
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.worker import RelayWorker
from kindroid_relay.relay import HistoryCooldownError
from kindroid_relay.backup import backup_database
from kindroid_relay.diagnostics import export_diagnostics
from test_agency import setup
from test_config import write_env
from test_web import dashboard, MUTATION_HEADERS


def clock_fixture(monkeypatch, **overrides):
    now = [int(time.time())]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    return now


def ready(tmp_path, monkeypatch, **overrides):
    now = clock_fixture(monkeypatch)
    config, state, remote, app = setup(tmp_path, **{
        'curiosity_enabled': True, 'curiosity_schedule_hours': None,
        'curiosity_interval_seconds': 3600, 'curiosity_jitter_seconds': 0,
        'curiosity_quiet_hours': None, 'initiative_quiet_hours': None,
        'initiative_enabled': False, **overrides})
    with state.connection:
        state.connection.execute('UPDATE messages SET received_at=?', (now[0] - 7200,))
    scheduler = CuriosityScheduler(state, config)
    assert scheduler.tick() is False
    return config, state, remote, app, scheduler, now


def due(scheduler, now):
    with scheduler.db:
        scheduler.db.execute('UPDATE curiosity_schedule SET next_due_at=? WHERE group_id=?', (now, scheduler.group))


def test_disabled_defaults_and_config_validation(tmp_path):
    env = tmp_path / 'fixture.env'
    write_env(env)
    config = Settings.load(env)
    assert config.curiosity_enabled is False
    assert config.curiosity_schedule_hours == (10, 16)
    assert config.curiosity_quiet_hours == (1320, 480)
    write_env(env, 'RELAY_CURIOSITY_ENABLED=true\nRELAY_CURIOSITY_SCHEDULE_HOURS=\n')
    assert Settings.load(env).curiosity_schedule_hours is None


@pytest.mark.parametrize('line', [
    'RELAY_CURIOSITY_SCHEDULE_HOURS=25', 'RELAY_CURIOSITY_SCHEDULE_HOURS=bad',
    'RELAY_CURIOSITY_QUIET_HOURS=25:00-08:00', 'RELAY_CURIOSITY_ENABLED=maybe',
    'RELAY_CURIOSITY_WINDOW_SECONDS=901', 'RELAY_CURIOSITY_INTERVAL_SECONDS=3599',
    'RELAY_CURIOSITY_JITTER_SECONDS=21600', 'RELAY_CURIOSITY_IDLE_SECONDS=0',
    'RELAY_CURIOSITY_MAX_ATTEMPTS_24H=0', 'RELAY_CURIOSITY_MAX_INTERPRETATIONS_24H=-1',
])
def test_invalid_curiosity_settings_fail_closed(tmp_path, line):
    env = tmp_path / 'fixture.env'
    write_env(env, line + '\n')
    with pytest.raises(ConfigurationError):
        Settings.load(env)


def utc(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def berlin_fixture_local(stamp):
    # Deterministic synthetic timezone with 2026 European-style gap/fold rules.
    offset = 7200 if utc('2026-03-29T01:00') <= stamp < utc('2026-10-25T01:00') else 3600
    return time.gmtime(stamp + offset)


def berlin_fixture_epoch(parts):
    return calendar.timegm(parts[:6]) - (7200 if parts[-1] == 1 else 3600)


def test_fixed_calendar_rollover_gap_and_fold_without_changing_host_timezone():
    def next_at(stamp, hours):
        return next_fixed_due(utc(stamp), hours, local=berlin_fixture_local, epoch=berlin_fixture_epoch)
    assert next_at('2026-01-01T22:59', (0, 6)) == utc('2026-01-01T23:00')
    assert next_at('2026-03-28T23:00', (2,)) == utc('2026-03-30T00:00')  # nonexistent 02:00 skipped
    assert next_at('2026-03-28T23:00', (3,)) == utc('2026-03-29T01:00')
    assert next_at('2026-10-24T23:00', (2,)) == utc('2026-10-25T00:00')  # first occurrence only
    assert next_at('2026-10-25T00:01', (2,)) == utc('2026-10-26T01:00')  # not second 02:00


def test_quiet_hours_boundaries(monkeypatch):
    for minute, expected in [(1319, False), (1320, True), (0, True), (479, True), (480, False)]:
        monkeypatch.setattr(time, 'localtime', lambda _now, minute=minute: time.struct_time((2026, 1, 1, minute // 60, minute % 60, 0, 0, 1, -1)))
        assert inside_quiet(1, (1320, 480)) is expected
    assert inside_quiet(1, (0, 0)) is True
    assert inside_quiet(1, None) is False
    assert inside_quiet(1, (400, 500)) is True


def test_fractional_calendar_ticks_skip_dst_gap_and_repeated_occurrences():
    def next_at(stamp):
        return next_fixed_due(utc(stamp), (2, 3), local=berlin_fixture_local,
                              epoch=berlin_fixture_epoch, minute_offsets=(0, 15, 30, 45))
    assert next_at('2026-03-29T00:59') == utc('2026-03-29T01:00')  # 03:00 after gap
    assert next_at('2026-10-25T00:01') == utc('2026-10-25T00:15')  # first 02:15
    assert next_at('2026-10-25T00:46') == utc('2026-10-25T02:00')  # skip all repeated 02:xx


def test_interval_jitter_is_bounded_deterministic_and_persisted(tmp_path, monkeypatch):
    config, state, _, _, scheduler, now = ready(tmp_path, monkeypatch, curiosity_interval_seconds=7200, curiosity_jitter_seconds=1200)
    with state:
        stamp = scheduler.next_due(now[0])
        assert 6000 <= stamp - now[0] <= 8400
        assert scheduler.next_due(now[0]) == stamp
        assert scheduler.snapshot()['next_due_at'] == stamp
        for delta in range(20):
            assert 6000 <= scheduler.next_due(now[0] + delta) - now[0] - delta <= 8400
        assert CuriosityScheduler(state, config).tick() is False
        assert scheduler.snapshot()['next_due_at'] == stamp


def test_initialization_reconfiguration_and_disable_never_send_immediately(tmp_path, monkeypatch):
    config, state, remote, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        disabled = CuriosityScheduler(state, replace(config, curiosity_enabled=False))
        disabled.tick()
        assert disabled.snapshot()['next_due_at'] is None
        now[0] += 86400
        assert scheduler.tick() is False
        assert scheduler.snapshot()['next_due_at'] > now[0]
        changed = CuriosityScheduler(state, replace(config, curiosity_interval_seconds=7200))
        assert changed.tick() is False
        assert changed.snapshot()['next_due_at'] == now[0] + 7200
        assert not remote.sent


def test_due_slot_reserves_command_and_advances_once(tmp_path, monkeypatch):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0])
        assert scheduler.tick()
        assert not scheduler.tick()
        assert len(state.commands.recent()) == 1
        cmd = state.commands.claim_next()
        assert cmd.command_type == CommandType.SCHEDULED_EXPLORATION
        assert cmd.command_type in SIDE_EFFECTING_COMMANDS
        assert not remote.sent
        RelayWorker(config)._run_command(state, app, cmd)
        result = state.commands.get(cmd.id)
        assert result.status == CommandStatus.COMPLETED
        assert result.result['scheduled_outcome'] == 'invitation_sent'
        assert len(remote.sent) == 1
        assert remote.history_requests == 2  # bootstrap and one pre-invitation synchronization
        events = list(state.connection.execute('SELECT actor,kind FROM agency_events WHERE session_id=?', (cmd.id,)))
        assert any(r['actor'] == 'system' and r['kind'] == 'exploration_scheduled' for r in events)
        assert not any(r['kind'] == 'exploration_requested' for r in events)
        assert scheduler.snapshot()['recent'][0]['session_status'] == 'awaiting_sync'
        assert app.start_scheduled_exploration(cmd.id)['invitations'] == 0
        assert len(remote.sent) == 1


@pytest.mark.parametrize('reason', ['paused', 'not_bootstrapped', 'pending_message', 'recent_conversation',
    'history_cooldown', 'history_budget', 'delivery_unresolved', 'social_check_in_due', 'quiet_hours',
    'attention_budget', 'command_conflict', 'empty_room'])
def test_skips_are_recorded_without_history_or_sends(tmp_path, monkeypatch, reason):
    config, state, remote, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        if reason == 'paused': state.pause('Fixture')
        elif reason == 'not_bootstrapped': state.set_meta('bootstrapped', '0')
        elif reason == 'pending_message': state.add_message('g1', ChatMessage('pending', 'ai1', 'ai', 'Hannah', now[0], 'Fixture'), processed=False)
        elif reason == 'recent_conversation': state.add_message('g1', ChatMessage('recent', 'user', 'user', 'Marc', now[0] * 1000, 'Fixture'), processed=True)
        elif reason == 'history_cooldown': state.set_meta('history_cooldown_until', now[0] + 100)
        elif reason == 'history_budget': config = replace(config, max_history_requests_24h=1)
        elif reason == 'delivery_unresolved': state.create_outbox('Fixture')
        elif reason == 'social_check_in_due':
            config = replace(config, initiative_enabled=True)
            state.set_meta('initiative_due_at', now[0])
        elif reason == 'quiet_hours': config = replace(config, curiosity_quiet_hours=(0, 0))
        elif reason == 'attention_budget': config = replace(config, exploration_auto_interpret=True, curiosity_max_interpretations_24h=1)
        elif reason == 'command_conflict': state.commands.enqueue(CommandType.RESUME, {}, idempotency_key='other')
        elif reason == 'empty_room':
            with state.connection: state.connection.execute('DELETE FROM messages')
        scheduler = CuriosityScheduler(state, config)
        scheduler.tick()
        due(scheduler, now[0])
        assert not scheduler.tick()
        assert scheduler.snapshot()['recent'][0]['outcome'] == reason
        assert scheduler.snapshot()['budget']['attempts'] == 0
        assert scheduler.snapshot()['next_due_at'] > now[0]
        assert remote.history_requests == 1 and not remote.sent


def test_missed_slots_do_not_catch_up_or_burst(tmp_path, monkeypatch):
    _, state, remote, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0] - 86400)
        assert not scheduler.tick()
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'missed_window'
        assert scheduler.snapshot()['next_due_at'] == now[0] + 3600
        assert not scheduler.tick()
        assert len(scheduler.snapshot()['recent']) == 1
        assert not remote.sent


def test_new_message_discovered_at_sync_cancels_scheduled_invitation(tmp_path, monkeypatch):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0]); scheduler.tick()
        command = state.commands.claim_next()
        def synchronize():
            state.add_message('g1', ChatMessage('new', 'ai1', 'ai', 'Hannah', now[0], 'A new question'), processed=False)
        monkeypatch.setattr(app.relay, 'synchronize_context', synchronize)
        RelayWorker(config)._run_command(state, app, command)
        assert state.commands.get(command.id).result['scheduled_outcome'] == 'pending_message'
        assert not remote.sent
        assert not state.connection.execute('SELECT 1 FROM exploration_sessions').fetchone()


def test_rate_limit_during_sync_does_not_retry_or_pause_for_a_non_send(tmp_path, monkeypatch):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0]); scheduler.tick()
        cmd = state.commands.claim_next()
        monkeypatch.setattr(app.relay, 'synchronize_context', lambda: (_ for _ in ()).throw(HistoryCooldownError(now[0] + 100)))
        RelayWorker(config)._run_command(state, app, cmd)
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'history_cooldown'
        assert not remote.sent and not state.is_paused()


def test_queued_slot_expires_and_interrupted_slot_is_not_replayed(tmp_path, monkeypatch):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0]); scheduler.tick()
        cmd = state.commands.claim_next()
        now[0] += config.curiosity_window_seconds + 1
        RelayWorker(config)._run_command(state, app, cmd)
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'missed_window'
        assert not remote.sent
        due(scheduler, now[0]); scheduler.tick()
        state.commands.claim_next()
        RelayWorker(config)._recover(state)
        assert state.is_paused()
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'interrupted'
        assert state.commands.claim_next() is None


def test_slot_command_and_next_due_roll_back_together(tmp_path, monkeypatch):
    _, state, _, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0])
        monkeypatch.setattr(state.activity, 'add', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('fixture')))
        with pytest.raises(ValueError): scheduler.tick()
        assert scheduler.deadline() == now[0]
        assert not state.connection.execute('SELECT 1 FROM curiosity_slots').fetchone()
        assert state.commands.claim_next() is None


def test_attempt_and_attention_reservations_have_separate_rolling_limits(tmp_path, monkeypatch):
    config, state, _, _, scheduler, now = ready(tmp_path, monkeypatch, exploration_auto_interpret=True,
                                               curiosity_max_attempts_24h=3, curiosity_max_interpretations_24h=2)
    with state:
        due(scheduler, now[0]); assert scheduler.tick()
        cmd = state.commands.claim_next(); state.commands.transition(cmd.id, CommandStatus.COMPLETED)
        now[0] += 3600
        assert not scheduler.tick()
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'attention_budget'
        config = replace(config, curiosity_max_interpretations_24h=20, curiosity_max_attempts_24h=1)
        scheduler = CuriosityScheduler(state, config); scheduler.tick(); due(scheduler, now[0] + 1); now[0] += 1
        assert not scheduler.tick()
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'daily_budget'
        now[0] += 86400
        due(scheduler, now[0]); assert scheduler.tick()


@pytest.mark.parametrize('response_status', ['needs_review', 'failed'])
def test_review_reservations_expire_without_manual_closure(tmp_path, monkeypatch, response_status):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch,
        exploration_auto_interpret=True, curiosity_max_interpretations_24h=2)
    def invalid(*args):
        raise ValueError('Fixture interpretation failure')
    app.exploration.interpreter.interpret = invalid
    with state:
        due(scheduler, now[0]); assert scheduler.tick()
        cmd = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, cmd)
        store = app.exploration.store
        assert store.row(cmd.id, 0)['status'] == 'failed'
        store.set_status(store.row(cmd.id, 0), response_status)
        now[0] += 3600
        # Do not refund the rolling reservation merely because interpretation ended.
        assert scheduler.gate(now[0]) == 'attention_budget'
        now[0] += 86400
        assert scheduler.gate(now[0]) is None
        due(scheduler, now[0]); assert scheduler.tick()
        assert store.row(cmd.id, 0)['status'] == response_status
        assert len(remote.sent) == 1  # queuing never retries the old offer/follow-up


def test_migration_and_read_only_api_do_not_enable_scheduler(tmp_path):
    path = tmp_path / 'old.db'
    db = open_database(path); apply_migrations(db, MIGRATIONS[:8]); db.close()
    assert migrate_database(path) == [9, 10, 11, 12, 13]
    config, factory, _, client = dashboard(tmp_path)
    before = client.get('/api/v1/exploration').json()['curiosity']
    assert before['enabled'] is False and before['next_due_at'] is None
    with factory.connect() as state:
        assert not state.connection.execute('SELECT 1 FROM curiosity_schedule').fetchone()
        assert not state.connection.execute('SELECT 1 FROM curiosity_slots').fetchone()
    assert client.post('/api/v1/commands/scheduled-exploration', json={'idempotency_key': 'cannot-force'}, headers=MUTATION_HEADERS).status_code in {404, 405}


def test_schedule_survives_backup_and_private_state_stays_out_of_diagnostics(tmp_path, monkeypatch):
    config, state, _, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0]); scheduler.tick()
        backup = backup_database(config.state_path, tmp_path / 'backup')
        output = export_diagnostics(config.state_path, config.log_path, tmp_path / 'diagnostics.json')
        exported = output.read_text()
        assert scheduler.snapshot()['recent'][0]['command_id'] not in exported
        assert scheduler.basis() not in exported
        with RelayState(backup / 'state.sqlite3') as copy:
            assert CuriosityScheduler(copy, config).snapshot()['next_due_at'] == scheduler.snapshot()['next_due_at']


def test_worker_loop_claims_scheduled_command_without_direct_timer_effects(tmp_path, monkeypatch):
    config, state, remote, _, scheduler, now = ready(tmp_path, monkeypatch)
    due(scheduler, now[0]); state.close()
    worker = RelayWorker(config)
    from kindroid_relay.application import RelayApplication
    class Application(RelayApplication):
        def start_scheduled_exploration(self, command_id):
            try:
                return super().start_scheduled_exploration(command_id)
            finally:
                worker.stop()
    worker.application_factory = lambda connection: nullcontext(Application(config, connection, remote, lambda: nullcontext(None)))
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start(); thread.join(3)
    if thread.is_alive():
        worker.stop(); thread.join(2)
    assert not thread.is_alive()
    with RelayState(config.state_path) as state:
        record = state.commands.recent()[0]
        assert record.command_type == CommandType.SCHEDULED_EXPLORATION
        assert record.status == CommandStatus.COMPLETED
    assert len(remote.sent) == 1


def test_two_connections_cannot_queue_the_same_slot_twice(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    config, state, _, _, scheduler, now = ready(tmp_path, monkeypatch)
    due(scheduler, now[0]); state.close()
    factory = RelayStateFactory(config.state_path)
    def attempt():
        with factory.connect() as connection:
            return CuriosityScheduler(connection, config).tick(now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [False, True]
    with factory.connect() as connection:
        assert len(connection.commands.recent()) == 1
        assert len(CuriosityScheduler(connection, config).snapshot()['recent']) == 1


def test_scheduled_response_interpretation_is_attributed_and_reserves_attention(tmp_path, monkeypatch):
    from test_exploration_response import Interpreter
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch, exploration_auto_interpret=True)
    app.exploration.interpreter = Interpreter(('choose',))
    with state:
        due(scheduler, now[0]); scheduler.tick()
        cmd = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, cmd)
        assert len(remote.sent) == 2
        assert len(app.exploration.interpreter.calls) == 1
        assert scheduler.snapshot()['budget']['interpretations'] == 2
        event = state.connection.execute("SELECT actor FROM agency_events WHERE kind='response_flow_enabled'").fetchone()
        assert event['actor'] == 'system'
        row = state.connection.execute('SELECT budget_at FROM curiosity_slots').fetchone()
        assert row['budget_at'] == now[0]


def test_scheduled_uncertain_send_pauses_and_never_retries(tmp_path, monkeypatch):
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch)
    calls = []
    def uncertain(*args):
        calls.append(True)
        raise RuntimeError('fixture private text')
    monkeypatch.setattr(remote, 'post_user_message', uncertain)
    with state:
        due(scheduler, now[0]); scheduler.tick()
        cmd = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, cmd)
        assert state.is_paused()
        assert state.commands.get(cmd.id).status == CommandStatus.FAILED
        assert 'fixture private text' not in state.commands.get(cmd.id).error
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'failed_inspect_delivery'
        app.start_scheduled_exploration(cmd.id)
        assert calls == [True]


def test_disablement_and_quiet_hours_hold_a_saved_scheduled_acknowledgement(tmp_path, monkeypatch):
    from test_exploration_response import Interpreter
    config, state, remote, app, scheduler, now = ready(tmp_path, monkeypatch, exploration_auto_interpret=True)
    interpreter = Interpreter(('choose',))
    original = interpreter.interpret
    def choose_then_pause(*args):
        result = original(*args)
        state.pause('Fixture pause after interpretation')
        return result
    interpreter.interpret = choose_then_pause
    app.exploration.interpreter = interpreter
    with state:
        due(scheduler, now[0]); scheduler.tick()
        cmd = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, cmd)
        assert len(remote.sent) == 1
        assert app.exploration.store.row(cmd.id, 0)['status'] == 'ready'
        state.resume()
        app.settings = replace(config, curiosity_enabled=False)
        assert not app.exploration.advance(synchronized=True)
        app.settings = replace(config, curiosity_quiet_hours=(0, 0))
        assert not app.exploration.advance(synchronized=True)
        assert len(remote.sent) == 1


def test_shared_manual_exploration_budget_is_not_bypassed(tmp_path, monkeypatch):
    config, state, _, app, scheduler, now = ready(tmp_path, monkeypatch, exploration_max_sessions_24h=1)
    with state:
        from test_agency import command
        manual = command(state)
        state.commands.claim_next()
        app.start_exploration(manual.id)
        state.commands.transition(manual.id, CommandStatus.COMPLETED)
        app.relay.synchronize_context()
        now[0] += 7200
        due(scheduler, now[0]); assert not scheduler.tick()
        assert scheduler.snapshot()['recent'][0]['outcome'] == 'exploration_budget'


def test_backward_clock_and_repeat_slot_do_not_duplicate_or_cross_groups(tmp_path, monkeypatch):
    config, state, _, _, scheduler, now = ready(tmp_path, monkeypatch)
    with state:
        due(scheduler, now[0]); assert scheduler.tick()
        cmd = state.commands.claim_next(); state.commands.transition(cmd.id, CommandStatus.COMPLETED)
        previous = now[0]
        now[0] -= 100
        due(scheduler, previous)
        assert not scheduler.tick()
        now[0] = previous
        assert not scheduler.tick()
        assert len(state.commands.recent()) == 1
        other = CuriosityScheduler(state, replace(config, group_id='other-group'))
        assert other.snapshot()['recent'] == []
        assert other.snapshot()['budget']['attempts'] == 0
        assert other.guard(cmd.id) == 'missing_schedule_authorization'
