from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import time

import pytest

from kindroid_relay.activity import WorkerPhase
from kindroid_relay.config import Settings
from kindroid_relay.kindroid_api import KindroidAPIError, MessagePage
from kindroid_relay.models import AgentReply, ChatMessage
from kindroid_relay.relay import HistoryCooldownError, Relay
from kindroid_relay.state import RelayState


class FakeKindroid:
    def __init__(self) -> None:
        self.messages = [ChatMessage("h1", "ai1", "ai", "Hannah", 1, "Hello")]
        self.sent: list[str] = []
        self.generated = False
        self.history_requests = 0
        self.turn_requests: list[bool] = []
        self.persona_changes: list[str] = []

    def get_messages_page(self, group_id, limit=100, start_after_timestamp=None):
        self.history_requests += 1
        visible = self.messages
        if start_after_timestamp is not None:
            visible = [m for m in visible if m.timestamp > start_after_timestamp]
        return MessagePage(visible, False, max((m.timestamp for m in visible), default=1))

    def post_user_message(self, group_id, message):
        self.sent.append(message)
        sequence = len(self.sent)
        timestamp = max((item.timestamp for item in self.messages), default=0) + 1
        self.messages.append(
            ChatMessage(
                f"u{sequence}",
                "user",
                "user",
                "Codex Test",
                timestamp,
                message,
            )
        )

    def activate_user_persona(self, persona_id, **profile):
        self.persona_changes.append(profile["name"])

    def get_turn(self, group_id, allow_user):
        self.turn_requests.append(allow_user)
        return "ai1"

    def generate_ai_response(self, group_id, ai_id):
        self.generated = True
        timestamp = max(item.timestamp for item in self.messages) + 1
        self.messages.append(ChatMessage(f"h{len(self.sent) + 1}", "ai1", "ai", "Hannah", timestamp, "Bye"))
        return "Bye"


class FakeAgent:
    def __init__(self) -> None:
        self.calls = 0

    def reply_to(self, message):
        self.calls += 1
        if self.calls == 1:
            return AgentReply("Hi Hannah", True)
        return AgentReply("Talk later", False)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="kn_test",
        group_id="g1",
        base_url="https://example.test/v1",
        poll_seconds=180,
        min_history_interval_seconds=0,
        max_consecutive_turns=4,
        max_history_requests_24h=100,
        max_reply_chars=3500,
        initiative_enabled=True,
        initiative_interval_seconds=600,
        initiative_schedule_hours=None,
        initiative_jitter_seconds=0,
        initiative_max_checks_24h=144,
        initiative_quiet_hours=None,
        state_path=tmp_path / "state.sqlite3",
        log_path=tmp_path / "relay.log",
        codex_model=None,
        codex_cwd=tmp_path,
        approved_sandbox="workspace-write",
        codex_display_name="Codex Test",
        kindroid_display_name="Hannah",
        codex_message_prefix="[CODEX]: ",
        persona_switch_enabled=True,
        codex_persona_id="codex-persona",
        codex_persona_gender="Nonbinary",
        codex_persona_backstory="External agent",
        restore_persona_id="marc-persona",
        restore_persona_name="Marc",
        restore_persona_gender="Male",
        restore_persona_backstory="",
        prompt_path=tmp_path / "prompt.md",
    )


