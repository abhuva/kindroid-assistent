from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path


logger = logging.getLogger(__name__)

Migration = tuple[int, Callable[[sqlite3.Connection], None]]


class SchemaVersionError(RuntimeError):
    pass


BASE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        remote_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        display_name TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        body TEXT NOT NULL,
        processed INTEGER NOT NULL DEFAULT 0,
        received_at INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_pending
        ON messages(processed, timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        body_hash TEXT NOT NULL,
        source_message_id TEXT,
        request_kindroid_reply INTEGER,
        status TEXT NOT NULL,
        error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outbox_hash ON outbox(body_hash)",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        source_message_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        instruction TEXT NOT NULL,
        risk TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS history_requests (
        requested_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS initiative_checks (
        checked_at INTEGER NOT NULL,
        outcome TEXT NOT NULL
    )
    """,
)


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _migration_1(connection: sqlite3.Connection) -> None:
    for statement in BASE_SCHEMA_STATEMENTS:
        connection.execute(statement)

    outbox_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(outbox)").fetchall()
    }
    if "source_message_id" not in outbox_columns:
        connection.execute("ALTER TABLE outbox ADD COLUMN source_message_id TEXT")
    if "request_kindroid_reply" not in outbox_columns:
        connection.execute(
            "ALTER TABLE outbox ADD COLUMN request_kindroid_reply INTEGER"
        )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_source_message "
        "ON outbox(source_message_id) WHERE source_message_id IS NOT NULL"
    )
    connection.execute(
        "UPDATE messages SET sender_type = sender WHERE sender_type = ''"
    )


RUNTIME_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE commands (
        id TEXT PRIMARY KEY,
        command_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (
            status IN (
                'queued', 'running', 'completed', 'failed',
                'interrupted', 'cancelled'
            )
        ),
        requested_at INTEGER NOT NULL,
        started_at INTEGER,
        completed_at INTEGER,
        result_json TEXT,
        error TEXT
    )
    """,
    "CREATE INDEX idx_commands_queue ON commands(status, requested_at, id)",
    "CREATE INDEX idx_commands_recent ON commands(requested_at DESC, id DESC)",
    """
    CREATE TABLE activity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (
            severity IN ('debug', 'info', 'warning', 'error')
        ),
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        correlation_id TEXT,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX idx_activity_events_created
        ON activity_events(created_at, id)
    """,
    """
    CREATE TABLE worker_status (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        phase TEXT NOT NULL,
        phase_started_at INTEGER NOT NULL,
        heartbeat_at INTEGER NOT NULL,
        active_command_id TEXT,
        note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(active_command_id) REFERENCES commands(id)
    )
    """,
)


def _migration_2(connection: sqlite3.Connection) -> None:
    for statement in RUNTIME_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migration_3(connection: sqlite3.Connection) -> None:
    # Keep legacy primary keys stable: outbox, approvals, and activity refer to
    # them. New locally captured messages acquire a separate remote alias later.
    connection.execute("ALTER TABLE messages ADD COLUMN kindroid_id TEXT")
    connection.execute("UPDATE messages SET kindroid_id = remote_id")
    connection.execute("CREATE UNIQUE INDEX idx_messages_kindroid_id ON messages(kindroid_id)")
    connection.execute("ALTER TABLE messages ADD COLUMN sync_after INTEGER")
    connection.execute("""
        CREATE TABLE generation_turns (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            outbox_id TEXT NOT NULL UNIQUE REFERENCES outbox(id),
            anchor_message_id TEXT NOT NULL REFERENCES messages(remote_id),
            status TEXT NOT NULL CHECK(status IN ('generating', 'received', 'uncertain', 'reconciled')),
            after_timestamp INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)


MIGRATIONS: tuple[Migration, ...] = (
    (1, _migration_1),
    (2, _migration_2),
    (3, _migration_3),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def current_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def require_current_schema(connection: sqlite3.Connection) -> None:
    current = current_schema_version(connection)
    if current != LATEST_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} does not match required version "
            f"{LATEST_SCHEMA_VERSION}"
        )


def _validate_registry(migrations: Sequence[Migration]) -> None:
    versions = [version for version, _ in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError("migration versions must be contiguous and start at 1")


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> list[int]:
    _validate_registry(migrations)
    current = current_schema_version(connection)
    latest = migrations[-1][0] if migrations else 0
    if current > latest:
        raise SchemaVersionError(
            f"database schema version {current} is newer than supported version {latest}"
        )

    applied: list[int] = []
    for version, migration in migrations:
        if version <= current:
            continue
        logger.info("Applying database migration %s", version)
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration(connection)
            connection.execute(f"PRAGMA user_version={version}")
            connection.commit()
        except Exception:
            connection.rollback()
            logger.exception("Database migration %s failed", version)
            raise
        logger.info("Database migration %s completed", version)
        applied.append(version)
        current = version
    return applied


def migrate_database(
    path: Path,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> list[int]:
    connection = open_database(path)
    try:
        return apply_migrations(connection, migrations)
    finally:
        connection.close()
