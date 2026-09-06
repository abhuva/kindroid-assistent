import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from kindroid_relay.activity import WorkerPhase
from kindroid_relay.application import RelayApplication
from kindroid_relay.command_queue import CommandStatus, CommandType
from kindroid_relay.migrations import migrate_database
from kindroid_relay.models import AgentReply
from kindroid_relay.state import RelayState, RelayStateFactory
from kindroid_relay.worker import RelayWorker, request_pause

from test_relay import FakeKindroid, settings


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

    def trigger_initiative(self) -> dict:
        self.calls.append("trigger_initiative")
        if self.fail_trigger:
            raise RuntimeError("trigger failed")
        return {"handled": 1, "initiative_outcome": "check_in_answered", "check_in_sent": True}


class ConversationAgent:
    def manual_conversation_opener(self, _latest) -> str:
        return "Opening the conversation"

    def reply_to(self, _message) -> AgentReply:
        return AgentReply("Closing the conversation", False)


def complete_application_worker(config, kindroid, agent) -> RelayWorker:
    worker = RelayWorker(config)
    worker.application_factory = lambda state: nullcontext(
        RelayApplication(
            config,
            state,
            kindroid,
            lambda: nullcontext(agent),
            phase_context=lambda phase, note: worker._scoped_phase(
                state, phase, note
            ),
        )
    )
    return worker


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


def test_scoped_worker_phase_restores_command_context_after_failure(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    worker = RelayWorker(config)
    with RelayState(config.state_path) as state:
        queued, _ = state.commands.enqueue(
            CommandType.EXECUTE_APPROVAL,
            {"approval_id": "approval-1", "post_result": False},
            idempotency_key="execute-approval-1",
        )
        assert state.commands.claim_next() is not None
        state.worker_status.update(
            WorkerPhase.EXECUTING_APPROVAL,
            active_command_id=queued.id,
            note="execute_approval",
        )

        with pytest.raises(RuntimeError, match="boundary failed"):
            with worker._scoped_phase(
                state,
                WorkerPhase.RUNNING_CODEX,
                "Executing approved Codex action",
            ):
                active = state.worker_status.get()
                assert active is not None
                assert active.phase == WorkerPhase.RUNNING_CODEX
                assert active.active_command_id == queued.id
                raise RuntimeError("boundary failed")

        restored = state.worker_status.get()
        assert restored is not None
        assert restored.phase == WorkerPhase.EXECUTING_APPROVAL
        assert restored.active_command_id == queued.id


def test_worker_records_complete_poll_cycle(tmp_path: Path) -> None:
    config = replace(
        settings(tmp_path),
        worker_heartbeat_seconds=1,
        worker_stale_seconds=3,
    )
    migrate_database(config.state_path)
    stop = threading.Event()
    wake = threading.Event()
    calls: list[str] = []

    class StopAfterPoll(FakeApplication):
        def run_once(self) -> int:
            self.calls.append("poll")
            stop.set()
            wake.set()
            return 0

    worker = RelayWorker(
        config,
        stop_event=stop,
        wake_event=wake,
        application_factory=lambda state: nullcontext(
            StopAfterPoll(state, calls)
        ),
    )
    worker.run()

    with RelayState(config.state_path, migrate=False) as state:
        kinds = [event.kind for event in state.activity.after()]
    assert "relay_cycle_started" in kinds
    assert "relay_cycle_completed" in kinds
    assert kinds.index("relay_cycle_started") < kinds.index("relay_cycle_completed")


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


def test_duplicate_conversation_commands_send_only_one_conversation(
    tmp_path: Path,
) -> None:
    config = replace(
        settings(tmp_path),
        poll_seconds=60,
        idle_poll_seconds=60,
        worker_heartbeat_seconds=1,
        worker_stale_seconds=3,
    )
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    with factory.connect() as state:
        first, first_created = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="dashboard-start-request",
        )
        duplicate, duplicate_created = state.commands.enqueue(
            CommandType.START_CONVERSATION,
            {},
            idempotency_key="dashboard-start-request",
        )

    kindroid = FakeKindroid()
    worker = complete_application_worker(config, kindroid, ConversationAgent())
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        wait_until(
            lambda: command_status(factory, first.id) == CommandStatus.COMPLETED
        )
    finally:
        worker.stop()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert first_created
    assert not duplicate_created
    assert duplicate.id == first.id
    assert kindroid.sent == [
        "[CODEX]: Opening the conversation",
        "[CODEX]: Closing the conversation",
    ]
    with factory.connect() as state:
        command = state.commands.get(first.id)
        assert command is not None
        assert command.result == {"handled": 1}
        assert [event.kind for event in state.activity.after()].count(
            "command_completed"
        ) == 1


def test_polling_finishes_before_queued_manual_command_external_effects(
    tmp_path: Path,
) -> None:
    config = replace(
        settings(tmp_path),
        poll_seconds=60,
        idle_poll_seconds=60,
        worker_heartbeat_seconds=1,
        worker_stale_seconds=3,
    )
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    history_entered = threading.Event()
    release_history = threading.Event()
    events: list[str] = []
    event_lock = threading.Lock()
    active_effects = 0
    maximum_active_effects = 0

    @contextmanager
    def external_effect(name: str):
        nonlocal active_effects, maximum_active_effects
        with event_lock:
            active_effects += 1
            maximum_active_effects = max(maximum_active_effects, active_effects)
            events.append(f"{name}:start")
        try:
            yield
        finally:
            with event_lock:
                events.append(f"{name}:end")
                active_effects -= 1

    class BlockingKindroid(FakeKindroid):
        def get_messages_page(self, *args, **kwargs):
            with external_effect("history"):
                if not history_entered.is_set():
                    history_entered.set()
                    if not release_history.wait(2):
                        raise AssertionError("test did not release history request")
                return super().get_messages_page(*args, **kwargs)

        def post_user_message(self, *args, **kwargs):
            with external_effect("send"):
                return super().post_user_message(*args, **kwargs)

        def get_turn(self, *args, **kwargs):
            with external_effect("turn"):
                return super().get_turn(*args, **kwargs)

        def generate_ai_response(self, *args, **kwargs):
            with external_effect("generate"):
                return super().generate_ai_response(*args, **kwargs)

    class TrackedConversationAgent(ConversationAgent):
        def manual_conversation_opener(self, latest) -> str:
            with external_effect("codex_opener"):
                return super().manual_conversation_opener(latest)

        def reply_to(self, message) -> AgentReply:
            with external_effect("codex_reply"):
                return super().reply_to(message)

    kindroid = BlockingKindroid()
    worker = complete_application_worker(
        config, kindroid, TrackedConversationAgent()
    )
    thread = threading.Thread(target=worker.run)
    thread.start()
    command_id = ""
    try:
        assert history_entered.wait(1)
        with factory.connect() as state:
            command, _ = state.commands.enqueue(
                CommandType.START_CONVERSATION,
                {},
                idempotency_key="manual-during-poll",
            )
            command_id = command.id
        with event_lock:
            events.append("command:queued")
        worker.wake_event.set()
        release_history.set()
        wait_until(
            lambda: command_status(factory, command_id) == CommandStatus.COMPLETED
        )
    finally:
        release_history.set()
        worker.stop()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert maximum_active_effects == 1
    assert events[:3] == ["history:start", "command:queued", "history:end"]
    assert events.index("codex_opener:start") > events.index("history:end")
    assert kindroid.sent == [
        "[CODEX]: Opening the conversation",
        "[CODEX]: Closing the conversation",
    ]
