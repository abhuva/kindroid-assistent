from __future__ import annotations

import logging
import hashlib
import secrets
import sqlite3
import time
import html as html_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..activity import ActivitySeverity
from ..command_queue import (
    CommandConflictError,
    CommandRecord,
    CommandType,
    CommandValidationError,
)
from ..config import Settings
from ..log_tail import read_log_tail as _read_log_tail
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
    CommandPageResponse,
    DeliveryResponse,
    DeliveryPageResponse,
    CommandSubmission,
    MemorySubmission,
    InitiativeLimitRequest,
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
        approval_id=command.payload.get("approval_id"),
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
    room_links: list[tuple[str, str]] | None = None,
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

    @app.exception_handler(sqlite3.Error)
    async def database_error_handler(_request: Request, exc: sqlite3.Error) -> JSONResponse:
        # A handled error avoids Uvicorn's generic exception/connection-close path.
        # Extended SQLite codes have the primary code in the low byte (BUSY=5,
        # LOCKED=6); Python 3.10 lacks sqlite_errorcode, so allow known text there.
        code = getattr(exc, "sqlite_errorcode", 0) or 0
        busy = (code & 255) in {5, 6} or str(exc) in {
            "database is locked", "database table is locked",
        }
        logger.warning("Dashboard database request failed (%s)", "busy" if busy else "unavailable")
        return _error(
            503 if busy else 500,
            "database_busy" if busy else "database_unavailable",
            "Database is busy; inspect command status before retrying the same request."
            if busy else "Database is unavailable; preserve local state and investigate.",
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Dashboard request failed")
        return _error(500, "internal_error", "The dashboard request failed.")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        template = (static_dir / "index.html").read_text(encoding="utf-8")
        html = template.replace("{{CSRF_TOKEN}}", token).replace(
            "{{POLL_MS}}", str(settings.web_poll_seconds * 1000)
        )
        links = ''.join('<a href="' + html_module.escape(url, quote=True) + '">' + html_module.escape(label) + '</a> '
                        for label, url in (room_links or []))
        html = html.replace('{{ROOM_LINKS}}', links).replace('{{ROOM_LABEL}}', html_module.escape(settings.room_label or settings.kindroid_display_name))
        prefix = request.scope.get('root_path', '')
        html = html.replace('src="/static/', f'src="{prefix}/static/').replace('href="/static/', f'href="{prefix}/static/')
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
            initiative_budget = state.initiative_budget(settings.initiative_max_checks_24h)
            from ..schedule import DailyScheduler
            daily = DailyScheduler(state, settings)
            shared_schedule = daily.snapshot()
            if shared_schedule['configured']:
                relay_status['initiative_due_at'] = shared_schedule['next_due_at']
            from ..encounters import Encounters
            encounters = Encounters(state, settings)
            if settings.room_mode == 'shared':
                relay_status['initiative_due_at'] = state.get_int('shared_due_at') or None
            elif encounters.controls_schedule():
                choice = encounters.current()
                relay_status['initiative_due_at'] = choice['due_at'] if choice['status'] in {'pending', 'queued'} else None
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
                "daily": shared_schedule,
                "initiative_enabled": settings.initiative_enabled,
                "initiative_budget": initiative_budget,
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
                "activity_retention_days": settings.activity_retention_days,
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

    def message_response(state: RelayState, row: Any) -> MessageResponse:
        body = str(row["body"])
        known = row["sender_type"] == "user" and state.is_known_outbound(body)
        name = str(row["display_name"])
        participant = "unknown"
        if known:
            participant = "codex"
        elif row["sender_type"] == "ai" and (name.casefold() == settings.kindroid_display_name.casefold()
                or any(name.casefold() == p[1].casefold() for p in settings.participants)):
            participant = "hannah" if name.casefold() == 'hannah' else 'companion'
        elif row["sender_type"] == "user" and name.casefold() == settings.restore_persona_name.casefold():
            participant = "marc"
        return MessageResponse(
            id=str(row["remote_id"]), speaker=name,
            sender=str(row["sender"]), sender_type=str(row["sender_type"]),
            display_name=name, timestamp=int(row["timestamp"]), body=body,
            known_codex_outbound=known, participant=participant,
            history_confirmed=row["kindroid_id"] is not None,
            identity_fallback=known and name.casefold() != settings.codex_display_name.casefold(),
        )

    @app.get("/api/v1/messages/{message_id}", response_model=MessageResponse)
    def message_detail(message_id: str) -> MessageResponse:
        with state_factory.connect() as state:
            row = state.connection.execute(
                "SELECT * FROM messages WHERE remote_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="message not found")
            return message_response(state, row)

    @app.get("/api/v1/messages", response_model=MessagePageResponse)
    def messages(
        before: int | None = Query(default=None, ge=0),
        before_id: str | None = Query(default=None, max_length=256),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> MessagePageResponse:
        with state_factory.connect() as state:
            parameters: list[Any] = []
            predicate = ""
            if before is not None:
                if before_id is not None:
                    predicate = "WHERE (timestamp < ? OR (timestamp = ? AND remote_id < ?))"
                    parameters.extend([before, before, before_id])
                else:
                    predicate = "WHERE timestamp < ?"
                    parameters.append(before)
            parameters.append(limit + 1)
            rows = state.connection.execute(
                f"SELECT * FROM messages {predicate} "
                "ORDER BY timestamp DESC, remote_id DESC LIMIT ?",
                parameters,
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [message_response(state, row) for row in rows]
        next_before = items[-1].timestamp if items and has_more else None
        return MessagePageResponse(
            items=items,
            next_before=next_before,
            has_more=has_more,
            next_before_id=items[-1].id if items and has_more else None,
        )

    def delivery_response(row: Any) -> DeliveryResponse:
        return DeliveryResponse(
            id=row["id"], status=row["status"], source_message_id=row["source_message_id"],
            created_at=_utc(row["created_at"]), updated_at=_utc(row["updated_at"]),
        )

    @app.get("/api/v1/deliveries", response_model=DeliveryPageResponse)
    def deliveries() -> DeliveryPageResponse:
        with state_factory.connect() as state:
            rows = state.connection.execute(
                "SELECT * FROM outbox WHERE status IN ('uncertain', 'sending') "
                "ORDER BY updated_at DESC, id DESC LIMIT 51"
            ).fetchall()
        return DeliveryPageResponse(items=[delivery_response(row) for row in rows[:50]], has_more=len(rows) > 50)

    @app.get("/api/v1/deliveries/{delivery_id}", response_model=DeliveryResponse)
    def delivery_detail(delivery_id: str) -> DeliveryResponse:
        with state_factory.connect() as state:
            row = state.connection.execute("SELECT * FROM outbox WHERE id = ?", (delivery_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        return delivery_response(row)

    @app.get("/api/v1/commands", response_model=CommandPageResponse)
    def recent_commands() -> CommandPageResponse:
        with state_factory.connect() as state:
            rows = state.connection.execute(
                "SELECT * FROM commands ORDER BY status IN ('queued', 'running') DESC, "
                "requested_at DESC, rowid DESC LIMIT 50"
            ).fetchall()
            return CommandPageResponse(items=[_command_response(state.commands._record(row)) for row in rows])

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
    def log_tail(request: Request, lines: int = Query(default=100, ge=1, le=500)) -> Response:
        path = settings.log_path
        selected, truncated = _read_log_tail(path, lines)
        payload = LogTailResponse(
            lines=[redact_text(value, maximum=4000) for value in selected],
            truncated=truncated,
            source=path.name,
        )
        content = payload.model_dump_json()
        etag = '"' + hashlib.sha256(content.encode()).hexdigest() + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(content, media_type="application/json", headers={"ETag": etag})

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

    @app.get("/api/v1/memory")
    def memory_search(q: str = Query(default="", max_length=500)) -> dict[str, Any]:
        from ..memory_service import MemoryService
        with state_factory.connect() as state:
            memory = MemoryService(state, settings)
            return {**memory.store.search(q), "progress": memory.status()}

    @app.get("/api/v1/garden")
    def garden_items(layer: str = Query(default='', pattern='^(roots|growing|passing)?$'),
                     status: str = Query(default='', pattern='^(active|paused|archived|superseded)?$'),
                     after: str = Query(default='', max_length=128), limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        from ..garden import GardenStore, public_garden
        with state_factory.connect() as state:
            return public_garden(GardenStore(state.connection, settings.group_id).list_items(layer, status, after, limit))

    @app.get("/api/v1/garden/{item_id}")
    def garden_detail(item_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        from ..garden import GardenStore, public_garden
        with state_factory.connect() as state:
            try:
                return public_garden(GardenStore(state.connection, settings.group_id).detail(item_id, after, limit))
            except ValueError:
                raise HTTPException(status_code=404, detail='Garden item not found') from None

    @app.get("/api/v1/garden/{item_id}/trail")
    def garden_trail(item_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        from ..garden import GardenStore, public_garden
        with state_factory.connect() as state:
            try:
                return public_garden(GardenStore(state.connection, settings.group_id).trail(item_id, after, limit))
            except ValueError:
                raise HTTPException(status_code=404, detail='Garden item not found') from None

    @app.post("/api/v1/garden/corrections", response_model=CommandResponse, status_code=202)
    def garden_correct(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.GARDEN_CORRECT, body, body.payload)

    @app.get("/api/v1/exploration")
    def exploration_status() -> dict[str, Any]:
        from ..agency import AgencyStore
        from ..curiosity import CuriosityScheduler
        with state_factory.connect() as state:
            from ..schedule import effective_settings
            effective = effective_settings(state, settings)
            return {**AgencyStore(state, settings.group_id).snapshot(effective.exploration_max_sessions_24h),
                    'curiosity': CuriosityScheduler(state, settings).snapshot(),
                    'enabled': settings.exploration_enabled, 'paused': state.is_paused()}

    @app.get('/api/v1/discovery')
    def discovery_status() -> dict[str, Any]:
        from ..discovery import DiscoveryStore
        with state_factory.connect() as state:
            return DiscoveryStore(state, settings.group_id).snapshot()

    @app.post('/api/v1/settings/discovery', response_model=CommandResponse, status_code=202)
    def set_discovery(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.SET_DISCOVERY, body, body.payload)

    @app.post('/api/v1/discovery/public-topic', response_model=CommandResponse, status_code=202)
    def approve_public_topic(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.APPROVE_PUBLIC_TOPIC, body, body.payload)

    @app.post("/api/v1/commands/start-exploration", response_model=CommandResponse, status_code=202)
    def start_exploration(body: CommandSubmission) -> CommandResponse:
        if not settings.exploration_enabled:
            raise HTTPException(status_code=409, detail="Exploration disabled pending operator acceptance")
        return enqueue(CommandType.START_EXPLORATION, body)

    @app.post("/api/v1/exploration/candidates/review", response_model=CommandResponse, status_code=202)
    def review_candidate(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.REVIEW_CANDIDATE, body, body.payload)

    @app.post("/api/v1/exploration/close", response_model=CommandResponse, status_code=202)
    def close_exploration(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.CLOSE_EXPLORATION, body, body.payload)

    @app.post("/api/v1/exploration/interpret", response_model=CommandResponse, status_code=202)
    def interpret_exploration(body: MemorySubmission) -> CommandResponse:
        if not settings.exploration_enabled:
            raise HTTPException(status_code=409, detail="Exploration disabled")
        return enqueue(CommandType.INTERPRET_EXPLORATION, body, body.payload)

    @app.get("/api/v1/memory/{statement_id}")
    def memory_detail(statement_id: str) -> dict[str, Any]:
        from ..memory import MemoryStore
        with state_factory.connect() as state:
            item = MemoryStore(state.connection, settings.group_id).detail(statement_id)
        if item is None:
            raise HTTPException(status_code=404, detail="observation not found")
        return item

    @app.post("/api/v1/memory/commands/{operation}", response_model=CommandResponse, status_code=202)
    def memory_command(operation: str, body: MemorySubmission) -> CommandResponse:
        if operation not in {"save", "revise", "link", "evidence", "extract", "reindex"}:
            raise HTTPException(status_code=404, detail="unknown memory operation")
        return enqueue(CommandType("memory_" + operation), body, body.payload)

    @app.post(
        "/api/v1/commands/start-conversation",
        response_model=CommandResponse,
        status_code=202,
    )
    def start_conversation(body: CommandSubmission) -> CommandResponse:
        return enqueue(CommandType.START_CONVERSATION, body)

    @app.post("/api/v1/settings/initiative-limit", response_model=CommandResponse, status_code=202)
    def set_initiative_limit(body: InitiativeLimitRequest) -> CommandResponse:
        return enqueue(CommandType.SET_INITIATIVE_LIMIT, body, {"limit": body.limit})

    @app.get("/api/v1/schedule")
    def schedule_status() -> dict[str, Any]:
        from ..schedule import DailyScheduler
        with state_factory.connect() as state:
            return DailyScheduler(state, settings).snapshot()

    @app.get('/api/v1/encounters')
    def encounters_status():
        from ..encounters import Encounters
        from ..shared_recovery import recovery_status
        import json
        with state_factory.connect() as state:
            row = state.connection.execute('SELECT * FROM shared_sessions ORDER BY rowid DESC LIMIT 1').fetchone()
            return {**Encounters(state, settings).snapshot(), 'mode': settings.room_mode,
                    'session': dict(row) if row else None, 'next_shared_at': state.get_int('shared_due_at'),
                    'recovery': recovery_status(state, settings),
                    'shared_config': json.loads(state.get_meta('shared_config') or json.dumps({
                        'enabled': True, 'daily_time': f'{settings.shared_hour:02}:{settings.shared_minute:02}'}))}

    @app.post('/api/v1/settings/shared-session', response_model=CommandResponse, status_code=202)
    def set_shared_schedule(body: MemorySubmission):
        if settings.room_mode != 'shared':
            raise HTTPException(409, 'This is not a shared room')
        return enqueue(CommandType.SET_SHARED_SCHEDULE, body, body.payload)

    @app.post('/api/v1/settings/encounter', response_model=CommandResponse, status_code=202)
    def set_encounter(body: MemorySubmission):
        if not settings.participant_scheduling:
            raise HTTPException(409, 'Participant scheduling is not enabled for this room')
        return enqueue(CommandType.SET_ENCOUNTER, body, body.payload)

    @app.post('/api/v1/commands/end-session', response_model=CommandResponse, status_code=202)
    def end_session(body: CommandSubmission):
        return enqueue(CommandType.END_SESSION, body)

    @app.post('/api/v1/commands/recover-shared', response_model=CommandResponse, status_code=202)
    def recover_shared_session(body: MemorySubmission):
        if settings.room_mode != 'shared':
            raise HTTPException(409, 'This is not a shared room')
        return enqueue(CommandType.RECOVER_SHARED, body, body.payload)

    @app.post("/api/v1/settings/schedule", response_model=CommandResponse, status_code=202)
    def set_schedule(body: MemorySubmission) -> CommandResponse:
        return enqueue(CommandType.SET_SCHEDULE, body, body.payload)

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

    @app.post('/api/v1/commands/sync-history', response_model=CommandResponse, status_code=202)
    def sync_history(body: CommandSubmission) -> CommandResponse:
        return enqueue(CommandType.SYNC_HISTORY, body)

    @app.post("/api/v1/control/pause", response_model=PauseResponse)
    def pause(body: PauseRequest) -> PauseResponse:
        in_flight = request_pause(state_factory, wake_event, body.reason)
        return PauseResponse(
            paused=True,
            reason=body.reason or "paused from dashboard",
            work_in_flight=in_flight,
        )

    def approval_retry(body: CommandSubmission, command_type: CommandType, payload: dict[str, Any]) -> CommandResponse | None:
        with state_factory.connect() as state:
            row = state.connection.execute(
                "SELECT * FROM commands WHERE idempotency_key = ?", (body.idempotency_key,)
            ).fetchone()
            if row is not None:
                record = state.commands._record(row)
                if record.command_type != command_type or record.payload != payload:
                    raise CommandConflictError("idempotency key already belongs to a different action")
                return _command_response(record, created=False)
        return None

    @app.post(
        "/api/v1/approvals/{approval_id}/reject",
        response_model=CommandResponse,
        status_code=202,
    )
    def reject(approval_id: str, body: CommandSubmission) -> CommandResponse:
        previous = approval_retry(body, CommandType.REJECT_APPROVAL, {"approval_id": approval_id})
        if previous is not None:
            return previous
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
        previous = approval_retry(body, CommandType.EXECUTE_APPROVAL, {"approval_id": approval_id, "post_result": body.post_result})
        if previous is not None:
            return previous
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
