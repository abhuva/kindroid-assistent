from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import ActionProposal, ChatMessage
from .migrations import migrate_database, open_database, require_current_schema
from .activity import ActivityStore, WorkerStatusStore
from .command_queue import CommandStore
from .config import DASHBOARD_INITIATIVE_LIMIT_MAX
from .turns import TurnStore


NON_SPENDING_INITIATIVE_OUTCOMES = (
    "missed", "pending_message", "empty_room", "latest_not_codex", "quiet_hours",
    "paused", "disabled", "bootstrapped", "daily_budget",
    "recent_conversation", "delivery_unresolved",
)
_SPENDING_INITIATIVE_SQL = "outcome NOT IN (" + ",".join("?" for _ in NON_SPENDING_INITIATIVE_OUTCOMES) + ")"


class RelayAlreadyRunning(RuntimeError):
    pass


class HistoryBudgetError(RuntimeError):
    """Legacy compatibility name; advisory history measurement never raises it."""


def _windows_pid_is_active(pid: int) -> bool:
    """Check a Windows PID without sending a console control event."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER is the normal response for a PID that does
        # not exist. Other failures are treated conservatively as active so a
        # lock is never removed merely because the process could not be read.
        return ctypes.get_last_error() != error_invalid_parameter
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_active(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_lock_is_active(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    return _pid_is_active(pid)


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._owned = False

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError as exc:
                if attempt == 0 and self._remove_stale_lock():
                    continue
                raise RelayAlreadyRunning(
                    f"another relay process owns the lock: {self.path}"
                ) from exc
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        self._owned = True
        return self

    def _remove_stale_lock(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        if not _pid_is_active(pid):
            self.path.unlink(missing_ok=True)
            return True
        return False

    def __exit__(self, *_: object) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False


class RelayState:
    def __init__(self, path: Path, *, migrate: bool = True, account_path: Path | None = None) -> None:
        self.path = path
        if migrate:
            migrate_database(path)
        self.connection = open_database(path)
        require_current_schema(self.connection)
        self.commands = CommandStore(self.connection)
        self.activity = ActivityStore(self.connection)
        self.worker_status = WorkerStatusStore(self.connection)
        self.turns = TurnStore(self.connection)
        self.account = (RelayState(account_path, migrate=migrate)
                        if account_path is not None and account_path.resolve() != path.resolve() else None)

    def close(self) -> None:
        self.connection.close()
        if self.account is not None:
            self.account.close()

    def __enter__(self) -> "RelayState":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        if self.account is not None and key in ACCOUNT_META:
            return self.account.get_meta(key, default)
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str | int | bool) -> None:
        if self.account is not None and key in ACCOUNT_META:
            self.account.set_meta(key, value)
            return
        serialized = "1" if value is True else "0" if value is False else str(value)
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, serialized),
            )

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get_meta(key)
        return int(value) if value is not None else default

    def is_paused(self) -> bool:
        return self.get_meta("paused", "0") == "1"

    def pause(self, reason: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('paused', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = '1'"
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('pause_reason', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (reason,),
            )

    def resume(self) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('paused', '0') "
                "ON CONFLICT(key) DO UPDATE SET value = '0'"
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('pause_reason', '') "
                "ON CONFLICT(key) DO UPDATE SET value = ''"
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('consecutive_turns', '0') "
                "ON CONFLICT(key) DO UPDATE SET value = '0'"
            )

    def add_message(self, group_id: str, message: ChatMessage, processed: bool = False) -> bool:
        if not message.id:
            return False
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO messages(
                    remote_id, group_id, sender, sender_type, display_name,
                    timestamp, body, processed, received_at, kindroid_id
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    group_id,
                    message.sender,
                    message.sender_type,
                    message.display_name,
                    message.timestamp,
                    message.message,
                    int(processed),
                    int(time.time()),
                    message.id,
                ),
            )
        return cursor.rowcount == 1

    def mark_all_messages_processed(self) -> None:
        with self.connection:
            self.connection.execute("UPDATE messages SET processed = 1")

    def mark_message_processed(self, remote_id: str, processed: bool = True) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE messages SET processed = ? WHERE remote_id = ?",
                (int(processed), remote_id),
            )

    def latest_ai_message(self) -> ChatMessage | None:
        row = self.connection.execute(
            "SELECT * FROM messages WHERE sender_type = 'ai' "
            "ORDER BY timestamp DESC, received_at DESC LIMIT 1"
        ).fetchone()
        return self._row_to_message(row) if row else None

    def latest_message(self) -> ChatMessage | None:
        row = self.connection.execute(
            "SELECT * FROM messages ORDER BY timestamp DESC, received_at DESC LIMIT 1"
        ).fetchone()
        return self._row_to_message(row) if row else None

    def pending_ai_messages(self) -> list[ChatMessage]:
        rows = self.connection.execute(
            "SELECT * FROM messages WHERE processed = 0 AND sender_type = 'ai' "
            "ORDER BY timestamp, received_at"
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def recent_messages(self, limit: int = 50) -> list[ChatMessage]:
        rows = self.connection.execute(
            "SELECT * FROM messages ORDER BY timestamp DESC, received_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            id=str(row["remote_id"]),
            sender=str(row["sender"]),
            sender_type=str(row["sender_type"]),
            display_name=str(row["display_name"]),
            timestamp=int(row["timestamp"]),
            message=str(row["body"]),
        )

    def create_outbox(
        self,
        body: str,
        *,
        source_message_id: str | None = None,
        request_kindroid_reply: bool | None = None,
    ) -> str:
        outbox_id = uuid.uuid4().hex
        now = int(time.time())
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO outbox(
                    id, body, body_hash, source_message_id,
                    request_kindroid_reply, status, error, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
                """,
                (
                    outbox_id,
                    body,
                    digest,
                    source_message_id,
                    (
                        int(request_kindroid_reply)
                        if request_kindroid_reply is not None
                        else None
                    ),
                    now,
                    now,
                ),
            )
        return outbox_id

    def outbox_for_source(self, source_message_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM outbox WHERE source_message_id = ? LIMIT 1",
            (source_message_id,),
        ).fetchone()

    def set_outbox_status(self, outbox_id: str, status: str, error: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE outbox SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, int(time.time()), outbox_id),
            )

    def is_known_outbound(self, body: str) -> bool:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        row = self.connection.execute(
            "SELECT 1 FROM outbox WHERE body_hash = ? AND status IN ('sending', 'sent', 'uncertain') LIMIT 1",
            (digest,),
        ).fetchone()
        return row is not None

    def add_approval(self, source_message_id: str, proposal: ActionProposal) -> str:
        approval_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self.connection:
            self.connection.execute(
                "INSERT INTO approvals VALUES(?, ?, ?, ?, ?, 'pending', NULL, ?, ?)",
                (
                    approval_id,
                    source_message_id,
                    proposal.summary,
                    proposal.instruction,
                    proposal.risk,
                    now,
                    now,
                ),
            )
        return approval_id

    def get_approval(self, approval_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()

    def list_approvals(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        return self.connection.execute(
            "SELECT * FROM approvals ORDER BY created_at DESC"
        ).fetchall()

    def update_approval(self, approval_id: str, status: str, result: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE approvals SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, result, int(time.time()), approval_id),
            )

    def record_history_request(self, maximum: int) -> None:
        if self.account is not None:
            self.account.record_history_request(maximum)
            return
        now = int(time.time())
        cutoff = now - 86400
        with self.connection:
            self.connection.execute(
                "DELETE FROM history_requests WHERE requested_at < ?", (cutoff,)
            )
            # `maximum` is an advisory dashboard threshold, never permission to read.
            self.connection.execute(
                "INSERT INTO history_requests(requested_at) VALUES(?)", (now,)
            )

    def has_active_conversation_work(self) -> bool:
        """Only an existing session may drive automatic conversation work.

        Saved messages alone (including a manual history import) are not a
        request to reply. A session without a pending reply needs no polling.
        """
        if self.connection.execute("SELECT 1 FROM shared_sessions WHERE status='active'").fetchone():
            return True
        return bool(self.get_meta('activity_session') and
                    (self.pending_ai_messages() or self.turns.has_uncertain()))

    def history_request_count(self) -> int:
        return self.history_measurement()["used"]

    def history_measurement(self) -> dict[str, int]:
        """Account-wide reporting day, not a prediction of Kindroid's quota window."""
        if self.account is not None:
            return self.account.history_measurement()
        start = int(time.time()) // 86400 * 86400
        used = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM history_requests WHERE requested_at >= ? AND requested_at < ?",
                (start, start + 86400),
            ).fetchone()[0]
        )
        return {"used": used, "starts_at": start, "resets_at": start + 86400}

    def record_initiative_check(self, maximum: int, outcome: str) -> bool:
        now = int(time.time())
        cutoff = now - 86400
        with self.connection:
            self.connection.execute(
                "DELETE FROM initiative_checks WHERE checked_at <= ?", (cutoff,)
            )
            count = self.initiative_check_count()
            if outcome not in NON_SPENDING_INITIATIVE_OUTCOMES and count >= maximum:
                return False
            self.connection.execute(
                "INSERT INTO initiative_checks(checked_at, outcome) VALUES(?, ?)",
                (now, outcome),
            )
        return True

    def update_latest_initiative_outcome(self, outcome: str) -> None:
        with self.connection:
            row = self.connection.execute(
                "SELECT rowid FROM initiative_checks ORDER BY checked_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if row:
                self.connection.execute(
                    "UPDATE initiative_checks SET outcome = ? WHERE rowid = ?",
                    (outcome, row["rowid"]),
                )

    def initiative_check_count(self) -> int:
        cutoff = int(time.time()) - 86400
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM initiative_checks WHERE checked_at > ? AND " + _SPENDING_INITIATIVE_SQL,
                (cutoff, *NON_SPENDING_INITIATIVE_OUTCOMES),
            ).fetchone()[0]
        )

    def initiative_limit(self, configured: int) -> int:
        raw = self.get_meta("initiative_limit_override")
        try:
            value = int(raw) if raw is not None else configured
        except ValueError:
            return configured
        return value if raw is None or 1 <= value <= DASHBOARD_INITIATIVE_LIMIT_MAX else configured

    def initiative_budget(self, configured: int) -> dict[str, object]:
        now = int(time.time())
        limit = self.initiative_limit(configured)
        # Use one ordered read so count and availability agree even around expiry.
        rows = self.connection.execute(
            "SELECT checked_at FROM initiative_checks WHERE checked_at > ? AND "
            + _SPENDING_INITIATIVE_SQL + " ORDER BY checked_at, rowid",
            (now - 86400, *NON_SPENDING_INITIATIVE_OUTCOMES),
        ).fetchall()
        used = len(rows)
        return {
            "used": used, "limit": limit,
            "available_at": int(rows[used - limit][0]) + 86400 if used >= limit else None,
            "source": "dashboard" if self.get_meta("initiative_limit_override") == str(limit) else "configuration",
        }

    def status(self) -> dict[str, object]:
        now = int(time.time())
        history = self.history_measurement()
        pending_messages = self.connection.execute(
            "SELECT COUNT(*) FROM messages WHERE processed = 0 AND sender_type = 'ai'"
        ).fetchone()[0]
        pending_approvals = self.connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
        ).fetchone()[0]
        uncertain = self.connection.execute(
            "SELECT COUNT(*) FROM outbox WHERE status = 'uncertain'"
        ).fetchone()[0]
        last_initiative = self.connection.execute(
            "SELECT checked_at, outcome FROM initiative_checks "
            "ORDER BY checked_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return {
            "bootstrapped": self.get_meta("bootstrapped", "0") == "1",
            "paused": self.is_paused(),
            "pause_reason": self.get_meta("pause_reason", ""),
            "codex_thread_id": self.get_meta("codex_thread_id", ""),
            "cursor_timestamp": self.get_int("cursor_timestamp", 0),
            "consecutive_turns": self.get_int("consecutive_turns", 0),
            "pending_messages": int(pending_messages),
            "pending_approvals": int(pending_approvals),
            "uncertain_outbox": int(uncertain),
            "history_requests_24h": history["used"],
            "history_window_start_at": history["starts_at"],
            "history_window_reset_at": history["resets_at"],
            "history_cooldown_until": self.get_int("history_cooldown_until", 0),
            "history_last_429_at": self.get_int("history_last_429_at", 0),
            "history_rate_limit_streak": self.get_int(
                "history_rate_limit_streak", 0
            ),
            "history_rate_limited": self.get_int(
                "history_cooldown_until", 0
            ) > now,
            "initiative_checks_24h": self.initiative_check_count(),
            "initiative_due_at": self.get_int("initiative_due_at", 0),
            "initiative_schedule_basis": self.get_meta(
                "initiative_schedule_basis", ""
            ),
            "last_initiative_check_at": (
                int(last_initiative["checked_at"]) if last_initiative else 0
            ),
            "last_initiative_outcome": (
                str(last_initiative["outcome"]) if last_initiative else ""
            ),
        }


ACCOUNT_META = frozenset({'last_history_request_at_ms', 'history_cooldown_until',
                          'history_last_429_at', 'history_rate_limit_streak'})


class RelayStateFactory:
    """Creates thread-owned state connections after startup migration."""

    def __init__(self, path: Path, account_path: Path | None = None) -> None:
        self.path = path
        self.account_path = account_path

    @contextmanager
    def connect(self) -> Iterator[RelayState]:
        state = RelayState(self.path, migrate=False, account_path=self.account_path)
        try:
            yield state
        finally:
            state.close()
