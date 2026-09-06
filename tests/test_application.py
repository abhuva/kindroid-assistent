from contextlib import nullcontext
from pathlib import Path

import pytest

from kindroid_relay.application import (
    ApprovalStateError,
    RelayApplication,
    pause_relay,
    reject_approval,
    resume_relay,
)
from kindroid_relay.models import ActionProposal, ExecutionResult
from kindroid_relay.state import RelayState

from test_relay import FakeKindroid, settings


class ExecutingAgent:
    def __init__(self, result: ExecutionResult, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail

    def execute_approved(self, *args):
        if self.fail:
            raise RuntimeError("execution failed")
        return self.result


def test_local_application_operations(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        pause_relay(state, "testing")
        assert state.is_paused()
        assert state.get_meta("pause_reason") == "testing"
        resume_relay(state)
        assert not state.is_paused()
        assert [event.kind for event in state.activity.after()] == [
            "relay_paused",
            "relay_resumed",
        ]

        approval_id = state.add_approval(
            "m1", ActionProposal("A task", "Do exactly one thing", "low")
        )
        reject_approval(state, approval_id)
        assert state.get_approval(approval_id)["status"] == "rejected"
        with pytest.raises(ApprovalStateError):
            reject_approval(state, approval_id)


def test_application_executes_and_records_approval(tmp_path: Path) -> None:
    config = settings(tmp_path)
    result = ExecutionResult(True, "Completed", "Done in the group")
    agent = ExecutingAgent(result)
    kindroid = FakeKindroid()

    with RelayState(config.state_path) as state:
        approval_id = state.add_approval(
            "m1", ActionProposal("A task", "Do exactly one thing", "low")
        )
        application = RelayApplication(
            config,
            state,
            kindroid,
            lambda: nullcontext(agent),
        )

        actual = application.execute_approval(approval_id, post_result=False)

        assert actual == result
        assert state.get_approval(approval_id)["status"] == "executed"
        assert [event.kind for event in state.activity.after() if event.kind != "message_received"] == [
            "approval_status",
            "codex_started",
            "codex_completed",
            "approval_status",
        ]


def test_application_records_failed_execution(tmp_path: Path) -> None:
    config = settings(tmp_path)
    result = ExecutionResult(False, "Not used", "")
    agent = ExecutingAgent(result, fail=True)

    with RelayState(config.state_path) as state:
        approval_id = state.add_approval(
            "m1", ActionProposal("A task", "Do exactly one thing", "low")
        )
        application = RelayApplication(
            config,
            state,
            FakeKindroid(),
            lambda: nullcontext(agent),
        )

        with pytest.raises(RuntimeError, match="execution failed"):
            application.execute_approval(approval_id)

        row = state.get_approval(approval_id)
        assert row["status"] == "failed"
        assert row["result"] == "execution failed"
        kinds = [event.kind for event in state.activity.after() if event.kind != "message_received"]
        assert kinds == [
            "approval_status",
            "codex_started",
            "codex_failed",
            "approval_status",
        ]


def test_approval_failure_redacts_credentials_from_persisted_state(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    class LeakingAgent:
        def execute_approved(self, *args):
            raise RuntimeError(
                "PROMPT_SECRET_SENTINEL kn_fake-secret Bearer abc.def "
                "Cookie: session-value"
            )

    with RelayState(config.state_path) as state:
        approval_id = state.add_approval(
            "m1", ActionProposal("A task", "Do exactly one thing", "low")
        )
        application = RelayApplication(
            config,
            state,
            FakeKindroid(),
            lambda: nullcontext(LeakingAgent()),
        )

        with pytest.raises(RuntimeError):
            application.execute_approval(approval_id)

        persisted = state.get_approval(approval_id)["result"]
        assert "kn_fake" not in persisted
        assert "abc.def" not in persisted
        assert "session-value" not in persisted
        serialized_events = repr(state.activity.after())
        assert "kn_fake" not in serialized_events
        assert "abc.def" not in serialized_events
        assert "session-value" not in serialized_events
        assert "PROMPT_SECRET_SENTINEL" not in serialized_events
