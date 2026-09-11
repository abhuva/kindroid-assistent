from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .redaction import redact_mapping, redact_text


class ActivitySeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WorkerPhase(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    POLLING = "polling"
    WAITING_FOR_KINDROID = "waiting_for_kindroid"
    RUNNING_CODEX = "running_codex"
    SENDING = "sending"
    EXECUTING_APPROVAL = "executing_approval"
    PAUSED = "paused"
    DEGRADED = "degraded"
    STOPPING = "stopping"


COMMON_DETAIL_FIELDS = frozenset(
    {
        "approval_id",
        "command_id",
        "command_type",
        "cooldown_until",
        "display_name",
        "error_type",
        "failure_stage",
        "http_status",
        "group_id",
        "message_id",
        "outbox_id",
        "outcome",
        "phase",
        "reason",
        "request_count",
        "retry_after_seconds",
        "risk",
        "sender_type",
        "source_message_id",
        "status",
        "turn_count",
        "limit",
        "previous_limit",
    }
)

ACTIVITY_KINDS = frozenset(
    {
        'shared_check_requested', 'shared_check_decided',
        'encounter_choice', 'shared_session_ended', 'shared_context_changed', 'shared_session_recovered', 'room_work_failed',
        'discovery_changed', 'discovery_source', 'experience_updated',
        "schedule_changed",
        "schedule_queued",
        "schedule_skipped",
        "curiosity_scheduled",
        "curiosity_skipped",
        "curiosity_completed",
        "process_started",
        "process_stopped",
        "worker_phase",
        "relay_paused",
        "relay_resumed",
        "relay_cycle_started",
        "relay_cycle_completed",
        "relay_cycle_failed",
        "history_rate_limited",
        "history_sync",
        "message_received",
        "message_processed",
        "reply_turn_decision",
        "codex_started",
        "codex_completed",
        "codex_failed",
        "outbound_created",
        "outbound_sending",
        "outbound_sent",
        "outbound_uncertain",
        "generation_uncertain",
        "generation_history_fallback",
        "identity_fallback",
        "initiative_due",
        "initiative_limit_changed",
        "initiative_skipped",
        "initiative_offered",
        "initiative_answered",
        "initiative_declined",
        "command_queued",
        "command_started",
        "command_completed",
        "command_failed",
        "command_interrupted",
        "command_cancelled",
        "approval_created",
        "approval_status",
        "database_migrated",
    }
)


SAFETY_CRITICAL_ACTIVITY_KINDS = frozenset(
    {
        'encounter_choice', 'shared_session_ended', 'shared_context_changed', 'shared_session_recovered', 'room_work_failed',
        'discovery_changed', 'experience_updated',
        "schedule_changed",
        "relay_paused",
        "initiative_limit_changed",
        "relay_resumed",
        "outbound_created",
        "outbound_sending",
        "outbound_sent",
        "outbound_uncertain",
        "generation_uncertain",
        "generation_history_fallback",
        "identity_fallback",
        "command_queued",
        "command_started",
        "command_completed",
        "command_failed",
        "command_interrupted",
        "command_cancelled",
        "approval_created",
        "approval_status",
        "database_migrated",
    }
)


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    id: int
    kind: str
    severity: ActivitySeverity
    summary: str
    details: dict[str, Any]
    correlation_id: str | None
    created_at: int


@dataclass(frozen=True, slots=True)
class WorkerStatusRecord:
    phase: WorkerPhase
    phase_started_at: int
    heartbeat_at: int
    active_command_id: str | None
    note: str


class ActivityStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _record(row: sqlite3.Row) -> ActivityRecord:
        return ActivityRecord(
            id=int(row["id"]),
            kind=str(row["kind"]),
            severity=ActivitySeverity(str(row["severity"])),
            summary=str(row["summary"]),
            details=json.loads(str(row["details_json"])),
            correlation_id=(
                str(row["correlation_id"])
                if row["correlation_id"] is not None
                else None
            ),
            created_at=int(row["created_at"]),
        )

    def add(
        self,
        kind: str,
        severity: ActivitySeverity,
        summary: str,
        *,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> ActivityRecord:
        if kind not in ACTIVITY_KINDS:
            raise ValueError(f"unknown activity kind: {kind}")
        safe_details = redact_mapping(
            details or {}, allowed_keys=COMMON_DETAIL_FIELDS
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO activity_events(
                    kind, severity, summary, details_json, correlation_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    severity.value,
                    redact_text(summary, maximum=1000),
                    json.dumps(safe_details, sort_keys=True, separators=(",", ":")),
                    redact_text(correlation_id, maximum=128)
                    if correlation_id
                    else None,
                    int(time.time()),
                ),
            )
            row = self.connection.execute(
                "SELECT * FROM activity_events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._record(row)

    def after(self, after_id: int = 0, limit: int = 100) -> list[ActivityRecord]:
        rows = self.connection.execute(
            "SELECT * FROM activity_events WHERE id > ? ORDER BY id LIMIT ?",
            (max(0, after_id), max(1, min(limit, 500))),
        ).fetchall()
        return [self._record(row) for row in rows]

    def prune(self, retention_days: int, *, now: int | None = None) -> int:
        """Remove expired operational noise while retaining safety audit events."""
        cutoff = (int(time.time()) if now is None else now) - retention_days * 86400
        placeholders = ",".join("?" for _ in SAFETY_CRITICAL_ACTIVITY_KINDS)
        with self.connection:
            cursor = self.connection.execute(
                f"DELETE FROM activity_events WHERE created_at < ? "
                f"AND kind NOT IN ({placeholders})",
                (cutoff, *sorted(SAFETY_CRITICAL_ACTIVITY_KINDS)),
            )
        return cursor.rowcount


class WorkerStatusStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def update(
        self,
        phase: WorkerPhase,
        *,
        active_command_id: str | None = None,
        note: str = "",
        change_phase: bool = True,
    ) -> WorkerStatusRecord:
        now = int(time.time())
        existing = self.get()
        phase_started_at = (
            now
            if change_phase or existing is None or existing.phase != phase
            else existing.phase_started_at
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO worker_status(
                    singleton, phase, phase_started_at, heartbeat_at,
                    active_command_id, note
                ) VALUES(1, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    phase = excluded.phase,
                    phase_started_at = excluded.phase_started_at,
                    heartbeat_at = excluded.heartbeat_at,
                    active_command_id = excluded.active_command_id,
                    note = excluded.note
                """,
                (
                    phase.value,
                    phase_started_at,
                    now,
                    active_command_id,
                    redact_text(note, maximum=500),
                ),
            )
        current = self.get()
        if current is None:  # pragma: no cover - guarded by successful upsert
            raise RuntimeError("worker status update was not persisted")
        return current

    def heartbeat(self) -> WorkerStatusRecord | None:
        existing = self.get()
        if existing is None:
            return None
        return self.update(
            existing.phase,
            active_command_id=existing.active_command_id,
            note=existing.note,
            change_phase=False,
        )

    def get(self) -> WorkerStatusRecord | None:
        row = self.connection.execute(
            "SELECT * FROM worker_status WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return None
        return WorkerStatusRecord(
            phase=WorkerPhase(str(row["phase"])),
            phase_started_at=int(row["phase_started_at"]),
            heartbeat_at=int(row["heartbeat_at"]),
            active_command_id=(
                str(row["active_command_id"])
                if row["active_command_id"] is not None
                else None
            ),
            note=str(row["note"]),
        )
