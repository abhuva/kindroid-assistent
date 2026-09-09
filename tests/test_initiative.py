from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import time

import pytest

from kindroid_relay.application import RelayApplication
from kindroid_relay.models import AgentReply, ChatMessage
from kindroid_relay.relay import HistoryCooldownError
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from test_relay import FakeKindroid, settings


class CheckInAgent:
    def __init__(self):
        self.check_ins = 0

    def scheduled_check_in(self, latest):
        self.check_ins += 1
        return "A mock check-in"

    def reply_to(self, message):
        return AgentReply("A mock closing", False)


class CurrentHistoryKindroid(FakeKindroid):
    def generate_ai_response(self, group_id, ai_id):
        self.generated = True
        timestamp = max(message.timestamp for message in self.messages) + 1
        self.messages.append(ChatMessage(f"h-{timestamp}", ai_id, "ai", "Hannah", timestamp, "Mock answer"))
        return "Mock answer"


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    def unexpected_sleep(_delay):
        pytest.fail("Unexpected retry sleep in initiative mock test")
    monkeypatch.setattr("kindroid_relay.relay.time.sleep", unexpected_sleep)


def setup_application(state, config, *, speaker="Codex Test"):
    remote = CurrentHistoryKindroid()
    body = "[CODEX]: Previous mock reply"
    remote.messages = [ChatMessage("u0", "user", "user", speaker, int(time.time()) - 3600, body)]
    delivery = state.create_outbox(body)
    state.set_outbox_status(delivery, "sent")
    agent = CheckInAgent()
    app = RelayApplication(config, state, remote, lambda: nullcontext(agent))
    return app, remote, agent


@pytest.mark.parametrize("hours", [None, (0, 6, 12, 18)])
@pytest.mark.parametrize("speaker", ["Codex Test", "Marc"])
def test_manual_trigger_survives_new_history_and_stored_identity_fallback(tmp_path: Path, hours, speaker) -> None:
    config = replace(settings(tmp_path), initiative_schedule_hours=hours)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config, speaker=speaker)
        app.bootstrap()
        body = "[CODEX]: Newer mock reply"
        delivery = state.create_outbox(body)
        state.set_outbox_status(delivery, "sent")
        remote.messages.append(ChatMessage("previous-new", "user", "user", speaker, int(time.time()) - 1800, body))
        result = app.trigger_initiative()
        assert result == {"handled": 1, "initiative_outcome": "check_in_answered", "check_in_sent": True}
        assert agent.check_ins == 1
        assert len(remote.sent) == 2
        assert remote.turn_requests == [True]
        assert state.get_int("initiative_due_at") > time.time()


def test_scheduled_check_in_recognizes_known_codex_stored_as_marc(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config, speaker="Marc")
        app.bootstrap()
        state.set_meta("initiative_due_at", 1)
        assert app.run_once() == 1
        assert agent.check_ins == 1
        assert len(remote.sent) == 2


@pytest.mark.parametrize("reason", ["paused", "disabled", "quiet_hours", "daily_budget", "recent_conversation", "empty_room"])
def test_manual_trigger_preserves_guards_and_reports_its_own_outcome(tmp_path: Path, reason) -> None:
    config = replace(settings(tmp_path), initiative_max_checks_24h=1)
    if reason == "disabled":
        config = replace(config, initiative_enabled=False)
    if reason == "quiet_hours":
        config = replace(config, initiative_quiet_hours=(0, 0))
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        if reason == "empty_room":
            remote.messages = []
        if reason == "recent_conversation":
            # Activity matters, not a speaker label or textual prefix.
            remote.messages = [ChatMessage("human", "user", "user", "Marc", int(time.time()), "[CODEX]: Not in our outbox")]
        app.bootstrap()
        if reason == "paused":
            state.pause("operator pause")
        if reason == "daily_budget":
            state.record_initiative_check(1, "previous_check")
        result = app.trigger_initiative()
        assert result == {"handled": 0, "initiative_outcome": reason, "check_in_sent": False}
        assert remote.sent == []
        assert agent.check_ins == 0
        assert any(event.kind == "initiative_skipped" and event.details["outcome"] == reason for event in state.activity.after())
        assert state.initiative_check_count() == (1 if reason == "daily_budget" else 0)
        if reason == "daily_budget":
            assert state.status()["last_initiative_outcome"] == "previous_check"


