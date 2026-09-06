import sqlite3
import threading
import time
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


def test_activity_retention_preserves_safety_audit_events(tmp_path: Path) -> None:
    now = 2_000_000_000
    with RelayState(tmp_path / "state.sqlite3") as state:
        operational = state.activity.add(
            "worker_phase",
            ActivitySeverity.INFO,
            "Old operational event",
            details={"phase": "idle"},
        )
        safety = state.activity.add(
            "outbound_sent",
            ActivitySeverity.INFO,
            "Old safety audit event",
            details={"outbox_id": "outbox-1"},
        )
        with state.connection:
            state.connection.execute(
                "UPDATE activity_events SET created_at = ? WHERE id IN (?, ?)",
                (now - 31 * 86400, operational.id, safety.id),
            )

        assert state.activity.prune(30, now=now) == 1
        assert [item.id for item in state.activity.after()] == [safety.id]


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


def test_request_connections_read_while_worker_connection_is_writing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    with RelayState(path):
        pass
    factory = RelayStateFactory(path)
    worker_ready = threading.Event()
    release_worker = threading.Event()
    failures: list[Exception] = []

    def hold_worker_transaction() -> None:
        try:
            with factory.connect() as state:
                state.connection.execute("BEGIN IMMEDIATE")
                state.connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('worker_write', 'open')"
                )
                worker_ready.set()
                if not release_worker.wait(2):
                    raise AssertionError("test did not release worker transaction")
                state.connection.commit()
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    worker_thread = threading.Thread(target=hold_worker_transaction)
    worker_thread.start()
    assert worker_ready.wait(1)
    started = time.monotonic()
    try:
        with factory.connect() as request_state:
            snapshot = request_state.status()
    finally:
        release_worker.set()
        worker_thread.join(timeout=2)

    assert not worker_thread.is_alive()
    assert failures == []
    assert snapshot["pending_messages"] == 0
    assert time.monotonic() - started < 1


def test_request_can_enqueue_command_while_worker_writes_other_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    with RelayState(path):
        pass
    factory = RelayStateFactory(path)
    worker_ready = threading.Event()
    request_ready = threading.Event()
    release_worker = threading.Event()
    enqueue_started = threading.Event()
    command_ids: list[str] = []
    failures: list[Exception] = []

    def hold_worker_transaction() -> None:
        try:
            with factory.connect() as state:
                state.connection.execute("BEGIN IMMEDIATE")
                state.connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('worker_cycle', 'active')"
                )
                worker_ready.set()
                if not release_worker.wait(2):
                    raise AssertionError("test did not release worker transaction")
                state.connection.commit()
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    def enqueue_request() -> None:
        try:
            with factory.connect() as state:
                request_ready.set()
                assert worker_ready.wait(1)
                state.connection.set_trace_callback(
                    lambda statement: (
                        enqueue_started.set()
                        if "BEGIN IMMEDIATE" in statement
                        else None
                    )
                )
                command, _ = state.commands.enqueue(
                    CommandType.TRIGGER_INITIATIVE,
                    {},
                    idempotency_key="concurrent-trigger",
                )
                command_ids.append(command.id)
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    worker_thread = threading.Thread(target=hold_worker_transaction)
    request_thread = threading.Thread(target=enqueue_request)
    request_thread.start()
    assert request_ready.wait(1)
    worker_thread.start()
    assert enqueue_started.wait(1)
    time.sleep(0.05)
    release_started = time.monotonic()
    release_worker.set()
    worker_thread.join(timeout=2)
    request_thread.join(timeout=2)

    assert not worker_thread.is_alive()
    assert not request_thread.is_alive()
    assert failures == []
    assert len(command_ids) == 1
    assert time.monotonic() - release_started < 1
    with factory.connect() as state:
        command = state.commands.get(command_ids[0])
        assert command is not None
        assert command.status == CommandStatus.QUEUED


def test_short_request_transaction_does_not_stall_worker_polling(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    with RelayState(path):
        pass
    factory = RelayStateFactory(path)
    worker_ready = threading.Event()
    begin_poll = threading.Event()
    poll_write_started = threading.Event()
    poll_completed = threading.Event()
    failures: list[Exception] = []

    def poll_with_worker_connection() -> None:
        try:
            with factory.connect() as state:
                state.connection.set_trace_callback(
                    lambda statement: (
                        poll_write_started.set()
                        if "DELETE FROM history_requests" in statement
                        else None
                    )
                )
                worker_ready.set()
                if not begin_poll.wait(1):
                    raise AssertionError("test did not start worker poll")
                state.record_history_request(100)
                poll_completed.set()
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    worker_thread = threading.Thread(target=poll_with_worker_connection)
    worker_thread.start()
    assert worker_ready.wait(1)
    with factory.connect() as request_state:
        request_state.connection.execute("BEGIN IMMEDIATE")
        request_state.connection.execute(
            "INSERT INTO metadata(key, value) VALUES('request_write', 'active')"
        )
        begin_poll.set()
        assert poll_write_started.wait(1)
        time.sleep(0.05)
        released_at = time.monotonic()
        request_state.connection.commit()

    assert poll_completed.wait(1)
    worker_thread.join(timeout=2)
    assert not worker_thread.is_alive()
    assert failures == []
    assert time.monotonic() - released_at < 1
    with factory.connect() as state:
        assert state.history_request_count() == 1
