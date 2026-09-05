from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..activity import ActivitySeverity
from ..command_queue import (
    CommandConflictError,
    CommandRecord,
    CommandType,
    CommandValidationError,
)
from ..config import Settings
from ..redaction import redact_text
from ..state import RelayState, RelayStateFactory
from ..worker import request_pause
from .schemas import (
    ActivityPageResponse,
    ActivityResponse,
    ApprovalExecutionRequest,
    ApprovalPageResponse,
    ApprovalResponse,
    CommandResponse,
    CommandSubmission,
    ErrorResponse,
    HealthResponse,
    LogTailResponse,
    MessagePageResponse,
    MessageResponse,
    PauseRequest,
    PauseResponse,
    SnapshotResponse,
    WorkerResponse,
)


logger = logging.getLogger(__name__)
MAX_MUTATION_BODY = 16_384


def _utc(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _command_response(
    command: CommandRecord,
    *,
    created: bool | None = None,
) -> CommandResponse:
    return CommandResponse(
        id=command.id,
        command_type=command.command_type.value,
        status=command.status.value,
        created=created,
        requested_at=_utc(command.requested_at),  # type: ignore[arg-type]
        started_at=_utc(command.started_at),
        completed_at=_utc(command.completed_at),
        result=command.result,
        error=command.error,
    )


def _approval_response(row: Any, settings: Settings) -> ApprovalResponse:
    return ApprovalResponse(
        id=str(row["id"]),
        source_message_id=str(row["source_message_id"]),
        summary=str(row["summary"]),
        instruction=str(row["instruction"]),
        risk=str(row["risk"]),
        status=str(row["status"]),
        result=str(row["result"]) if row["result"] is not None else None,
        created_at=_utc(int(row["created_at"])),  # type: ignore[arg-type]
        updated_at=_utc(int(row["updated_at"])),  # type: ignore[arg-type]
        effective_sandbox=settings.approved_sandbox,
    )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, message=message).model_dump(),
    )


