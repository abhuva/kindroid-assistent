"""Independent, default-off scheduling of the existing observational invitation.

The scheduler only queues durable commands. The worker rechecks after history
sync; no schedule or chat text grants broader capability or identity authority.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, timedelta
from typing import Any

from .activity import ActivitySeverity


VERSION = 'curiosity-schedule-v1'


def next_fixed_due(now: int, hours: tuple[int, ...], *, local=None, epoch=None,
                   minute_offsets: tuple[int, ...] = (0,)) -> int:
    """System local calendar. Skip nonexistent hours; use a folded hour once.

    Round trips through the OS timezone account for DST without freezing today's
    UTC offset. Both DST flags are tried; the earliest valid occurrence wins.
    Conversion hooks allow deterministic tests without changing the host timezone.
    """
    local, epoch = local or time.localtime, epoch or time.mktime
    today = date(*local(now)[:3])
    for offset in range(8):
        day = today + timedelta(days=offset)
        for hour, minute in ((h, m) for h in sorted(hours) for m in sorted(minute_offsets)):
            wall = (day.year, day.month, day.day, hour, minute, 0)
            candidates = []
            for dst in (-1, 0, 1):
                stamp = int(epoch((*wall, 0, 0, dst)))
                if local(stamp)[:6] == wall:
                    candidates.append(stamp)
            if candidates and min(candidates) > now:
                return min(candidates)
    raise ValueError('no valid future curiosity slot')


def inside_quiet(now: int, quiet: tuple[int, int] | None) -> bool:
    if quiet is None:
        return False
    start, end = quiet
    local = time.localtime(now)
    current = local.tm_hour * 60 + local.tm_min
    return start == end or (start <= current < end if start < end else current >= start or current < end)


class CuriosityScheduler:
    def __init__(self, state: Any, settings: Any):
        from .schedule import effective_settings
        self.state, self.db, self.settings = state, state.connection, effective_settings(state, settings)
        self.group = settings.group_id

    @property
    def enabled(self) -> bool:
        return self.settings.curiosity_enabled and self.settings.exploration_enabled

    def basis(self) -> str:
        values = {name: getattr(self.settings, name) for name in (
            'curiosity_enabled', 'curiosity_schedule_hours', 'curiosity_interval_seconds',
            'curiosity_jitter_seconds', 'curiosity_quiet_hours', 'curiosity_idle_seconds',
            'curiosity_max_attempts_24h', 'curiosity_max_interpretations_24h', 'curiosity_window_seconds',
            'exploration_enabled', 'exploration_auto_interpret', 'exploration_max_sessions_24h',
            'initiative_quiet_hours')}
        # Detect OS timezone changes at startup; DST offset changes are not basis changes.
        values['timezone'] = time.tzname
        row = self.db.execute('SELECT revision FROM daily_schedule WHERE group_id=?', (self.group,)).fetchone()
        if row:
            values['daily_revision'] = row[0]
        return hashlib.sha256((VERSION + json.dumps(values, sort_keys=True)).encode()).hexdigest()

    def next_due(self, now: int) -> int:
        if self.settings.curiosity_schedule_hours:
            return next_fixed_due(now, self.settings.curiosity_schedule_hours)
        jitter = self.settings.curiosity_jitter_seconds
        sample = int(hashlib.sha256(f'{self.group}:{self.basis()}:{now}'.encode()).hexdigest()[:16], 16)
        delta = sample % (2 * jitter + 1) - jitter
        return now + max(3600, self.settings.curiosity_interval_seconds + delta)

    def _budget(self, now: int, command_id: str = '') -> dict[str, int]:
        row = self.db.execute('''SELECT COALESCE(SUM(CASE WHEN recorded_at>? THEN 1 ELSE 0 END),0) n,
            COALESCE(SUM(CASE WHEN budget_at>? THEN reserved_interpretations ELSE 0 END),0) calls
            FROM curiosity_slots WHERE group_id=? AND command_id IS NOT NULL
            AND command_id!=?''', (now - 86400, now - 86400, self.group, command_id)).fetchone()
        return {'attempts': row['n'], 'interpretations': row['calls']}

    def gate(self, now: int, command_id: str = '', *, synchronized: bool = False) -> str | None:
        s = self.settings
        if not self.enabled:
            return 'disabled'
        if self.state.is_paused():
            return 'paused'
        if self.state.get_meta('bootstrapped', '0') != '1':
            return 'not_bootstrapped'
        if inside_quiet(now, s.curiosity_quiet_hours) or inside_quiet(now, s.initiative_quiet_hours):
            return 'quiet_hours'
        if self.state.get_int('history_cooldown_until', 0) > now:
            return 'history_cooldown'
        if not synchronized and self.state.history_request_count() >= s.max_history_requests_24h:
            return 'history_budget'
        if self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','running') AND id!=? LIMIT 1", (command_id,)).fetchone():
            return 'command_conflict'
        if self.db.execute("SELECT 1 FROM messages WHERE group_id=? AND processed=0 AND sender_type='ai' LIMIT 1", (self.group,)).fetchone():
            return 'pending_message'
        if self.db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain') LIMIT 1").fetchone():
            return 'delivery_unresolved'
        if self.db.execute("SELECT 1 FROM generation_turns WHERE status IN ('generating','received','uncertain') LIMIT 1").fetchone():
            return 'generation_unresolved'
        from .agency import AgencyStore
        if AgencyStore(self.state, self.group).has_unresolved_work(command_id):
            return 'exploration_unresolved'
        latest = self.db.execute('''SELECT timestamp,received_at FROM messages WHERE group_id=?
            ORDER BY timestamp DESC,rowid DESC LIMIT 1''', (self.group,)).fetchone()
        if latest is None:
            return 'empty_room'
        stamp = latest['timestamp'] // 1000 if latest['timestamp'] > 10_000_000_000 else latest['timestamp']
        if s.curiosity_idle_seconds > 0 and (stamp <= 0 or max(stamp, latest['received_at']) + s.curiosity_idle_seconds > now):
            return 'recent_conversation'
        from .schedule import saved_schedule
        shared_schedule = saved_schedule(self.state, self.group)
        if shared_schedule is not None and time.localtime(now).tm_hour in shared_schedule['dead_hours']:
            return 'dead_time'
        due = self.state.get_int('initiative_due_at', 0) if s.initiative_enabled and shared_schedule is None else 0
        if due and now - 90 <= due <= now + s.curiosity_window_seconds:
            return 'social_check_in_due'
        shared = self.db.execute('''SELECT COUNT(*) FROM exploration_sessions WHERE group_id=?
            AND created_at>? AND id!=?''', (self.group, now - 86400, command_id)).fetchone()[0]
        if shared >= s.exploration_max_sessions_24h:
            return 'exploration_budget'
        budget = self._budget(now, command_id)
        if budget['attempts'] >= s.curiosity_max_attempts_24h:
            return 'daily_budget'
        reserve = 2 if s.exploration_auto_interpret else 0
        if budget['interpretations'] + reserve > s.curiosity_max_interpretations_24h:
            return 'attention_budget'
        # Pending follow-through reservations also remain charged after 24 hours.
        pending = self.db.execute('''SELECT COALESCE(SUM(c.reserved_interpretations),0) FROM curiosity_slots c
            WHERE c.group_id=? AND c.command_id!=? AND c.budget_at<=? AND EXISTS(
                SELECT 1 FROM exploration_responses r JOIN exploration_sessions e ON e.id=r.session_id
                WHERE r.session_id=c.command_id AND e.status!='closed'
                AND r.status NOT IN ('complete','failed','needs_review'))''',
                (self.group, command_id, now - 86400)).fetchone()[0]
        if budget['interpretations'] + pending + reserve > s.curiosity_max_interpretations_24h:
            return 'attention_budget'
        return None

    def tick(self, now: int | None = None) -> bool:
        """Called only by the worker at safe boundaries; true means a command queued."""
        from .schedule import saved_schedule
        if saved_schedule(self.state, self.group) is not None:
            return False
        now = int(time.time()) if now is None else now
        basis = self.basis()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM curiosity_schedule WHERE group_id=?', (self.group,)).fetchone()
            if not self.enabled:
                if row and (row['basis'] != basis or row['next_due_at'] is not None):
                    self.db.execute('UPDATE curiosity_schedule SET basis=?,next_due_at=NULL,updated_at=? WHERE group_id=?', (basis, now, self.group))
                return False
            if row is None or row['basis'] != basis or row['next_due_at'] is None:
                self.db.execute('''INSERT INTO curiosity_schedule VALUES (?,?,?,?)
                    ON CONFLICT(group_id) DO UPDATE SET basis=excluded.basis,next_due_at=excluded.next_due_at,updated_at=excluded.updated_at''',
                    (self.group, basis, self.next_due(now), now))
                return False  # enabling/reconfiguration never sends immediately
            due = row['next_due_at']
            if due > now:
                return False
            reason = 'missed_window' if now > due + self.settings.curiosity_window_seconds else self.gate(now)
            slot = hashlib.sha256(f'{VERSION}:{self.group}:{due}'.encode()).hexdigest()
            exists = self.db.execute('SELECT 1 FROM curiosity_slots WHERE id=?', (slot,)).fetchone()
            self.db.execute('UPDATE curiosity_schedule SET next_due_at=?,updated_at=? WHERE group_id=?',
                            (self.next_due(now), now, self.group))
            if exists:
                return False
            command_id = None if reason else slot
            if command_id:
                # One transaction reserves the slot, command, budget and next due time.
                # Public command enqueue commits internally, so it cannot own this transaction.
                self.db.execute('''INSERT INTO commands VALUES (?,'scheduled_exploration','{}',?,'queued',?,NULL,NULL,NULL,NULL)''',
                                (command_id, 'curiosity:' + slot, now))
            self.db.execute('INSERT INTO curiosity_slots VALUES (?,?,?,?,?,?,?,?,?)',
                (slot, self.group, basis, due, now, now, reason or 'queued', command_id,
                 2 if command_id and self.settings.exploration_auto_interpret else 0))
            self.state.activity.add('curiosity_skipped' if reason else 'curiosity_scheduled', ActivitySeverity.INFO,
                f'Scheduled exploration skipped: {reason.replace("_", " ")}' if reason else 'Scheduled exploration queued',
                details={'outcome': reason or 'queued', **({'command_id': command_id} if command_id else {})})
        return command_id is not None

    def guard(self, command_id: str, *, synchronized: bool = False) -> str | None:
        from .schedule import DailyScheduler
        daily = DailyScheduler(self.state, self.settings)
        if self.db.execute('SELECT 1 FROM daily_slots WHERE command_id=?', (command_id,)).fetchone():
            return daily.guard(command_id, synchronized=synchronized)
        now = int(time.time())
        slot = self.db.execute('''SELECT s.*,c.status command_status,c.command_type FROM curiosity_slots s
            JOIN commands c ON c.id=s.command_id WHERE s.command_id=? AND s.group_id=?''', (command_id, self.group)).fetchone()
        if slot is None:
            return 'missing_schedule_authorization'
        if slot['command_status'] != 'running' or slot['command_type'] != 'scheduled_exploration':
            return 'invalid_command_state'
        if slot['outcome'] != 'queued':
            return 'already_attempted'
        if slot['basis'] != self.basis():
            return 'configuration_changed'
        if now < slot['scheduled_at'] or now > slot['scheduled_at'] + self.settings.curiosity_window_seconds:
            return 'missed_window'
        return self.gate(now, command_id, synchronized=synchronized)

    def finish(self, command_id: str, outcome: str) -> None:
        from .schedule import DailyScheduler
        DailyScheduler(self.state, self.settings).finish(command_id, outcome)
        with self.db:
            changed = self.db.execute("UPDATE curiosity_slots SET outcome=? WHERE command_id=? AND group_id=? AND outcome='queued'",
                                      (outcome, command_id, self.group)).rowcount
            if changed:
                self.state.activity.add('curiosity_completed', ActivitySeverity.INFO,
                    'Scheduled exploration finished', details={'command_id': command_id, 'outcome': outcome})

    def deadline(self) -> int | None:
        from .schedule import DailyScheduler
        daily = DailyScheduler(self.state, self.settings)
        if daily.configured:
            return daily.row()['next_due_at']
        if not self.enabled:
            return None
        row = self.db.execute('SELECT next_due_at FROM curiosity_schedule WHERE group_id=?', (self.group,)).fetchone()
        return row[0] if row else None

    def snapshot(self) -> dict[str, Any]:
        row = self.db.execute('SELECT * FROM curiosity_schedule WHERE group_id=?', (self.group,)).fetchone()
        valid = row is not None and row['basis'] == self.basis()
        recent = [dict(r) for r in self.db.execute('''SELECT s.scheduled_at,s.recorded_at,s.outcome,s.command_id,
            s.reserved_interpretations,c.status AS command_status,e.status AS session_status
            FROM curiosity_slots s LEFT JOIN commands c ON c.id=s.command_id
            LEFT JOIN exploration_sessions e ON e.id=s.command_id
            WHERE s.group_id=? ORDER BY s.recorded_at DESC,s.rowid DESC LIMIT 10''', (self.group,))]
        for slot in recent:
            if slot['outcome'] == 'queued' and slot['command_status'] in {'interrupted', 'failed', 'cancelled'}:
                slot['outcome'] = slot['command_status']
        from .schedule import DailyScheduler
        daily = DailyScheduler(self.state, self.settings)
        shared = daily.configured
        return {'enabled': self.enabled, 'configured': self.settings.curiosity_enabled, 'shared_schedule': shared,
                'host': 'serve_worker_only', 'next_due_at': daily.row()['next_due_at'] if shared else row['next_due_at'] if valid and self.enabled else None,
                'initializing': self.enabled and not valid and not shared, 'hours': self.settings.curiosity_schedule_hours,
                'interval_seconds': self.settings.curiosity_interval_seconds, 'jitter_seconds': self.settings.curiosity_jitter_seconds,
                'quiet_hours': self.settings.curiosity_quiet_hours, 'idle_seconds': self.settings.curiosity_idle_seconds,
                'attempt_limit': self.settings.curiosity_max_attempts_24h,
                'interpretation_limit': self.settings.curiosity_max_interpretations_24h,
                'budget': self._budget(int(time.time())), 'recent': recent}
