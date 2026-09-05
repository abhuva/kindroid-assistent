import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from kindroid_relay.command_queue import CommandStatus, CommandType
from kindroid_relay.migrations import migrate_database
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.worker import RelayWorker, request_pause

from test_relay import settings


class FakeApplication:
    def __init__(
        self,
        state: RelayState,
        calls: list[str],
        *,
        fail_trigger: bool = False,
    ) -> None:
        self.state = state
        self.calls = calls
        self.fail_trigger = fail_trigger

    def run_once(self) -> int:
        self.calls.append("poll")
        return 0

    def start_conversation(self) -> int:
        self.calls.append("start_conversation")
        return 1

    def trigger_initiative(self) -> int:
        self.calls.append("trigger_initiative")
        if self.fail_trigger:
            raise RuntimeError("trigger failed")
        return 1


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def command_status(factory: RelayStateFactory, command_id: str) -> CommandStatus:
    with factory.connect() as state:
        command = state.commands.get(command_id)
        assert command is not None
        return command.status


def test_worker_serially_executes_queued_commands(tmp_path: Path) -> None:
    config = replace(
        settings(tmp_path),
        poll_seconds=60,
        worker_heartbeat_seconds=1,
        worker_stale_seconds=3,
    )
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    with factory.connect() as state:
        first, _ = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="start",
        )
        second, _ = state.commands.enqueue(
            CommandType.TRIGGER_INITIATIVE,
            {},
            idempotency_key="trigger",
        )

    calls: list[str] = []
    worker = RelayWorker(
        config,
        application_factory=lambda state: nullcontext(
            FakeApplication(state, calls)
        ),
    )
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        wait_until(
            lambda: command_status(factory, second.id) == CommandStatus.COMPLETED
        )
    finally:
        worker.stop()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert calls[:2] == ["start_conversation", "trigger_initiative"]
    assert command_status(factory, first.id) == CommandStatus.COMPLETED
    assert command_status(factory, second.id) == CommandStatus.COMPLETED


def test_worker_uses_shorter_poll_after_activity_and_longer_poll_when_idle(
    tmp_path: Path,
) -> None:
    config = replace(settings(tmp_path), poll_seconds=300, idle_poll_seconds=600)
    worker = RelayWorker(config)

    assert worker._poll_delay(1) == 300
    assert worker._poll_delay(0) == 600


def test_command_failure_does_not_stop_worker(tmp_path: Path) -> None:
    config = replace(
        settings(tmp_path),
        poll_seconds=60,
        worker_heartbeat_seconds=1,
        worker_stale_seconds=3,
    )
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    with factory.connect() as state:
        failed, _ = state.commands.enqueue(
            CommandType.TRIGGER_INITIATIVE,
            {},
            idempotency_key="trigger",
        )
        completed, _ = state.commands.enqueue(
            CommandType.RESUME,
            {},
            idempotency_key="resume",
        )

    calls: list[str] = []
    worker = RelayWorker(
        config,
        application_factory=lambda state: nullcontext(
            FakeApplication(state, calls, fail_trigger=True)
        ),
    )
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        wait_until(
            lambda: command_status(factory, completed.id) == CommandStatus.COMPLETED
        )
    finally:
        worker.stop()
        thread.join(timeout=2)

    assert command_status(factory, failed.id) == CommandStatus.FAILED
    assert not thread.is_alive()


def test_worker_recovery_pauses_after_interrupted_side_effect(tmp_path: Path) -> None:
    config = settings(tmp_path)
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    with factory.connect() as state:
        queued, _ = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="start",
        )
        assert state.commands.claim_next() is not None

    stop_event = threading.Event()
    stop_event.set()
    worker = RelayWorker(
        config,
        stop_event=stop_event,
        application_factory=lambda state: nullcontext(
            FakeApplication(state, [])
        ),
    )
    worker.run()

    with factory.connect() as state:
        recovered = state.commands.get(queued.id)
        assert recovered is not None
        assert recovered.status == CommandStatus.INTERRUPTED
        assert state.is_paused()
        assert "interrupted" in (state.get_meta("pause_reason") or "")


def test_pause_is_immediate_and_wakes_worker(tmp_path: Path) -> None:
    config = settings(tmp_path)
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    wake = threading.Event()

    assert request_pause(factory, wake, "operator requested") is False
    assert wake.is_set()
    with factory.connect() as state:
        assert state.is_paused()
        assert state.get_meta("pause_reason") == "operator requested"
        assert state.activity.after()[-1].kind == "relay_paused"
