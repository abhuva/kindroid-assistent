"""Bounded, source-linked choices of the next encounter, applied by the worker."""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from .activity import ActivitySeverity
from .command_queue import CommandType

NEXT_CHOICE_SCHEMA = {'anyOf': [{'type': 'null'}, {'type': 'object', 'properties': {
    'kind': {'type': 'string', 'enum': ['after', 'at', 'none']},
    'delay_seconds': {'type': ['integer', 'null']}, 'at': {'type': ['string', 'null']},
    'evidence': {'type': 'string'}}, 'required': ['kind', 'delay_seconds', 'at', 'evidence'],
    'additionalProperties': False}]}


class Encounters:
    def __init__(self, state, settings):
        self.state, self.settings, self.db = state, settings, state.connection

    def current(self):
        return self.db.execute("SELECT * FROM encounter_choices ORDER BY rowid DESC LIMIT 1").fetchone()

    def controls_schedule(self) -> bool:
        return self.settings.participant_scheduling and self.current() is not None

    def attributed(self, message) -> bool:
        if message.sender_type != 'ai' or message.message.lstrip().startswith('[CODEX]:'):
            return False
        people = self.settings.participants
        turn = self.db.execute('SELECT * FROM generation_turns WHERE id=?', (message.id,)).fetchone()
        if people:
            ai, name = people[0]
            if message.sender not in {'', 'ai'}:
                return message.sender == ai
            return bool(turn and turn['ai_id'] == ai and turn['status'] == 'reconciled'
                        and message.display_name.casefold() == name.casefold())
        # Legacy single-companion deployments use the existing confirmed identity rule.
        if not turn or turn['status'] not in {'received', 'reconciled'}:
            return False
        return self.state.turns._same_ai(message, turn)

    def choose(self, message, choice: dict) -> str:
        if not self.settings.participant_scheduling or not self.attributed(message):
            raise ValueError('Participant scheduling is unavailable for this source')
        old = self.db.execute('SELECT * FROM encounter_choices WHERE source_message_id=?', (message.id,)).fetchone()
        if old:
            return self.receipt(old) if self.current()['id'] == old['id'] else 'That earlier timing choice has been superseded.'
        if (not isinstance(choice, dict) or set(choice) != {'kind', 'delay_seconds', 'at', 'evidence'}
                or choice['kind'] not in {'after', 'at', 'none'}
                or not isinstance(choice['evidence'], str) or not 1 <= len(choice['evidence']) <= 500
                or choice['evidence'].casefold() not in message.message.casefold()):
            raise ValueError('Next encounter requires an explicit source-linked choice')
        now = int(time.time())
        due = None
        if choice['kind'] == 'after':
            delay = choice['delay_seconds']
            if type(delay) is not int or not 60 <= delay <= 30 * 86400 or choice['at'] is not None:
                raise ValueError('Choose a delay between one minute and thirty days')
            stamp = message.timestamp // 1000 if message.timestamp > 10_000_000_000 else message.timestamp
            if stamp <= 0 or stamp > now + 60:
                raise ValueError('Scheduling source has no reliable time')
            due = stamp + delay
        elif choice['kind'] == 'at':
            if not isinstance(choice['at'], str) or len(choice['at']) > 40 or choice['delay_seconds'] is not None:
                raise ValueError('Choose a timestamp with a timezone offset')
            dt = datetime.fromisoformat(choice['at'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('Choose a timestamp with a timezone offset')
            due = int(dt.timestamp())
        elif choice['delay_seconds'] is not None or choice['at'] is not None:
            raise ValueError('No-follow-up cannot also contain a time')
        if due is not None and not now < due <= now + 30 * 86400:
            raise ValueError('Next encounter must be in the next thirty days')
        if due is not None and self.policy_block(due):
            raise ValueError('Requested time is outside the locally enabled encounter schedule')
        self._save(message.id, choice['kind'], due, 'participant')
        return self.receipt(self.current())

    def _save(self, source, kind, due, actor):
        with self.db:
            self.db.execute("UPDATE encounter_choices SET status='superseded' WHERE status IN ('pending','queued')")
            self.db.execute('INSERT INTO encounter_choices VALUES(?,?,?,?,?,?,NULL,?)',
                (uuid.uuid4().hex, source, kind, due, int(time.time()), 'pending' if due else 'waiting', actor))
        self.state.activity.add('encounter_choice', ActivitySeverity.INFO,
            'Next encounter saved' if due else 'Automatic encounters stopped',
            details={'source_message_id': source, 'outcome': 'scheduled' if due else 'waiting'})

    def override(self, due):
        if due is not None and (type(due) is not int or not time.time() < due <= time.time() + 30 * 86400):
            raise ValueError('Choose a future encounter within thirty days')
        if due is not None and self.policy_block(due):
            raise ValueError('Requested time is outside the locally enabled encounter schedule')
        self._save(None, 'at' if due else 'none', due, 'operator')
        return self.snapshot()

    @staticmethod
    def receipt(row):
        if row['due_at'] is None:
            return 'No further automatic encounters are scheduled.'
        stamp = datetime.fromtimestamp(row['due_at']).astimezone().isoformat(timespec='minutes')
        return f'Next encounter saved for {stamp}; this replaces automatic check-ins and exploration invitations.'

    def snapshot(self):
        row = self.current()
        return {'enabled': self.settings.participant_scheduling,
                'choice': dict(row) if row else None,
                'controls_schedule': self.controls_schedule()}

    def policy_block(self, at):
        from .schedule import saved_schedule
        from .curiosity import inside_quiet
        if not self.settings.initiative_enabled:
            return 'disabled'
        schedule = saved_schedule(self.state, self.settings.group_id)
        if schedule is not None:
            if not schedule['enabled']:
                return 'disabled'
            return 'dead_time' if time.localtime(at).tm_hour in schedule['dead_hours'] else None
        return 'quiet_hours' if inside_quiet(at, self.settings.initiative_quiet_hours) else None

    def tick(self):
        if self.queue_exploration():
            return True
        row = self.current()
        if not self.settings.participant_scheduling or not row or row['status'] != 'pending':
            return False
        now = int(time.time())
        if row['due_at'] > now:
            return False
        # A requested meeting is not caught up after a long outage or pause.
        if now > row['due_at'] + 3600:
            with self.db:
                self.db.execute("UPDATE encounter_choices SET status='missed' WHERE id=?", (row['id'],))
            return False
        if self.state.is_paused() or self.state.pending_ai_messages():
            return False
        if self.policy_block(now) or self.state.turns.has_uncertain():
            return False
        from .agency import AgencyStore
        if AgencyStore(self.state, self.settings.group_id).has_unresolved_work():
            return False
        if self.db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')").fetchone():
            return False
        if self.state.initiative_check_count() >= self.state.initiative_limit(self.settings.initiative_max_checks_24h):
            return False
        if self.state.get_int('history_cooldown_until') > now or self.state.history_request_count() >= self.settings.max_history_requests_24h:
            return False
        if self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','running')").fetchone():
            return False
        command_id = 'encounter-' + row['id']
        # The command and its controlling choice commit atomically.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute("UPDATE encounter_choices SET status='queued',command_id=? WHERE id=? AND status='pending'", (command_id, row['id'])).rowcount
            if not changed:
                return False
            self.db.execute("INSERT INTO commands VALUES(?, 'chosen_encounter', '{}', ?, 'queued', ?, NULL,NULL,NULL,NULL)",
                            (command_id, 'encounter:' + row['id'], now))
        return True

    def prepare_exploration(self, message):
        import hashlib
        command_id = hashlib.sha256(('chosen-exploration:' + message.id).encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO encounter_explorations VALUES(?,?,'pending')", (message.id, command_id))

    def queue_exploration(self):
        if not self.settings.participant_scheduling or not self.settings.exploration_enabled or self.state.is_paused():
            return False
        row = self.db.execute('''SELECT e.* FROM encounter_explorations e
            JOIN messages m ON m.remote_id=e.source_message_id JOIN outbox o ON o.source_message_id=m.remote_id
            WHERE e.status='pending' AND m.processed=1 AND o.status='sent' ORDER BY e.rowid LIMIT 1''').fetchone()
        if row is None or self.state.pending_ai_messages():
            return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute("UPDATE encounter_explorations SET status='queued' WHERE source_message_id=? AND status='pending'", (row['source_message_id'],)).rowcount
            if not changed:
                return False
            self.db.execute("INSERT INTO commands VALUES(?,'start_exploration','{}',?,'queued',?,NULL,NULL,NULL,NULL)",
                            (row['command_id'], 'chosen-exploration:' + row['command_id'], int(time.time())))
            self.db.execute("INSERT INTO metadata VALUES('encounter_exploration_command',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (row['command_id'],))
        return True

    def run(self, application, command_id):
        row = self.current()
        if not row or row['command_id'] != command_id or row['status'] != 'queued':
            return {'outcome': 'superseded'}
        # Consume before external work, so a crash or failed send cannot requeue it.
        with self.db:
            self.db.execute("UPDATE encounter_choices SET status='attempted' WHERE id=?", (row['id'],))
        if not self.settings.participant_scheduling or time.time() > row['due_at'] + 3600 or self.policy_block(int(time.time())):
            return {'outcome': 'missed_or_disabled'}
        application.relay.chosen_encounter = True
        try:
            return application.trigger_initiative()
        finally:
            application.relay.chosen_encounter = False

    def finish_exploration(self, application):
        """Return from a chosen bounded exploration to the timing conversation."""
        command_id = self.state.get_meta('encounter_exploration_command')
        if not self.settings.participant_scheduling or not command_id or self.state.is_paused():
            return False
        command = self.state.commands.get(command_id)
        if command is None or command.status.value in {'queued', 'running'}:
            return False
        if command.status.value != 'completed':
            self.state.set_meta('encounter_exploration_command', '')
            return False
        from .agency import AgencyStore
        if AgencyStore(self.state, self.settings.group_id).has_unresolved_work():
            return False
        if self.db.execute("SELECT 1 FROM exploration_experiences WHERE session_id=? AND status IN ('queued','ready','researching','sending','awaiting_reflection','uncertain')", (command_id,)).fetchone():
            return False
        if self.state.pending_ai_messages():
            return False
        receipt = self.state.get_meta('encounter_exploration_receipt')
        if self.state.get_int('encounter_choice_received') and not receipt:
            self.state.set_meta('encounter_exploration_command', '')
            return False
        outbox = self.state.get_meta('encounter_finish_outbox')
        if not outbox:
            outbox = application.relay._send_user_message(
                receipt or 'When would you like our next check-in? We can explore more ideas then if you feel like it.',
                request_kindroid_reply=not bool(receipt),
                outbox_created=lambda oid: self.state.set_meta('encounter_finish_outbox', oid))
        else:
            row = self.db.execute('SELECT status FROM outbox WHERE id=?', (outbox,)).fetchone()
            if row is None or row['status'] != 'sent':
                raise ValueError('Inspect the unfinished encounter closing message')
        if not receipt:
            self.state.set_meta('encounter_asked', 1)
            application.relay._request_ai_turn(outbox_id=outbox, allow_user=True)
        self.state.set_meta('encounter_exploration_command', '')
        self.state.set_meta('encounter_finish_outbox', '')
        self.state.set_meta('encounter_exploration_receipt', '')
        return True
