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
        self._startup_history_pending = True

    def _sync_startup_history(self, state: RelayState, application: RelayApplication) -> bool:
        """One read-only synchronization per launch, deferred by quiet/cooldown.

        Consume the attempt before I/O: failure must not create an idle retry loop.
        Paused rooms may import/reconcile history, but are never resumed here.
        """
        if not self._startup_history_pending or not isinstance(application, RelayApplication):
            return False
        from .quiet_hours import QuietHours
        if (QuietHours(state, application.settings).blocked()
                or state.get_int('history_cooldown_until') > time.time()):
            return False
        self._startup_history_pending = False
        if not state.has_active_conversation_work():
            # A stale audit marker must not turn newly imported chat into a session.
            application.relay._end_activity_session('inactive_before_startup_sync')
        with self._scoped_phase(state, WorkerPhase.POLLING, 'Synchronizing startup history once'):
            try:
                application.sync_history(None)
            except Exception as exc:
                state.activity.add('history_sync', ActivitySeverity.ERROR,
                                   'Startup history sync failed; use manual Sync history to retry',
                                   details={'reason': 'startup', 'outcome': 'history_error',
                                            'error_type': type(exc).__name__})
        return True

    @contextmanager
    def _default_application(self, state: RelayState) -> Iterator[RelayApplication]:
        with KindroidClient(self.settings.api_key, self.settings.base_url) as kindroid:
            agent_factory = lambda: CodexAgent(
                state,
                cwd=self.settings.codex_cwd,
                prompt_path=self.settings.prompt_path,
                model=self.settings.codex_model,
                approved_sandbox=self.settings.approved_sandbox,
                memory_settings=self.settings,
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
        from .experience import ExperienceStore
        if ExperienceStore(state, self.settings.group_id, self.settings.kindroid_display_name).recover():
            state.pause('Exploration experience interrupted; inspect result delivery before resuming')
        from .agency import AgencyStore
        AgencyStore(state, self.settings.group_id).recover_interrupted()
        from .exploration_response import ResponseStore
        if ResponseStore(state, self.settings.group_id, self.settings.kindroid_display_name).recover():
            state.pause('Exploration follow-up interrupted; inspect delivery before resuming')
        recovered = state.commands.recover_interrupted()
        from .retired_exploration import retire_safe_work
        retire_safe_work(state, self.settings)
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
        from .command_queue import RETIRED_SCHEDULING_COMMANDS
        if self.settings.participant_scheduling and command.command_type in RETIRED_SCHEDULING_COMMANDS:
            return {'outcome': 'retired', 'reason': 'Kin-chosen check-ins replaced daily schedules and invitations'}
        if command.command_type == CommandType.SET_QUIET_HOURS:
            from .quiet_hours import QuietHours
            return QuietHours(application.state, application.settings).save(command.payload)
        from .encounters import Encounters
        from .shared import SharedConversation
        if command.command_type == CommandType.RECOVER_SHARED:
            from .shared_recovery import recover_shared
            return recover_shared(application, command.id, command.payload)
        if command.command_type == CommandType.SET_SHARED_SCHEDULE:
            return SharedConversation(application).configure(command.payload)
        if command.command_type == CommandType.SET_ENCOUNTER:
            if not application.settings.participant_scheduling:
                raise ValueError('Participant scheduling is not enabled for this room')
            return Encounters(application.state, application.settings).override(command.payload['due_at'])
        if command.command_type == CommandType.CHOSEN_ENCOUNTER:
            return Encounters(application.state, application.settings).run(application, command.id)
        if command.command_type == CommandType.END_SESSION:
            return SharedConversation(application).end()
        if command.command_type == CommandType.START_SHARED:
            shared = SharedConversation(application)
            raw = application.state.get_meta('shared_config')
            import json
            due = command.payload['scheduled_at']
            local = time.localtime(due)
            if (not due <= time.time() <= due + 180
                    or (raw and not json.loads(raw)['enabled'])
                    or (local.tm_hour, local.tm_min) != (shared.settings.shared_hour, shared.settings.shared_minute)):
                return {'outcome': 'missed_or_changed'}
            return shared.start(command.id)
        if command.command_type == CommandType.SYNC_HISTORY:
            return application.sync_history(command.id)
        if command.command_type == CommandType.SET_DISCOVERY:
            return application.discovery.save(command.payload, command.id)
        if command.command_type == CommandType.APPROVE_PUBLIC_TOPIC:
            return application.experiences.store.approve_topic(command.payload['session_id'], command.payload['topic'], command.id)
        if command.command_type == CommandType.SET_SCHEDULE:
            from .schedule import DailyScheduler
            return DailyScheduler(application.state, self.settings).save(command.payload, command.id)
        if command.command_type == CommandType.SCHEDULED_CHECK_IN:
            return application.start_scheduled_check_in(command.id)
        if command.command_type == CommandType.SCHEDULED_EXPLORATION:
            return application.start_scheduled_exploration(command.id)
        if command.command_type == CommandType.GARDEN_CORRECT:
            from .garden import GardenStore
            return GardenStore(application.state.connection, application.settings.group_id).correct(command.id, command.payload)
        if command.command_type == CommandType.INTERPRET_EXPLORATION:
            return application.interpret_exploration(command.id, **command.payload)
        if command.command_type == CommandType.START_EXPLORATION:
            return application.start_exploration(command.id)
        if command.command_type == CommandType.CLOSE_EXPLORATION:
            from .agency import AgencyStore
            return AgencyStore(application.state, application.settings.group_id).close(command.id, **command.payload)
        if command.command_type == CommandType.REVIEW_CANDIDATE:
            from .agency import AgencyStore
            return AgencyStore(application.state, application.settings.group_id).revise_candidate(command.id, **command.payload)
        if command.command_type.value.startswith("memory_"):
            return application.memory.command(command.command_type.value.removeprefix("memory_"), command.payload, command.id)
        if command.command_type == CommandType.START_CONVERSATION:
            if self.settings.room_mode == 'shared':
                return SharedConversation(application).start(command.id)
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
        rejections_before = getattr(getattr(application, 'relay', None), 'history_rejections', 0)
        try:
            result = self._dispatch(application, command)
        except Exception as exc:
            memory_failure = (command.command_type in {CommandType.SET_QUIET_HOURS, CommandType.RECOVER_SHARED, CommandType.SET_ENCOUNTER, CommandType.CHOSEN_ENCOUNTER,
                                                      CommandType.START_SHARED, CommandType.END_SESSION, CommandType.SET_SHARED_SCHEDULE}
                              or command.command_type.value.startswith("memory_")
                              or command.command_type in {CommandType.SYNC_HISTORY, CommandType.SET_SCHEDULE, CommandType.SCHEDULED_CHECK_IN,
                                                         CommandType.SET_DISCOVERY, CommandType.APPROVE_PUBLIC_TOPIC}
                              or command.command_type in {CommandType.START_EXPLORATION, CommandType.SCHEDULED_EXPLORATION, CommandType.REVIEW_CANDIDATE, CommandType.CLOSE_EXPLORATION, CommandType.INTERPRET_EXPLORATION, CommandType.GARDEN_CORRECT})
            if isinstance(application, RelayApplication) and command.command_type in SIDE_EFFECTING_COMMANDS:
                application.relay._end_activity_session('failed_inspect_activity')
            state.commands.transition(
                command.id,
                CommandStatus.FAILED,
                error="History sync failed; inspect activity and history limits" if command.command_type == CommandType.SYNC_HISTORY else
                      "Local context operation failed; inspect its source, budget and configuration" if memory_failure else f"{exc.__class__.__name__}: {exc}",
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
            if command.command_type == CommandType.SYNC_HISTORY:
                logger.warning("History sync command %s failed", command.id)
            elif memory_failure:
                logger.warning("Memory command %s failed", command.id)
            else:
                logger.exception("Command %s failed", command.id)
            return
        if getattr(getattr(application, 'relay', None), 'history_rejections', 0) > rejections_before:
            state.commands.transition(command.id, CommandStatus.FAILED, result=result,
                                      error='Kindroid rejected history access (HTTP 429); inspect before resuming')
            state.activity.add('command_failed', ActivitySeverity.ERROR,
                               'Command failed because Kindroid rejected history access',
                               details={'command_id': command.id, 'outcome': 'history_rate_limited'},
                               correlation_id=command.id)
            return
        state.commands.transition(
            command.id,
            CommandStatus.COMPLETED,
            result=result,
        )
        sync_blocked = (command.command_type == CommandType.SYNC_HISTORY
                        and result.get('sync_outcome') not in {'synced', 'bootstrapped'})
        recovery_blocked = (command.command_type == CommandType.RECOVER_SHARED
                            and result.get('outcome') not in {'recovered', 'already_recovered'})
        state.activity.add(
            "command_completed",
            ActivitySeverity.WARNING if sync_blocked or recovery_blocked else ActivitySeverity.INFO,
            ('Shared session recovery blocked; inspect the command outcome' if recovery_blocked else
             'History sync blocked; saved conversation was not fully synchronized' if sync_blocked else
             f"Command {command.command_type.value} completed")
            + (f": {result['initiative_outcome']}" if command.command_type == CommandType.TRIGGER_INITIATIVE else ""),
            details={
                "command_id": command.id,
                "command_type": command.command_type.value,
                **({"outcome": result["initiative_outcome"]} if command.command_type == CommandType.TRIGGER_INITIATIVE else {}),
            },
            correlation_id=command.id,
        )

    def run(self) -> None:
        self._startup_history_pending = True
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
            from .curiosity import CuriosityScheduler
            from .schedule import DailyScheduler, effective_settings
            daily = DailyScheduler(state, self.settings)
            next_poll_at = 0.0
            try:
                with self._application(state) as application:
                    while not self.stop_event.is_set():
                        if isinstance(application, RelayApplication):
                            application.settings = effective_settings(state, self.settings)
                            application.relay.settings = application.settings
                        curiosity = CuriosityScheduler(state, self.settings)
                        state.worker_status.heartbeat()
                        from .quiet_hours import QuietHours, deferred_commands
                        quiet = QuietHours(state, self.settings).blocked()
                        if self._sync_startup_history(state, application):
                            continue
                        command = state.commands.claim_next(exclude=deferred_commands() if quiet else frozenset())
                        if command is not None:
                            self._run_command(state, application, command)
                            if command.command_type == CommandType.SYNC_HISTORY:
                                # This command already pulled history. Do not force an
                                # immediate second read or conversation cycle afterward.
                                next_poll_at = max(next_poll_at, time.time() + self.settings.poll_seconds)
                                self._phase(state, WorkerPhase.PAUSED if state.is_paused() else WorkerPhase.IDLE)
                            elif (not command.command_type.value.startswith("memory_")
                                    and command.command_type not in {CommandType.REVIEW_CANDIDATE, CommandType.CLOSE_EXPLORATION, CommandType.GARDEN_CORRECT}):
                                next_poll_at = min(next_poll_at, time.time())
                            continue

                        if quiet:
                            self._phase(state, WorkerPhase.IDLE, note='Companion quiet hours; automatic work is waiting')
                            self.wake_event.wait(1.0)
                            self.wake_event.clear()
                            continue
                        from .encounters import Encounters
                        if Encounters(state, self.settings).tick() or (not self.settings.participant_scheduling and (daily.tick() or curiosity.tick())):
                            continue  # the next loop claims its durable command normally
                        now = time.time()
                        active_conversation = state.has_active_conversation_work()
                        memory_deadline = now + self.settings.memory_extraction_timeout_seconds
                        if self.settings.participant_scheduling:
                            choice = Encounters(state, self.settings).current()
                            next_initiative = choice['due_at'] if choice else 0
                        else:
                            next_initiative = daily.row()['next_due_at'] if daily.configured else state.get_int("initiative_due_at", 0) if self.settings.initiative_enabled else 0
                        if (not state.is_paused() and (not active_conversation or memory_deadline < next_poll_at)
                                and (not next_initiative or memory_deadline < next_initiative)
                                and hasattr(application, "memory")):
                            try:
                                application.memory.automatic_tick()
                            except Exception:
                                # Optional local memory must not fail or repeat a conversation cycle.
                                logger.warning("Local memory capture unavailable")
                        now = time.time()
                        cooldown_until = state.get_int("history_cooldown_until", 0)
                        due_at = state.get_int("initiative_due_at", 0)
                        initiative_due = bool(
                            not self.settings.participant_scheduling and not daily.configured and self.settings.initiative_enabled
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
                        elif (active_conversation and now >= next_poll_at) or initiative_due:
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
                            self._phase(state, WorkerPhase.IDLE, note='Waiting for a scheduled check-in or manual start; chat monitoring is off')

                        deadlines = [
                            time.time() + self.settings.worker_heartbeat_seconds,
                        ]
                        if active_conversation:
                            deadlines.append(next_poll_at)
                        curiosity_due = curiosity.deadline()
                        if curiosity_due:
                            deadlines.append(float(curiosity_due))
                        if (
                            not self.settings.participant_scheduling and not daily.configured and self.settings.initiative_enabled
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
