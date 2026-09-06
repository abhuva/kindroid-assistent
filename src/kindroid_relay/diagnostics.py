"""Bounded, read-only, deliberately lossy diagnostics for human-reviewed sharing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activity import ACTIVITY_KINDS, ActivitySeverity, WorkerPhase
from .log_tail import read_log_tail
from .migrations import LATEST_SCHEMA_VERSION, current_schema_version

MAX_EVENTS = 100
MAX_LOG_LINES = 200
MAX_BUNDLE_BYTES = 512 * 1024
READ_TIMEOUT_SECONDS = 5
REVIEW_NOTICE = "Review this bundle before sharing. Timing, counts and operational history may still be private."
_REFERENCE_FIELDS = frozenset({"approval_id", "command_id", "message_id", "outbox_id", "source_message_id"})
_NUMBER_FIELDS = frozenset({"cooldown_until", "request_count", "retry_after_seconds", "turn_count", "limit", "previous_limit", "http_status"})
_ENUM_FIELDS = {
    "failure_stage": {"request", "capture"},
    "phase": {phase.value for phase in WorkerPhase},
    "command_type": {"start_conversation", "trigger_initiative", "resume", "execute_approval", "reject_approval", "set_initiative_limit"},
    "status": {"queued", "running", "completed", "failed", "interrupted", "cancelled", "pending", "executing", "executed", "rejected", "sending", "sent", "uncertain"},
    "risk": {"low", "medium", "high"},
    "sender_type": {"user", "ai"},
    "error_type": {"KindroidAPIError", "HistoryCooldownError", "TimeoutError", "RuntimeError", "ValueError", "OSError", "OperationalError", "ConnectError", "ReadTimeout", "InvalidRequestError", "EmptyGeneratedReply", "ReconciliationError", "ReconciliationPending", "IntegrityError", "DatabaseError", "TypeError", "UnexpectedError"},
}
_LOGGERS = {"httpx", "uvicorn.error"} | {
    f"kindroid_relay.{name}" for name in ("cli", "relay", "worker", "server", "application", "kindroid_api", "codex_agent", "migrations", "web.app")
}
_LOG_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) ([a-z_.]+): ")


class DiagnosticError(RuntimeError):
    """Safe operator-facing error; never wrap raw database/log contents."""


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -(2**63) <= value < 2**63 else None
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value):
        return int(value)
    return None


def _read_state(path: Path, now: int, reference: Callable[[object], str | None]) -> dict[str, Any]:
    deadline = time.monotonic() + READ_TIMEOUT_SECONDS
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            version = current_schema_version(connection)
            if version != LATEST_SCHEMA_VERSION:
                raise DiagnosticError("Diagnostic export requires the current database schema; no migration was attempted.")
            status: dict[str, Any] = {}
            for key in ("paused", "bootstrapped", "consecutive_turns", "history_cooldown_until", "history_last_429_at", "history_rate_limit_streak", "initiative_due_at"):
                row = connection.execute("SELECT substr(value, 1, 32) FROM metadata WHERE key = ?", (key,)).fetchone()
                value = _integer(row[0]) if row else 0
                status[key] = (None if value is None else value == 1) if key in {"paused", "bootstrapped"} else value
            for name, query in {
                "pending_messages": "SELECT COUNT(*) FROM messages WHERE processed = 0 AND sender_type = 'ai'",
                "pending_approvals": "SELECT COUNT(*) FROM approvals WHERE status = 'pending'",
                "uncertain_outbox": "SELECT COUNT(*) FROM outbox WHERE status = 'uncertain'",
                "pending_commands": "SELECT COUNT(*) FROM commands WHERE status IN ('queued', 'running')",
            }.items():
                status[name] = connection.execute(query).fetchone()[0]
            status["history_requests_24h"] = connection.execute(
                "SELECT COUNT(*) FROM history_requests WHERE requested_at > ?", (now - 86400,)
            ).fetchone()[0]
            worker_row = connection.execute(
                "SELECT substr(phase, 1, 64) AS phase, phase_started_at, heartbeat_at, "
                "substr(active_command_id, 1, 128) AS active_command_id FROM worker_status WHERE singleton = 1"
            ).fetchone()
            worker = None if worker_row is None else {
                "phase": worker_row["phase"] if worker_row["phase"] in _ENUM_FIELDS["phase"] else "unknown",
                "phase_started_at": _integer(worker_row["phase_started_at"]),
                "heartbeat_at": _integer(worker_row["heartbeat_at"]),
                "active_command_ref": reference(worker_row["active_command_id"]),
            }
            rows = connection.execute(
                "SELECT id, substr(kind, 1, 64) AS kind, substr(severity, 1, 16) AS severity, created_at, "
                "substr(correlation_id, 1, 128) AS correlation_id, "
                "CASE WHEN length(CAST(details_json AS BLOB)) <= 8192 THEN details_json END AS details_json "
                "FROM activity_events ORDER BY id DESC LIMIT ?", (MAX_EVENTS + 1,)
            ).fetchall()
            events = []
            for row in rows[:MAX_EVENTS]:
                try:
                    details = json.loads(row["details_json"] or "null")
                except (ValueError, RecursionError):
                    details = None
                safe = {}
                if isinstance(details, dict):
                    for key, value in details.items():
                        if key in _REFERENCE_FIELDS and isinstance(value, str):
                            safe[key.replace("_id", "_ref")] = reference(value)
                        elif key in _NUMBER_FIELDS:
                            safe[key] = _integer(value)
                        elif key in _ENUM_FIELDS:
                            safe[key] = value if isinstance(value, str) and value in _ENUM_FIELDS[key] else "unknown"
                events.append({
                    "id": row["id"],
                    "kind": row["kind"] if row["kind"] in ACTIVITY_KINDS else "unknown",
                    "severity": row["severity"] if row["severity"] in {s.value for s in ActivitySeverity} else "unknown",
                    "created_at": _integer(row["created_at"]),
                    "correlation_ref": reference(row["correlation_id"]),
                    "details": safe,
                    "details_omitted": not isinstance(details, dict),
                })
            return {"schema_version": version, "status": status, "worker": worker,
                    "activity": {"items": events, "truncated": len(rows) > MAX_EVENTS}}
    except sqlite3.Error:
        raise DiagnosticError("Cannot read diagnostic state: database missing, unreadable, busy, corrupt, or read deadline exceeded.") from None


def export_diagnostics(state_path: Path, log_path: Path, destination: Path) -> Path:
    """Write a new JSON file only; never overwrite files or mutate relay state."""
    destination = destination.resolve()
    protected = {state_path.resolve(), log_path.resolve()} | {
        Path(str(state_path.resolve()) + suffix) for suffix in ("-wal", "-shm", "-journal")
    }
    protected.add(state_path.with_suffix(".lock").resolve())
    if destination in protected:
        raise DiagnosticError("Diagnostic destination must not be a runtime database, log, or lock path.")
    if destination.exists():
        raise DiagnosticError("Destination already exists; choose a new filename.")
    key = os.urandom(32)

    def reference(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return "ref-" + hashlib.blake2s(value[:128].encode("utf-8", errors="replace"), key=key, digest_size=12).hexdigest()

    now = int(time.time())
    bundle = _read_state(state_path, now, reference)
    try:
        lines, truncated = read_log_tail(log_path, MAX_LOG_LINES)
    except OSError:
        raise DiagnosticError("Cannot read the configured diagnostic log.") from None
    # Regex redaction cannot promise to remove arbitrary chat/credential text.
    # Retain only recognized header metadata, never free-form payloads/tracebacks.
    headers = []
    for line in lines:
        match = _LOG_HEADER.match(line)
        if match and match[3] in _LOGGERS:
            headers.append({"local_time": match[1], "level": match[2], "logger": match[3]})
    bundle.update({
        "format_version": 1,
        "created_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "review_notice": REVIEW_NOTICE,
        "privacy": "Free-form text, configuration, paths and raw identifiers are excluded. References are pseudonymized per bundle.",
        "log_tail": {"headers": headers, "lines_examined": len(lines), "truncated": truncated,
                     "notice": "Message text and traceback lines omitted; missing/empty current logs yield an empty tail. Rotated logs are not included."},
    })
    content = (json.dumps(bundle, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(content) > MAX_BUNDLE_BYTES:
        raise DiagnosticError("Diagnostic bundle exceeds the size limit; nothing was written.")
    try:
        # Exclusive creation also protects against concurrent exports and aliases
        # of the database, configuration, logs, or any other existing user file.
        with destination.open("xb") as handle:
            handle.write(content)
    except OSError:
        raise DiagnosticError("Cannot create the diagnostic file; use a new filename in an existing writable directory. A write failure may leave a partial file.") from None
    return destination
