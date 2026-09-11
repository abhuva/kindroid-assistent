"""Operator-only abandonment of an uncaptured shared generation; never retry it."""
from __future__ import annotations

import time

from .activity import ActivitySeverity
from .command_queue import CommandType, validate_payload
from .kindroid_api import KindroidAPIError
from .relay import HistoryCooldownError
from .state import HistoryBudgetError


def recovery_status(state, settings) -> dict:
    db = state.connection
    session = db.execute('SELECT * FROM shared_sessions ORDER BY rowid DESC LIMIT 1').fetchone()
    result = {'eligible': False, 'session_id': session['id'] if session else None,
              'turn_id': session['pending_turn_id'] if session else None}
    if settings.room_mode != 'shared':
        return {**result, 'reason': 'Recovery is available only in the shared companion room.'}
    if not state.is_paused():
        return {**result, 'reason': 'Pause this room before recovering its interrupted session.'}
    if not session or not session['pending_turn_id']:
        return {**result, 'reason': 'There is no interrupted shared generation to recover.'}
    if db.execute('SELECT 1 FROM shared_recoveries WHERE session_id=?', (session['id'],)).fetchone():
        return {**result, 'reason': 'This session was already recovered; resume and start a new conversation when ready.'}
    turn = db.execute('SELECT * FROM generation_turns WHERE id=?', (session['pending_turn_id'],)).fetchone()
    if (not turn or turn['group_id'] != settings.group_id or turn['outbox_id'] is not None
            or turn['status'] not in {'generating', 'uncertain'}
            or db.execute('SELECT 1 FROM messages WHERE remote_id=?', (turn['id'],)).fetchone()):
        return {**result, 'reason': 'Recovery requires a shared generation without captured reply text.'}
    if (db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')").fetchone()
            or db.execute('SELECT 1 FROM messages WHERE kindroid_id IS NULL').fetchone()
            or db.execute("SELECT 1 FROM active_generation_turns WHERE id!=? AND status!='reconciled'", (turn['id'],)).fetchone()
            or session['attention_id'] or state.get_meta('shared_result_outbox')):
        return {**result, 'reason': 'Other unresolved replies or deliveries need inspection before this recovery.'}
    return {**result, 'eligible': True, 'reason': 'End this interrupted relay session and adopt fresh Kindroid history.'}


def recover_shared(app, command_id: str, payload: dict) -> dict:
    payload = validate_payload(CommandType.RECOVER_SHARED, payload)
    state, settings = app.state, app.settings
    db = state.connection
    if settings.room_mode != 'shared':
        raise ValueError('Recovery is available only in the shared companion room')
    # Also handles crash after committing recovery but before command completion,
    # and a second operator request for the same already-recovered session.
    previous = db.execute('SELECT * FROM shared_recoveries WHERE session_id=?', (payload['session_id'],)).fetchone()
    if previous:
        if previous['generation_id'] != payload['turn_id']:
            raise ValueError('Recovery request no longer matches this session')
        return {'outcome': 'already_recovered', 'paused': state.is_paused(),
                'session_id': previous['session_id']}

    def check_target():
        status = recovery_status(state, settings)
        if (status['session_id'] != payload['session_id'] or status['turn_id'] != payload['turn_id']):
            raise ValueError('The selected interrupted session changed; refresh before recovering')
        if not status['eligible']:
            raise ValueError(status['reason'])

    check_target()
    # No transaction over HTTP and no partial imports. An initial/full cursor
    # asks for the current provider history, not a stale pinned local window.
    try:
        history = app.relay.fetch_history(None)
    except HistoryCooldownError:
        return {'outcome': 'recovery_blocked', 'reason': 'History API cooldown; recovery did not change this session.'}
    except HistoryBudgetError:
        return {'outcome': 'recovery_blocked', 'reason': 'History allowance exhausted; recovery did not change this session.'}
    except KindroidAPIError:
        return {'outcome': 'recovery_blocked', 'reason': 'History request failed; recovery did not change this session.'}
    tail = next((message for message in reversed(history) if message.message.strip()), None)
    if tail is None:
        return {'outcome': 'recovery_blocked', 'reason': 'Fetched history has no conversation message to continue from.'}
    now = int(time.time())
    with db:
        db.execute('BEGIN IMMEDIATE')
        check_target()  # Pause/target/other effects could change during the read.
        added = 0
        anchor = None
        for message in history:
            known = db.execute('SELECT * FROM messages WHERE group_id=? AND kindroid_id=?',
                               (settings.group_id, message.id)).fetchone()
            if known:
                # This action abandons an uncaptured turn; it is not a silent
                # rewrite of earlier source evidence, identities or approvals.
                if known['body'].strip() != message.message.strip():
                    raise ValueError('Previously confirmed message text changed; inspect it before recovery')
                local_id = known['remote_id']
            else:
                local_id = message.id
                db.execute('''INSERT INTO messages(remote_id,group_id,sender,sender_type,
                    display_name,timestamp,body,processed,received_at,kindroid_id)
                    VALUES(?,?,?,?,?,?,?,1,?,?)''',
                    (local_id, settings.group_id, message.sender, message.sender_type,
                     message.display_name, message.timestamp, message.message, now, message.id))
                added += 1
            if message.id == tail.id:
                anchor = local_id
        db.execute('''INSERT INTO shared_recoveries VALUES(?,?,?,?,?,?,'operator')''',
                   (command_id, payload['session_id'], payload['turn_id'], anchor, now, len(history)))
        # Keep the pending generation ID, old anchor, counts and attention audit
        # on the ended session. Never mark that generation as reconciled.
        db.execute('''UPDATE shared_sessions SET status='ended',ended_at=COALESCE(ended_at,?),
            reason=CASE WHEN status='active' THEN 'operator_recovery' ELSE reason END
            WHERE id=?''', (now, payload['session_id']))
        db.execute('UPDATE messages SET processed=1 WHERE group_id=?', (settings.group_id,))
        metadata = {'shared_history_tail': anchor, 'shared_history_seen': anchor,
                    'cursor_timestamp': str(max(m.timestamp for m in history)),
                    'context_synced_at': str(now), 'paused': '1',
                    'pause_reason': 'Interrupted shared session recovered; resume and start a new conversation when ready'}
        for key, value in metadata.items():
            db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        state.activity.add('shared_session_recovered', ActivitySeverity.WARNING,
            'Operator ended interrupted relay session and adopted Kindroid history; generation attribution remains unknown',
            details={'command_id': command_id, 'outcome': 'operator_abandoned', 'message_id': anchor},
            correlation_id=command_id)
    return {'outcome': 'recovered', 'session_id': payload['session_id'],
            'messages_added': added, 'paused': True}
