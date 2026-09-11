"""Retire only safely finished/never-sent invitation work, without erasing audit."""
from .activity import ActivitySeverity


def retire_safe_work(state, settings):
    if not settings.participant_scheduling:
        return
    db = state.connection
    # Never interpret removal of a feature as confirmation of an external effect.
    if state.turns.has_uncertain() or db.execute(
        "SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain')"
    ).fetchone():
        return
    with db:
        rows = db.execute("""SELECT s.id FROM exploration_sessions s
            WHERE s.group_id=? AND (s.status IN ('observed','silent')
                OR (s.status IN ('prepared','interrupted') AND s.outbox_id IS NULL))
            AND NOT EXISTS (SELECT 1 FROM exploration_responses r WHERE r.session_id=s.id
                AND r.status IN ('sending','uncertain'))
            AND NOT EXISTS (SELECT 1 FROM exploration_experiences e WHERE e.session_id=s.id
                AND e.status IN ('researching','sending','uncertain'))""", (settings.group_id,)).fetchall()
        for row in rows:
            db.execute("UPDATE exploration_sessions SET status='closed' WHERE id=?", (row['id'],))
            db.execute("""UPDATE exploration_experiences SET status='cancelled',
                note='Invitation workflow retired; records retained'
                WHERE session_id=? AND status IN ('eligible','queued','ready','needs_public_topic','awaiting_reflection')""",
                (row['id'],))
        if rows:
            state.activity.add('exploration_retired', ActivitySeverity.INFO,
                'Retired safe exploration follow-ups; existing evidence retained', details={'outcome': 'retired'})
