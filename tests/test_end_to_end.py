"""Real loopback HTTP + combined host + SQLite/worker; external adapters are fakes."""
import re
import socket
import sqlite3
import threading
import time
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace

import httpx
import pytest
import uvicorn

from kindroid_relay.application import RelayApplication
from kindroid_relay.command_queue import CommandType
from kindroid_relay.kindroid_api import KindroidAPIError
from kindroid_relay.models import AgentReply
from kindroid_relay.server import serve_dashboard
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from test_relay import FakeKindroid, settings


def eventually(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Expected local test state before deadline")


class JourneyKindroid(FakeKindroid):
    failure = None

    def get_messages_page(self, *args, **kwargs):
        if self.failure == "429":
            self.history_requests += 1
            raise KindroidAPIError("history", 429, "fixture quota", retry_after_seconds=600)
        return super().get_messages_page(*args, **kwargs)

    def post_user_message(self, *args, **kwargs):
        super().post_user_message(*args, **kwargs)
        if self.failure == "outbound":
            raise KindroidAPIError("send", None, "ReadTimeout")

    def activate_user_persona(self, persona_id, **profile):
        super().activate_user_persona(persona_id, **profile)
        if self.failure == "persona" and profile["name"] == "Marc":
            raise KindroidAPIError("restore", 500, "fixture failure")


class JourneyAgent:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.failure = False
        self.calls = 0

    def manual_conversation_opener(self, _latest):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(5), "Test must release Codex fake"
        if self.failure:
            raise RuntimeError("fixture Codex failure")
        return "Opening the conversation"

    def reply_to(self, _message):
        return AgentReply("Closing the conversation", False)


class DashboardJourney:
    def __init__(self, client, config, kindroid, agent):
        self.client, self.config, self.kindroid, self.agent = client, config, kindroid, agent
        self.refresh()

    def refresh(self):
        page = self.client.get("/")
        assert page.status_code == 200
        token = re.search(r'name="relay-csrf" content="([^"]+)"', page.text)[1]
        self.headers = {"X-Relay-CSRF": token, "Origin": str(self.client.base_url).rstrip("/")}

    def read(self, path):
        response = self.client.get("/api/v1/" + path)
        assert response.status_code == 200, response.text
        return response.json()

    def submit(self, key="conversation"):
        response = self.client.post("/api/v1/commands/start-conversation", json={"idempotency_key": key}, headers=self.headers)
        assert response.status_code == 202, response.text
        return response.json()

    def finished(self, command_id, status="completed"):
        return eventually(lambda: (row if (row := self.read("commands/" + command_id))["status"] == status else None))


@pytest.fixture
def journey_host(tmp_path, monkeypatch):
    @contextmanager
    def start(*, interrupted=False, crash_worker=False, recovering=False):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = replace(settings(tmp_path), web_port=port, web_open_browser=False,
                         initiative_enabled=False, poll_seconds=600, idle_poll_seconds=600,
                         worker_heartbeat_seconds=60, worker_stale_seconds=120)
        kindroid, agent = JourneyKindroid(), JourneyAgent()
        with RelayState(config.state_path) as state:
            state.add_message(config.group_id, kindroid.messages[0], processed=True)
            state.set_meta("bootstrapped", 1)
            if interrupted:
                command, _ = state.commands.enqueue(CommandType.START_CONVERSATION, {}, idempotency_key="interrupted")
                state.commands.claim_next()
                outbox = state.create_outbox("[CODEX]: Already possibly sent")
                state.set_outbox_status(outbox, "sending")
        servers, failures = [], []

        class TestWorker(RelayWorker):
            @contextmanager
            def _default_application(self, state):
                yield RelayApplication(config, state, kindroid, lambda: nullcontext(agent),
                    phase_context=lambda phase, note: self._scoped_phase(state, phase, note))

            def _run_command(self, *args):
                if crash_worker:
                    raise RuntimeError("fixture worker crash")
                return super()._run_command(*args)

        original_server = uvicorn.Server

        class SocketServer(original_server):
            def __init__(self, config):
                super().__init__(config)
                servers.append(self)

            def run(self):
                # Real Uvicorn in a non-main thread: no console signals/handlers.
                return super().run(sockets=[listener])

        def run_host():
            try:
                serve_dashboard(config)
            except Exception as exc:
                failures.append(exc)

        with monkeypatch.context() as patch:
            patch.setattr("kindroid_relay.server.RelayWorker", TestWorker)
            patch.setattr("kindroid_relay.server.uvicorn.Server", SocketServer)
            # The listener is already exclusively reserved for this test.
            patch.setattr("kindroid_relay.server.ensure_port_available", lambda *_args: None)
            thread = threading.Thread(target=run_host, daemon=True)
            thread.start()
            try:
                eventually(lambda: servers and servers[0].started)
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=8, trust_env=False) as client:
                    journey = DashboardJourney(client, config, kindroid, agent)
                    if not interrupted and not recovering:
                        eventually(lambda: journey.read("snapshot")["worker"]["phase"] == "idle")
                        assert kindroid.history_requests == 1  # One startup sync, no polling.
                    yield journey
            finally:
                agent.release.set()
                if servers:
                    servers[0].should_exit = True
                thread.join(5)
                listener.close()
            assert not thread.is_alive(), "Combined test host did not stop"
            assert not failures
            assert not config.state_path.with_suffix(".lock").exists()
    return start


