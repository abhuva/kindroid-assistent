"""Local daily allocation. Only the worker reserves slots and executes commands."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from typing import Any

from .activity import ActivitySeverity

ACTIVITIES = ('check_in', 'exploration')
LIMITS = ('check_in_limit', 'exploration_limit', 'attempt_limit', 'interpretation_limit')
WINDOW = 180


def validate_schedule(value: dict) -> dict:
    # Old clients and saved schedules omit this field and remain hourly.
    value = {'tick_minutes': 60, **value}
    if set(value) != {'enabled', 'weights', 'dead_hours', 'tick_minutes', *LIMITS}:
        raise ValueError('Schedule requires enabled, weights, dead_hours and all four limits')
    if type(value['tick_minutes']) is not int or value['tick_minutes'] not in (15, 30, 60):
        raise ValueError('Tick interval must be 15, 30 or 60 minutes')
    if type(value['enabled']) is not bool:
        raise ValueError('enabled must be a boolean')
    weights = value['weights']
    if (not isinstance(weights, dict) or set(weights) != set(ACTIVITIES)
            or any(type(n) is not int or not 0 <= n <= 100 for n in weights.values())
            or sum(weights.values()) != 100):
        raise ValueError('Activity percentages must be whole numbers totaling 100')
    hours = value['dead_hours']
    if (not isinstance(hours, list) or len(hours) > 24
            or any(type(h) is not int or not 0 <= h <= 23 for h in hours)
            or len(set(hours)) != len(hours)):
        raise ValueError('Dead hours must be unique whole hours from 0 to 23')
    if any(type(value[k]) is not int or not 1 <= value[k] <= 1000 for k in LIMITS):
        raise ValueError('Limits must be whole numbers from 1 to 1000')
    return {**value, 'weights': {k: weights[k] for k in ACTIVITIES}, 'dead_hours': sorted(hours)}


def allocate(weights: dict[str, int], hours: list[int]) -> dict[int, str]:
    """Largest remainders, then smooth weighted distribution of the whole counts."""
    kinds = list(weights)
    counts = {k: len(hours) * weights[k] // 100 for k in kinds}
    order = sorted(kinds, key=lambda k: -(len(hours) * weights[k] % 100))
    for k in order[:len(hours) - sum(counts.values())]:
        counts[k] += 1
    score = dict.fromkeys(kinds, 0)
    result = {}
    for hour in hours:
        for k in kinds:
            score[k] += counts[k]
        chosen = max(kinds, key=lambda k: score[k])
        score[chosen] -= len(hours)
        result[hour] = chosen
    return result


def saved_schedule(state: Any, group: str) -> dict | None:
    row = state.connection.execute('SELECT config_json FROM daily_schedule WHERE group_id=?', (group,)).fetchone()
    return json.loads(row[0]) if row else None


def effective_settings(state: Any, settings: Any) -> Any:
    config = saved_schedule(state, settings.group_id)
    if config is None:
        return settings
    # Shared ticks determine spacing; legacy inactivity timers must not suppress them.
    # Capability grants, transport cooldowns and unresolved-work gates are unchanged.
    return replace(settings, initiative_quiet_hours=None, curiosity_quiet_hours=None,
        initiative_idle_seconds=0, curiosity_idle_seconds=0,
        curiosity_enabled=config['enabled'] and config['weights']['exploration'] > 0,
        curiosity_max_attempts_24h=config['attempt_limit'],
        curiosity_max_interpretations_24h=config['interpretation_limit'],
        exploration_max_sessions_24h=config['exploration_limit'])


class DailyScheduler:
    def __init__(self, state: Any, settings: Any):
        self.state, self.db, self.settings = state, state.connection, settings
        self.group = settings.group_id

    def row(self):
        return self.db.execute('SELECT * FROM daily_schedule WHERE group_id=?', (self.group,)).fetchone()

    @property
    def configured(self) -> bool:
        return self.row() is not None

    def defaults(self) -> dict:
        return {'enabled': True, 'tick_minutes': 60, 'weights': {'check_in': 40, 'exploration': 60},
            'dead_hours': [], 'check_in_limit': self.state.initiative_limit(self.settings.initiative_max_checks_24h),
            'exploration_limit': self.settings.exploration_max_sessions_24h,
            'attempt_limit': self.settings.curiosity_max_attempts_24h,
            'interpretation_limit': self.settings.curiosity_max_interpretations_24h}

    @staticmethod
    def plan(config: dict) -> dict[int, str]:
        return allocate(config['weights'], [m for m in range(0, 1440, config.get('tick_minutes', 60))
                                           if m // 60 not in config['dead_hours']])

    def next_due(self, config: dict, now: int) -> int | None:
        from .curiosity import next_fixed_due
        hours = tuple(h for h in range(24) if h not in config['dead_hours'])
        return next_fixed_due(now, hours, minute_offsets=tuple(range(0, 60, config.get('tick_minutes', 60)))) if config['enabled'] and hours else None

    def save(self, value: dict, revision: str) -> dict:
        config = validate_schedule(value)
        now = int(time.time())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('''INSERT INTO daily_schedule VALUES (?,?,?,?,?)
                ON CONFLICT(group_id) DO UPDATE SET revision=excluded.revision,
                config_json=excluded.config_json,next_due_at=excluded.next_due_at,updated_at=excluded.updated_at''',
                (self.group, revision, json.dumps(config, sort_keys=True), self.next_due(config, now), now))
            self.db.execute('''INSERT INTO metadata VALUES ('initiative_limit_override',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (str(config['check_in_limit']),))
            self.state.activity.add('schedule_changed', ActivitySeverity.INFO,
                'Daily schedule saved; applies to future ticks', details={'command_id': revision})
        return {'revision': revision, 'enabled': config['enabled']}

    def gate(self, now: int, activity: str, command_id: str = '', *, synchronized: bool = False) -> str | None:
        from .encounters import Encounters
        if self.settings.room_mode == 'shared' or Encounters(self.state, self.settings).controls_schedule():
            return 'participant_schedule'
        if self.settings.participant_scheduling:
            activity = 'check_in'
        config = saved_schedule(self.state, self.group)
        if config is None or not config['enabled']:
            return 'disabled'
        if time.localtime(now).tm_hour in config['dead_hours']:
            return 'dead_time'
        if activity == 'exploration':
            from .curiosity import CuriosityScheduler
            return CuriosityScheduler(self.state, self.settings).gate(now, command_id, synchronized=synchronized)
        if not self.settings.initiative_enabled:
            return 'disabled'
        if self.state.is_paused():
            return 'paused'
        if self.state.get_meta('bootstrapped', '0') != '1':
            return 'not_bootstrapped'
        if self.state.get_int('history_cooldown_until', 0) > now:
            return 'history_cooldown'
        if not synchronized and self.state.history_request_count() >= self.settings.max_history_requests_24h:
            return 'history_budget'
        if self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','running') AND id!=?", (command_id,)).fetchone():
            return 'command_conflict'
        if self.state.pending_ai_messages():
            return 'pending_message'
        if self.db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')").fetchone():
            return 'delivery_unresolved'
        if self.db.execute("SELECT 1 FROM active_generation_turns WHERE status IN ('generating','received','uncertain')").fetchone():
            return 'generation_unresolved'
        from .agency import AgencyStore
        if AgencyStore(self.state, self.group).has_unresolved_work():
            return 'exploration_unresolved'
        latest = self.state.latest_message()
        if latest is None:
            return 'empty_room'
        return None

    def tick(self, now: int | None = None) -> bool:
        from .encounters import Encounters
        if self.settings.room_mode == 'shared' or Encounters(self.state, self.settings).controls_schedule():
            return False
        now = int(time.time()) if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.row()
            if row is None or row['next_due_at'] is None or row['next_due_at'] > now:
                return False
            config = json.loads(row['config_json'])
            due = row['next_due_at']
            local = time.localtime(due)
            # Preserve legacy top-of-hour identities; finer ticks add their minute.
            wall = time.strftime('%Y-%m-%dT%H', local) + (f':{local.tm_min:02d}' if local.tm_min else '')
            slot = hashlib.sha256(f'daily:{self.group}:{wall}'.encode()).hexdigest()
            activity = self.plan(config)[local.tm_hour * 60 + local.tm_min]
            if self.settings.participant_scheduling:
                activity = 'check_in'
            reason = 'missed_window' if now > due + WINDOW else self.gate(now, activity)
            self.db.execute('UPDATE daily_schedule SET next_due_at=? WHERE group_id=?',
                (self.next_due(config, now), self.group))
            if self.db.execute('SELECT 1 FROM daily_slots WHERE id=?', (slot,)).fetchone():
                return False
            command_id = None if reason else slot
            if command_id:
                command_type = 'scheduled_exploration' if activity == 'exploration' else 'scheduled_check_in'
                self.db.execute('''INSERT INTO commands VALUES (?,?,'{}',?,'queued',?,NULL,NULL,NULL,NULL)''',
                    (slot, command_type, 'daily:' + slot, now))
                if activity == 'exploration':
                    from .curiosity import CuriosityScheduler
                    self.db.execute('INSERT INTO curiosity_slots VALUES (?,?,?,?,?,?,?,?,?)',
                        (slot, self.group, CuriosityScheduler(self.state, self.settings).basis(), due, now, now,
                         'queued', slot, 2 if self.settings.exploration_auto_interpret else 0))
            self.db.execute('INSERT INTO daily_slots VALUES (?,?,?,?,?,?,?,?)',
                (slot, self.group, row['revision'], wall, due, activity, reason or 'queued', command_id))
            self.state.activity.add('schedule_skipped' if reason else 'schedule_queued', ActivitySeverity.INFO,
                f'Daily {activity.replace("_", "-")} tick skipped: {reason.replace("_", " ")}' if reason else 'Daily tick queued',
                details={'outcome': reason or 'queued', **({'command_id': slot} if command_id else {})})
        return command_id is not None

    def guard(self, command_id: str, *, synchronized: bool = False) -> str | None:
        slot = self.db.execute('''SELECT s.*,c.status,c.command_type FROM daily_slots s JOIN commands c ON c.id=s.command_id
            WHERE s.command_id=? AND s.group_id=?''', (command_id, self.group)).fetchone()
        row = self.row()
        if slot is None or row is None:
            return 'missing_schedule_authorization'
        expected_type = 'scheduled_exploration' if slot['activity'] == 'exploration' else 'scheduled_check_in'
        if slot['status'] != 'running' or slot['outcome'] != 'queued' or slot['command_type'] != expected_type:
            return 'already_attempted'
        if slot['revision'] != row['revision']:
            return 'configuration_changed'
        now = int(time.time())
        if not slot['scheduled_at'] <= now <= slot['scheduled_at'] + WINDOW:
            return 'missed_window'
        return self.gate(now, slot['activity'], command_id, synchronized=synchronized)

    def finish(self, command_id: str, outcome: str) -> None:
        with self.db:
            self.db.execute("UPDATE daily_slots SET outcome=? WHERE command_id=? AND group_id=? AND outcome='queued'", (outcome, command_id, self.group))

    def snapshot(self) -> dict:
        row = self.row()
        config = json.loads(row['config_json']) if row else self.defaults()
        config.setdefault('tick_minutes', 60)
        if self.settings.participant_scheduling:
            config['weights'] = {'check_in': 100, 'exploration': 0}
        config['check_in_limit'] = self.state.initiative_limit(self.settings.initiative_max_checks_24h)
        now = int(time.time())
        today = time.strftime('%Y-%m-%d', time.localtime(now))
        recent = [dict(r) for r in self.db.execute('''SELECT s.*,c.status AS command_status FROM daily_slots s
            LEFT JOIN commands c ON c.id=s.command_id WHERE s.group_id=? ORDER BY scheduled_at DESC LIMIT 192''', (self.group,))]
        for slot in recent:
            if slot['command_status'] in ('failed', 'interrupted', 'cancelled') and slot['outcome'] == 'queued':
                slot['outcome'] = slot['command_status']
        plan = self.plan(config)
        actual = {int(s['wall_slot'][11:13]) * 60 + (int(s['wall_slot'][14:16]) if len(s['wall_slot']) > 13 else 0): s
                  for s in recent if s['wall_slot'].startswith(today)}
        from .encounters import Encounters
        encounter = Encounters(self.state, self.settings)
        return {'configured': row is not None, 'revision': row['revision'] if row else None,
            'participant_scheduling': self.settings.participant_scheduling,
            'room_mode': self.settings.room_mode, 'encounters': encounter.snapshot(),
            'config': config, 'date': today, 'timezone': time.tzname,
            'server_time': now, 'next_due_at': row['next_due_at'] if row else None,
            'next_due_local': time.strftime('%Y-%m-%d %H:%M', time.localtime(row['next_due_at'])) if row and row['next_due_at'] else None,
            'counts': {k: sum(v == k for v in plan.values()) for k in ACTIVITIES},
            'timeline': [{'hour': m // 60, 'minute': m, 'activity': plan.get(m, 'dead'), 'actual': actual.get(m)}
                         for m in range(0, 1440, config['tick_minutes'])],
            'recent': recent, 'capabilities': {'check_in': self.settings.initiative_enabled,
                'exploration': self.settings.exploration_enabled},
            'auto_interpret': self.settings.exploration_auto_interpret,
            'check_in_budget': self.state.initiative_budget(self.settings.initiative_max_checks_24h)}
