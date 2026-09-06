import threading
import time
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from kindroid_relay.activity import ActivitySeverity, WorkerPhase
from kindroid_relay.command_queue import CommandType, CommandStatus
from kindroid_relay.migrations import migrate_database
from kindroid_relay.models import ActionProposal, ChatMessage
from kindroid_relay.state import RelayStateFactory
from kindroid_relay.web import create_web_app

from test_relay import settings


CSRF = "test-csrf-token"
MUTATION_HEADERS = {
    "X-Relay-CSRF": CSRF,
    "Origin": "http://testserver",
    "Content-Type": "application/json",
}


def test_database_error_response_and_logs_exclude_raw_details(tmp_path, monkeypatch, caplog):
    _config, factory, _wake, client = dashboard(tmp_path)
    def unavailable():
        raise sqlite3.OperationalError("private-database-fixture-value")
    monkeypatch.setattr(factory, "connect", unavailable)
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 500
    assert response.json()["error"] == "database_unavailable"
    assert "private-database-fixture-value" not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"


def dashboard(tmp_path: Path, **settings_overrides):
    config = replace(
        settings(tmp_path),
        web_host="127.0.0.1",
        web_port=8765,
        worker_stale_seconds=20,
        **settings_overrides,
    )
    migrate_database(config.state_path)
    factory = RelayStateFactory(config.state_path)
    wake = threading.Event()
    with factory.connect() as state:
        state.worker_status.update(WorkerPhase.IDLE)
    app = create_web_app(
        config,
        factory,
        wake,
        csrf_token=CSRF,
        testing=True,
    )
    return config, factory, wake, TestClient(app)


