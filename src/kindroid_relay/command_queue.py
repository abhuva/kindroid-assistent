from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .redaction import redact_text


class CommandType(str, Enum):
    START_CONVERSATION = "start_conversation"
    TRIGGER_INITIATIVE = "trigger_initiative"
    RESUME = "resume"
    EXECUTE_APPROVAL = "execute_approval"
    REJECT_APPROVAL = "reject_approval"


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
        CommandType.START_CONVERSATION,
        CommandType.TRIGGER_INITIATIVE,
        CommandType.EXECUTE_APPROVAL,
    }
)

SINGLE_ACTIVE_COMMANDS = frozenset(
    {
        CommandType.START_CONVERSATION,
        CommandType.TRIGGER_INITIATIVE,
        CommandType.RESUME,
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
    if command_type in {
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
