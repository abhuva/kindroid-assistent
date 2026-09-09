"""Durable direct replies and conservative history reconciliation.

messages.remote_id is the legacy name for the stable local key. kindroid_id is
the remote alias; assigning it never changes source-message or approval links.
"""
from __future__ import annotations

import sqlite3
import time
import uuid

from .models import ChatMessage


class ReconciliationError(RuntimeError):
    """History cannot safely be associated with a locally recorded effect."""


class ReconciliationPending(RuntimeError):
    """A successful effect is not visible in history yet; defer context work."""


class EmptyGeneratedReply(ReconciliationError):
    """The request succeeded but contained no usable reply text."""


class TurnStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def _insert_local(self, message_id: str, group_id: str, sender: str,
                      role: str, name: str, body: str, after: int) -> None:
        latest = self.connection.execute(
            "SELECT MAX(timestamp) FROM messages WHERE group_id = ?", (group_id,)
        ).fetchone()[0] or 0
        # Provisional observed time only; never used as the remote history cursor.
        scale = 1000 if latest > 10_000_000_000 else 1
        timestamp = max(latest + 1, int(time.time() * scale))
        self.connection.execute("""
            INSERT INTO messages(remote_id, group_id, sender, sender_type,
                display_name, timestamp, body, processed, received_at, sync_after)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, group_id, sender, role, name, timestamp, body,
              int(role != "ai"), int(time.time()), after))

    def record_sent(self, outbox_id: str, group_id: str, name: str, after: int) -> None:
        with self.connection:
            row = self.connection.execute("SELECT body FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
            self._insert_local("local-user:" + outbox_id, group_id, "user", "user", name, row["body"], after)
            self.connection.execute("UPDATE outbox SET status = 'sent', updated_at = ? WHERE id = ?",
                                    (int(time.time()), outbox_id))

    def begin(self, group_id: str, ai_id: str, name: str, outbox_id: str, after: int) -> str:
        turn_id = "local-ai:" + uuid.uuid4().hex
        with self.connection:
            anchor = self.connection.execute("SELECT remote_id FROM messages WHERE remote_id = ?", ("local-user:" + outbox_id,)).fetchone()
            if anchor is None:
                # Recovery of an outbox created before migration 3 must anchor
                # to its already synchronized message rather than create a copy.
                candidates = self.connection.execute("""
                    SELECT remote_id FROM messages WHERE group_id = ? AND sender_type = 'user'
                        AND body = (SELECT body FROM outbox WHERE id = ?)
                """, (group_id, outbox_id)).fetchall()
                if len(candidates) != 1:
                    raise ReconciliationError("Delivered message has no unique history anchor; synchronize before generation")
                anchor = candidates[0]
            self.connection.execute("""
                INSERT INTO generation_turns VALUES(?, ?, ?, ?, ?, ?, 'generating', ?, ?)
            """, (turn_id, group_id, ai_id, name, outbox_id, anchor[0], after, int(time.time())))
        return turn_id

    def for_outbox(self, outbox_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM generation_turns WHERE outbox_id = ?", (outbox_id,)).fetchone()

    def complete(self, turn_id: str, body: str) -> ChatMessage:
        if not body.strip():
            raise EmptyGeneratedReply("Kindroid generation returned an empty reply")
        with self.connection:
            row = self.connection.execute("SELECT * FROM generation_turns WHERE id = ?", (turn_id,)).fetchone()
            if row is None or row["status"] != "generating":
                raise ReconciliationError("Kindroid generation is not awaiting a response")
            self._insert_local(turn_id, row["group_id"], row["ai_id"], "ai", row["display_name"], body, row["after_timestamp"])
            self.connection.execute("UPDATE generation_turns SET status = 'received' WHERE id = ?", (turn_id,))
        return self.message(turn_id)

    def message(self, message_id: str) -> ChatMessage:
        row = self.connection.execute("SELECT * FROM messages WHERE remote_id = ?", (message_id,)).fetchone()
        return ChatMessage(row["remote_id"], row["sender"], row["sender_type"], row["display_name"], row["timestamp"], row["body"])

    def mark_uncertain(self, turn_id: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE generation_turns SET status = 'uncertain' WHERE id = ? AND status = 'generating'", (turn_id,))

    def has_uncertain(self) -> bool:
        return self.connection.execute("SELECT 1 FROM generation_turns WHERE status IN ('generating', 'uncertain') LIMIT 1").fetchone() is not None

    def sync_cursor(self, group_id: str, cursor: int) -> int:
        row = self.connection.execute("""
            SELECT MIN(value) FROM (
                SELECT sync_after AS value FROM messages WHERE group_id = ? AND kindroid_id IS NULL
                UNION ALL SELECT after_timestamp FROM generation_turns WHERE group_id = ? AND status != 'reconciled'
            )
        """, (group_id, group_id)).fetchone()
        return min(cursor, row[0]) if row[0] is not None else cursor

    def require_reconciled(self, group_id: str) -> None:
        if self.has_uncertain():
            raise ReconciliationError("Kindroid generation remains uncertain; synchronize history before continuing")
        if self.connection.execute("SELECT 1 FROM messages WHERE group_id = ? AND kindroid_id IS NULL LIMIT 1", (group_id,)).fetchone():
            raise ReconciliationPending("Locally recorded messages are not yet visible in Kindroid history; context work deferred")

    def expected_display_name(self, group_id: str, ai_id: str, configured: str) -> str:
        """Resolve the generic default using already confirmed group history.

        A real sender ID takes precedence. Role-only history can resolve the
        default 'Kindroid' label only when it contains one unambiguous AI name.
        Explicit configured names and multiple possible speakers stay strict.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT sender, display_name FROM messages "
            "WHERE group_id = ? AND sender_type = 'ai' AND kindroid_id IS NOT NULL",
            (group_id,),
        ).fetchall()
        identified = {row["display_name"] for row in rows if row["sender"] == ai_id}
        if len(identified) == 1:
            return next(iter(identified))
        if configured.casefold() != "kindroid":
            return configured
        # A conflicting concrete AI ID must never be treated as a role-only
        # observation of the selected participant.
        if any(row["sender"] not in {"", "ai"} for row in rows):
            return configured
        names = {row["display_name"].casefold(): row["display_name"]
                 for row in rows if row["display_name"].strip()}
        return next(iter(names.values())) if len(names) == 1 else configured

    def _same_ai(self, message: ChatMessage, turn: sqlite3.Row) -> bool:
        if message.sender_type != "ai":
            return False
        # Some history responses contain only a role in sender, not an AI ID.
        if message.sender not in {"", "ai"}:
            return message.sender == turn["ai_id"]
        expected = self.expected_display_name(
            turn["group_id"], turn["ai_id"], turn["display_name"]
        )
        return message.display_name.casefold() == expected.casefold()

    def _bind(self, local_id: str, remote: ChatMessage) -> None:
        self.connection.execute("""
            UPDATE messages SET kindroid_id = ?, sender = ?, sender_type = ?,
                display_name = ?, timestamp = ?, sync_after = NULL WHERE remote_id = ?
        """, (remote.id, remote.sender, remote.sender_type, remote.display_name, remote.timestamp, local_id))

    def reconcile(self, group_id: str, history: list[ChatMessage]) -> set[str]:
        """Match outbound anchors, then their AI replies, in one transaction.

        Ambiguous repeated text or interleaved participants stops reconciliation.
        No body-only global deduplication and no invented remote IDs.
        """
        bound: set[str] = set()
        with self.connection:
            known = {row[0] for row in self.connection.execute("SELECT kindroid_id FROM messages WHERE kindroid_id IS NOT NULL")}
            users = self.connection.execute("""
                SELECT * FROM messages WHERE group_id = ? AND kindroid_id IS NULL
                    AND sender_type = 'user' ORDER BY rowid
            """, (group_id,)).fetchall()
            # An identical external message is not sufficient evidence for an
            # association. Repeated local sends must have exactly as many matches.
            for local in users:
                if local["remote_id"] in bound:
                    continue
                peers = [u for u in users if u["body"] == local["body"] and u["sync_after"] == local["sync_after"]]
                matches = [m for m in history if m.id not in known and m.sender_type == "user"
                           and m.message == local["body"] and m.timestamp >= local["sync_after"]]
                if not matches:
                    continue
                if len(matches) < len(peers):
                    raise ReconciliationPending("Repeated local sends are not all visible in history; context work deferred")
                if len(matches) != len(peers):
                    raise ReconciliationError("Ambiguous outbound history association; inspect conversation before continuing")
                for peer, remote in zip(peers, matches):
                    self._bind(peer["remote_id"], remote)
                    bound.add(peer["remote_id"])
                    known.add(remote.id)

            turns = self.connection.execute("SELECT * FROM generation_turns WHERE group_id = ? AND status != 'reconciled' ORDER BY rowid", (group_id,)).fetchall()
            for turn in turns:
                anchor = self.connection.execute("SELECT kindroid_id FROM messages WHERE remote_id = ?", (turn["anchor_message_id"],)).fetchone()
                if anchor is None or anchor[0] is None:
                    continue
                positions = [i for i, m in enumerate(history) if m.id == anchor[0]]
                if not positions:
                    continue
                following = []
                for remote in history[positions[0] + 1:]:
                    if remote.sender_type == "user":
                        break
                    # Kindroid may append an empty AI record after the real answer.
                    # It is not a competing reply or evidence generation succeeded.
                    # Keep other speakers/roles and user boundaries conservative.
                    if remote.sender_type == "ai" and not remote.message.strip() and self._same_ai(remote, turn):
                        continue
                    following.append(remote)
                local = self.connection.execute("SELECT * FROM messages WHERE remote_id = ?", (turn["id"],)).fetchone()
                candidates = [m for m in following if m.id not in known and self._same_ai(m, turn)
                              and (local is None or m.message.strip() == local["body"].strip())]
                if not following:
                    continue
                if len(candidates) != 1 or len(following) != 1:
                    raise ReconciliationError("Ambiguous generated reply in history; inspect conversation before continuing")
                remote = candidates[0]
                if local is None:
                    self._insert_local(turn["id"], group_id, turn["ai_id"], "ai", turn["display_name"], remote.message, turn["after_timestamp"])
                self._bind(turn["id"], remote)
                self.connection.execute("UPDATE generation_turns SET status = 'reconciled' WHERE id = ?", (turn["id"],))
                known.add(remote.id)
            # Commit aliases together only after the entire window is resolved.
            # Otherwise a partial sync could lose the later attribution audit or
            # make a potential copy look like an independently received event.
            self.require_reconciled(group_id)
            return known
