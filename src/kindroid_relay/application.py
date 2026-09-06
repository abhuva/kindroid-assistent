from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from .activity import ActivitySeverity, WorkerPhase
from .models import ExecutionResult
from .redaction import redact_text
from .relay import PhaseContext, Relay
from .state import RelayState
from .command_queue import CommandType, validate_payload


class ApprovalStateError(RuntimeError):
    pass


AgentFactory = Callable[[], AbstractContextManager[Any]]


class RelayApplication:
    """Reusable application operations shared by CLI and server workers."""

    def __init__(
        self,
        settings: Any,
        state: RelayState,
        kindroid: Any,
        agent_factory: AgentFactory,
        phase_context: PhaseContext | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.agent_factory = agent_factory
        self.phase_context = phase_context or (lambda _phase, _note: nullcontext())
        self.relay = Relay(
            settings,
            state,
            kindroid,
            agent_factory,
            phase_context=self.phase_context,
        )

    def bootstrap(
        self,
        *,
        respond_to_latest: bool = False,
        force: bool = False,
    ) -> int:
        return self.relay.bootstrap(
            respond_to_latest=respond_to_latest,
            force=force,
        )

    def run_once(self) -> int:
        return self.relay.run_once()

    def trigger_initiative(self) -> dict[str, Any]:
        handled = self.relay.trigger_initiative_now()
        outcome = self.relay.initiative_outcome or "unknown"
        return {
            "handled": handled,
            "initiative_outcome": outcome,
            "check_in_sent": outcome in {
                "check_in_sent", "check_in_answered", "check_in_sent_user_turn",
                "check_in_waiting_for_history",
            },
        }

    def start_conversation(self) -> int:
        return self.relay.start_conversation_now()

    def set_initiative_limit(self, limit: int) -> dict[str, Any]:
        limit = validate_payload(CommandType.SET_INITIATIVE_LIMIT, {"limit": limit})["limit"]
        previous = self.state.initiative_limit(self.settings.initiative_max_checks_24h)
        with self.state.connection:
            self.state.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('initiative_limit_override', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(limit),)
            )
            self.state.activity.add(
                "initiative_limit_changed", ActivitySeverity.INFO,
                f"Daily check-in limit changed from {previous} to {limit}",
                details={"previous_limit": previous, "limit": limit},
            )
        return {"limit": limit, "previous_limit": previous}

    def run_forever(self) -> None:
        self.relay.run_forever()

    def execute_approval(
        self,
        approval_id: str,
        *,
        post_result: bool = False,
    ) -> ExecutionResult:
        row = self.state.get_approval(approval_id)
        if row is None or row["status"] != "pending":
            raise ApprovalStateError("approval is missing or not pending")
        # Context acquisition is read-only and precedes the executing transition.
        # A cooldown or unresolved effect leaves the approval pending.
        self.relay.synchronize_context()
        self.state.update_approval(approval_id, "executing")
        self.state.activity.add(
            "approval_status",
            ActivitySeverity.INFO,
            "Approval execution started",
            details={"approval_id": approval_id, "status": "executing"},
            correlation_id=approval_id,
        )
        self.state.activity.add(
            "codex_started",
            ActivitySeverity.INFO,
            "Codex started approved execution",
            details={"approval_id": approval_id},
            correlation_id=approval_id,
        )
        try:
            with self.phase_context(
                WorkerPhase.RUNNING_CODEX, "Executing approved Codex action"
            ):
                with self.agent_factory() as agent:
                    result = agent.execute_approved(
                        row["id"],
                        row["summary"],
                        row["instruction"],
                        row["risk"],
                    )
        except Exception as exc:
            safe_error = redact_text(exc, maximum=500)
            self.state.update_approval(approval_id, "failed", safe_error)
            self.state.activity.add(
                "codex_failed",
                ActivitySeverity.ERROR,
                "Codex approved execution failed",
                details={
                    "approval_id": approval_id,
                    "error_type": exc.__class__.__name__,
                },
                correlation_id=approval_id,
            )
            self.state.activity.add(
                "approval_status",
                ActivitySeverity.ERROR,
                "Approval execution failed",
                details={"approval_id": approval_id, "status": "failed"},
                correlation_id=approval_id,
            )
            raise
        self.state.activity.add(
            "codex_completed",
            ActivitySeverity.INFO,
            "Codex approved execution completed",
            details={"approval_id": approval_id},
            correlation_id=approval_id,
        )
        status = "executed" if result.success else "failed"
        self.state.update_approval(approval_id, status, result.summary)
        self.state.activity.add(
            "approval_status",
            ActivitySeverity.INFO if result.success else ActivitySeverity.ERROR,
            f"Approval execution {status}",
            details={"approval_id": approval_id, "status": status},
            correlation_id=approval_id,
        )
        if post_result and result.group_reply:
            self.relay.post_execution_result(result.group_reply)
        return result


def pause_relay(state: RelayState, reason: str) -> dict[str, object]:
    was_paused = state.is_paused()
    state.pause(reason)
    if not was_paused:
        state.activity.add(
            "relay_paused",
            ActivitySeverity.WARNING,
            "Relay paused",
            details={"reason": reason},
        )
    return state.status()


def resume_relay(state: RelayState) -> dict[str, object]:
    was_paused = state.is_paused()
    state.resume()
    if was_paused:
        state.activity.add(
            "relay_resumed",
            ActivitySeverity.INFO,
            "Relay resumed",
        )
    return state.status()


def reject_approval(state: RelayState, approval_id: str) -> None:
    row = state.get_approval(approval_id)
    if row is None or row["status"] != "pending":
        raise ApprovalStateError("approval is missing or not pending")
    state.update_approval(approval_id, "rejected")
    state.activity.add(
        "approval_status",
        ActivitySeverity.INFO,
        "Approval rejected",
        details={"approval_id": approval_id, "status": "rejected"},
        correlation_id=approval_id,
    )