def test_relay_runs_bounded_back_and_forth(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        assert relay.run_once() == 1

        assert kindroid.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Talk later"]
        assert kindroid.persona_changes == [
            "Codex Test",
            "Marc",
            "Codex Test",
            "Marc",
        ]
        assert kindroid.generated
        assert kindroid.history_requests == 2
        assert not state.is_paused()
        assert state.pending_ai_messages() == []


def test_relay_emits_correlated_activity_and_external_boundary_phases(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()
    agent = FakeAgent()
    phases = []

    @contextmanager
    def phase_context(phase, _note):
        phases.append(phase)
        yield

    with RelayState(config.state_path) as state:
        relay = Relay(
            config,
            state,
            kindroid,
            lambda: nullcontext(agent),
            phase_context=phase_context,
        )
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()

        events = state.activity.after()
        kinds = {event.kind for event in events}
        assert {
            "message_received",
            "message_processed",
            "codex_started",
            "codex_completed",
            "outbound_created",
            "outbound_sending",
            "outbound_sent",
        } <= kinds
        assert any(
            event.kind == "message_processed"
            and event.correlation_id == "h1"
            for event in events
        )
        assert any(
            event.kind == "outbound_sent"
            and event.details.get("source_message_id") == "h1"
            for event in events
        )
        assert {
            WorkerPhase.WAITING_FOR_KINDROID,
            WorkerPhase.RUNNING_CODEX,
            WorkerPhase.SENDING,
        } <= set(phases)


def test_codex_session_open_failure_is_correlated_and_phased(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()
    phases = []

    @contextmanager
    def phase_context(phase, _note):
        phases.append(phase)
        yield

    class FailingContext:
        def __enter__(self):
            raise RuntimeError("PROMPT_SECRET_SENTINEL")

        def __exit__(self, *_args):
            return None

    with RelayState(config.state_path) as state:
        relay = Relay(
            config,
            state,
            kindroid,
            FailingContext,
            phase_context=phase_context,
        )
        relay.bootstrap(respond_to_latest=True)

        with pytest.raises(RuntimeError, match="PROMPT_SECRET_SENTINEL"):
            relay.run_once()

        failed = state.activity.after()[-1]
        assert failed.kind == "codex_failed"
        assert failed.correlation_id == "h1"
        assert "PROMPT_SECRET_SENTINEL" not in repr(failed)
        assert WorkerPhase.RUNNING_CODEX in phases


def test_history_429_creates_durable_cooldown_without_retrying(tmp_path: Path) -> None:
    config = settings(tmp_path)

    class RateLimitedKindroid(FakeKindroid):
        def get_messages_page(self, group_id, limit=100, start_after_timestamp=None):
            self.history_requests += 1
            raise KindroidAPIError(
                "get chat messages",
                429,
                "Too Many Requests",
                retry_after_seconds=7200,
            )

    kindroid = RateLimitedKindroid()
    before = int(time.time())
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: None)
        with pytest.raises(HistoryCooldownError) as caught:
            relay.sync_history()

        assert caught.value.retry_at >= before + 7200
        assert state.status()["history_rate_limited"] is True
        assert state.status()["history_last_429_at"] >= before
        assert state.activity.after()[-1].kind == "history_rate_limited"
        assert kindroid.history_requests == 1

        with pytest.raises(HistoryCooldownError):
            relay.sync_history()
        assert kindroid.history_requests == 1

        retry_at = caught.value.retry_at

    with RelayState(config.state_path) as reopened:
        assert reopened.status()["history_cooldown_until"] == retry_at
        assert reopened.status()["history_rate_limited"] is True


def test_active_exchange_uses_direct_reply_before_later_history_cooldown(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()
    agent = FakeAgent()
    original_history = kindroid.get_messages_page

    def limited_after_generation(group_id, limit=100, start_after_timestamp=None):
        if kindroid.generated:
            kindroid.history_requests += 1
            raise KindroidAPIError(
                "get chat messages",
                429,
                "Too Many Requests",
                retry_after_seconds=60,
            )
        return original_history(group_id, limit, start_after_timestamp)

    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        kindroid.get_messages_page = limited_after_generation

        assert relay.run_once() == 1
        assert not state.is_paused()
        assert kindroid.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Talk later"]
        with pytest.raises(HistoryCooldownError):
            relay.run_once()

        state.set_meta("history_cooldown_until", 0)
        kindroid.get_messages_page = original_history
        assert relay.run_once() == 0
        assert kindroid.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Talk later"]


def test_relay_uses_prefix_fallback_for_wrong_stored_persona(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()

    def wrong_persona(group_id, message):
        kindroid.sent.append(message)
        kindroid.messages.append(ChatMessage("u1", "user", "user", "Marc", 2, message))

    kindroid.post_user_message = wrong_persona
    class ClosingAgent:
        def reply_to(self, message):
            return AgentReply("The prefix identifies me", False)

    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(ClosingAgent()))
        relay.bootstrap(respond_to_latest=True)
        assert relay.run_once() == 1
        assert not state.is_paused()
        assert kindroid.sent == ["[CODEX]: The prefix identifies me"]
        assert not kindroid.generated
        assert state.get_meta("last_identity_fallback_name") is None

        relay.sync_history()
        assert state.get_meta("last_identity_fallback_name") == "Marc"
        assert state.activity.after()[-1].kind == "identity_fallback"


def test_successful_delivery_is_not_repeated_after_interruption(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()

    class AgentThatMustNotRun:
        def reply_to(self, message):
            raise AssertionError("a delivered reply must not be regenerated")

    with RelayState(config.state_path) as state:
        relay = Relay(
            config,
            state,
            kindroid,
            lambda: nullcontext(AgentThatMustNotRun()),
        )
        relay.bootstrap(respond_to_latest=True)
        body = relay._outbound_body("Already delivered")
        item = state.create_outbox(
            body,
            source_message_id="h1",
            request_kindroid_reply=False,
        )
        state.set_outbox_status(item, "sent")

        assert relay.run_once() == 1
        assert kindroid.sent == []
        assert state.pending_ai_messages() == []


def test_relay_restores_persona_when_delivery_fails(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()

    def failed_post(group_id, message):
        raise RuntimeError("delivery failed")

    kindroid.post_user_message = failed_post
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: None)
        with pytest.raises(RuntimeError, match="delivery failed"):
            relay._send_user_message("Test")

        assert kindroid.persona_changes == ["Codex Test", "Marc"]
        assert state.is_paused()
        assert state.status()["uncertain_outbox"] == 1
        kinds = [event.kind for event in state.activity.after()]
        assert {
            "outbound_created",
            "outbound_sending",
            "outbound_uncertain",
            "relay_paused",
        } <= set(kinds)


def test_idle_initiative_checks_in_before_offering_hannah_a_turn(tmp_path: Path) -> None:
    config = settings(tmp_path)
    kindroid = FakeKindroid()
    kindroid.messages = [
        ChatMessage("u0", "user", "user", "Codex Test", 1, "Talk later")
    ]

    class ClosingAgent:
        def scheduled_check_in(self, latest):
            assert latest.message == "Talk later"
            return "Checking in, Hannah. Is anything on your mind, or can I help?"

        def reply_to(self, message):
            return AgentReply("Good to hear from you", False)

    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(ClosingAgent()))
        relay.bootstrap()
        assert relay.run_once() == 1
        assert state.initiative_check_count() == 1
        assert kindroid.generated
        assert kindroid.sent == [
            "[CODEX]: Checking in, Hannah. Is anything on your mind, or can I help?",
            "[CODEX]: Good to hear from you",
        ]
        assert kindroid.turn_requests == [True]
        assert state.status()["last_initiative_outcome"] == "check_in_answered"
        kinds = {event.kind for event in state.activity.after()}
        assert {
            "initiative_due",
            "initiative_offered",
            "initiative_answered",
        } <= kinds


def test_manual_trigger_runs_fixed_schedule_initiative_now(tmp_path: Path) -> None:
    config = settings(tmp_path)
    config = replace(config, initiative_schedule_hours=(0, 6, 12, 18))
    kindroid = FakeKindroid()
    kindroid.messages = [
        ChatMessage("u0", "user", "user", "Codex Test", 1, "Talk later")
    ]

    class ClosingAgent:
        def scheduled_check_in(self, latest):
            return "Checking in, Hannah. Want to talk?"

        def reply_to(self, message):
            return AgentReply("I am here.", False)

    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(ClosingAgent()))
        relay.bootstrap()
        assert relay.trigger_initiative_now() == 1

        assert kindroid.sent == [
            "[CODEX]: Checking in, Hannah. Want to talk?",
            "[CODEX]: I am here.",
        ]
        assert kindroid.turn_requests == [True]
        assert state.status()["last_initiative_outcome"] == "check_in_answered"


def test_manual_conversation_is_independent_of_initiative_scheduler(tmp_path: Path) -> None:
    config = replace(
        settings(tmp_path),
        initiative_enabled=False,
        initiative_schedule_hours=(0, 6, 12, 18),
        initiative_quiet_hours=(0, 0),
    )
    kindroid = FakeKindroid()
    kindroid.messages = [
        ChatMessage("u0", "user", "user", "Codex Test", 1, "Talk later")
    ]

    class ManualAgent:
        def manual_conversation_opener(self, latest):
            assert latest.message == "A newer user message"
            return "Hannah, is there anything you would like to talk about?"

        def reply_to(self, message):
            assert message.display_name == "Hannah"
            return AgentReply("I am listening.", False)

    with RelayState(config.state_path) as state:
        relay = Relay(config, state, kindroid, lambda: nullcontext(ManualAgent()))
        relay.bootstrap()

        # Arriving after bootstrap used to overwrite a forced initiative's due time.
        kindroid.messages.append(
            ChatMessage("u-new", "user", "user", "Marc", 2, "A newer user message")
        )

        assert relay.start_conversation_now() == 1
        assert kindroid.sent == [
            "[CODEX]: Hannah, is there anything you would like to talk about?",
            "[CODEX]: I am listening.",
        ]
        assert kindroid.turn_requests == [False]
        assert state.initiative_check_count() == 0
        assert state.pending_ai_messages() == []


def test_fixed_initiative_schedule_uses_local_wall_clock() -> None:
    now = datetime.fromisoformat("2026-09-02T12:34:00+02:00")
    due = Relay._next_fixed_due(now, (0, 6, 12, 18))
    assert datetime.fromtimestamp(due, tz=now.tzinfo).isoformat() == (
        "2026-09-02T18:00:00+02:00"
    )
