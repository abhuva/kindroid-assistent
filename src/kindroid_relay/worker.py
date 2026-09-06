from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from typing import Any

from .activity import ActivitySeverity, WorkerPhase
from .application import RelayApplication, pause_relay, reject_approval, resume_relay
from .codex_agent import CodexAgent
from .command_queue import (
    CommandRecord,
    CommandStatus,
    CommandType,
    SIDE_EFFECTING_COMMANDS,
)
from .config import Settings
from .kindroid_api import KindroidClient
from .redaction import redact_text
from .relay import HistoryCooldownError
from .state import RelayState, RelayStateFactory


logger = logging.getLogger(__name__)


ApplicationFactory = Any


class RelayWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        wake_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        application_factory: ApplicationFactory | None = None,
    ) -> None:
        self.settings = settings
        self.wake_event = wake_event or threading.Event()
        self.stop_event = stop_event or threading.Event()
        self.application_factory = application_factory

    @contextmanager
    def _default_application(self, state: RelayState) -> Iterator[RelayApplication]:
        with KindroidClient(self.settings.api_key, self.settings.base_url) as kindroid:
            agent_factory = lambda: CodexAgent(
                state,
                cwd=self.settings.codex_cwd,
                prompt_path=self.settings.prompt_path,
                model=self.settings.codex_model,
                approved_sandbox=self.settings.approved_sandbox,
            )
            yield RelayApplication(
                self.settings,
                state,
                kindroid,
                agent_factory,
                phase_context=lambda phase, note: self._scoped_phase(
                    state, phase, note
                ),
            )

    def _application(self, state: RelayState) -> AbstractContextManager[Any]:
        if self.application_factory is not None:
            return self.application_factory(state)
        return self._default_application(state)

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def _poll_delay(self, handled: int) -> int:
        return (
            self.settings.poll_seconds
            if handled
            else self.settings.idle_poll_seconds
        )

    def _phase(
        self,
        state: RelayState,
        phase: WorkerPhase,
        *,
        command_id: str | None = None,
        note: str = "",
    ) -> None:
        previous = state.worker_status.get()
        state.worker_status.update(
            phase,
            active_command_id=command_id,
            note=note,
            change_phase=previous is None or previous.phase != phase,
        )
        if previous is None or previous.phase != phase:
            state.activity.add(
                "worker_phase",
                ActivitySeverity.INFO,
                f"Worker entered {phase.value}",
                details={
                    "phase": phase.value,
                    **({"command_id": command_id} if command_id else {}),
                },
                correlation_id=command_id,
            )

    @contextmanager
    def _scoped_phase(
        self,
        state: RelayState,
        phase: WorkerPhase,
        note: str,
    ) -> Iterator[None]:
        previous = state.worker_status.get()
        command_id = previous.active_command_id if previous else None
        self._phase(state, phase, command_id=command_id, note=note)
        try:
            yield
        finally:
            if state.is_paused():
                self._phase(
                    state,
                    WorkerPhase.PAUSED,
                    note=state.get_meta("pause_reason", "") or "",
                )
            elif previous is not None:
                self._phase(
                    state,
                    previous.phase,
                    command_id=previous.active_command_id,
                    note=previous.note,
                )

    def _recover(self, state: RelayState) -> None:
        recovered = state.commands.recover_interrupted()
        must_pause = False
        for command in recovered:
            state.activity.add(
                "command_interrupted",
                ActivitySeverity.WARNING,
                f"Command {command.command_type.value} was interrupted",
                details={
                    "command_id": command.id,
                    "command_type": command.command_type.value,
                },
                correlation_id=command.id,
            )
            must_pause = must_pause or command.command_type in SIDE_EFFECTING_COMMANDS
        if must_pause:
            reason = "a side-effecting command was interrupted; inspect before resuming"
            state.pause(reason)
            state.activity.add(
                "relay_paused",
                ActivitySeverity.WARNING,
                "Relay paused after interrupted command recovery",
                details={"reason": reason},
            )

    @staticmethod
    def _command_phase(command: CommandRecord) -> WorkerPhase:
        if command.command_type == CommandType.EXECUTE_APPROVAL:
            return WorkerPhase.EXECUTING_APPROVAL
        return WorkerPhase.POLLING

    def _dispatch(self, application: RelayApplication, command: CommandRecord) -> dict[str, Any]:
        if command.command_type == CommandType.START_CONVERSATION:
            return {"handled": application.start_conversation()}
        if command.command_type == CommandType.TRIGGER_INITIATIVE:
            return application.trigger_initiative()
        if command.command_type == CommandType.SET_INITIATIVE_LIMIT:
            return application.set_initiative_limit(command.payload["limit"])
        if command.command_type == CommandType.RESUME:
            return {"status": resume_relay(application.state)}
        if command.command_type == CommandType.REJECT_APPROVAL:
            reject_approval(application.state, str(command.payload["approval_id"]))
            return {"approval_id": command.payload["approval_id"], "status": "rejected"}
        if command.command_type == CommandType.EXECUTE_APPROVAL:
            result = application.execute_approval(
                str(command.payload["approval_id"]),
                post_result=bool(command.payload["post_result"]),
            )
            return {
                "approval_id": command.payload["approval_id"],
                "success": result.success,
                "summary": result.summary,
                "posted_result": bool(command.payload["post_result"] and result.group_reply),
            }
        raise RuntimeError(f"unsupported command type: {command.command_type.value}")

    def _run_command(
        self,
        state: RelayState,
        application: RelayApplication,
        command: CommandRecord,
    ) -> None:
        self._phase(
            state,
            self._command_phase(command),
            command_id=command.id,
            note=command.command_type.value,
        )
        state.activity.add(
            "command_started",
            ActivitySeverity.INFO,
            f"Command {command.command_type.value} started",
            details={
                "command_id": command.id,
                "command_type": command.command_type.value,
            },
            correlation_id=command.id,
        )
        try:
            result = self._dispatch(application, command)
        except Exception as exc:
            state.commands.transition(
                command.id,
                CommandStatus.FAILED,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            state.activity.add(
                "command_failed",
                ActivitySeverity.ERROR,
                f"Command {command.command_type.value} failed",
                details={
                    "command_id": command.id,
                    "command_type": command.command_type.value,
                    "error_type": exc.__class__.__name__,
                },
                correlation_id=command.id,
            )
            logger.exception("Command %s failed", command.id)
            return
        state.commands.transition(
            command.id,
            CommandStatus.COMPLETED,
            result=result,
        )
        state.activity.add(
            "command_completed",
            ActivitySeverity.INFO,
            f"Command {command.command_type.value} completed"
            + (f": {result['initiative_outcome']}" if command.command_type == CommandType.TRIGGER_INITIATIVE else ""),
            details={
                "command_id": command.id,
                "command_type": command.command_type.value,
                **({"outcome": result["initiative_outcome"]} if command.command_type == CommandType.TRIGGER_INITIATIVE else {}),
            },
            correlation_id=command.id,
        )

    def run(self) -> None:
        factory = RelayStateFactory(self.settings.state_path)
        with factory.connect() as state:
            state.activity.prune(self.settings.activity_retention_days)
            self._phase(state, WorkerPhase.STARTING)
            state.activity.add(
                "process_started",
                ActivitySeverity.INFO,
                "Relay worker started",
            )
            self._recover(state)
            next_poll_at = 0.0
            try:
                with self._application(state) as application:
                    while not self.stop_event.is_set():
                        state.worker_status.heartbeat()
                        command = state.commands.claim_next()
                        if command is not None:
                            self._run_command(state, application, command)
                            next_poll_at = min(next_poll_at, time.time())
                            continue

                        now = time.time()
                        cooldown_until = state.get_int("history_cooldown_until", 0)
                        due_at = state.get_int("initiative_due_at", 0)
                        initiative_due = bool(
                            self.settings.initiative_enabled
                            and due_at
                            and now >= due_at
                        )
                        if state.is_paused():
                            self._phase(
                                state,
                                WorkerPhase.PAUSED,
                                note=state.get_meta("pause_reason", "") or "",
                            )
                            next_poll_at = max(
                                next_poll_at,
                                now + self.settings.idle_poll_seconds,
                            )
                        elif cooldown_until > now:
                            cooldown_display = (
                                datetime.fromtimestamp(cooldown_until)
                                .astimezone()
                                .isoformat()
                            )
                            self._phase(
                                state,
                                WorkerPhase.DEGRADED,
                                note=(
                                    "Kindroid history polling is cooling down until "
                                    f"{cooldown_display}"
                                ),
                            )
                            next_poll_at = max(next_poll_at, float(cooldown_until))
                        elif now >= next_poll_at or initiative_due:
                            self._phase(state, WorkerPhase.POLLING)
                            state.activity.add(
                                "relay_cycle_started",
                                ActivitySeverity.INFO,
                                "Relay polling cycle started",
                                details={
                                    "request_count": state.history_request_count()
                                },
                            )
                            try:
                                handled = application.run_once()
                            except HistoryCooldownError as exc:
                                logger.warning(
                                    "Kindroid history polling is cooling down until %s",
                                    exc.retry_at,
                                )
                                state.activity.add(
                                    "relay_cycle_completed",
                                    ActivitySeverity.WARNING,
                                    "Relay polling cycle deferred by history cooldown",
                                    details={"outcome": "history_cooldown"},
                                )
                                self._phase(
                                    state,
                                    WorkerPhase.DEGRADED,
                                    note="Kindroid history rate limit cooldown",
                                )
                                next_poll_at = float(exc.retry_at)
                            except Exception as exc:
                                logger.exception("Relay cycle failed")
                                state.activity.add(
                                    "relay_cycle_failed",
                                    ActivitySeverity.ERROR,
                                    "Relay cycle failed",
                                    details={"error_type": exc.__class__.__name__},
                                )
                                self._phase(
                                    state,
                                    WorkerPhase.DEGRADED,
                                    note=redact_text(exc, maximum=500),
                                )
                                next_poll_at = (
                                    time.time() + self.settings.idle_poll_seconds
                                )
                            else:
                                state.activity.add(
                                    "relay_cycle_completed",
                                    ActivitySeverity.INFO,
                                    "Relay polling cycle completed",
                                    details={
                                        "request_count": state.history_request_count(),
                                        "turn_count": handled,
                                    },
                                )
                                if state.is_paused():
                                    self._phase(
                                        state,
                                        WorkerPhase.PAUSED,
                                        note=state.get_meta("pause_reason", "") or "",
                                    )
                                else:
                                    self._phase(state, WorkerPhase.IDLE)
                                delay = self._poll_delay(handled)
                                next_poll_at = time.time() + delay
                        else:
                            self._phase(state, WorkerPhase.IDLE)

                        deadlines = [
                            time.time() + self.settings.worker_heartbeat_seconds,
                            next_poll_at,
                        ]
                        if (
                            self.settings.initiative_enabled
                            and due_at
                            and cooldown_until <= time.time()
                        ):
                            deadlines.append(float(due_at))
                        timeout = max(0.05, min(deadlines) - time.time())
                        self.wake_event.wait(timeout)
                        self.wake_event.clear()
            finally:
                self._phase(state, WorkerPhase.STOPPING)
                state.activity.add(
                    "process_stopped",
                    ActivitySeverity.INFO,
                    "Relay worker stopped",
                )


def request_pause(
    state_factory: RelayStateFactory,
    wake_event: threading.Event,
    reason: str,
) -> bool:
    safe_reason = redact_text(reason or "paused from dashboard", maximum=500)
    with state_factory.connect() as state:
        status = state.worker_status.get()
        in_flight = bool(
            status
            and status.phase
            in {
                WorkerPhase.POLLING,
                WorkerPhase.WAITING_FOR_KINDROID,
                WorkerPhase.RUNNING_CODEX,
                WorkerPhase.SENDING,
                WorkerPhase.EXECUTING_APPROVAL,
            }
        )
        pause_relay(state, safe_reason)
    wake_event.set()
    return in_flight
