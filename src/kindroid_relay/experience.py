"""One selected curiosity, one bounded public lookup, one optional reflection."""
from __future__ import annotations

import json
import time

from .activity import ActivitySeverity, WorkerPhase
from .agency import AgencyStore
from .discovery import DiscoveryStore
from .memory import MemoryStore


class ExperienceStore:
    def __init__(self, state, group: str, name: str = 'Hannah'):
        self.state, self.db, self.group, self.name = state, state.connection, group, name

    def status(self, session: str, status: str, note: str) -> None:
        with self.db:
            self.db.execute('UPDATE exploration_experiences SET status=?,note=? WHERE session_id=? AND group_id=?',
                            (status, note, session, self.group))
            self.state.activity.add('experience_updated', ActivitySeverity.INFO,
                f'Exploration experience: {status.replace("_", " ")}. {note}', details={'outcome': status})

    def authorize_invitation(self, session: str) -> None:
        if DiscoveryStore(self.state, self.group).config()['follow_through']:
            with self.db:
                self.db.execute('''INSERT INTO exploration_experiences
                    (session_id,group_id,status,authorized_by,created_at) VALUES (?,?,'eligible',?,?)''',
                    (session, self.group, session, int(time.time())))

    def select(self, row, decision: dict) -> None:
        # Called inside the interpretation transaction; old sessions have no opt-in row.
        enabled = self.db.execute("SELECT 1 FROM exploration_experiences WHERE session_id=? AND group_id=? AND status='eligible'",
                                  (row['session_id'], self.group)).fetchone()
        if enabled and decision['classification'] in {'decline', 'defer'}:
            self.db.execute("UPDATE exploration_experiences SET status='cancelled',note='Declined or deferred; no work started' WHERE session_id=?", (row['session_id'],))
        if not enabled or decision['classification'] not in {'choose', 'alternative'}:
            return
        candidate = decision['linked_candidate_ids'][0]
        material = DiscoveryStore(self.state, self.group).public_material(candidate)
        # Never send private memory or Hannah's alternative text to a public search provider.
        topic = (material.get('query') or material.get('title', ''))[:180] if material else ''
        self.db.execute('''UPDATE exploration_experiences SET candidate_id=?,response_step=?,topic=?,status=?,note=?
            WHERE session_id=? AND group_id=?''', (candidate, row['step'], topic,
            'queued' if topic else 'needs_public_topic',
            'One selected candidate; waits for invitation follow-through to finish' if topic else
            'A local public-topic approval is needed; private conversation was not exported', row['session_id'], self.group))

    def approve_topic(self, session: str, topic: str, command_id: str) -> dict:
        with self.db:
            count = self.db.execute('''UPDATE exploration_experiences SET topic=?,authorized_by=?,status='queued',note='Public topic approved locally'
                WHERE session_id=? AND group_id=? AND status='needs_public_topic' ''',
                (topic, command_id, session, self.group)).rowcount
            if count != 1:
                raise ValueError('Experience is not waiting for a public topic; no replay allowed')
        return {'session_id': session, 'status': 'queued'}

    def recover(self) -> bool:
        with self.db:
            self.db.execute("UPDATE exploration_experiences SET status='failed',note='Lookup interrupted; not retried' WHERE group_id=? AND status='researching'", (self.group,))
            return bool(self.db.execute("UPDATE exploration_experiences SET status='uncertain',note='Result delivery interrupted; inspect outbox' WHERE group_id=? AND status='sending'", (self.group,)).rowcount)

    def reconcile(self) -> None:
        with self.db:
            self.db.execute('''UPDATE exploration_experiences SET status='cancelled',note='Invitation closed or interpretation stopped; no experience started'
                WHERE group_id=? AND status IN ('eligible','queued','ready','needs_public_topic') AND
                (EXISTS(SELECT 1 FROM exploration_sessions s WHERE s.id=session_id AND s.status='closed') OR
                 EXISTS(SELECT 1 FROM exploration_responses r WHERE r.session_id=exploration_experiences.session_id
                        AND r.status IN ('failed','needs_review')))''', (self.group,))
        rows = self.db.execute('''SELECT e.*,s.status AS session_status FROM exploration_experiences e
            JOIN exploration_sessions s ON s.id=e.session_id WHERE e.group_id=? AND e.outbox_id IS NOT NULL
            AND (e.status IN ('sending','uncertain','awaiting_reflection') OR EXISTS (
                SELECT 1 FROM generation_turns t JOIN messages m ON m.remote_id=t.id
                WHERE t.outbox_id=e.outbox_id AND m.processed=0))''', (self.group,)).fetchall()
        for row in rows:
            source = self.db.execute('''SELECT m.*,t.ai_id FROM generation_turns t JOIN messages m ON m.remote_id=t.id
                WHERE t.outbox_id=? AND m.group_id=?''', (row['outbox_id'], self.group)).fetchone()
            if source is None:
                continue
            with self.db:
                self.db.execute('UPDATE messages SET processed=1 WHERE remote_id=?', (source['remote_id'],))
                if not source['kindroid_id'] or row['reflection_message_id']:
                    continue
                self.db.execute('UPDATE exploration_experiences SET reflection_message_id=? WHERE session_id=?',
                                (source['remote_id'], row['session_id']))
                if not AgencyStore(self.state, self.group, self.name).trusted_response_speaker(source, source['ai_id']):
                    self.db.execute("UPDATE exploration_experiences SET status='needs_review',note='Reflection attribution is uncertain; no follow-up' WHERE session_id=?", (row['session_id'],))
                    continue
                statement = MemoryStore(self.db, self.group).save({'message_id': source['remote_id'],
                    'quote': source['body'][:2000], 'start': 0, 'kind': 'observation',
                    'interpretation': 'Companion response to a bounded public-source experience; no adoption or authority inferred.',
                    'temporal_scope': 'after this exploration experience', 'entities': []},
                    origin='system_selection', version='exploration-experience-v1')
                self.db.execute("UPDATE exploration_experiences SET reflection_statement_id=?,status='complete',note='Reflection recorded; no automatic promotion or further prompt' WHERE session_id=?",
                                (statement, row['session_id']))


