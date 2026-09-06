from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import time

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.kindroid_api import KindroidAPIError, MessagePage
from kindroid_relay.models import ActionProposal, AgentReply, ChatMessage
from kindroid_relay.relay import HistoryCooldownError, Relay
from kindroid_relay.state import RelayState
from kindroid_relay.turns import ReconciliationError, ReconciliationPending
from test_relay import FakeAgent, FakeKindroid, settings


def test_direct_reply_survives_restart_and_keeps_source_and_approval_links(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert remote.history_requests == 2
        local = state.latest_ai_message()
        assert local.id.startswith("local-ai:")
        delivery = state.outbox_for_source(local.id)["id"]
        approval = state.add_approval(local.id, ActionProposal("Test", "Mock action", "low"))
        assert state.get_int("cursor_timestamp") == 1  # Local time never advances remote cursor.
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        assert relay.run_once() == 0
        assert len(remote.sent) == 2
        assert agent.calls == 2
        assert len(state.recent_messages()) == len(remote.messages)
        assert state.latest_ai_message().id == local.id
        assert state.outbox_for_source(local.id)["id"] == delivery
        assert state.get_approval(approval)["source_message_id"] == local.id
        assert state.connection.execute("SELECT kindroid_id FROM messages WHERE remote_id = ?", (local.id,)).fetchone()[0] == "h2"


def test_delayed_history_defers_context_without_polling_or_duplicate_events(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        original = remote.get_messages_page
        remote.get_messages_page = lambda *a, **kw: MessagePage(remote.messages[:2], False, 2)
        with pytest.raises(ReconciliationPending):
            relay.run_once()
        assert not state.is_paused()
        assert len(remote.sent) == 2
        assert state.get_int("cursor_timestamp") == 1
        assert state.connection.execute("SELECT COUNT(*) FROM messages WHERE kindroid_id IS NULL").fetchone()[0] == 3
        remote.get_messages_page = original
        assert relay.run_once() == 0
        assert state.pending_ai_messages() == []
        assert len(state.recent_messages()) == 4


@pytest.mark.parametrize("interrupted", [False, True])
def test_unknown_generation_is_reconciled_after_restart_never_repeated(tmp_path, interrupted):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    original = remote.generate_ai_response
    calls = []
    def generate(*args):
        calls.append(args)
        original(*args)
        if interrupted:
            raise SystemExit("simulated process loss")
        raise RuntimeError("PRIVATE_BODY_SENTINEL")
    remote.generate_ai_response = generate
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        with pytest.raises(SystemExit if interrupted else ReconciliationError):
            relay.run_once()
        assert state.turns.has_uncertain()
        assert "PRIVATE_BODY_SENTINEL" not in repr(state.activity.after())
    class ClosingAgent:
        def reply_to(self, message):
            assert message.message == "Bye"
            return AgentReply("Recovered", False)
    with RelayState(config.state_path) as state:
        state.resume()
        relay = Relay(config, state, remote, lambda: nullcontext(ClosingAgent()))
        assert relay.run_once() == 1
        assert len(calls) == 1
        assert not state.turns.has_uncertain()
        assert remote.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Recovered"]


@pytest.mark.parametrize("body", ["", "timeout"])
def test_unresolved_generation_remains_blocked_even_after_resume(tmp_path, body):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    calls = []
    def generate(*args):
        calls.append(args)
        if body == "timeout":
            raise RuntimeError("PRIVATE_BODY_SENTINEL")
        return body
    remote.generate_ai_response = generate
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        with pytest.raises(ReconciliationError):
            relay.run_once()
        state.resume()
        with pytest.raises(ReconciliationError):
            relay.run_once()
        assert state.is_paused()
        assert len(calls) == 1
        assert remote.sent == ["[CODEX]: Hi Hannah"]


@pytest.mark.parametrize("ambiguity", ["duplicate_ai", "changed_text", "wrong_ai", "duplicate_user"])
def test_ambiguous_history_pauses_before_codex(tmp_path, ambiguity):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        if ambiguity == "duplicate_ai":
            remote.messages.insert(3, replace(remote.messages[2], id="other-ai"))
        elif ambiguity == "changed_text":
            remote.messages[2] = replace(remote.messages[2], message="Edited remotely")
        elif ambiguity == "wrong_ai":
            remote.messages[2] = replace(remote.messages[2], sender="another-ai")
        else:
            remote.messages.insert(2, replace(remote.messages[1], id="other-user"))
        with pytest.raises(ReconciliationError):
            relay.run_once()
        assert state.is_paused()
        assert agent.calls == 2
        assert len(remote.sent) == 2


def test_repeated_text_across_turns_is_reconciled_by_outbound_anchor(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    class RepeatingAgent:
        calls = 0
        def reply_to(self, message):
            self.calls += 1
            return AgentReply("Same reply", self.calls < 4)
    agent = RepeatingAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert remote.history_requests == 2
        assert agent.calls == 4
        history = remote.get_messages_page
        remote.get_messages_page = lambda *a, **kw: MessagePage(remote.messages[:3], False, 3)
        with pytest.raises(ReconciliationPending):
            relay.run_once()
        assert not state.is_paused()
        remote.get_messages_page = history
        relay.run_once()
        assert agent.calls == 4
        assert len(state.recent_messages()) == len(remote.messages)


def test_paginated_reconciliation_does_not_bind_before_all_pages_arrive(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    agent = FakeAgent()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        calls = []
        def history(*args, start_after_timestamp=None, **kwargs):
            calls.append(start_after_timestamp)
            visible = [m for m in remote.messages if m.timestamp > (start_after_timestamp or 0)]
            return MessagePage(visible[:2], len(visible) > 2, visible[min(1, len(visible) - 1)].timestamp)
        remote.get_messages_page = history
        assert relay.run_once() == 0
        assert calls == [0, 2]
        assert agent.calls == 2


def test_long_exchange_syncs_before_next_codex_turn(tmp_path, monkeypatch):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    ticks = [100.0]
    monkeypatch.setattr("kindroid_relay.relay.time.monotonic", lambda: ticks[0])
    class ContextAgent:
        calls = 0
        def reply_to(self, message):
            self.calls += 1
            if self.calls == 1:
                ticks[0] += config.poll_seconds + 1
                return AgentReply("Continue", True)
            assert any(m.message == "Marc interjected" for m in state.recent_messages())
            return AgentReply("Acknowledged", False)
    generate = remote.generate_ai_response
    def generate_with_interjection(*args):
        body = generate(*args)
        remote.messages.append(ChatMessage("marc", "user", "user", "Marc", 4, "Marc interjected"))
        return body
    remote.generate_ai_response = generate_with_interjection
    with RelayState(config.state_path) as state:
        agent = ContextAgent()
        relay = Relay(config, state, remote, lambda: nullcontext(agent))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert remote.history_requests == 3
        assert agent.calls == 2


def test_approval_context_failure_leaves_approval_pending(tmp_path):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        approval = state.add_approval("m1", ActionProposal("Mock", "Mock instruction", "low"))
        state.set_meta("history_cooldown_until", int(time.time()) + 100)
        app = RelayApplication(config, state, FakeKindroid(), lambda: pytest.fail("Codex must not start"))
        with pytest.raises(HistoryCooldownError):
            app.execute_approval(approval)
        assert state.get_approval(approval)["status"] == "pending"


def test_direct_exchange_preserves_automatic_turn_limit(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False, max_consecutive_turns=2)
    remote = FakeKindroid()
    class ContinuingAgent:
        def reply_to(self, message):
            return AgentReply("Continue", True)
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(ContinuingAgent()))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert len(remote.sent) == 2
        assert state.get_int("consecutive_turns") == 2
        assert state.is_paused()
        assert len(state.pending_ai_messages()) == 1
        assert remote.history_requests == 2


def test_reply_capture_rolls_back_atomically_and_can_recover(tmp_path, monkeypatch):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        insert = state.turns._insert_local
        def failing_insert(*args):
            insert(*args)
            if args[3] == "ai":
                raise RuntimeError("simulated storage failure after insert")
        monkeypatch.setattr(state.turns, "_insert_local", failing_insert)
        with pytest.raises(ReconciliationError):
            relay.run_once()
        with RelayState(config.state_path, migrate=False) as reader:
            assert reader.turns.has_uncertain()
            assert reader.pending_ai_messages() == []
            assert len(reader.recent_messages()) == 2  # No half-committed direct reply.
        monkeypatch.setattr(state.turns, "_insert_local", insert)
        state.resume()
        relay.synchronize_context()
        assert len(state.pending_ai_messages()) == 1
        assert not state.turns.has_uncertain()


def test_generated_reply_is_durable_before_codex_failure(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    class FailingAgent(FakeAgent):
        def reply_to(self, message):
            if self.calls:
                raise RuntimeError("simulated Codex failure")
            return super().reply_to(message)
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FailingAgent()))
        relay.bootstrap(respond_to_latest=True)
        with pytest.raises(RuntimeError, match="Codex failure"):
            relay.run_once()
        pending_id = state.pending_ai_messages()[0].id
    class ClosingAgent:
        def reply_to(self, message):
            assert message.id == pending_id
            return AgentReply("Recovered", False)
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(ClosingAgent()))
        relay.run_once()
        assert remote.sent == ["[CODEX]: Hi Hannah", "[CODEX]: Recovered"]
        assert remote.turn_requests == [False]


def test_dashboard_keeps_local_message_link_after_history_confirmation(tmp_path):
    from test_web import dashboard
    config, factory, _wake, client = dashboard(tmp_path)
    remote = FakeKindroid()
    with factory.connect() as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        local_id = state.latest_ai_message().id
        before = client.get(f"/api/v1/messages/{local_id}").json()
        assert before["history_confirmed"] is False
        relay.synchronize_context()
        after = client.get(f"/api/v1/messages/{local_id}").json()
        assert after["history_confirmed"] is True
        assert after["id"] == before["id"]
        assert after["body"] == before["body"]


def test_incomplete_pagination_does_not_bind_messages(tmp_path):
    config = settings(tmp_path)
    remote = FakeKindroid()
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        remote.get_messages_page = lambda *a, **kw: MessagePage(remote.messages[:2], True, 2)
        with pytest.raises(RuntimeError, match="pagination did not advance"):
            relay.synchronize_context()
        assert state.get_int("cursor_timestamp") == 1
        assert state.connection.execute("SELECT COUNT(*) FROM messages WHERE kindroid_id IS NULL").fetchone()[0] == 3


@pytest.mark.parametrize("failure", ["empty", "http", "network", "storage", "unexpected"])
def test_generation_failure_records_safe_specific_cause(tmp_path, monkeypatch, failure):
    import sqlite3
    from kindroid_relay.diagnostics import export_diagnostics
    config = replace(settings(tmp_path), initiative_enabled=False)
    remote = FakeKindroid()
    sentinel = "PRIVATE_FAILURE_BODY_SENTINEL"
    def generate(*args):
        if failure == "empty":
            return "  "
        if failure in {"http", "network"}:
            raise KindroidAPIError(sentinel, 500 if failure == "http" else None, sentinel)
        if failure == "unexpected":
            raise type(sentinel, (Exception,), {})(sentinel)
        return "A private generated reply"
    remote.generate_ai_response = generate
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        if failure == "storage":
            def fail_capture(*args):
                raise sqlite3.OperationalError(sentinel)
            monkeypatch.setattr(state.turns, "complete", fail_capture)
        with pytest.raises(ReconciliationError) as caught:
            relay.run_once()
        assert sentinel not in str(caught.value)
        event = next(e for e in state.activity.after() if e.kind == "generation_uncertain")
        assert event.details["failure_stage"] == ("capture" if failure in {"empty", "storage"} else "request")
        assert event.details["error_type"] == {
            "empty": "EmptyGeneratedReply", "http": "KindroidAPIError",
            "network": "KindroidAPIError", "storage": "OperationalError",
            "unexpected": "UnexpectedError",
        }[failure]
        assert event.correlation_id == event.details["outbox_id"]
        if failure == "http":
            assert event.details["http_status"] == 500
            assert "HTTP 500" in event.summary
        if failure == "empty":
            assert "empty response" in event.summary
        assert state.is_paused()
        assert state.turns.has_uncertain()
        assert len(remote.sent) == 1
        serialized = repr(state.activity.after())
        assert sentinel not in serialized
        assert "A private generated reply" not in serialized
        export = export_diagnostics(config.state_path, tmp_path / "absent.log", tmp_path / "diagnostic.json")
        content = export.read_text(encoding="utf-8")
        assert sentinel not in content
        assert "generation_uncertain" in content
        assert '"failure_stage"' in content


def test_generic_name_recovers_existing_uncertain_turn_from_role_only_history(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False, kindroid_display_name="Kindroid")
    remote = FakeKindroid()
    remote.messages = [replace(remote.messages[0], sender="ai")]
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: pytest.fail("Recovery must not invoke Codex"))
        relay.bootstrap()
        outbound = relay._send_user_message("Check-in")
        # Reproduce a turn already persisted by the previous code, with the
        # generic configured label instead of Hannah's observed name.
        turn_id = state.turns.begin("g1", "ai1", "Kindroid", outbound, 1)
        state.turns.mark_uncertain(turn_id)
        remote.messages.append(ChatMessage("recovered", "ai", "ai", "Hannah", 3, "Existing answer"))
        state.pause("fixture uncertainty")
        relay.synchronize_context()
        assert state.is_paused()  # Synchronization does not grant resume authority.
        assert not state.turns.has_uncertain()
        reply = state.pending_ai_messages()[0]
        assert reply.id == turn_id
        assert reply.display_name == "Hannah"
        assert reply.message == "Existing answer"
        assert remote.turn_requests == []
        assert not remote.generated
        assert len(remote.sent) == 1
        relay.synchronize_context()
        assert len(state.pending_ai_messages()) == 1


