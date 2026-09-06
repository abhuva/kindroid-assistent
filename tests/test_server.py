import socket
import threading
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from kindroid_relay.cli import _parser
from kindroid_relay.server import ensure_port_available
from kindroid_relay.server import serve_dashboard

from test_relay import settings


def test_cli_exposes_serve_command() -> None:
    assert _parser().parse_args(["serve"]).command == "serve"


def test_port_probe_accepts_an_available_loopback_port() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    ensure_port_available("127.0.0.1", port)


def test_port_probe_rejects_an_occupied_port() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            ensure_port_available("127.0.0.1", listener.getsockname()[1])
    finally:
        listener.close()


def test_combined_host_starts_and_stops_worker(monkeypatch, tmp_path: Path) -> None:
    config = replace(settings(tmp_path), web_port=0)
    lifecycle: list[str] = []

    class FakeWorker:
        def __init__(self, config, **kwargs):
            self.config = config
            self.stop_event = threading.Event()

        def run(self):
            from kindroid_relay.activity import WorkerPhase
            from kindroid_relay.state import RelayState

            lifecycle.append("worker-start")
            with RelayState(self.config.state_path, migrate=False) as state:
                state.worker_status.update(WorkerPhase.IDLE)
            self.stop_event.wait(2)
            lifecycle.append("worker-stop")

        def stop(self):
            lifecycle.append("stop-request")
            self.stop_event.set()

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.started = True
            self.should_exit = False

        def run(self):
            lifecycle.append("server-run")

    monkeypatch.setattr("kindroid_relay.server.RelayWorker", FakeWorker)
    monkeypatch.setattr("kindroid_relay.server.create_web_app", lambda *args: object())
    monkeypatch.setattr("kindroid_relay.server.uvicorn.Server", FakeServer)
    monkeypatch.setattr("kindroid_relay.server.ensure_port_available", lambda *args: None)

    assert serve_dashboard(config) == 0
    assert lifecycle == [
        "worker-start",
        "server-run",
        "stop-request",
        "worker-stop",
    ]
    assert not config.state_path.with_suffix(".lock").exists()


def test_combined_host_stops_worker_when_server_raises(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = replace(settings(tmp_path), web_port=0)
    stopped = threading.Event()

    class FakeWorker:
        def __init__(self, config, **kwargs):
            self.config = config
            self.stop_event = threading.Event()

        def run(self):
            from kindroid_relay.activity import WorkerPhase
            from kindroid_relay.state import RelayState

            with RelayState(self.config.state_path, migrate=False) as state:
                state.worker_status.update(WorkerPhase.IDLE)
            self.stop_event.wait(2)
            stopped.set()

        def stop(self):
            self.stop_event.set()

    class FailingServer:
        started = True
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            raise RuntimeError("web failed")

    monkeypatch.setattr("kindroid_relay.server.RelayWorker", FakeWorker)
    monkeypatch.setattr("kindroid_relay.server.create_web_app", lambda *args: object())
    monkeypatch.setattr("kindroid_relay.server.uvicorn.Server", FailingServer)
    monkeypatch.setattr("kindroid_relay.server.ensure_port_available", lambda *args: None)

    with pytest.raises(RuntimeError, match="web failed"):
        serve_dashboard(config)
    assert stopped.is_set()


def test_migration_failure_prevents_worker_start(monkeypatch, tmp_path: Path) -> None:
    config = replace(settings(tmp_path), web_port=0)
    started = False

    class WorkerThatMustNotStart:
        def __init__(self, *args, **kwargs):
            nonlocal started
            started = True

    monkeypatch.setattr(
        "kindroid_relay.server.migrate_database",
        lambda *args: (_ for _ in ()).throw(RuntimeError("migration failed")),
    )
    monkeypatch.setattr("kindroid_relay.server.RelayWorker", WorkerThatMustNotStart)
    monkeypatch.setattr("kindroid_relay.server.ensure_port_available", lambda *args: None)

    with pytest.raises(RuntimeError, match="migration failed"):
        serve_dashboard(config)
    assert not started


def test_worker_startup_failure_is_sanitized_and_prevents_http_start(
    monkeypatch,
    tmp_path: Path,
    caplog,
    capsys,
) -> None:
    config = replace(settings(tmp_path), web_port=0)
    server_ran = False
    stop_requested = threading.Event()

    class FailingWorker:
        def __init__(self, config, **kwargs):
            self.config = config

        def run(self):
            raise RuntimeError("worker rejected kn_super-secret-value")

        def stop(self):
            stop_requested.set()

    class ServerThatMustNotRun:
        started = False
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            nonlocal server_ran
            server_ran = True

    monkeypatch.setattr("kindroid_relay.server.RelayWorker", FailingWorker)
    monkeypatch.setattr("kindroid_relay.server.create_web_app", lambda *args: object())
    monkeypatch.setattr("kindroid_relay.server.uvicorn.Server", ServerThatMustNotRun)
    monkeypatch.setattr("kindroid_relay.server.ensure_port_available", lambda *args: None)

    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError, match="worker failed during startup"):
            serve_dashboard(config)

    captured = capsys.readouterr()
    visible_output = caplog.text + captured.out + captured.err
    assert "kn_super-secret-value" not in visible_output
    assert "[REDACTED_KINDROID_KEY]" in caplog.text
    assert stop_requested.is_set()
    assert not server_ran
    assert not config.state_path.with_suffix(".lock").exists()


def test_keyboard_interrupt_stops_worker_and_releases_process_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = replace(settings(tmp_path), web_port=0)
    worker_stopped = threading.Event()

    class FakeWorker:
        def __init__(self, config, **kwargs):
            self.config = config
            self.stop_event = threading.Event()

        def run(self):
            from kindroid_relay.activity import WorkerPhase
            from kindroid_relay.state import RelayState

            with RelayState(self.config.state_path, migrate=False) as state:
                state.worker_status.update(WorkerPhase.IDLE)
            self.stop_event.wait(2)
            worker_stopped.set()

        def stop(self):
            self.stop_event.set()

    class InterruptingServer:
        started = True
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            raise KeyboardInterrupt

    monkeypatch.setattr("kindroid_relay.server.RelayWorker", FakeWorker)
    monkeypatch.setattr("kindroid_relay.server.create_web_app", lambda *args: object())
    monkeypatch.setattr("kindroid_relay.server.uvicorn.Server", InterruptingServer)
    monkeypatch.setattr("kindroid_relay.server.ensure_port_available", lambda *args: None)

    with pytest.raises(KeyboardInterrupt):
        serve_dashboard(config)

    assert worker_stopped.is_set()
    assert not config.state_path.with_suffix(".lock").exists()