def test_pending_hannah_message_is_handled_without_an_extra_check_in(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        remote.messages.append(ChatMessage("h-new", "ai1", "ai", "Hannah", int(time.time()) + 1, "Mock pending question"))
        result = app.trigger_initiative()
        assert result == {"handled": 1, "initiative_outcome": "pending_message", "check_in_sent": False}
        assert agent.check_ins == 0
        assert len(remote.sent) == 1  # Normal reply, not an initiative opener.
        assert state.initiative_check_count() == 0


def test_first_trigger_bootstraps_without_replaying_history(tmp_path: Path) -> None:
    with RelayState((config := settings(tmp_path)).state_path) as state:
        app, remote, agent = setup_application(state, config)
        assert app.trigger_initiative()["initiative_outcome"] == "bootstrapped"
        assert remote.sent == []
        assert state.initiative_check_count() == 0
        assert app.trigger_initiative()["check_in_sent"] is True


def test_manual_trigger_does_not_bypass_history_cooldown(tmp_path: Path) -> None:
    with RelayState((config := settings(tmp_path)).state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        state.set_meta("history_cooldown_until", int(time.time()) + 3600)
        reads = remote.history_requests
        with pytest.raises(HistoryCooldownError):
            app.trigger_initiative()
        assert remote.history_requests == reads
        assert agent.check_ins == 0
        assert remote.sent == []


def test_sent_check_in_without_hannah_response_is_not_reported_as_skipped(tmp_path: Path) -> None:
    with RelayState((config := settings(tmp_path)).state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        remote.get_turn = lambda *_args, **_kwargs: None
        result = app.trigger_initiative()
        assert result == {"handled": 0, "initiative_outcome": "check_in_sent_user_turn", "check_in_sent": True}
        assert len(remote.sent) == 1
        assert state.initiative_check_count() == 1


@pytest.mark.parametrize("quiet", [False, True])
def test_api_worker_persists_check_in_outcome_after_another_poll(tmp_path: Path, quiet) -> None:
    from test_web import dashboard, MUTATION_HEADERS

    config, factory, _wake, client = dashboard(tmp_path, initiative_quiet_hours=(0, 0) if quiet else None)
    with factory.connect() as state:
        app, remote, agent = setup_application(state, config, speaker="Marc")
        app.bootstrap()
        response = client.post("/api/v1/commands/trigger-initiative", json={"idempotency_key": "manual-check-in"}, headers=MUTATION_HEADERS)
        assert response.status_code == 202
        command = state.commands.claim_next()
        RelayWorker(config)._run_command(state, app, command)
        app.run_once()  # A subsequent idle poll must not overwrite command results.
        completed_event = [event for event in state.activity.after() if event.kind == "command_completed"][-1]
    data = client.get(f"/api/v1/commands/{command.id}").json()
    expected = "quiet_hours" if quiet else "check_in_answered"
    assert data["status"] == "completed"
    assert data["result"]["initiative_outcome"] == expected
    assert data["result"]["check_in_sent"] is not quiet
    assert completed_event.details["outcome"] == expected
    assert completed_event.correlation_id == command.id
    assert client.get("/api/v1/commands").json()["items"][0]["result"] == data["result"]


@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('speaker,sender_type', [('Hannah', 'ai'), ('Marc', 'user'), ('Codex Test', 'user')])
def test_idle_check_in_after_any_processed_speaker_at_midnight(tmp_path, monkeypatch, manual, speaker, sender_type):
    now = int(datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    monkeypatch.setattr(time, 'time', lambda: now)
    config = replace(settings(tmp_path), initiative_schedule_hours=(0, 6, 12, 18))
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        remote.messages = [ChatMessage('last', 'ai1' if sender_type == 'ai' else 'user', sender_type,
                                       speaker, now - 3600, 'Good night')]
        app.bootstrap()  # Last message is explicitly processed, not pending.
        state.set_meta('initiative_due_at', now)
        state.set_meta('initiative_schedule_basis', app.relay._initiative_schedule_basis())
        reads = remote.history_requests
        if manual:
            assert app.trigger_initiative()['check_in_sent'] is True
        else:
            assert app.run_once() == 1
        assert agent.check_ins == 1
        assert remote.history_requests == reads + 1  # Normal pre-context sync only.
        assert remote.turn_requests == [True]
        assert state.initiative_check_count() == 1
        assert state.get_int('initiative_due_at') > now
        assert not any(e.details.get('outcome') == 'latest_not_codex' for e in state.activity.after())
        app.run_once()
        assert agent.check_ins == 1  # No replay of the consumed midnight slot.


@pytest.mark.parametrize('milliseconds', [False, True])
@pytest.mark.parametrize('age,allowed', [(299, False), (300, True), (-60, False)])
def test_idle_boundary_uses_message_time_not_speaker_or_import_time(tmp_path, monkeypatch, milliseconds, age, allowed):
    now = 2_000_000_000
    monkeypatch.setattr(time, 'time', lambda: now)
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        timestamp = (now - age) * (1000 if milliseconds else 1)
        remote.messages = [ChatMessage('last', 'ai1', 'ai', 'Hannah', timestamp, 'All done')]
        app.bootstrap()
        remote.get_turn = lambda *_args, **_kwargs: None
        result = app.trigger_initiative()
        assert result['check_in_sent'] is allowed
        assert agent.check_ins == int(allowed)
        assert state.initiative_check_count() == int(allowed)
        if not allowed:
            assert result['initiative_outcome'] == 'recent_conversation'


def test_fresh_marc_activity_discovered_during_sync_blocks_check_in(tmp_path):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        remote.messages.append(ChatMessage('new', 'user', 'user', 'Marc', int(time.time()), 'Still talking'))
        assert app.trigger_initiative()['initiative_outcome'] == 'recent_conversation'
        assert agent.check_ins == 0
        assert remote.sent == []
        assert state.initiative_check_count() == 0


@pytest.mark.parametrize('status', ['pending', 'sending', 'uncertain'])
def test_unresolved_delivery_prevents_new_check_in_without_retry(tmp_path, status):
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        app, remote, agent = setup_application(state, config)
        app.bootstrap()
        oid = state.create_outbox('Unresolved fixture delivery')
        state.set_outbox_status(oid, status)
        assert app.trigger_initiative()['initiative_outcome'] == 'delivery_unresolved'
        assert agent.check_ins == 0
        assert remote.sent == []
        assert state.initiative_check_count() == 0
        assert state.connection.execute('SELECT status FROM outbox WHERE id=?', (oid,)).fetchone()[0] == status
