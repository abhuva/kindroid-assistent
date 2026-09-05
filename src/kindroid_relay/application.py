from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .models import ExecutionResult
from .relay import Relay
from .state import RelayState


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
    ) -> None:
        self.settings = settings
        self.state = state
        self.agent_factory = agent_factory
        self.relay = Relay(settings, state, kindroid, agent_factory)

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

    def trigger_initiative(self) -> int:
        return self.relay.trigger_initiative_now()

    def start_conversation(self) -> int:
        return self.relay.start_conversation_now()

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
        self.state.update_approval(approval_id, "executing")
        try:
            with self.agent_factory() as agent:
                result = agent.execute_approved(
                    row["id"],
                    row["summary"],
                    row["instruction"],
                    row["risk"],
                )
        except Exception as exc:
            self.state.update_approval(approval_id, "failed", str(exc))
            raise
        status = "executed" if result.success else "failed"
        self.state.update_approval(approval_id, status, result.summary)
        if post_result and result.group_reply:
            self.relay.post_execution_result(result.group_reply)
        return result


def pause_relay(state: RelayState, reason: str) -> dict[str, object]:
    state.pause(reason)
    return state.status()


def resume_relay(state: RelayState) -> dict[str, object]:
    state.resume()
    return state.status()


def reject_approval(state: RelayState, approval_id: str) -> None:
    row = state.get_approval(approval_id)
    if row is None or row["status"] != "pending":
        raise ApprovalStateError("approval is missing or not pending")
    state.update_approval(approval_id, "rejected")

