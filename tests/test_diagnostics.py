from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kindroid_relay import cli, diagnostics
from kindroid_relay.activity import ActivitySeverity, WorkerPhase
from kindroid_relay.command_queue import CommandType
from kindroid_relay.diagnostics import DiagnosticError, export_diagnostics
from kindroid_relay.migrations import LATEST_SCHEMA_VERSION
from kindroid_relay.models import ActionProposal, ChatMessage
from kindroid_relay.state import RelayState
from test_relay import settings


def test_export_omits_sensitive_content_and_preserves_correlated_health(tmp_path: Path) -> None:
    config = settings(tmp_path)
    secret = "private-string-with-no-known-secret-prefix"
    key = "kn_seeded-test-credential"
    with RelayState(config.state_path) as state:
        state.pause(secret)
        state.set_meta("codex_thread_id", secret)
        state.set_meta("history_cooldown_until", 1234567890)
        state.add_message(config.group_id, ChatMessage("m1", "ai1", "ai", "Hannah", 1000, secret))
        approval = state.add_approval("m1", ActionProposal(secret, secret, "low"))
        command, _ = state.commands.enqueue(CommandType.EXECUTE_APPROVAL, {"approval_id": approval, "post_result": False}, idempotency_key="test-action")
        state.worker_status.update(WorkerPhase.PAUSED, active_command_id=command.id, note=secret)
        state.activity.add("command_queued", ActivitySeverity.INFO, secret,
                           details={"command_id": command.id, "command_type": "execute_approval", "reason": secret}, correlation_id=command.id)
        # Old/corrupt rows bypass current redaction and allow-list validation.
        state.connection.execute("UPDATE activity_events SET details_json = ?", (json.dumps({
            "command_id": command.id, "command_type": "execute_approval", "api_key": key,
            "authorization": f"Bearer {secret}", "cookie": secret, "message_body": secret,
            "reason": secret, "display_name": secret, "error_type": secret,
        }),))
        state.connection.commit()
        before = tuple(state.connection.iterdump())
        config.log_path.write_text(f"2026-09-06 12:00:00,001 ERROR kindroid_relay.relay: {secret} {key}\nAuthorization: Bearer {secret}\nCookie: a={secret}; b={key}\n", encoding="utf-8")
        destination = export_diagnostics(config.state_path, config.log_path, tmp_path / "diagnostics.json")
        assert tuple(state.connection.iterdump()) == before
        assert state.is_paused()
        assert state.get_approval(approval)["status"] == "pending"
    text = destination.read_text(encoding="utf-8")
    for omitted in (secret, key, command.id, str(tmp_path), config.group_id, "Create note.txt"):
        assert omitted not in text
    data = json.loads(text)
    assert data["schema_version"] == LATEST_SCHEMA_VERSION
    assert data["status"]["paused"] is True
    assert data["status"]["pending_messages"] == 1
    assert data["status"]["pending_approvals"] == 1
    assert data["status"]["pending_commands"] == 1
    event = data["activity"]["items"][0]
    assert event["details"]["command_ref"] == event["correlation_ref"] == data["worker"]["active_command_ref"]
    assert event["details"]["error_type"] == "unknown"
    assert data["log_tail"]["headers"][0]["level"] == "ERROR"
    assert len(data["log_tail"]["headers"]) == 1
    assert "Review" in data["review_notice"]
    other = export_diagnostics(config.state_path, config.log_path, tmp_path / "other.json")
    assert json.loads(other.read_text())["worker"]["active_command_ref"] != data["worker"]["active_command_ref"]


