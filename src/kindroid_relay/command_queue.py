from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .redaction import redact_text
from .config import DASHBOARD_INITIATIVE_LIMIT_MAX


class CommandType(str, Enum):
    RECOVER_SHARED = 'recover_shared'
    CHOSEN_ENCOUNTER = 'chosen_encounter'
    SET_ENCOUNTER = 'set_encounter'
    END_SESSION = 'end_session'
    START_SHARED = 'start_shared'
    SET_SHARED_SCHEDULE = 'set_shared_schedule'
    SET_DISCOVERY = 'set_discovery'
    APPROVE_PUBLIC_TOPIC = 'approve_public_topic'
    START_CONVERSATION = "start_conversation"
    TRIGGER_INITIATIVE = "trigger_initiative"
    SYNC_HISTORY = "sync_history"
    RESUME = "resume"
    EXECUTE_APPROVAL = "execute_approval"
    REJECT_APPROVAL = "reject_approval"
    SET_INITIATIVE_LIMIT = "set_initiative_limit"
    MEMORY_SAVE = "memory_save"
    MEMORY_REVISE = "memory_revise"
    MEMORY_LINK = "memory_link"
    MEMORY_EVIDENCE = "memory_evidence"
    MEMORY_EXTRACT = "memory_extract"
    MEMORY_REINDEX = "memory_reindex"
    START_EXPLORATION = "start_exploration"
    REVIEW_CANDIDATE = "review_candidate"
    CLOSE_EXPLORATION = "close_exploration"
    INTERPRET_EXPLORATION = "interpret_exploration"
    GARDEN_CORRECT = "garden_correct"
    SCHEDULED_EXPLORATION = "scheduled_exploration"
    SCHEDULED_CHECK_IN = "scheduled_check_in"
    SET_SCHEDULE = "set_schedule"


class CommandStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class CommandValidationError(ValueError):
    pass


class CommandConflictError(RuntimeError):
    pass


LEGAL_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.QUEUED: frozenset(
        {CommandStatus.RUNNING, CommandStatus.CANCELLED}
    ),
    CommandStatus.RUNNING: frozenset(
        {
            CommandStatus.COMPLETED,
            CommandStatus.FAILED,
            CommandStatus.INTERRUPTED,
        }
    ),
    CommandStatus.COMPLETED: frozenset(),
    CommandStatus.FAILED: frozenset(),
    CommandStatus.INTERRUPTED: frozenset(),
    CommandStatus.CANCELLED: frozenset(),
}

SIDE_EFFECTING_COMMANDS = frozenset(
    {
        CommandType.RECOVER_SHARED,
        CommandType.CHOSEN_ENCOUNTER,
        CommandType.START_SHARED,
        CommandType.SCHEDULED_CHECK_IN,
        CommandType.SCHEDULED_EXPLORATION,
        CommandType.INTERPRET_EXPLORATION,
        CommandType.START_EXPLORATION,
        CommandType.START_CONVERSATION,
        CommandType.TRIGGER_INITIATIVE,
        CommandType.EXECUTE_APPROVAL,
    }
)

SINGLE_ACTIVE_COMMANDS = frozenset(
    {
        CommandType.RECOVER_SHARED,
        CommandType.SYNC_HISTORY,
        CommandType.SCHEDULED_CHECK_IN,
        CommandType.SET_SCHEDULE,
        CommandType.SCHEDULED_EXPLORATION,
        CommandType.INTERPRET_EXPLORATION,
        CommandType.START_EXPLORATION,
        CommandType.START_CONVERSATION,
        CommandType.TRIGGER_INITIATIVE,
        CommandType.RESUME,
        CommandType.SET_INITIATIVE_LIMIT,
    }
)


@dataclass(frozen=True, slots=True)
class CommandRecord:
    id: str
    command_type: CommandType
    payload: dict[str, Any]
    idempotency_key: str
    status: CommandStatus
    requested_at: int
    started_at: int | None
    completed_at: int | None
    result: dict[str, Any] | None
    error: str | None


def _required_approval_id(payload: dict[str, Any]) -> str:
    value = payload.get("approval_id")
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise CommandValidationError("approval_id must be a non-empty string")
    return value.strip()


