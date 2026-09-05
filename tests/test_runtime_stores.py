import sqlite3
import threading
from pathlib import Path

import pytest

from kindroid_relay.activity import ActivitySeverity, WorkerPhase
from kindroid_relay.command_queue import (
    CommandConflictError,
    CommandStatus,
    CommandType,
    CommandValidationError,
    SIDE_EFFECTING_COMMANDS,
)
from kindroid_relay.state import RelayState, RelayStateFactory


def test_command_validation_and_lifecycle(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(CommandValidationError):
            state.commands.enqueue(
                CommandType.START_CONVERSATION,
                {"unexpected": True},
                idempotency_key="invalid",
            )

        queued, created = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="start-1",
        )
        assert created
        assert queued.status == CommandStatus.QUEUED

        same, created = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="start-1",
        )
        assert not created
        assert same.id == queued.id

        with pytest.raises(CommandConflictError, match="already queued"):
            state.commands.enqueue(
                CommandType.START_CONVERSATION,
                {},
                idempotency_key="start-2",
            )

        claimed = state.commands.claim_next()
        assert claimed is not None
        assert claimed.id == queued.id
        assert claimed.status == CommandStatus.RUNNING
        assert state.commands.claim_next() is None

        completed = state.commands.transition(
            claimed.id,
            CommandStatus.COMPLETED,
            result={"handled": 1},
        )
        assert completed.result == {"handled": 1}
        assert completed.completed_at is not None

        with pytest.raises(CommandConflictError, match="cannot transition"):
            state.commands.transition(completed.id, CommandStatus.FAILED)


@pytest.mark.parametrize(
    ("via_running", "terminal"),
    [
        (False, CommandStatus.CANCELLED),
        (True, CommandStatus.COMPLETED),
        (True, CommandStatus.FAILED),
        (True, CommandStatus.INTERRUPTED),
    ],
)
def test_every_legal_terminal_command_transition(
    tmp_path: Path,
    via_running: bool,
    terminal: CommandStatus,
) -> None:
    with RelayState(tmp_path / f"{terminal.value}.sqlite3") as state:
        queued, _ = state.commands.enqueue(
            CommandType.RESUME,
            {},
            idempotency_key=terminal.value,
        )
        current = queued
        if via_running:
            current = state.commands.claim_next()  # type: ignore[assignment]
            assert current is not None
        finished = state.commands.transition(current.id, terminal)
        assert finished.status == terminal
        for candidate in CommandStatus:
            with pytest.raises(CommandConflictError):
                state.commands.transition(finished.id, candidate)


def test_command_claim_is_fifo_when_timestamps_match(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        first, _ = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="first",
        )
        second, _ = state.commands.enqueue(
            CommandType.TRIGGER_INITIATIVE,
            {},
            idempotency_key="second",
        )
        third, _ = state.commands.enqueue(
            CommandType.RESUME,
            {},
            idempotency_key="third",
        )
        assert [
            state.commands.claim_next().id,
            state.commands.claim_next().id,
            state.commands.claim_next().id,
        ] == [first.id, second.id, third.id]


def test_approval_commands_conflict_for_same_approval(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        state.commands.enqueue(
            CommandType.EXECUTE_APPROVAL,
            {"approval_id": "a1", "post_result": False},
            idempotency_key="execute-a1",
        )
        with pytest.raises(CommandConflictError, match="already has"):
            state.commands.enqueue(
                CommandType.REJECT_APPROVAL,
                {"approval_id": "a1"},
                idempotency_key="reject-a1",
            )


def test_atomic_claim_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RelayState(path) as setup:
        command, _ = setup.commands.enqueue(
            CommandType.TRIGGER_INITIATIVE,
            {},
            idempotency_key="trigger-1",
        )

    claimed: list[str] = []
    failures: list[Exception] = []
    barrier = threading.Barrier(2)

    def claim() -> None:
        try:
            with RelayState(path, migrate=False) as state:
                barrier.wait()
                item = state.commands.claim_next()
                if item is not None:
                    claimed.append(item.id)
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert claimed == [command.id]


def test_running_commands_are_recovered_without_retry(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        queued, _ = state.commands.enqueue(
            CommandType.EXECUTE_APPROVAL,
            {"approval_id": "a1", "post_result": True},
            idempotency_key="execute-a1",
        )
        running = state.commands.claim_next()
        assert running is not None
        still_queued, _ = state.commands.enqueue(
            CommandType.RESUME,
            {},
            idempotency_key="resume",
        )

        recovered = state.commands.recover_interrupted()
        assert [item.id for item in recovered] == [queued.id]
        assert recovered[0].status == CommandStatus.INTERRUPTED
        assert recovered[0].command_type in SIDE_EFFECTING_COMMANDS
        assert state.commands.claim_next().id == still_queued.id


def test_activity_is_allow_listed_redacted_and_cursor_ordered(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        first = state.activity.add(
            "command_queued",
            ActivitySeverity.INFO,
            "Queued with kn_fake-secret and Bearer abc.def",
            details={"command_id": "c1", "reason": "Authorization: hidden"},
            correlation_id="c1",
        )
        second = state.activity.add(
            "command_started",
            ActivitySeverity.INFO,
            "Started",
            details={"command_id": "c1"},
            correlation_id="c1",
        )
        assert "kn_fake" not in first.summary
        assert "abc.def" not in first.summary
        assert first.details["reason"] == "Authorization=[REDACTED]"
        assert [item.id for item in state.activity.after(first.id)] == [second.id]

        with pytest.raises(ValueError, match="not allowed"):
            state.activity.add(
                "command_started",
                ActivitySeverity.INFO,
                "Invalid details",
                details={"secret": "must not be stored"},
            )


def test_worker_status_preserves_phase_start_on_heartbeat(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        initial = state.worker_status.update(WorkerPhase.STARTING)
        heartbeat = state.worker_status.heartbeat()
        assert heartbeat is not None
        assert heartbeat.phase == WorkerPhase.STARTING
        assert heartbeat.phase_started_at == initial.phase_started_at


def test_sqlite_connection_cannot_cross_threads(tmp_path: Path) -> None:
    state = RelayState(tmp_path / "state.sqlite3")
    errors: list[Exception] = []

    def wrong_thread() -> None:
        try:
            state.status()
        except Exception as exc:  # pragma: no branch - expected path
            errors.append(exc)

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join()
    state.close()

    assert len(errors) == 1
    assert isinstance(errors[0], sqlite3.ProgrammingError)


def test_state_factory_closes_request_scoped_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with RelayState(path):
        pass
    factory = RelayStateFactory(path)

    with factory.connect() as state:
        state.set_meta("visible", "yes")
        connection = state.connection

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
