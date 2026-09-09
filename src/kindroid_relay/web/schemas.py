from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ..config import DASHBOARD_INITIATIVE_LIMIT_MAX


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(StrictModel):
    error: str
    message: str


class CommandSubmission(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class MemorySubmission(CommandSubmission):
    payload: dict[str, Any]


class PauseRequest(StrictModel):
    reason: str = Field(default="paused from dashboard", max_length=500)


class ApprovalExecutionRequest(CommandSubmission):
    post_result: bool = False


class InitiativeLimitRequest(CommandSubmission):
    limit: int = Field(strict=True, ge=1, le=DASHBOARD_INITIATIVE_LIMIT_MAX)


class CommandResponse(StrictModel):
    id: str
    command_type: str
    status: str
    created: bool | None = None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    approval_id: str | None = None


class CommandPageResponse(StrictModel):
    items: list[CommandResponse]


class WorkerResponse(StrictModel):
    phase: str
    phase_started_at: datetime
    heartbeat_at: datetime
    active_command_id: str | None
    note: str
    stale: bool


class SnapshotResponse(StrictModel):
    server_time: datetime
    relay: dict[str, Any]
    worker: WorkerResponse | None
    active_command: CommandResponse | None
    schedule: dict[str, Any]
    effective_sandbox: str


class ActivityResponse(StrictModel):
    id: int
    kind: str
    severity: str
    summary: str
    details: dict[str, Any]
    correlation_id: str | None
    created_at: datetime


class ActivityPageResponse(StrictModel):
    items: list[ActivityResponse]
    next_after_id: int
    has_more: bool


class MessageResponse(StrictModel):
    id: str
    speaker: str
    sender_type: str
    display_name: str
    timestamp: int
    body: str
    known_codex_outbound: bool
    identity_fallback: bool
    sender: str = ""
    participant: str = "unknown"
    history_confirmed: bool = True


class MessagePageResponse(StrictModel):
    items: list[MessageResponse]
    next_before: int | None
    has_more: bool
    next_before_id: str | None = None


class DeliveryResponse(StrictModel):
    id: str
    status: str
    source_message_id: str | None
    created_at: datetime
    updated_at: datetime


class DeliveryPageResponse(StrictModel):
    items: list[DeliveryResponse]
    has_more: bool


class ApprovalResponse(StrictModel):
    id: str
    source_message_id: str
    summary: str
    instruction: str
    risk: str
    status: str
    result: str | None
    created_at: datetime
    updated_at: datetime
    effective_sandbox: str


class ApprovalPageResponse(StrictModel):
    items: list[ApprovalResponse]


class PauseResponse(StrictModel):
    paused: bool
    reason: str
    work_in_flight: bool


class HealthResponse(StrictModel):
    status: str


class LogTailResponse(StrictModel):
    lines: list[str]
    truncated: bool
    source: str
