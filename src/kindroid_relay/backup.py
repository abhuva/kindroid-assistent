"""Local, non-migrating SQLite maintenance; never starts relay work."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .migrations import current_schema_version

MAINTENANCE_TIMEOUT_SECONDS = 15
BACKUP_NOTICE = "Private backup: contains conversation text, approvals and operational state. Do not share as diagnostics."


class BackupError(RuntimeError):
    """Allow-listed operator error, with no raw SQLite contents or exceptions."""


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise BackupError("Database maintenance deadline exceeded; no completed backup was produced.")


def _open_readonly(path: Path, deadline: float) -> sqlite3.Connection:
    _check_deadline(deadline)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    return connection


def _integrity(connection: sqlite3.Connection, deadline: float) -> dict:
    _check_deadline(deadline)
    # Fetch at most two rows: anything except the sole 'ok' result is a failure.
    if connection.execute("PRAGMA integrity_check").fetchmany(2) != [("ok",)]:
        raise BackupError("Database integrity check failed; preserve the database and investigate before resuming.")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise BackupError("Database foreign-key check failed; preserve the database and investigate before resuming.")
    _check_deadline(deadline)
    return {"integrity": "ok", "foreign_keys": "ok", "schema_version": current_schema_version(connection)}


def check_integrity(path: Path) -> dict:
    """Check one consistent read snapshot, including older schemas, without migration."""
    deadline = time.monotonic() + MAINTENANCE_TIMEOUT_SECONDS
    try:
        with closing(_open_readonly(path, deadline)) as connection:
            connection.execute("BEGIN")
            return _integrity(connection, deadline)
    except (sqlite3.Error, OSError):
        raise BackupError("Cannot check database: missing, unreadable, busy, corrupt, or deadline exceeded.") from None


def backup_database(source: Path, destination: Path) -> Path:
    """Create a NEW directory with state.sqlite3 and a completion manifest.

    A missing manifest means incomplete. Failed output is retained for inspection;
    we never clean up paths that another process could have replaced meanwhile.
    """
    deadline = time.monotonic() + MAINTENANCE_TIMEOUT_SECONDS
    destination = destination.resolve()
    source = source.resolve()
    protected = {source, source.with_suffix(".lock")} | {
        Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")
    }
    if destination in protected:
        raise BackupError("Backup destination must not be a runtime database, sidecar, or lock path.")
    try:
        with closing(_open_readonly(source, deadline)) as connection:
            # Force an initial read before creating output; SQLite may defer errors.
            connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
            destination.mkdir(mode=0o700)  # Exclusive; parent must already exist.
            database = destination / "state.sqlite3"
            with database.open("xb"):
                pass
            with closing(sqlite3.connect(database, timeout=1)) as target:
                target.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                connection.backup(
                    target, pages=128, sleep=0.05,
                    progress=lambda _status, _remaining, _total: _check_deadline(deadline),
                )
                # Produce a standalone file, not a backup requiring WAL sidecars.
                target.execute("PRAGMA journal_mode=DELETE")
                result = _integrity(target, deadline)
            digest = hashlib.sha256()
            with database.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    _check_deadline(deadline)
                    digest.update(block)
            manifest = {
                "format_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "database": "state.sqlite3",
                "sha256": digest.hexdigest(),
                **result,
                "notice": BACKUP_NOTICE,
            }
            # Publish the completion marker only after the database has been checked.
            pending = destination / "manifest.pending"
            with pending.open("x", encoding="utf-8") as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _check_deadline(deadline)
            pending.rename(destination / "manifest.json")
    except FileExistsError:
        raise BackupError("Backup destination already exists; choose a new directory.") from None
    except (sqlite3.Error, OSError):
        raise BackupError("Backup failed: database or destination missing, unreadable, busy, corrupt, full, or deadline exceeded. A directory without manifest.json is incomplete.") from None
    return destination
