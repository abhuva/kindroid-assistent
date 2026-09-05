import os
import sqlite3
from pathlib import Path

import pytest

import kindroid_relay.state as state_module
from kindroid_relay.models import ActionProposal, ChatMessage
from kindroid_relay.state import ProcessLock, RelayState, process_lock_is_active


def test_state_deduplicates_messages_and_tracks_approval(tmp_path: Path) -> None:
    message = ChatMessage("m1", "ai1", "ai", "Hannah", 1, "Hello")
    with RelayState(tmp_path / "state.sqlite3") as state:
        assert state.add_message("g1", message)
        assert not state.add_message("g1", message)
        assert [item.id for item in state.pending_ai_messages()] == ["m1"]
        assert [item.id for item in state.recent_messages()] == ["m1"]
        state.mark_message_processed("m1")
        assert state.pending_ai_messages() == []

        approval_id = state.add_approval(
            "m1", ActionProposal("Write a file", "Create note.txt", "low")
        )
        assert state.get_approval(approval_id)["status"] == "pending"
        state.update_approval(approval_id, "rejected")
        assert state.get_approval(approval_id)["status"] == "rejected"


def test_uncertain_outbox_is_never_implicitly_retried(tmp_path: Path) -> None:
    with RelayState(tmp_path / "state.sqlite3") as state:
        item = state.create_outbox("same body")
        state.set_outbox_status(item, "uncertain", "timeout")
        assert state.is_known_outbound("same body")
        assert state.status()["uncertain_outbox"] == 1


def test_existing_outbox_schema_is_migrated_in_place(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
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

    with RelayState(path) as state:
        item = state.create_outbox(
            "reply",
            source_message_id="message-1",
            request_kindroid_reply=False,
        )
        row = state.outbox_for_source("message-1")
        assert row is not None
        assert row["id"] == item
        assert row["request_kindroid_reply"] == 0


def test_process_lock_removes_stale_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "relay.lock"
    lock_path.write_text("99999999", encoding="ascii")
    with ProcessLock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_process_lock_reports_current_process_as_active(tmp_path: Path) -> None:
    lock_path = tmp_path / "relay.lock"
    with ProcessLock(lock_path):
        assert process_lock_is_active(lock_path)
    assert not process_lock_is_active(lock_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows process-probe regression")
def test_windows_process_probe_never_uses_os_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unexpected_signal(*_args: object) -> None:
        raise AssertionError("Windows PID checks must not call os.kill")

    monkeypatch.setattr(state_module.os, "kill", unexpected_signal)
    lock_path = tmp_path / "relay.lock"
    with ProcessLock(lock_path):
        assert process_lock_is_active(lock_path)
