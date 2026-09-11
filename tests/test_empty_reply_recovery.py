from contextlib import nullcontext
from dataclasses import replace
import json

import httpx
import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.kindroid_api import KindroidAPIError, KindroidClient
from kindroid_relay.models import ActionProposal, AgentReply, ChatMessage, ExecutionResult
from kindroid_relay.relay import Relay
from kindroid_relay.state import RelayState
from kindroid_relay.turns import ReconciliationError
from test_relay import FakeAgent, FakeKindroid, settings


class EmptyBodyKindroid(FakeKindroid):
    generation_count = 0

    def generate_ai_response(self, group_id, ai_id):
        self.generation_count += 1
        super().generate_ai_response(group_id, ai_id)
        return ""


@pytest.mark.parametrize('blank_record', [False, True])
def test_empty_http_200_recovers_once_through_real_adapter(tmp_path, blank_record):
    """Exercise the actual HTTP parser, including role-only history, with mocks."""
    config = replace(settings(tmp_path), initiative_enabled=False, kindroid_display_name="Kindroid")
    backend = EmptyBodyKindroid()
    calls = []

    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        calls.append(path)
        if path == "get-chat-messages":
            rows = [dict(id=m.id, sender=m.sender_type, display_name=m.display_name,
                         timestamp=m.timestamp, message=m.message) for m in backend.messages]
            return httpx.Response(200, json={"messages": rows, "pagination": {"hasMore": False}})
        payload = json.loads(request.content)
        if path == "groupchats-user-message":
            backend.post_user_message(payload["group_id"], payload["message"])
        elif path == "groupchats-get-turn":
            return httpx.Response(200, text="ai1")
        elif path == "groupchats-ai-response":
            assert payload["stream"] is False
            backend.generate_ai_response(payload["group_id"], payload["ai_id"])
            if blank_record:
                backend.messages.append(ChatMessage('blank-ai', 'ai', 'ai', 'Hannah',
                    backend.messages[-1].timestamp + 1, ''))
        return httpx.Response(200, text="")

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test/v1")
    with http, RelayState(config.state_path) as state:
        agent = FakeAgent()
        relay = Relay(config, state, KindroidClient("fixture", client=http), lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        assert relay.run_once() == 1
        assert backend.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Talk later"]
        assert calls.count("groupchats-ai-response") == 1
        assert calls.count("get-chat-messages") == 3  # Bootstrap, entry, one fallback.
        assert not state.is_paused()
        assert not state.turns.has_uncertain()
        assert state.pending_ai_messages() == []
        if blank_record:
            blank = state.connection.execute("SELECT body,processed FROM messages WHERE remote_id='blank-ai'").fetchone()
            assert blank['body'] == '' and blank['processed'] == 1
        recovered = next(e for e in state.activity.after()
                         if e.kind == "generation_history_fallback" and e.details["outcome"] == "recovered")
        assert state.outbox_for_source(recovered.details["message_id"])["status"] == "sent"
        assert "generation_uncertain" not in {e.kind for e in state.activity.after()}
        assert relay.run_once() == 0
        assert backend.generation_count == 1
        assert agent.calls == 2


@pytest.mark.parametrize("operation", ["manual", "initiative", "approval"])
def test_empty_reply_recovery_applies_to_every_generation_entry_point(tmp_path, operation):
    config = replace(settings(tmp_path), max_history_requests_24h=1)
    remote = EmptyBodyKindroid()
    remote.messages = [ChatMessage("u0", "user", "user", "Codex Test", 1, "Previous reply")]
    class Agent:
        def reply_to(self, message):
            return AgentReply("Closing", False)
        def scheduled_check_in(self, latest):
            return "Check-in"
        def manual_conversation_opener(self, latest):
            return "Opener"
        def execute_approved(self, *args):
            return ExecutionResult(True, "Done", "Result")
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(Agent()))
        app.bootstrap()
        if operation == "manual":
            assert app.start_conversation() == 1
        elif operation == "initiative":
            assert app.trigger_initiative()["initiative_outcome"] == "check_in_answered"
        else:
            approval = state.add_approval("u0", ActionProposal("Fixture", "Fixture", "low"))
            assert app.execute_approval(approval, post_result=True).success
            assert state.get_approval(approval)["status"] == "executed"
            assert len(state.pending_ai_messages()) == 1
        assert remote.generation_count == 1
        assert remote.history_requests == 3
        assert not state.is_paused()
        assert not state.turns.has_uncertain()


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "wrong_ai", "history_error"])
def test_empty_reply_fallback_never_retries_or_accepts_unconfirmed_reply(tmp_path, failure):
    config = replace(settings(tmp_path), initiative_enabled=False,
                     max_history_requests_24h=1)
    remote = EmptyBodyKindroid()
    generate = remote.generate_ai_response
    history = remote.get_messages_page
    def broken_generate(*args):
        generate(*args)
        if failure == "missing":
            remote.messages.pop()
        elif failure == "ambiguous":
            remote.messages.append(replace(remote.messages[-1], id="duplicate"))
        elif failure == "wrong_ai":
            remote.messages[-1] = replace(remote.messages[-1], sender="other-ai")
        return ""
    def broken_history(*args, **kwargs):
        if failure == "history_error" and remote.generated:
            remote.history_requests += 1
            raise KindroidAPIError("fixture", 500, "PRIVATE_RESPONSE_SENTINEL")
        return history(*args, **kwargs)
    remote.generate_ai_response = broken_generate
    remote.get_messages_page = broken_history
    with RelayState(config.state_path) as state:
        agent = FakeAgent()
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        with pytest.raises(ReconciliationError):
            relay.run_once()
        assert state.is_paused()
        assert remote.generation_count == 1
        assert agent.calls == 1
        assert len(remote.sent) == 1
        assert remote.history_requests == 3
        assert "PRIVATE_RESPONSE_SENTINEL" not in repr(state.activity.after())