def test_complete_http_conversation_refresh_and_idempotent_retry(journey_host):
    with journey_host() as app:
        app.agent.release.clear()
        command = app.submit()
        assert app.agent.entered.wait(2)
        app.refresh()  # Actual HTTP page reload while the command remains running.
        assert app.read("snapshot")["active_command"]["id"] == command["id"]
        assert app.read("commands/" + command["id"])["status"] == "running"
        retry = app.submit()
        assert retry["id"] == command["id"] and not retry["created"]
        app.agent.release.set()
        assert app.finished(command["id"])["result"] == {"handled": 1}
        synced = app.client.post('/api/v1/commands/sync-history',
                                 json={'idempotency_key': 'confirm-history'}, headers=app.headers)
        assert synced.status_code == 202
        app.finished(synced.json()['id'])
        eventually(lambda: all(m["history_confirmed"] for m in app.read("messages")["items"]))
        messages = app.read("messages")["items"]
        assert len(messages) == 4
        assert len({m["id"] for m in messages}) == 4
        assert {m["participant"] for m in messages} == {"codex", "hannah"}
        kinds = [e["kind"] for e in app.read("activity?limit=500")["items"]]
        assert kinds.count("command_completed") == 2
        assert {"codex_started", "message_received", "message_processed", "outbound_sent"} <= set(kinds)
        assert app.agent.calls == 1
        assert app.kindroid.sent == ["[CODEX]: Opening the conversation", "[CODEX]: Closing the conversation"]
        app.submit()
        assert len(app.kindroid.sent) == 2


@pytest.mark.parametrize("failure", ["429", "outbound", "codex", "persona"])
def test_failure_visible_without_repeating_external_effects(journey_host, failure):
    with journey_host() as app:
        app.kindroid.failure = failure
        app.agent.failure = failure == "codex"
        command = app.submit()
        app.finished(command["id"], "failed")
        app.refresh()
        assert app.submit()["id"] == command["id"]
        snapshot = app.read("snapshot")
        kinds = {e["kind"] for e in app.read("activity?limit=500")["items"]}
        assert "command_failed" in kinds
        if failure == "429":
            assert snapshot["relay"]["history_cooldown_until"] > time.time()
            assert "history_rate_limited" in kinds
            assert app.kindroid.history_requests == 2
        if failure in {"outbound", "persona"}:
            assert snapshot["relay"]["paused"]
            assert "outbound_uncertain" in kinds
            assert [row["status"] for row in app.read("deliveries")["items"]] == ["uncertain"]
            assert app.kindroid.persona_changes == ["Codex Test", "Marc"]
        assert len(app.kindroid.sent) == (1 if failure in {"outbound", "persona"} else 0)
        assert not app.kindroid.generated


def test_database_busy_request_has_safe_error_and_can_be_retried(journey_host):
    with journey_host() as app:
        with closing(sqlite3.connect(app.config.state_path)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            response = app.client.post("/api/v1/commands/start-conversation", json={"idempotency_key": "busy"}, headers=app.headers)
            assert response.status_code == 503
            assert response.json()["error"] == "database_busy"
            assert response.headers["cache-control"] == "no-store"
            assert "locked" not in response.text and "Traceback" not in response.text
        assert app.read("commands")["items"] == []
        assert app.kindroid.sent == []
        app.finished(app.submit("busy")["id"])


def test_worker_crash_keeps_dashboard_available_and_command_durable(journey_host):
    with journey_host(crash_worker=True) as app:
        command = app.submit()
        eventually(lambda: app.read("snapshot")["worker"]["phase"] == "degraded")
        app.refresh()
        assert app.read("commands/" + command["id"])["status"] == "running"
        assert "relay_cycle_failed" in {e["kind"] for e in app.read("activity")["items"]}
        assert app.kindroid.sent == []
    # Restart the combined host against the SAME database, without replaying work.
    with journey_host(recovering=True) as restarted:
        assert restarted.read("commands/" + command["id"])["status"] == "interrupted"
        assert restarted.read("snapshot")["relay"]["paused"]
        assert restarted.kindroid.sent == []
        eventually(lambda: restarted.kindroid.history_requests == 1)


def test_restart_recovers_interrupted_command_without_replay(journey_host):
    with journey_host(interrupted=True) as app:
        commands = app.read("commands")["items"]
        assert len(commands) == 1 and commands[0]["status"] == "interrupted"
        assert app.read("snapshot")["relay"]["paused"]
        assert app.read("deliveries")["items"][0]["status"] == "sending"
        assert app.kindroid.sent == []
        eventually(lambda: app.kindroid.history_requests == 1)


def test_real_host_rejects_cross_origin_and_host_spoofing(journey_host):
    with journey_host() as app:
        response = app.client.post("/api/v1/commands/start-conversation", json={"idempotency_key": "hostile"},
                                   headers={**app.headers, "Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert app.client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
        assert app.read("commands")["items"] == []
        assert app.kindroid.sent == []