def test_dashboard_html_and_security_headers(tmp_path: Path) -> None:
    _config, _factory, _wake, client = dashboard(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert CSRF in response.text
    assert "{{CSRF_TOKEN}}" not in response.text
    assert "start-conversation-status" in response.text
    assert "worker-heartbeat" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    api_client = client.get("/static/api-client.js")
    assert api_client.status_code == 200
    assert "class RelayApiClient" in api_client.text
    assert client.get("/static/ui-model.js").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    script = client.get("/static/app.js").text
    assert "list.prepend(li)" in script
    assert "for (const message of messages.values())" in script


def test_host_header_and_mutation_security(tmp_path: Path) -> None:
    _config, _factory, _wake, client = dashboard(tmp_path)

    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    body = {"idempotency_key": "start-1"}
    path = "/api/v1/commands/start-conversation"
    assert client.post(path, json=body).status_code == 403
    assert client.post(
        path,
        json=body,
        headers={**MUTATION_HEADERS, "Origin": "https://evil.example"},
    ).status_code == 403
    assert client.post(
        path,
        content="idempotency_key=start-1",
        headers={
            "X-Relay-CSRF": CSRF,
            "Origin": "http://testserver",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    ).status_code == 415
    assert client.post(
        path,
        content=b"x" * 20_000,
        headers=MUTATION_HEADERS,
    ).status_code == 413


def test_read_endpoints_do_not_enable_jsonp_or_permissive_cors(
    tmp_path: Path,
) -> None:
    _config, _factory, _wake, client = dashboard(tmp_path)

    response = client.get(
        "/api/v1/snapshot?callback=steal",
        headers={"Origin": "https://evil.example"},
    )
    preflight = client.options(
        "/api/v1/snapshot",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert not response.text.lstrip().startswith("steal(")
    assert "access-control-allow-origin" not in response.headers
    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in preflight.headers


def test_rendered_dashboard_and_api_responses_do_not_expose_configuration_secret(
    tmp_path: Path,
) -> None:
    seeded_key = "kn_seeded-dashboard-credential-12345"
    config, factory, _wake, client = dashboard(tmp_path, api_key=seeded_key)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.write_text(f"configured with {seeded_key}\n", encoding="utf-8")
    with factory.connect() as state:
        state.activity.add(
            "relay_cycle_failed",
            ActivitySeverity.ERROR,
            f"Cycle failed using {seeded_key}",
            details={"error_type": "SeededFailure"},
        )

    responses = [
        client.get("/"),
        client.get("/api/v1/snapshot"),
        client.get("/api/v1/activity"),
        client.get("/api/v1/messages"),
        client.get("/api/v1/approvals"),
        client.get("/api/v1/diagnostics/log-tail"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert seeded_key not in "\n".join(response.text for response in responses)


def test_health_snapshot_activity_and_message_reads(tmp_path: Path) -> None:
    config, factory, _wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        state.add_message(
            config.group_id,
            ChatMessage("h1", "ai", "ai", "Hannah", 1000, "Hello"),
        )
        outbound = state.create_outbox("[CODEX]: Hi")
        state.set_outbox_status(outbound, "sent")
        state.add_message(
            config.group_id,
            ChatMessage("u1", "user", "user", "Marc", 2000, "[CODEX]: Hi"),
            processed=True,
        )
        state.activity.add(
            "message_received",
            ActivitySeverity.INFO,
            "Hannah message received",
            details={"message_id": "h1", "display_name": "Hannah"},
        )

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    snapshot = client.get("/api/v1/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["worker"]["phase"] == "idle"
    assert snapshot.json()["schedule"]["history_request_limit_24h"] == 100
    assert snapshot.json()["schedule"]["active_poll_seconds"] == 180
    assert snapshot.json()["schedule"]["idle_poll_seconds"] == 600
    assert snapshot.json()["schedule"]["activity_retention_days"] == 30
    assert snapshot.json()["relay"]["initiative_schedule_basis"] == ""

    activity = client.get("/api/v1/activity?after_id=0&limit=1").json()
    assert len(activity["items"]) == 1
    assert activity["next_after_id"] == activity["items"][0]["id"]

    messages = client.get("/api/v1/messages?limit=10").json()["items"]
    assert [item["id"] for item in messages] == ["u1", "h1"]
    assert messages[0]["known_codex_outbound"]
    assert messages[0]["identity_fallback"]


def test_snapshot_detects_stale_worker_without_sleeping(tmp_path: Path) -> None:
    config, factory, _wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        stale_at = int(time.time()) - config.worker_stale_seconds - 1
        with state.connection:
            state.connection.execute(
                "UPDATE worker_status SET heartbeat_at = ? WHERE singleton = 1",
                (stale_at,),
            )

    assert client.get("/api/v1/snapshot").json()["worker"]["stale"] is True


def test_command_submission_is_durable_idempotent_and_wakes_worker(
    tmp_path: Path,
) -> None:
    _config, factory, wake, client = dashboard(tmp_path)
    path = "/api/v1/commands/start-conversation"
    body = {"idempotency_key": "start-1"}

    first = client.post(path, json=body, headers=MUTATION_HEADERS)
    second = client.post(path, json=body, headers=MUTATION_HEADERS)

    assert first.status_code == 202
    assert first.json()["created"] is True
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert first.json()["id"] == second.json()["id"]
    tracked = client.get(f"/api/v1/commands/{first.json()['id']}")
    assert tracked.status_code == 200
    assert tracked.json()["status"] == "queued"
    assert wake.is_set()
    with factory.connect() as state:
        assert state.commands.get(first.json()["id"]).command_type == (
            CommandType.START_CONVERSATION
        )
        assert state.activity.after()[-1].kind == "command_queued"

    conflict = client.post(
        path,
        json={"idempotency_key": "start-2"},
        headers=MUTATION_HEADERS,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "command_conflict"


def test_pause_and_approval_commands(tmp_path: Path) -> None:
    _config, factory, wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        approval_id = state.add_approval(
            "h1",
            ActionProposal("Write a file", "Create note.txt", "low"),
        )

    pause = client.post(
        "/api/v1/control/pause",
        json={"reason": "operator review"},
        headers=MUTATION_HEADERS,
    )
    assert pause.status_code == 200
    assert pause.json()["paused"] is True
    assert wake.is_set()
    with factory.connect() as state:
        assert state.is_paused()

    detail = client.get(f"/api/v1/approvals/{approval_id}")
    assert detail.status_code == 200
    assert detail.json()["effective_sandbox"] == "workspace-write"

    execute = client.post(
        f"/api/v1/approvals/{approval_id}/execute",
        json={"idempotency_key": "execute-1", "post_result": True},
        headers=MUTATION_HEADERS,
    )
    assert execute.status_code == 202
    with factory.connect() as state:
        command = state.commands.get(execute.json()["id"])
        assert command.payload == {"approval_id": approval_id, "post_result": True}


def test_log_tail_is_bounded_and_redacted(tmp_path: Path) -> None:
    config, _factory, _wake, client = dashboard(tmp_path)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.write_text(
        "safe line\nAuthorization: secret\nkn_fake-secret\n",
        encoding="utf-8",
    )

    response = client.get("/api/v1/diagnostics/log-tail?lines=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is True
    assert "secret" not in "\n".join(payload["lines"])
    assert "kn_fake" not in "\n".join(payload["lines"])


def test_log_tail_handles_missing_current_log_without_reading_rotated_file(
    tmp_path: Path,
) -> None:
    config, _factory, _wake, client = dashboard(tmp_path)
    rotated = config.log_path.with_name(f"{config.log_path.name}.1")
    rotated.parent.mkdir(parents=True, exist_ok=True)
    rotated.write_text("old rotated content\n", encoding="utf-8")

    response = client.get("/api/v1/diagnostics/log-tail")

    assert response.status_code == 200
    assert response.json() == {
        "lines": [],
        "truncated": False,
        "source": config.log_path.name,
    }


def test_log_tail_bounds_oversized_file_and_repairs_invalid_utf8(
    tmp_path: Path,
) -> None:
    config, _factory, _wake, client = dashboard(tmp_path)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    config.log_path.write_bytes(
        b"old line\n" * 300_000 + b"damaged \xff line\nlast line\n"
    )

    response = client.get("/api/v1/diagnostics/log-tail?lines=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["lines"] == ["damaged � line", "last line"]
    assert payload["truncated"] is True
    assert payload["source"] == config.log_path.name


def test_validation_and_missing_items_use_safe_error_shape(tmp_path: Path) -> None:
    _config, _factory, _wake, client = dashboard(tmp_path)

    invalid = client.post(
        "/api/v1/commands/start-conversation",
        json={"idempotency_key": "ok", "unexpected": True},
        headers=MUTATION_HEADERS,
    )
    missing = client.get("/api/v1/approvals/missing")

    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": "invalid_request",
        "message": "The request body or query is invalid.",
    }
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"


def test_message_pages_preserve_timestamp_ties_and_stored_attribution(tmp_path: Path) -> None:
    config, factory, _wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        for remote_id, name, sender_type in [("m3", "Hannah", "ai"), ("m2", "Marc", "user"), ("m1", "Someone else", "ai")]:
            state.add_message(config.group_id, ChatMessage(remote_id, remote_id, sender_type, name, 1000, "text"))
    first = client.get("/api/v1/messages?limit=2").json()
    assert [item["id"] for item in first["items"]] == ["m3", "m2"]
    second = client.get("/api/v1/messages", params={"limit": 2, "before": first["next_before"], "before_id": first["next_before_id"]}).json()
    assert [item["id"] for item in second["items"]] == ["m1"]
    assert second["has_more"] is False
    assert [item["participant"] for item in first["items"]] == ["hannah", "marc"]
    assert second["items"][0]["participant"] == "unknown"
    assert client.get("/api/v1/messages/m2").json()["sender"] == "m2"
    with factory.connect() as state:
        state.create_outbox("text")
    assert client.get("/api/v1/messages/m3").json()["participant"] == "hannah"


def test_delivery_details_are_bounded_and_omit_body_and_raw_error(tmp_path: Path) -> None:
    _config, factory, _wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        delivery = state.create_outbox("private body", source_message_id="m1")
        state.set_outbox_status(delivery, "uncertain", "raw remote response")
    data = client.get("/api/v1/deliveries").json()
    assert data["items"][0]["id"] == delivery
    assert data["items"][0]["source_message_id"] == "m1"
    detail = client.get(f"/api/v1/deliveries/{delivery}")
    assert "private body" not in detail.text
    assert "raw remote response" not in detail.text
    assert client.get("/api/v1/deliveries/missing").status_code == 404


def test_command_recovery_and_approval_retry_after_completion(tmp_path: Path) -> None:
    _config, factory, _wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        approval_id = state.add_approval("m1", ActionProposal("Write file", "Write note", "low"))
    url = f"/api/v1/approvals/{approval_id}/execute"
    body = {"idempotency_key": "stable-approval", "post_result": False}
    initial = client.post(url, json=body, headers=MUTATION_HEADERS).json()
    with factory.connect() as state:
        state.commands.claim_next()
        state.commands.transition(initial["id"], CommandStatus.COMPLETED)
        state.update_approval(approval_id, "executed", "Done")
    retried = client.post(url, json=body, headers=MUTATION_HEADERS)
    assert retried.status_code == 202
    assert retried.json()["id"] == initial["id"]
    assert retried.json()["status"] == "completed"
    assert retried.json()["approval_id"] == approval_id
    assert client.get("/api/v1/commands").json()["items"][0]["id"] == initial["id"]
    changed = client.post(url, json={**body, "post_result": True}, headers=MUTATION_HEADERS)
    assert changed.status_code == 409


def test_log_refresh_uses_etag_and_changes_when_log_rotates(tmp_path: Path) -> None:
    config, _factory, _wake, client = dashboard(tmp_path)
    config.log_path.write_text("first\n", encoding="utf-8")
    response = client.get("/api/v1/diagnostics/log-tail")
    headers = {"If-None-Match": response.headers["etag"]}
    assert client.get("/api/v1/diagnostics/log-tail", headers=headers).status_code == 304
    config.log_path.write_text("new log\n", encoding="utf-8")
    changed = client.get("/api/v1/diagnostics/log-tail", headers=headers)
    assert changed.status_code == 200
    assert changed.json()["lines"] == ["new log"]
