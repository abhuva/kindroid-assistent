import sqlite3
from pathlib import Path

import pytest

from kindroid_relay.migrations import (
    LATEST_SCHEMA_VERSION,
    SchemaVersionError,
    apply_migrations,
    current_schema_version,
    migrate_database,
    open_database,
)
from kindroid_relay.state import RelayState


def test_empty_database_is_migrated_and_reopen_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    assert migrate_database(path) == list(range(1, LATEST_SCHEMA_VERSION + 1))
    assert migrate_database(path) == []

    connection = open_database(path)
    try:
        assert current_schema_version(connection) == LATEST_SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "metadata",
            "messages",
            "outbox",
            "approvals",
            "commands",
            "activity_events",
            "worker_status",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_current_unversioned_database_is_upgraded_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO metadata VALUES ('kept', 'yes')")
    connection.execute(
        """
        CREATE TABLE outbox (
            id TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            body_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    assert migrate_database(path) == list(range(1, LATEST_SCHEMA_VERSION + 1))

    connection = open_database(path)
    try:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key = 'kept'"
        ).fetchone()[0] == "yes"
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(outbox)").fetchall()
        }
        assert {"source_message_id", "request_kindroid_reply"} <= columns
    finally:
        connection.close()


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = open_database(path)

    def failing_migration(database: sqlite3.Connection) -> None:
        database.execute("CREATE TABLE should_not_exist (id INTEGER)")
        raise RuntimeError("deliberate migration failure")

    try:
        with pytest.raises(RuntimeError, match="deliberate"):
            apply_migrations(connection, ((1, failing_migration),))
        assert current_schema_version(connection) == 0
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'should_not_exist'"
        ).fetchone() is None
    finally:
        connection.close()


def test_newer_database_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = open_database(path)
    connection.execute(f"PRAGMA user_version={LATEST_SCHEMA_VERSION + 1}")
    connection.close()

    with pytest.raises(SchemaVersionError, match="newer than supported"):
        migrate_database(path)


def test_non_migrating_connection_requires_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = open_database(path)
    connection.close()

    with pytest.raises(SchemaVersionError, match="does not match required"):
        RelayState(path, migrate=False)