class Experiences:
    def __init__(self, app, public=None):
        from .public_sources import PublicSources
        self.app, self.public = app, public or PublicSources()
        self.store = ExperienceStore(app.state, app.settings.group_id, app.settings.kindroid_display_name)

    def gate(self) -> bool:
        app = self.app
        if (not app.settings.exploration_enabled or not DiscoveryStore(app.state, app.settings.group_id).config()['follow_through']
                or app.state.is_paused() or app.relay._inside_quiet_hours() or app.state.pending_ai_messages()):
            return False
        from .curiosity import inside_quiet
        from .schedule import saved_schedule
        daily = saved_schedule(app.state, app.settings.group_id)
        if daily is not None:
            if not daily['enabled'] or time.localtime().tm_hour in daily['dead_hours']:
                return False
        elif inside_quiet(int(time.time()), app.settings.curiosity_quiet_hours):
            return False
        db = app.state.connection
        return not (db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')").fetchone()
            or db.execute("SELECT 1 FROM active_generation_turns WHERE status!='reconciled'").fetchone())

    def advance(self) -> bool:
        app, store = self.app, self.store
        store.reconcile()
        if not self.gate():
            return False
        row = store.db.execute('''SELECT e.*,r.outbox_id AS acknowledgement FROM exploration_experiences e
            JOIN exploration_responses r ON r.session_id=e.session_id AND r.step=e.response_step
            JOIN exploration_sessions s ON s.id=e.session_id
            WHERE e.group_id=? AND e.status IN ('queued','ready') AND r.status='complete'
            AND s.status!='closed' ORDER BY e.created_at,e.session_id LIMIT 1''', (store.group,)).fetchone()
        if row is None:
            return False
        session = row['session_id']
        response = app.exploration.store.row(session, row['response_step'])
        source = app.exploration.store.source(response)
        if (not source or app.exploration.store.fingerprint(source) != response['source_hash']
                or not app.exploration.store.trusted_speaker(source, session)):
            store.status(session, 'cancelled', 'Choice source or attribution changed; no experience sent')
            return True
        evidence = store.db.execute('''SELECT m.status,c.status AS candidate_status FROM exploration_responses r
            JOIN memory_statements m ON m.id=r.statement_id JOIN passing_candidates c ON c.id=?
            WHERE r.session_id=? AND r.step=?''', (row['candidate_id'], session, row['response_step'])).fetchone()
        if not evidence or evidence['status'] != 'active' or evidence['candidate_status'] != 'active':
            store.status(session, 'cancelled', 'Choice was revised or candidate archived; no result sent')
            return True
        now = int(time.time())
        if now - row['created_at'] > 86400:
            store.status(session, 'cancelled', 'Experience expired after 24 hours; no catch-up or stale prompt')
            return True
        if row['status'] == 'queued':
            limit = DiscoveryStore(app.state, store.group).config()['research_limit']
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                used = store.db.execute('SELECT COUNT(*) FROM exploration_experiences WHERE group_id=? AND started_at>?', (store.group, now - 86400)).fetchone()[0]
                if used >= limit:
                    return False
                claimed = store.db.execute("UPDATE exploration_experiences SET status='researching',started_at=? WHERE session_id=? AND status='queued'", (now, session)).rowcount
            if not claimed:
                return False
            try:
                with app.phase_context(WorkerPhase.POLLING, 'Looking up public references (bounded, no model tools)'):
                    sources = self.public.research(row['topic'])
                with store.db:
                    store.db.execute("UPDATE exploration_experiences SET result_json=?,status='ready',note='Public reference excerpts ready; not a comprehensive investigation' WHERE session_id=?",
                                     (json.dumps(sources), session))
                store.status(session, 'ready', 'Public reference lookup completed; result retained locally')
            except Exception:
                store.status(session, 'failed', 'Public lookup failed or exceeded its budget; not retried')
            return True  # Return at the next safe boundary, never stack immediate sends.
        latest = store.db.execute('SELECT timestamp,received_at FROM messages WHERE group_id=? ORDER BY timestamp DESC,rowid DESC LIMIT 1', (store.group,)).fetchone()
        stamp = latest['timestamp'] // 1000 if latest and latest['timestamp'] > 10_000_000_000 else latest['timestamp'] if latest else now
        if app.settings.initiative_idle_seconds > 0 and (stamp <= 0 or max(stamp, latest['received_at'] if latest else now) + app.settings.initiative_idle_seconds > now):
            return False
        sources = json.loads(row['result_json'])
        body = ('A small follow-through on the curiosity you selected: ' + row['topic'] +
                '. These are Wikipedia reference excerpts (CC BY-SA), not a comprehensive investigation or verified current-news report.\n\n' +
                '\n\n'.join(f'{s["title"]}: “{s["excerpt"]}”\n{s["url"]}' for s in sources) +
                '\n\nWhat, if anything, caught your attention—or did this not resonate? You can leave it here. Nothing has been published or adopted as a preference.')
        if len(body) + len(app.settings.codex_message_prefix) + 1 > app.settings.max_reply_chars:
            store.status(session, 'needs_review', 'Result exceeds reply limit; retained locally, not truncated or sent')
            return True
        if not self.gate():
            return False
        def bind(oid):
            with store.db:
                changed = store.db.execute("UPDATE exploration_experiences SET outbox_id=?,status='sending' WHERE session_id=? AND status='ready'", (oid, session)).rowcount
                if changed != 1:
                    raise ValueError('Experience result already attempted')
        try:
            if not row['acknowledgement']:
                store.status(session, 'needs_review', 'Missing acknowledgement source link; no result sent')
                return True
            oid = app.relay._send_user_message(body, source_message_id='local-user:' + row['acknowledgement'],
                                               request_kindroid_reply=False, outbox_created=bind)
            incoming = app.relay._request_ai_turn(outbox_id=oid, allow_user=True)
            if incoming:
                app.state.mark_message_processed(incoming.id)
            store.status(session, 'awaiting_reflection' if incoming else 'complete',
                         'Optional reflection awaits history' if incoming else 'No reflection requested again; silence is valid')
            store.reconcile()
        except Exception:
            store.status(session, 'uncertain', 'Result delivery or reflection generation uncertain; inspect before resuming')
            app.state.pause('Exploration experience delivery uncertain; inspect its outbox before resuming')
        return True