def test_export_is_bounded_and_tolerates_bad_event_json(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        for _ in range(105):
            state.activity.add("relay_cycle_completed", ActivitySeverity.INFO, "done", details={"turn_count": 1})
        state.connection.execute("UPDATE activity_events SET details_json = ? WHERE id = 105", ("not json",))
        state.connection.execute("UPDATE activity_events SET details_json = ? WHERE id = 104", ('{"reason":"' + "x" * 10000 + '"}',))
        state.connection.execute("UPDATE activity_events SET details_json = ? WHERE id = 103", ('{"message_id":"\\ud800"}',))
        state.connection.commit()
    config.log_path.write_bytes(b"previous line\n" * 200000 + b"2026-09-06 12:00:00,001 INFO kindroid_relay.relay: done\n" * 250)
    path = export_diagnostics(config.state_path, config.log_path, tmp_path / "diagnostics.json")
    data = json.loads(path.read_text())
    assert path.stat().st_size <= diagnostics.MAX_BUNDLE_BYTES
    assert data["activity"]["truncated"] is True
    events = data["activity"]["items"]
    assert len(events) == diagnostics.MAX_EVENTS
    assert events[0]["id"] == 105
    assert events[0]["details_omitted"] is True
    assert events[1]["details_omitted"] is True
    assert events[2]["details"]["message_ref"].startswith("ref-")
    assert len(data["log_tail"]["headers"]) == diagnostics.MAX_LOG_LINES
    assert data["log_tail"]["truncated"] is True


def test_export_never_overwrites_or_creates_runtime_files(tmp_path: Path) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path):
        pass
    existing = tmp_path / "existing.json"
    existing.write_text("keep this")
    for destination in (existing, config.state_path, config.log_path, config.state_path.with_suffix(".lock")):
        with pytest.raises(DiagnosticError):
            export_diagnostics(config.state_path, config.log_path, destination)
    assert existing.read_text() == "keep this"
    assert not config.log_path.exists()
    with pytest.raises(DiagnosticError):
        export_diagnostics(config.state_path, config.log_path, tmp_path / "missing-directory" / "out.json")
    assert not (tmp_path / "missing-directory").exists()


@pytest.mark.parametrize("database_kind", ["missing", "corrupt", "old", "future"])
def test_export_fails_safely_without_creating_or_migrating_database(tmp_path: Path, database_kind: str) -> None:
    config = settings(tmp_path)
    if database_kind == "corrupt":
        config.state_path.write_bytes(b"private-corrupt-database")
    elif database_kind in {"old", "future"}:
        with sqlite3.connect(config.state_path) as connection:
            connection.execute(f"PRAGMA user_version={1 if database_kind == 'old' else 999}")
    before = config.state_path.read_bytes() if config.state_path.exists() else None
    destination = tmp_path / "out.json"
    with pytest.raises(DiagnosticError) as error:
        export_diagnostics(config.state_path, config.log_path, destination)
    assert "private" not in str(error.value)
    assert not destination.exists()
    assert (config.state_path.read_bytes() if config.state_path.exists() else None) == before


def test_export_respects_query_deadline_and_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path) as state:
        state.connection.executemany("INSERT INTO history_requests VALUES(?)", [(0,)] * 1000)
        state.connection.commit()
    monkeypatch.setattr(diagnostics, "READ_TIMEOUT_SECONDS", -1)
    with pytest.raises(DiagnosticError, match="deadline"):
        export_diagnostics(config.state_path, config.log_path, tmp_path / "timeout.json")
    assert not (tmp_path / "timeout.json").exists()
    monkeypatch.setattr(diagnostics, "READ_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(diagnostics, "MAX_BUNDLE_BYTES", 1)
    with pytest.raises(DiagnosticError, match="size limit"):
        export_diagnostics(config.state_path, config.log_path, tmp_path / "size.json")
    assert not (tmp_path / "size.json").exists()


def test_cli_exports_without_logging_migrations_locks_or_external_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    config = settings(tmp_path)
    with RelayState(config.state_path):
        pass
    monkeypatch.setattr(cli.Settings, "load", lambda _path: config)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Diagnostics must not initialize or mutate the relay")

    for name in ("_configure_logging", "RelayState", "ProcessLock", "KindroidClient", "_agent_factory"):
        monkeypatch.setattr(cli, name, forbidden)
    destination = tmp_path / "out.json"
    assert cli.main(["diagnostics", "--output", str(destination)]) == 0
    assert "Review this bundle before sharing" in capsys.readouterr().out
    assert cli.main(["diagnostics", "--output", str(destination)]) == 1
    assert "already exists" in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        cli.main(["diagnostics"])
    assert error.value.code == 2
