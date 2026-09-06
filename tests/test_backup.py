import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from kindroid_relay import backup, cli
from kindroid_relay.backup import BackupError, backup_database, check_integrity
from kindroid_relay.command_queue import CommandType
from kindroid_relay.migrations import LATEST_SCHEMA_VERSION
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState
from test_relay import settings


def test_online_backup_preserves_live_wal_state_and_source(tmp_path: Path):
    source = tmp_path / "live.sqlite3"
    with RelayState(source) as state:
        state.add_message("g1", ChatMessage("h1", "ai1", "ai", "Hannah", 1, "Private fixture"))
        state.pause("keep paused")
        command, _ = state.commands.enqueue(CommandType.START_CONVERSATION, {}, idempotency_key="one")
        state.commands.claim_next()
        outbox = state.create_outbox("[CODEX]: Private output", source_message_id="h1")
        state.set_outbox_status(outbox, "uncertain")
        before = tuple(state.connection.iterdump())
        assert Path(str(source) + "-wal").stat().st_size > 0
        destination = backup_database(source, tmp_path / "backup")
        assert tuple(state.connection.iterdump()) == before
        # The relay can continue writing while the independent backup is inspected.
        state.set_meta("after_backup", "not in snapshot")
        with closing(sqlite3.connect(destination / "state.sqlite3")) as restored:
            assert tuple(restored.iterdump()) == before
            assert restored.execute("SELECT status FROM commands WHERE id=?", (command.id,)).fetchone()[0] == "running"
    assert set(p.name for p in destination.iterdir()) == {"state.sqlite3", "manifest.json"}
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["schema_version"] == LATEST_SCHEMA_VERSION
    assert manifest["integrity"] == manifest["foreign_keys"] == "ok"
    assert datetime.fromisoformat(manifest["created_at"]).utcoffset().total_seconds() == 0
    assert manifest["sha256"] == hashlib.sha256((destination / "state.sqlite3").read_bytes()).hexdigest()
    assert check_integrity(destination / "state.sqlite3")["integrity"] == "ok"
    assert "Private fixture" not in (destination / "manifest.json").read_text()


@pytest.mark.parametrize("version", [0, 1, 999])
def test_maintenance_preserves_old_or_future_schema_without_migration(tmp_path, version):
    source = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute(f"PRAGMA user_version={version}")
    before = source.read_bytes()
    assert check_integrity(source)["schema_version"] == version
    destination = backup_database(source, tmp_path / "backup")
    assert source.read_bytes() == before
    assert check_integrity(destination / "state.sqlite3")["schema_version"] == version


@pytest.mark.parametrize("kind", ["missing", "corrupt"])
def test_invalid_source_fails_without_creation_or_disclosing_data(tmp_path, kind):
    source = tmp_path / "source.sqlite3"
    if kind == "corrupt":
        source.write_bytes(b"private-corrupt-fixture")
    before = source.read_bytes() if source.exists() else None
    for action in (lambda: backup_database(source, tmp_path / "backup"), lambda: check_integrity(source)):
        with pytest.raises(BackupError) as error:
            action()
        assert "private" not in str(error.value)
    assert not (tmp_path / "backup").exists()
    assert (source.read_bytes() if source.exists() else None) == before


def test_existing_and_runtime_destinations_are_never_overwritten(tmp_path):
    source = tmp_path / "state.sqlite3"
    with RelayState(source):
        pass
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep")
    for target in (existing, sentinel, source, source.with_suffix(".lock"), *[Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")]):
        with pytest.raises(BackupError):
            backup_database(source, target)
    assert sentinel.read_text() == "keep"
    with pytest.raises(BackupError):
        backup_database(source, tmp_path / "absent" / "backup")
    assert not (tmp_path / "absent").exists()


def test_foreign_key_violation_fails_without_publishing_completion(tmp_path):
    source = tmp_path / "bad.sqlite3"
    with closing(sqlite3.connect(source)) as connection:
        connection.executescript("CREATE TABLE parent(id PRIMARY KEY); CREATE TABLE child(id REFERENCES parent(id)); INSERT INTO child VALUES ('private-fixture');")
    for action in (lambda: check_integrity(source), lambda: backup_database(source, tmp_path / "backup")):
        with pytest.raises(BackupError, match="foreign-key") as error:
            action()
        assert "private-fixture" not in str(error.value)
    assert not (tmp_path / "backup" / "manifest.json").exists()


def test_deadlines_abort_integrity_and_busy_online_backup(tmp_path, monkeypatch):
    source = tmp_path / "busy.sqlite3"
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("CREATE TABLE example (id INTEGER)")
        writer.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(backup, "MAINTENANCE_TIMEOUT_SECONDS", 0.02)
        with pytest.raises(BackupError):
            check_integrity(source)
        with pytest.raises(BackupError):
            backup_database(source, tmp_path / "backup")
    assert not (tmp_path / "backup" / "manifest.json").exists()


def test_backup_progress_deadline_leaves_no_completed_manifest(tmp_path, monkeypatch):
    source = tmp_path / "state.sqlite3"
    with RelayState(source):
        pass
    original = backup._check_deadline
    def expire_during_copy(deadline):
        if (tmp_path / "backup" / "state.sqlite3").exists():
            raise BackupError("Database maintenance deadline exceeded")
        original(deadline)
    monkeypatch.setattr(backup, "_check_deadline", expire_during_copy)
    with pytest.raises(BackupError, match="deadline"):
        backup_database(source, tmp_path / "backup")
    assert not (tmp_path / "backup" / "manifest.json").exists()


def test_write_failure_never_publishes_manifest(tmp_path, monkeypatch):
    source = tmp_path / "state.sqlite3"
    with RelayState(source):
        pass
    monkeypatch.setattr(backup.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("private fixture")))
    with pytest.raises(BackupError) as error:
        backup_database(source, tmp_path / "backup")
    assert "private fixture" not in str(error.value)
    assert not (tmp_path / "backup" / "manifest.json").exists()


def test_cli_maintenance_never_initializes_relay(tmp_path, monkeypatch, capsys):
    config = settings(tmp_path)
    with RelayState(config.state_path):
        pass
    monkeypatch.setattr(cli.Settings, "load", lambda _path: config)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Maintenance must not initialize the relay")
    for name in ("_configure_logging", "RelayState", "ProcessLock", "KindroidClient", "_agent_factory"):
        monkeypatch.setattr(cli, name, forbidden)
    destination = tmp_path / "backup"
    assert cli.main(["backup", "--output", str(destination)]) == 0
    assert "Private backup" in capsys.readouterr().out
    assert cli.main(["backup", "--output", str(destination)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["integrity"]) == 0
    monkeypatch.setattr(cli.Settings, "load", forbidden)
    assert cli.main(["integrity", "--database", str(destination / "state.sqlite3")]) == 0
    assert cli.main(["integrity", "--database", str(tmp_path / "absent")]) == 1
