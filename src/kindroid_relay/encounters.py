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
        return self.settings.participant_scheduling

    def attributed(self, message) -> bool:
        if message.sender_type != 'ai' or message.message.lstrip().startswith('[CODEX]:'):
            return False
        people = self.settings.participants
        turn = self.db.execute('SELECT * FROM generation_turns WHERE id=?', (message.id,)).fetchone()
        if people:
            if message.sender not in {'', 'ai'}:
                return message.sender in {ai for ai, _ in people}
            return any(bool(turn and turn['ai_id'] == ai and turn['status'] == 'reconciled'
                           and message.display_name.casefold() == name.casefold()) for ai, name in people)
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
        if row['kind'] == 'retry':
            return f'The relay has scheduled a follow-up for {stamp}, subject to quiet hours and availability.'
        return f'Next check-in saved for {stamp}.'

    def snapshot(self):
        row = self.current()
        return {'enabled': self.settings.participant_scheduling,
                'choice': dict(row) if row else None,
                'controls_schedule': self.controls_schedule(), 'retry_seconds': 21600}

    def next_open(self, at):
        """First eligible minute; daily windows repeat, with room for a DST day."""
        if not self.policy_block(at):
            return at
        if self.policy_block(at) == 'disabled':
            return None
        candidate = (at // 60 + 1) * 60
        for _ in range(3 * 24 * 60):
            if not self.policy_block(candidate):
                return candidate
            candidate += 60
        return None

    def retry_later(self):
        """A system fallback never replaces a pending choice or an explicit refusal."""
        if not self.settings.participant_scheduling:
            return None
        row = self.current()
        if row and (row['status'] in {'pending', 'queued'} or row['kind'] == 'none'):
            return row
        at = int(time.time()) + 21600
        self._save(None, 'retry', self.next_open(at) or at, 'system')
        self.state.activity.add('encounter_retry', ActivitySeverity.INFO,
            'No usable next time; system retry set for six hours later or the next open time',
            details={'outcome': 'retry_scheduled'})
        return self.current()

    def policy_block(self, at):
        from .quiet_hours import QuietHours
        from .schedule import saved_schedule
        from .curiosity import inside_quiet
        if not self.settings.initiative_enabled:
            return 'disabled'
        if QuietHours(self.state, self.settings).blocked(at):
            return 'quiet_hours'
        if self.settings.participant_scheduling:
            return None
        schedule = saved_schedule(self.state, self.settings.group_id)
        if schedule is not None:
            if not schedule['enabled']:
                return 'disabled'
            return 'dead_time' if time.localtime(at).tm_hour in schedule['dead_hours'] else None
        return 'quiet_hours' if inside_quiet(at, self.settings.initiative_quiet_hours) else None

    def tick(self):
        if self.settings.participant_scheduling and self.current() is None:
            if not self.state.is_paused():
                self.retry_later()
        row = self.current()
        if not self.settings.participant_scheduling or not row or row['status'] not in {'pending', 'missed'}:
            return False
        now = int(time.time())
        if row['due_at'] > now:
            return False
        # Missing an appointment changes the invitation, never replays its work.
        if row['status'] == 'missed' or (row['kind'] not in {'missed', 'retry'} and now > row['due_at'] + 3600):
            with self.db:
                self.db.execute("UPDATE encounter_choices SET status='missed' WHERE id=?", (row['id'],))
                self.db.execute('INSERT INTO encounter_choices VALUES(?,NULL,?,?,?,\'pending\',NULL,\'system\')',
                    ('missed-' + row['id'], 'missed', now, now))
            self.state.activity.add('encounter_missed', ActivitySeverity.WARNING,
                'Encounter missed; a check-in will offer now or rescheduling at the next open time',
                details={'outcome': 'recovery_pending'})
            row = self.current()
        # Imported messages alone do not start work, but must not indefinitely
        # block the next chosen check-in now that idle monitoring is disabled.
        if self.state.is_paused() or self.state.has_active_conversation_work():
            return False
        if self.db.execute("SELECT 1 FROM shared_sessions WHERE status='active'").fetchone():
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
        if self.state.get_int('history_cooldown_until') > now:
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
        if self.settings.participant_scheduling:
            return  # Retired; never revive a legacy invitation intent.
        import hashlib
        command_id = hashlib.sha256(('chosen-exploration:' + message.id).encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO encounter_explorations VALUES(?,?,'pending')", (message.id, command_id))

    def queue_exploration(self):
        if self.settings.participant_scheduling:
            return False
        from .quiet_hours import QuietHours
        if QuietHours(self.state, self.settings).blocked():
            return False
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
        # If a queued appointment expired, create a fresh recovery invitation.
        if row['kind'] not in {'missed', 'retry'} and time.time() > row['due_at'] + 3600:
            with self.db:
                self.db.execute("UPDATE encounter_choices SET status='missed' WHERE id=?", (row['id'],))
                self.db.execute('INSERT INTO encounter_choices VALUES(?,NULL,?,?,?,\'pending\',NULL,\'system\')',
                    ('missed-' + row['id'], 'missed', int(time.time()), int(time.time())))
            return {'outcome': 'recovery_pending'}
        if not self.settings.participant_scheduling or self.policy_block(int(time.time())):
            # No external attempt occurred. Leave a new pending decision, not a
            # runnable duplicate of this command, for the next open window.
            with self.db:
                self.db.execute("UPDATE encounter_choices SET status='superseded' WHERE id=?", (row['id'],))
            self._save(None, row['kind'], row['due_at'], 'system')
            return {'outcome': 'waiting_for_open_time'}
        # Consume before external work, so a crash or failed send cannot requeue it.
        with self.db:
            self.db.execute("UPDATE encounter_choices SET status='attempted' WHERE id=?", (row['id'],))
        self.retry_later()
        application.relay.chosen_encounter = True
        application.relay.encounter_opener = (
            'We missed our planned encounter because the relay was unavailable or unable to start. '
            'Would you like to spend some time together now, or choose another time?'
            if row['kind'] == 'missed' else
            'We did not settle on a usable next time, so this is the follow-up check-in. '
            'Would you like to meet now, or choose another time?'
            if row['kind'] == 'retry' else None)
        try:
            if self.settings.room_mode == 'shared':
                from .shared import SharedConversation
                return SharedConversation(application).start(command_id, opener=application.relay.encounter_opener or
                    'It is time for our planned check-in. Would you like to talk now, or choose another time?')
            return application.trigger_initiative()
        finally:
            application.relay.chosen_encounter = False
            application.relay.encounter_opener = None

    def finish_exploration(self, application):
        """Return from a chosen bounded exploration to the timing conversation."""
        if self.settings.participant_scheduling:
            return False
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
            self.retry_later()
            application.relay._request_ai_turn(outbox_id=outbox, allow_user=True)
        self.state.set_meta('encounter_exploration_command', '')
        self.state.set_meta('encounter_finish_outbox', '')
        self.state.set_meta('encounter_exploration_receipt', '')
        return True