def validate_payload(
    command_type: CommandType,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    value = dict(payload or {})
    if command_type == CommandType.RECOVER_SHARED:
        if (set(value) != {'session_id', 'turn_id', 'confirm'} or value['confirm'] is not True
                or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 128
                       or any(ord(c) < 32 for c in value[k]) for k in ('session_id', 'turn_id'))):
            raise CommandValidationError('Recovery requires the selected session, generation and explicit confirmation')
        return value
    if command_type == CommandType.START_SHARED:
        if set(value) != {'scheduled_at'} or type(value['scheduled_at']) is not int or value['scheduled_at'] < 0:
            raise CommandValidationError('A daily shared start requires its scheduled timestamp')
        return value
    if command_type == CommandType.SET_SHARED_SCHEDULE:
        from .shared import validate_shared_config
        try:
            return validate_shared_config(value)
        except ValueError as exc:
            raise CommandValidationError(str(exc)) from exc
    if command_type == CommandType.SET_ENCOUNTER:
        if set(value) != {'due_at'} or (value['due_at'] is not None and type(value['due_at']) is not int):
            raise CommandValidationError('Encounter requires due_at as an integer timestamp or null')
        if value['due_at'] is not None and not 0 <= value['due_at'] <= 253402300799:
            raise CommandValidationError('Encounter time is outside the supported range')
        return value
    if command_type == CommandType.SET_DISCOVERY:
        from .discovery import validate_discovery
        try:
            return validate_discovery(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise CommandValidationError(str(exc)) from exc
    if command_type == CommandType.APPROVE_PUBLIC_TOPIC:
        if (set(value) != {'session_id', 'topic'} or not isinstance(value['session_id'], str)
                or not 1 <= len(value['session_id']) <= 128 or not isinstance(value['topic'], str)
                or not 1 <= len(value['topic'].strip()) <= 180 or any(ord(c) < 32 for c in value['topic'])):
            raise CommandValidationError('Public topic approval requires a session and a short public search phrase')
        return {**value, 'topic': value['topic'].strip()}
    if command_type == CommandType.SET_SCHEDULE:
        from .schedule import validate_schedule
        try:
            validated = validate_schedule(value)
            if 'tick_minutes' not in value:
                # Preserve serialization for same-key retries from pre-selector clients.
                validated.pop('tick_minutes')
            return validated
        except (ValueError, TypeError, KeyError) as exc:
            raise CommandValidationError(str(exc)) from exc
    if command_type == CommandType.GARDEN_CORRECT:
        from .garden import validate_correction
        try:
            return validate_correction(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise CommandValidationError('Invalid garden correction payload') from exc
    if command_type == CommandType.INTERPRET_EXPLORATION:
        if (set(value) != {'session_id'} or not isinstance(value['session_id'], str)
                or not 1 <= len(value['session_id']) <= 128):
            raise CommandValidationError('Interpretation requires a session ID')
        return value
    if command_type == CommandType.CLOSE_EXPLORATION:
        if (set(value) != {'session_id', 'reason'}
                or not isinstance(value['session_id'], str) or not 1 <= len(value['session_id']) <= 128
                or not isinstance(value['reason'], str) or not 1 <= len(value['reason'].strip()) <= 500):
            raise CommandValidationError('Closing an exploration requires its ID and an inspection reason')
        return value
    if command_type == CommandType.REVIEW_CANDIDATE:
        if (set(value) != {'candidate_id', 'status', 'reason'}
                or not isinstance(value['candidate_id'], str) or not 1 <= len(value['candidate_id']) <= 128
                or value['status'] not in ('active', 'archived')
                or not isinstance(value['reason'], str) or not 1 <= len(value['reason'].strip()) <= 500):
            raise CommandValidationError('Candidate review requires an ID, active/archived status and reason')
        return value
    if command_type.value.startswith("memory_"):
        from .memory import validate_memory_payload
        try:
            return validate_memory_payload(command_type.value.removeprefix("memory_"), value)
        except (ValueError, TypeError, KeyError) as exc:
            raise CommandValidationError("Invalid memory command payload") from exc
    if command_type == CommandType.SET_INITIATIVE_LIMIT:
        limit = value.get("limit")
        if set(value) != {"limit"} or type(limit) is not int or not 1 <= limit <= DASHBOARD_INITIATIVE_LIMIT_MAX:
            raise CommandValidationError(f"initiative limit must be an integer from 1 to {DASHBOARD_INITIATIVE_LIMIT_MAX}")
        return {"limit": limit}
    if command_type in {
        CommandType.CHOSEN_ENCOUNTER,
        CommandType.END_SESSION,
        CommandType.SYNC_HISTORY,
        CommandType.SCHEDULED_CHECK_IN,
        CommandType.SCHEDULED_EXPLORATION,
        CommandType.START_EXPLORATION,
        CommandType.START_CONVERSATION,
        CommandType.TRIGGER_INITIATIVE,
        CommandType.RESUME,
    }:
        if value:
            raise CommandValidationError(
                f"{command_type.value} does not accept a payload"
            )
        return {}
    if command_type == CommandType.REJECT_APPROVAL:
        if set(value) != {"approval_id"}:
            raise CommandValidationError(
                "reject_approval accepts only approval_id"
            )
        return {"approval_id": _required_approval_id(value)}
    if command_type == CommandType.EXECUTE_APPROVAL:
        if set(value) != {"approval_id", "post_result"}:
            raise CommandValidationError(
                "execute_approval requires approval_id and post_result"
            )
        post_result = value.get("post_result")
        if not isinstance(post_result, bool):
            raise CommandValidationError("post_result must be a boolean")
        return {
            "approval_id": _required_approval_id(value),
            "post_result": post_result,
        }
    raise CommandValidationError(f"unsupported command type: {command_type.value}")


class CommandStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _record(row: sqlite3.Row) -> CommandRecord:
        result_json = row["result_json"]
        return CommandRecord(
            id=str(row["id"]),
            command_type=CommandType(str(row["command_type"])),
            payload=json.loads(str(row["payload_json"])),
            idempotency_key=str(row["idempotency_key"]),
            status=CommandStatus(str(row["status"])),
            requested_at=int(row["requested_at"]),
            started_at=(
                int(row["started_at"]) if row["started_at"] is not None else None
            ),
            completed_at=(
                int(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            result=json.loads(str(result_json)) if result_json is not None else None,
            error=str(row["error"]) if row["error"] is not None else None,
        )

    def enqueue(
        self,
        command_type: CommandType,
        payload: dict[str, Any] | None,
        *,
        idempotency_key: str,
    ) -> tuple[CommandRecord, bool]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise CommandValidationError(
                "idempotency_key must be a non-empty string of at most 128 characters"
            )
        validated = validate_payload(command_type, payload)
        serialized = json.dumps(validated, sort_keys=True, separators=(",", ":"))
        now = int(time.time())
        command_id = uuid.uuid4().hex
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT * FROM commands WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["command_type"] != command_type.value or existing["payload_json"] != serialized:
                    raise CommandConflictError("idempotency key already belongs to a different action")
                self.connection.commit()
                return self._record(existing), False

            active_rows = self.connection.execute(
                "SELECT * FROM commands WHERE status IN ('queued', 'running')"
            ).fetchall()
            for row in active_rows:
                active = self._record(row)
                if (
                    command_type in SINGLE_ACTIVE_COMMANDS
                    and active.command_type == command_type
                ):
                    raise CommandConflictError(
                        f"{command_type.value} is already queued or running"
                    )
                requested_approval = validated.get("approval_id")
                active_approval = active.payload.get("approval_id")
                if requested_approval and requested_approval == active_approval:
                    raise CommandConflictError(
                        f"approval {requested_approval} already has an active command"
                    )

            self.connection.execute(
                """
                INSERT INTO commands(
                    id, command_type, payload_json, idempotency_key, status,
                    requested_at, started_at, completed_at, result_json, error
                ) VALUES(?, ?, ?, ?, 'queued', ?, NULL, NULL, NULL, NULL)
                """,
                (
                    command_id,
                    command_type.value,
                    serialized,
                    idempotency_key,
                    now,
                ),
            )
            row = self.connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self._record(row), True

    def claim_next(self) -> CommandRecord | None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT * FROM commands WHERE status = 'queued' "
                "ORDER BY requested_at, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            now = int(time.time())
            cursor = self.connection.execute(
                "UPDATE commands SET status = 'running', started_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (now, row["id"]),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return None
            claimed = self.connection.execute(
                "SELECT * FROM commands WHERE id = ?", (row["id"],)
            ).fetchone()
            self.connection.commit()
            return self._record(claimed)
        except Exception:
            self.connection.rollback()
            raise

    def transition(
        self,
        command_id: str,
        status: CommandStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> CommandRecord:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
            if row is None:
                raise KeyError(command_id)
            current = CommandStatus(str(row["status"]))
            if status not in LEGAL_TRANSITIONS[current]:
                raise CommandConflictError(
                    f"cannot transition command from {current.value} to {status.value}"
                )
            terminal = status in {
                CommandStatus.COMPLETED,
                CommandStatus.FAILED,
                CommandStatus.INTERRUPTED,
                CommandStatus.CANCELLED,
            }
            result_json = (
                json.dumps(result, sort_keys=True, separators=(",", ":"))
                if result is not None
                else None
            )
            self.connection.execute(
                "UPDATE commands SET status = ?, completed_at = ?, "
                "result_json = ?, error = ? WHERE id = ?",
                (
                    status.value,
                    int(time.time()) if terminal else None,
                    result_json,
                    redact_text(error, maximum=2000) if error else None,
                    command_id,
                ),
            )
            updated = self.connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
            self.connection.commit()
            return self._record(updated)
        except Exception:
            self.connection.rollback()
            raise

    def get(self, command_id: str) -> CommandRecord | None:
        row = self.connection.execute(
            "SELECT * FROM commands WHERE id = ?", (command_id,)
        ).fetchone()
        return self._record(row) if row is not None else None

    def recent(self, limit: int = 50) -> list[CommandRecord]:
        rows = self.connection.execute(
            "SELECT * FROM commands ORDER BY requested_at DESC, rowid DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [self._record(row) for row in rows]

    def recover_interrupted(self) -> list[CommandRecord]:
        rows = self.connection.execute(
            "SELECT * FROM commands WHERE status = 'running' "
            "ORDER BY requested_at, rowid"
        ).fetchall()
        return [
            self.transition(
                str(row["id"]),
                CommandStatus.INTERRUPTED,
                error="process stopped before command completion was recorded",
            )
            for row in rows
        ]