def test_generic_name_is_resolved_before_direct_reply_capture(tmp_path):
    config = replace(settings(tmp_path), initiative_enabled=False, kindroid_display_name="Kindroid")
    remote = FakeKindroid()
    remote.messages = [replace(remote.messages[0], sender="ai")]
    with RelayState(config.state_path) as state:
        relay = Relay(config, state, remote, lambda: nullcontext(FakeAgent()))
        relay.bootstrap(respond_to_latest=True)
        relay.run_once()
        assert state.latest_ai_message().display_name == "Hannah"
        turn = state.connection.execute("SELECT display_name FROM generation_turns").fetchone()
        assert turn[0] == "Hannah"
        assert remote.history_requests == 2


@pytest.mark.parametrize("scenario", ["multiple_names", "other_group", "explicit_name", "conflicting_id", "unconfirmed_name"])
def test_generic_name_fallback_requires_unambiguous_existing_group_evidence(tmp_path, scenario):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        configured = "Alice" if scenario == "explicit_name" else "Kindroid"
        group = "other" if scenario == "other_group" else "g1"
        sender = "other-ai" if scenario == "conflicting_id" else "ai"
        state.add_message(group, ChatMessage("known", sender, "ai", "Hannah", 1, "Fixture"), processed=True)
        if scenario == "unconfirmed_name":
            with state.connection:
                state.connection.execute("UPDATE messages SET kindroid_id = NULL")
        if scenario == "multiple_names":
            state.add_message("g1", ChatMessage("other", "ai", "ai", "Alice", 2, "Fixture"), processed=True)
        assert state.turns.expected_display_name("g1", "ai1", configured) == configured


def test_concrete_sender_id_resolves_name_in_multi_ai_group(tmp_path):
    with RelayState(tmp_path / "state.sqlite3") as state:
        state.add_message("g1", ChatMessage("h", "ai1", "ai", "Hannah", 1, "Fixture"))
        state.add_message("g1", ChatMessage("a", "ai2", "ai", "Alice", 2, "Fixture"))
        assert state.turns.expected_display_name("g1", "ai1", "Kindroid") == "Hannah"