def create_web_app(
    settings: Settings,
    state_factory: RelayStateFactory,
    wake_event: Any,
    *,
    csrf_token: str | None = None,
    testing: bool = False,
) -> FastAPI:
    token = csrf_token or secrets.token_urlsafe(32)
    expected_origin = f"http://{settings.web_host}:{settings.web_port}"
    allowed_hosts = {
        settings.web_host,
        f"{settings.web_host}:{settings.web_port}",
    }
    if testing:
        allowed_hosts.add("testserver")

    app = FastAPI(
        title="Kindroid Relay Dashboard",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return _error(400, "invalid_host", "The Host header is not allowed.")

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin is not None and origin != expected_origin and not (
                testing and origin == "http://testserver"
            ):
                return _error(403, "invalid_origin", "The request origin is not allowed.")
            if request.headers.get("x-relay-csrf") != token:
                return _error(403, "invalid_csrf", "The request token is missing or invalid.")
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if content_type != "application/json":
                return _error(415, "invalid_content_type", "JSON is required.")
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    return _error(400, "invalid_content_length", "Content-Length is invalid.")
                if declared_length > MAX_MUTATION_BODY:
                    return _error(413, "request_too_large", "The request body is too large.")
            body = await request.body()
            if len(body) > MAX_MUTATION_BODY:
                return _error(413, "request_too_large", "The request body is too large.")

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(CommandConflictError)
    async def command_conflict_handler(
        _request: Request, exc: CommandConflictError
    ) -> JSONResponse:
        return _error(409, "command_conflict", redact_text(exc, maximum=500))

    @app.exception_handler(CommandValidationError)
    async def command_validation_handler(
        _request: Request, exc: CommandValidationError
    ) -> JSONResponse:
        return _error(422, "invalid_command", redact_text(exc, maximum=500))

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error(422, "invalid_request", "The request body or query is invalid.")

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        codes = {404: "not_found", 409: "conflict", 422: "invalid_request"}
        return _error(
            exc.status_code,
            codes.get(exc.status_code, "http_error"),
            redact_text(exc.detail, maximum=500),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Dashboard request failed")
        return _error(500, "internal_error", "The dashboard request failed.")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        template = (static_dir / "index.html").read_text(encoding="utf-8")
        html = template.replace("{{CSRF_TOKEN}}", token).replace(
            "{{POLL_MS}}", str(settings.web_poll_seconds * 1000)
        )
        return HTMLResponse(html)

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="live")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse | JSONResponse:
        with state_factory.connect() as state:
            status = state.worker_status.get()
        if status is None or status.phase.value == "stopping":
            return _error(503, "not_ready", "The relay worker is not ready.")
        return HealthResponse(status="ready")

    @app.get("/api/v1/snapshot", response_model=SnapshotResponse)
    def snapshot() -> SnapshotResponse:
        now = int(time.time())
        with state_factory.connect() as state:
            relay_status = state.status()
            worker = state.worker_status.get()
            active = (
                state.commands.get(worker.active_command_id)
                if worker and worker.active_command_id
                else None
            )
        worker_response = None
        if worker is not None:
            worker_response = WorkerResponse(
                phase=worker.phase.value,
                phase_started_at=_utc(worker.phase_started_at),  # type: ignore[arg-type]
                heartbeat_at=_utc(worker.heartbeat_at),  # type: ignore[arg-type]
                active_command_id=worker.active_command_id,
                note=worker.note,
                stale=now - worker.heartbeat_at > settings.worker_stale_seconds,
            )
        return SnapshotResponse(
            server_time=_utc(now),  # type: ignore[arg-type]
            relay=relay_status,
            worker=worker_response,
            active_command=_command_response(active) if active else None,
            schedule={
                "initiative_enabled": settings.initiative_enabled,
                "initiative_schedule_hours": settings.initiative_schedule_hours,
                "initiative_interval_seconds": settings.initiative_interval_seconds,
                "initiative_quiet_hours": settings.initiative_quiet_hours,
                "history_request_limit_24h": settings.max_history_requests_24h,
                "active_poll_seconds": settings.poll_seconds,
                "idle_poll_seconds": settings.idle_poll_seconds,
                "active_history_interval_seconds": (
                    settings.min_history_interval_seconds
                ),
                "history_429_cooldown_seconds": (
                    settings.history_429_cooldown_seconds
                ),
                "max_consecutive_turns": settings.max_consecutive_turns,
            },
            effective_sandbox=settings.approved_sandbox,
        )

    @app.get("/api/v1/activity", response_model=ActivityPageResponse)
    def activity(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ActivityPageResponse:
        with state_factory.connect() as state:
            rows = state.activity.after(after_id, limit + 1)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            ActivityResponse(
                id=row.id,
                kind=row.kind,
                severity=row.severity.value,
                summary=row.summary,
                details=row.details,
                correlation_id=row.correlation_id,
                created_at=_utc(row.created_at),  # type: ignore[arg-type]
            )
            for row in rows
        ]
        return ActivityPageResponse(
            items=items,
            next_after_id=items[-1].id if items else after_id,
            has_more=has_more,
        )

    @app.get("/api/v1/messages", response_model=MessagePageResponse)
    def messages(
        before: int | None = Query(default=None, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> MessagePageResponse:
        with state_factory.connect() as state:
            parameters: list[Any] = []
            predicate = ""
            if before is not None:
                predicate = "WHERE timestamp < ?"
                parameters.append(before)
            parameters.append(limit + 1)
            rows = state.connection.execute(
                f"SELECT * FROM messages {predicate} "
                "ORDER BY timestamp DESC, received_at DESC LIMIT ?",
                parameters,
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = []
            for row in rows:
                body = str(row["body"])
                known = state.is_known_outbound(body)
                fallback = bool(
                    known
                    and str(row["display_name"]).casefold()
                    != settings.codex_display_name.casefold()
                )
                items.append(
                    MessageResponse(
                        id=str(row["remote_id"]),
                        speaker=str(row["display_name"]),
                        sender_type=str(row["sender_type"]),
                        display_name=str(row["display_name"]),
                        timestamp=int(row["timestamp"]),
                        body=body,
                        known_codex_outbound=known,
                        identity_fallback=fallback,
                    )
                )
        next_before = items[-1].timestamp if items and has_more else None
        return MessagePageResponse(
            items=items,
            next_before=next_before,
            has_more=has_more,
        )

    @app.get("/api/v1/approvals", response_model=ApprovalPageResponse)
    def approvals(status: str | None = None) -> ApprovalPageResponse:
        if status not in {None, "pending", "executing", "executed", "failed", "rejected"}:
            raise HTTPException(status_code=422, detail="invalid approval status")
        with state_factory.connect() as state:
            rows = state.list_approvals(status)
        return ApprovalPageResponse(
            items=[_approval_response(row, settings) for row in rows]
        )

    @app.get("/api/v1/approvals/{approval_id}", response_model=ApprovalResponse)
    def approval(approval_id: str) -> ApprovalResponse:
        with state_factory.connect() as state:
            row = state.get_approval(approval_id)
        if row is None:
            raise HTTPException(status_code=404, detail="approval not found")
        return _approval_response(row, settings)

    @app.get("/api/v1/commands/{command_id}", response_model=CommandResponse)
    def command(command_id: str) -> CommandResponse:
        with state_factory.connect() as state:
            row = state.commands.get(command_id)
        if row is None:
            raise HTTPException(status_code=404, detail="command not found")
        return _command_response(row)

    @app.get("/api/v1/diagnostics/log-tail", response_model=LogTailResponse)
    def log_tail(lines: int = Query(default=100, ge=1, le=500)) -> LogTailResponse:
        path = settings.log_path
        if not path.exists():
            return LogTailResponse(lines=[], truncated=False, source=path.name)
        values = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = values[-lines:]
        return LogTailResponse(
            lines=[redact_text(value, maximum=4000) for value in selected],
            truncated=len(values) > len(selected),
            source=path.name,
        )

    def enqueue(
        command_type: CommandType,
        body: CommandSubmission,
        payload: dict[str, Any] | None = None,
    ) -> CommandResponse:
        with state_factory.connect() as state:
            command, created = state.commands.enqueue(
                command_type,
                payload or {},
                idempotency_key=body.idempotency_key,
            )
            if created:
                state.activity.add(
                    "command_queued",
                    ActivitySeverity.INFO,
                    f"Command {command_type.value} queued",
                    details={
                        "command_id": command.id,
                        "command_type": command_type.value,
                    },
                    correlation_id=command.id,
                )
        wake_event.set()
        return _command_response(command, created=created)

    @app.post(
        "/api/v1/commands/start-conversation",
        response_model=CommandResponse,
        status_code=202,
    )
    def start_conversation(body: CommandSubmission) -> CommandResponse:
        return enqueue(CommandType.START_CONVERSATION, body)

    @app.post(
        "/api/v1/commands/trigger-initiative",
        response_model=CommandResponse,
        status_code=202,
    )
    def trigger_initiative(body: CommandSubmission) -> CommandResponse:
        return enqueue(CommandType.TRIGGER_INITIATIVE, body)

    @app.post(
        "/api/v1/commands/resume",
        response_model=CommandResponse,
        status_code=202,
    )
    def resume(body: CommandSubmission) -> CommandResponse:
        return enqueue(CommandType.RESUME, body)

    @app.post("/api/v1/control/pause", response_model=PauseResponse)
    def pause(body: PauseRequest) -> PauseResponse:
        in_flight = request_pause(state_factory, wake_event, body.reason)
        return PauseResponse(
            paused=True,
            reason=body.reason or "paused from dashboard",
            work_in_flight=in_flight,
        )

    @app.post(
        "/api/v1/approvals/{approval_id}/reject",
        response_model=CommandResponse,
        status_code=202,
    )
    def reject(approval_id: str, body: CommandSubmission) -> CommandResponse:
        with state_factory.connect() as state:
            row = state.get_approval(approval_id)
            if row is None:
                raise HTTPException(status_code=404, detail="approval not found")
            if row["status"] != "pending":
                raise HTTPException(status_code=409, detail="approval is not pending")
        return enqueue(
            CommandType.REJECT_APPROVAL,
            body,
            {"approval_id": approval_id},
        )

    @app.post(
        "/api/v1/approvals/{approval_id}/execute",
        response_model=CommandResponse,
        status_code=202,
    )
    def execute(
        approval_id: str,
        body: ApprovalExecutionRequest,
    ) -> CommandResponse:
        with state_factory.connect() as state:
            row = state.get_approval(approval_id)
            if row is None:
                raise HTTPException(status_code=404, detail="approval not found")
            if row["status"] != "pending":
                raise HTTPException(status_code=409, detail="approval is not pending")
        return enqueue(
            CommandType.EXECUTE_APPROVAL,
            body,
            {"approval_id": approval_id, "post_result": body.post_result},
        )

    return app
