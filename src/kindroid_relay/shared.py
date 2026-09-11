"""One scheduled companion conversation, advanced one durable turn at a time."""
from __future__ import annotations

import json
import time
import uuid

from .activity import ActivitySeverity, WorkerPhase
from .command_queue import CommandType
from .turns import EmptyGeneratedReply, ReconciliationError
from .relay import HistoryCooldownError
from .shared_ending import END_REASONS, has_farewell_cue


class SharedConversation:
    def __init__(self, application):
        self.app = application
        self.state, self.settings = application.state, application.settings
        self.db, self.relay = self.state.connection, application.relay
        from dataclasses import replace
        raw = self.state.get_meta('shared_config')
        if raw:
            value = validate_shared_config(json.loads(raw))
            self.settings = replace(self.settings, shared_hour=int(value['daily_time'][:2]),
                                    shared_minute=int(value['daily_time'][3:]))

    def configure(self, value):
        value = validate_shared_config(value)
        if self.settings.room_mode != 'shared':
            raise ValueError('Shared scheduling applies only to the shared room')
        from .curiosity import next_fixed_due
        due = next_fixed_due(int(time.time()), (int(value['daily_time'][:2]),),
                             minute_offsets=(int(value['daily_time'][3:]),)) if value['enabled'] else 0
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES('shared_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),))
            self.db.execute("INSERT INTO metadata VALUES('shared_due_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(due),))
        return value

    def active(self):
        return self.db.execute("SELECT * FROM shared_sessions WHERE status='active'").fetchone()

    def snapshot(self):
        row = self.db.execute('SELECT * FROM shared_sessions ORDER BY rowid DESC LIMIT 1').fetchone()
        return {'mode': self.settings.room_mode, 'session': dict(row) if row else None,
                'next_due_at': self.state.get_int('shared_due_at'),
                'daily_time': f'{self.settings.shared_hour:02}:{self.settings.shared_minute:02}',
                'check_every': self.settings.shared_check_every,
                'max_messages': self.settings.shared_max_messages}

    def tick(self):
        if self.settings.participant_scheduling:
            return False  # Encounters owns timing in every room.
        from .quiet_hours import QuietHours
        if QuietHours(self.state, self.settings).blocked():
            return False
        if self.settings.room_mode != 'shared':
            return False
        raw = self.state.get_meta('shared_config')
        if raw and not json.loads(raw)['enabled']:
            return False
        from .curiosity import next_fixed_due
        now = int(time.time())
        due = self.state.get_int('shared_due_at')
        if not due:
            self.state.set_meta('shared_due_at', next_fixed_due(now, (self.settings.shared_hour,),
                                minute_offsets=(self.settings.shared_minute,)))
            return False
        if due > now:
            return False
        # Persist the due key before advancing: retries recover the same command.
        date = time.strftime('%Y-%m-%d', time.localtime(due))
        already_scheduled = self.db.execute('SELECT 1 FROM commands WHERE idempotency_key=?',
                                            ('shared-daily:' + date,)).fetchone()
        if now <= due + 180 and not self.state.is_paused() and self.active() is None and not already_scheduled:
            self.state.commands.enqueue(CommandType.START_SHARED, {'scheduled_at': due}, idempotency_key='shared-daily:' + date)
        self.state.set_meta('shared_due_at', next_fixed_due(now, (self.settings.shared_hour,),
                            minute_offsets=(self.settings.shared_minute,)))
        return True

    def start(self, session_id: str, *, opener: str | None = None):
        from .quiet_hours import QuietHours
        if opener and not self.settings.initiative_enabled:
            return {'outcome': 'disabled'}
        if QuietHours(self.state, self.settings).blocked():
            return {'outcome': 'quiet_hours'}
        if self.settings.room_mode != 'shared' or len(self.settings.participants) != 2:
            raise ValueError('This room is not configured for companion sessions')
        if self.active() or self.db.execute('SELECT 1 FROM shared_sessions WHERE id=?', (session_id,)).fetchone():
            return {'outcome': 'already_started'}
        if self.state.is_paused():
            return {'outcome': 'paused'}
        if self.state.get_meta('bootstrapped') != '1':
            self.relay.bootstrap()
        else:
            self.relay.synchronize_context()
        self.state.turns.require_reconciled(self.settings.group_id)
        if self.state.is_paused():
            return {'outcome': 'paused'}
        if QuietHours(self.state, self.settings).blocked():
            return {'outcome': 'quiet_hours'}
        if self.state.connection.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')").fetchone():
            raise ReconciliationError('Resolve outbound delivery before starting a shared session')
        tail = self.state.get_meta('shared_history_tail')
        latest = self.state.turns.message(tail) if tail else self.state.latest_message()
        if latest is None:
            raise ValueError('Add an initial message in Kindroid before starting this room')
        # Continue after the latest known participant when possible.
        speaker = self._speaker(latest)
        next_speaker = 1 - speaker if speaker is not None else 0
        from .encounters import Encounters
        Encounters(self.state, self.settings).retry_later()
        if opener:
            if not self.state.record_initiative_check(
                    self.state.initiative_limit(self.settings.initiative_max_checks_24h), 'started'):
                return {'outcome': 'daily_budget'}
            outbox = self.relay._send_user_message(opener, request_kindroid_reply=False)
            latest = self.state.turns.message('local-user:' + outbox)
        now = int(time.time())
        with self.db:
            self.db.execute('''INSERT INTO shared_sessions
                (id,status,started_at,next_speaker,next_turn_at,anchor_id)
                VALUES(?,'active',?,?,?,?)''', (session_id, now, next_speaker, now, latest.id))
            self.db.execute('UPDATE messages SET processed=1')
            self.db.execute("DELETE FROM metadata WHERE key='shared_result_outbox'")
            self.db.execute("INSERT INTO metadata VALUES('shared_history_seen',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (tail or '',))
            self.db.execute("INSERT INTO metadata VALUES('activity_session',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (session_id,))
        self.state.activity.add('session_started', ActivitySeverity.INFO, 'Companion session started', details={'session_id': session_id})
        return {'outcome': 'started', 'session_id': session_id}

    def end(self, reason='operator'):
        session = self.active()
        with self.db:
            self.db.execute("UPDATE shared_sessions SET status='ended',ended_at=?,reason=? WHERE status='active'",
                            (int(time.time()), reason))
        self.state.activity.add('shared_session_ended', ActivitySeverity.INFO,
                                'Companion session ended', details={'outcome': reason, 'session_id': session['id'] if session else ''})
        self.state.set_meta('activity_session', '')
        from .encounters import Encounters
        Encounters(self.state, self.settings).retry_later()
        return {'outcome': 'ended', 'reason': reason}

    def _speaker(self, message):
        if message.sender_type != 'ai' or message.message.lstrip().startswith('[CODEX]:'):
            return None
        for index, (ai, name) in enumerate(self.settings.participants):
            if message.sender == ai:
                return index
            if message.sender in {'', 'ai'} and message.display_name.casefold() == name.casefold():
                return index  # Conversational routing only, never action authorization.
        return None

    def _finish_turn(self, session, turn_id):
        message = self.state.turns.message(turn_id)
        with self.db:
            self.db.execute('''UPDATE shared_sessions SET pending_turn_id=NULL,anchor_id=?,
                next_speaker=1-next_speaker,message_count=message_count+1,next_turn_at=?
                WHERE id=? AND pending_turn_id=?''',
                (message.id, int(time.time()) + self.settings.shared_turn_seconds, session['id'], turn_id))
            self.db.execute('UPDATE messages SET processed=1 WHERE remote_id=?', (message.id,))

    def _adopt_history(self, session):
        """Integrate app participation once, without a send or a new model call."""
        tail = self.state.get_meta('shared_history_tail')
        if not tail or tail == self.state.get_meta('shared_history_seen'):
            return False
        anchor = self.db.execute('SELECT kindroid_id FROM messages WHERE remote_id=?', (session['anchor_id'],)).fetchone()
        # A new direct reply/post after this snapshot is newer than its tail.
        # Consume the old snapshot without rewinding that locally recorded effect.
        if anchor is None or anchor['kindroid_id'] is None:
            self.state.set_meta('shared_history_seen', tail)
            return False
        external_count = self.db.execute('''SELECT COUNT(*) FROM messages m
            WHERE m.processed=0 AND m.sender_type='ai' AND m.kindroid_id IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM generation_turns t WHERE t.id=m.remote_id)''').fetchone()[0]
        if tail == session['anchor_id'] and not external_count:
            self.state.set_meta('shared_history_seen', tail)
            return False
        self.state.turns.require_reconciled(self.settings.group_id)
        if self.db.execute("SELECT 1 FROM outbox WHERE status IN ('sending','uncertain')").fetchone():
            raise ReconciliationError('Inspect uncertain outbound delivery before adopting updated shared history')
        message = self.state.turns.message(tail)
        speaker = self._speaker(message)
        # The other companion follows an app-generated companion response; a
        # human interjection retains the next companion's turn. No impersonation.
        next_speaker = 1 - speaker if speaker is not None else session['next_speaker']
        attention = self.db.execute('SELECT * FROM shared_attention WHERE id=?', (session['attention_id'],)).fetchone()
        with self.db:
            if attention is not None:
                # An unsent stale answer must not be delivered to the old context.
                # Sending/uncertain effects were excluded above; sent ones remain
                # delivered and their source/proposal linkage is preserved.
                if attention['outbox_id']:
                    self.db.execute("UPDATE outbox SET status='cancelled',updated_at=? WHERE id=? AND status='pending'",
                                    (int(time.time()), attention['outbox_id']))
                self.db.execute("UPDATE shared_attention SET status='superseded' WHERE id=?", (attention['id'],))
            self.db.execute('''UPDATE shared_sessions SET anchor_id=?,next_speaker=?,
                attention_id=NULL,assistance_active=0,message_count=message_count+? WHERE id=?''',
                (tail, next_speaker, external_count, session['id']))
            self.db.execute('UPDATE messages SET processed=1 WHERE kindroid_id IS NOT NULL')
            self.db.execute("INSERT INTO metadata VALUES('shared_history_seen',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (tail,))
            self.state.activity.add('shared_context_changed', ActivitySeverity.INFO,
                'Shared session continued from updated Kindroid conversation',
                details={'message_id': tail, 'outcome': 'external_companion' if speaker is not None else 'external_message'})
        return True

    def step(self):
        from .quiet_hours import QuietHours
        if QuietHours(self.state, self.settings).blocked():
            return False
        session = self.active()
        if session is None or self.state.is_paused():
            return False
        if self.state.get_meta('activity_session') != session['id']:
            self.state.set_meta('activity_session', session['id'])
        now = int(time.time())
        # Reconcile interrupted effects even when the session would otherwise end.
        if session['pending_turn_id']:
            turn = self.db.execute('SELECT * FROM generation_turns WHERE id=?', (session['pending_turn_id'],)).fetchone()
            if turn['status'] not in {'received', 'reconciled'}:
                self.relay.synchronize_context()
            self._finish_turn(session, turn['id'])
            return True
        result_outbox = self.state.get_meta('shared_result_outbox')
        if result_outbox:
            delivered = self.db.execute('SELECT status FROM outbox WHERE id=?', (result_outbox,)).fetchone()
            if delivered is None or delivered['status'] != 'sent':
                raise ReconciliationError('Approved result delivery needs inspection')
            with self.db:
                self.db.execute('UPDATE shared_sessions SET anchor_id=? WHERE id=?', ('local-user:' + result_outbox, session['id']))
                self.db.execute("DELETE FROM metadata WHERE key='shared_result_outbox'")
            return True
        if session['message_count'] >= self.settings.shared_max_messages or now >= session['started_at'] + self.settings.shared_max_seconds:
            self.end('session_limit')
            return True
        if self._adopt_history(session):
            return True
        if session['attention_id']:
            self._attention(session, session['attention_id'])
            return True
        message = self.state.turns.message(session['anchor_id'])
        is_companion = self._speaker(message) is not None
        needs_help = is_companion and ('codex' in message.message.casefold() or session['assistance_active'])
        farewell = is_companion and has_farewell_cue(message.message)
        checkpoint = session['message_count'] - session['checked_count'] >= self.settings.shared_check_every
        attention_id = session['id'] + ':' + message.id
        seen = self.db.execute('SELECT 1 FROM shared_attention WHERE id=?', (attention_id,)).fetchone()
        if session['message_count'] and is_companion and not seen and (needs_help or farewell or checkpoint):
            if needs_help:
                trigger = 'mention' if 'codex' in message.message.casefold() else 'assistance_followup'
                attention_status = 'help'
            else:
                trigger = 'farewell' if farewell else 'periodic'
                attention_status = 'farewell' if farewell else 'ending'
            with self.db:
                self.db.execute('INSERT INTO shared_attention VALUES(?,?,?, ?,NULL,NULL)',
                    (attention_id, session['id'], message.id, attention_status))
                self.db.execute('UPDATE shared_sessions SET attention_id=? WHERE id=?', (attention_id, session['id']))
                self.state.activity.add('shared_check_requested', ActivitySeverity.INFO,
                    'Shared conversation check requested: ' + trigger,
                    details={'message_id': message.id, 'reason': trigger,
                             'turn_count': session['message_count']}, correlation_id=attention_id)
            return True
        if session['next_turn_at'] > now:
            return False
        if self.state.is_paused():
            return False
        ai, name = self.settings.participants[session['next_speaker']]
        with self.db:
            turn_id = self.state.turns.begin_after(self.settings.group_id, ai, name,
                session['anchor_id'], self.state.get_int('cursor_timestamp'))
            self.db.execute('UPDATE shared_sessions SET pending_turn_id=? WHERE id=?', (turn_id, session['id']))
        try:
            with self.relay.phase_context(WorkerPhase.WAITING_FOR_KINDROID, 'Waiting for companion response'):
                body = self.relay.kindroid.generate_ai_response(self.settings.group_id, ai)
            self.state.turns.complete(turn_id, body)
        except EmptyGeneratedReply:
            self.state.turns.mark_uncertain(turn_id)
            try:
                self.relay.synchronize_context()  # One budgeted recovery; never regenerate.
            except HistoryCooldownError:
                raise
            except Exception:
                self.state.pause('Shared reply requires history recovery; inspect before resuming')
                raise
        except Exception:
            self.state.turns.mark_uncertain(turn_id)
            self.state.pause('Shared generation outcome is uncertain; inspect history before resuming')
            raise ReconciliationError('Shared generation outcome is uncertain') from None
        self._finish_turn(session, turn_id)
        return True

    def _attention(self, session, attention_id):
        row = self.db.execute('SELECT * FROM shared_attention WHERE id=?', (attention_id,)).fetchone()
        message = self.state.turns.message(row['source_message_id'])
        # A manual sync can replace the old source even after a decision was
        # saved. Check before deciding OR delivering a previously saved answer.
        if self._adopt_history(session):
            return
        if row['decision_json'] is None:
            ending_only = row['status'] in {'ending', 'farewell'}
            # Sync only at attention boundaries (or empty-body recovery), never
            # on an independent polling timer in the shared room.
            if (self.db.execute('SELECT 1 FROM messages WHERE kindroid_id IS NULL LIMIT 1').fetchone()
                    or time.time() - self.state.get_int('context_synced_at') >= self.settings.poll_seconds):
                self.relay.synchronize_context()
            if self._adopt_history(session):
                return
            # Invocations are read-only. Persist the decision before public effects.
            with self.relay._agent_session(message.id) as agent:
                decision = self.relay._call_codex('shared attention',
                    lambda: agent.shared_attention(ending_only=ending_only,
                                                   assistance_active=bool(session['assistance_active'])),
                    details={'message_id': message.id}, correlation_id=message.id)
            timing_keys = {'next_encounter', 'scheduling_source_id'}
            if (not isinstance(decision, dict) or set(decision) - timing_keys != {'reply','end_session','end_reason','await_reply','action'}
                    or not isinstance(decision['reply'], str)
                    or type(decision['end_session']) is not bool or type(decision['await_reply']) is not bool
                    or not isinstance(decision['end_reason'], str)
                    or decision['end_reason'] not in END_REASONS
                    or END_REASONS[decision['end_reason']] != decision['end_session']):
                raise ValueError('Invalid shared attention decision')
            if ending_only:
                decision.update(reply='', await_reply=False, action=None)
            if decision['end_session']:
                decision['await_reply'] = False
                if any(mark in decision['reply'] for mark in '?\uff1f\u061f'):
                    decision['reply'] = ''
            action = decision['action']
            if action is not None and (not isinstance(action, dict) or set(action) != {'summary','instruction','risk'}
                    or action['risk'] not in {'low','medium','high'}
                    or any(not isinstance(action[k], str) or not 1 <= len(action[k]) <= 8000 for k in ('summary','instruction'))):
                raise ValueError('Invalid assistance proposal')
            # Save proposal and decision atomically; never repeat a proposal on restart.
            with self.db:
                if action:
                    self.db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,\'pending\',NULL,?,?)',
                        (uuid.uuid4().hex, message.id, action['summary'], action['instruction'], action['risk'], int(time.time()), int(time.time())))
                self.db.execute("UPDATE shared_attention SET decision_json=?,status='decided' WHERE id=?",
                                (json.dumps(decision), attention_id))
                outcome = 'end' if decision['end_session'] else 'continue'
                self.state.activity.add('shared_check_decided', ActivitySeverity.INFO,
                    'Shared conversation check: ' + outcome + ' (' + decision['end_reason'] + ')',
                    details={'message_id': message.id, 'outcome': outcome,
                             'reason': decision['end_reason'], 'turn_count': session['message_count']},
                    correlation_id=attention_id)
        else:
            decision = json.loads(row['decision_json'])
        if self.settings.participant_scheduling and decision.get('next_encounter') is not None:
            from .encounters import Encounters
            encounters = Encounters(self.state, self.settings)
            source_id = decision.get('scheduling_source_id')
            # Only current-session context actually shown to the model may schedule.
            sources = {m.id: m for m in self.state.recent_messages(50)}
            source = sources.get(source_id) if isinstance(source_id, str) else None
            stamp = source.timestamp if source else 0
            if stamp > 10_000_000_000:
                stamp //= 1000
            try:
                if source is None or stamp < session['started_at']:
                    raise ValueError('Scheduling source is outside this session')
                encounters.choose(source, decision['next_encounter'])
            except (ValueError, TypeError, OverflowError):
                encounters.retry_later()
        anchor = session['anchor_id']
        if self.state.is_paused():
            return
        if decision['reply'].strip():
            outbox = self.relay._send_user_message(self.relay._limit_reply(decision['reply']), source_message_id=message.id,
                request_kindroid_reply=False, outbox_created=lambda oid: self._bind_attention(attention_id, oid))
            anchor = 'local-user:' + outbox
        with self.db:
            self.db.execute("UPDATE shared_attention SET status='completed' WHERE id=?", (attention_id,))
            next_speaker = self._speaker(message) if decision['await_reply'] else session['next_speaker']
            self.db.execute('''UPDATE shared_sessions SET attention_id=NULL,checked_count=message_count,
                assistance_active=?,anchor_id=?,next_speaker=? WHERE id=?''',
                (int(decision['await_reply']), anchor, next_speaker, session['id']))
            if decision['end_session']:
                # Commit termination with the completed decision, so a crash
                # cannot lose the stop between decision cleanup and session end.
                self.db.execute("UPDATE shared_sessions SET status='ended',ended_at=?,reason='natural_end' WHERE id=?",
                                (int(time.time()), session['id']))
        if decision['end_session']:
            from .encounters import Encounters
            Encounters(self.state, self.settings).retry_later()
            self.state.activity.add('shared_session_ended', ActivitySeverity.INFO,
                'Companion session ended', details={'outcome': 'natural_end', 'session_id': session['id']})
            self.state.set_meta('activity_session', '')

    def _bind_attention(self, attention_id, outbox):
        with self.db:
            self.db.execute('UPDATE shared_attention SET outbox_id=? WHERE id=?', (outbox, attention_id))


def validate_shared_config(value):
    import re
    if (not isinstance(value, dict) or set(value) != {'enabled', 'daily_time'}
            or type(value['enabled']) is not bool or not isinstance(value['daily_time'], str)
            or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value['daily_time'])):
        raise ValueError('Shared schedule requires enabled and a daily HH:MM time')
    return dict(value)