@pytest.mark.parametrize("turn_limit", [1, 4])
def test_empty_reply_429_stops_session_until_explicit_recovery_and_resume(tmp_path, turn_limit):
    config = replace(settings(tmp_path), initiative_enabled=False, max_consecutive_turns=turn_limit)
    remote = EmptyBodyKindroid()
    history = remote.get_messages_page
    def limited_history(*args, **kwargs):
        if remote.generated:
            remote.history_requests += 1
            raise KindroidAPIError("history", 429, "fixture", retry_after_seconds=60)
        return history(*args, **kwargs)
    remote.get_messages_page = limited_history
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        assert relay.run_once() == 1
        assert state.is_paused()
        assert state.turns.has_uncertain()
        assert state.get_int("history_cooldown_until") > 0
        assert remote.history_requests == 3
        assert remote.generation_count == 1
    with RelayState(config.state_path) as state:
        state.set_meta("history_cooldown_until", 0)
        remote.get_messages_page = history
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.synchronize_context()  # Explicit read reconciles, but does not resume.
        assert state.is_paused()
        assert state.get_int('consecutive_turns') == 1
        state.resume()
        relay.run_once()
        assert remote.generation_count == 1
        assert not state.turns.has_uncertain()
        assert not state.is_paused()
        assert len(remote.sent) == 2


def test_empty_reply_recovery_respects_minimum_history_spacing(tmp_path, monkeypatch):
    config = replace(settings(tmp_path), initiative_enabled=False, min_history_interval_seconds=15)
    remote = EmptyBodyKindroid()
    now = [1_800_000_000.0]
    sleeps = []
    monkeypatch.setattr("kindroid_relay.relay.time.time", lambda: now[0])
    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
    monkeypatch.setattr("kindroid_relay.relay.time.sleep", sleep)
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert sleeps == [15, 15]
        assert remote.history_requests == 3
        assert not state.is_paused()


def test_exchange_continues_when_every_generation_returns_empty_text(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = EmptyBodyKindroid()
    class Agent:
        calls = 0
        def reply_to(self, message):
            self.calls += 1
            return AgentReply("Same Codex text", self.calls < 4)
    agent = Agent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert agent.calls == 4
        assert len(remote.sent) == 4
        assert remote.generation_count == 3
        assert remote.history_requests == 5  # One read for each empty response.
        assert not state.is_paused()
        assert state.pending_ai_messages() == []
        relay.run_once()
        assert agent.calls == 4
        assert remote.generation_count == 3


@pytest.mark.parametrize("operation", ["manual", "initiative", "approval"])
def test_entry_point_cooldown_retains_successful_send_outcome(tmp_path, operation):
    config = settings(tmp_path)
    remote = EmptyBodyKindroid()
    remote.messages = [ChatMessage("u0", "user", "user", "Codex Test", 1, "Previous reply")]
    history = remote.get_messages_page
    def limited(*args, **kwargs):
        if remote.generated:
            remote.history_requests += 1
            raise KindroidAPIError("history", 429, "fixture", retry_after_seconds=60)
        return history(*args, **kwargs)
    remote.get_messages_page = limited
    class Agent:
        def scheduled_check_in(self, latest):
            return "Check-in"
        def manual_conversation_opener(self, latest):
            return "Opener"
        def execute_approved(self, *args):
            return ExecutionResult(True, "Done", "Result")
        def reply_to(self, message):
            pytest.fail("No confirmed reply is available during cooldown")
    with RelayState(config.state_path) as state:
        app = RelayApplication(config, state, remote, lambda: nullcontext(Agent()))
        app.bootstrap()
        if operation == "manual":
            assert app.start_conversation() == 1
        elif operation == "initiative":
            result = app.trigger_initiative()
            assert result == {"handled": 0, "initiative_outcome": "check_in_waiting_for_history", "check_in_sent": True}
        else:
            approval = state.add_approval("u0", ActionProposal("Fixture", "Fixture", "low"))
            assert app.execute_approval(approval, post_result=True).success
            assert state.get_approval(approval)["status"] == "executed"
        assert len(remote.sent) == 1
        assert remote.generation_count == 1
        assert state.turns.has_uncertain()
        assert state.is_paused()
