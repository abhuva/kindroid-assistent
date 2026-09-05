from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from .activity import ActivitySeverity, WorkerPhase
from .config import Settings
from .migrations import migrate_database
from .redaction import redact_text
from .state import ProcessLock, RelayStateFactory
from .web import create_web_app
from .worker import RelayWorker


logger = logging.getLogger(__name__)


def ensure_port_available(host: str, port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(
            f"dashboard address {host}:{port} is unavailable"
        ) from exc
    finally:
        probe.close()


def _open_when_ready(server: uvicorn.Server, url: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not server.should_exit:
        if server.started:
            webbrowser.open(url)
            return
        time.sleep(0.05)


def _wait_for_worker_started(
    state_factory: RelayStateFactory,
    worker_thread: threading.Thread,
    timeout: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with state_factory.connect() as state:
            if state.worker_status.get() is not None:
                return
        if not worker_thread.is_alive():
            raise RuntimeError("relay worker stopped during startup")
        time.sleep(0.05)
    raise RuntimeError("relay worker did not become ready before timeout")


def serve_dashboard(settings: Settings) -> int:
    settings.ensure_runtime_directories()
    ensure_port_available(settings.web_host, settings.web_port)
    lock_path = settings.state_path.with_suffix(".lock")
    url = f"http://{settings.web_host}:{settings.web_port}"

    with ProcessLock(lock_path):
        migrate_database(settings.state_path)
        state_factory = RelayStateFactory(settings.state_path)
        wake_event = threading.Event()
        stop_event = threading.Event()
        worker = RelayWorker(
            settings,
            wake_event=wake_event,
            stop_event=stop_event,
        )

        def worker_entry() -> None:
            try:
                worker.run()
            except Exception as exc:
                logger.exception("Relay worker terminated unexpectedly")
                try:
                    with state_factory.connect() as state:
                        state.worker_status.update(
                            WorkerPhase.DEGRADED,
                            note=redact_text(exc, maximum=500),
                        )
                        state.activity.add(
                            "relay_cycle_failed",
                            ActivitySeverity.ERROR,
                            "Relay worker terminated unexpectedly",
                            details={"error_type": exc.__class__.__name__},
                        )
                except Exception:
                    logger.exception("Could not record worker termination")

        worker_thread = threading.Thread(
            target=worker_entry,
            name="kindroid-relay-worker",
            daemon=False,
        )
        app = create_web_app(settings, state_factory, wake_event)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.web_host,
                port=settings.web_port,
                log_config=None,
                access_log=False,
            )
        )

        worker_thread.start()
        _wait_for_worker_started(state_factory, worker_thread)
        logger.info("Dashboard available at %s", url)
        print(f"Dashboard: {url}")
        browser_thread = None
        if settings.web_open_browser:
            browser_thread = threading.Thread(
                target=_open_when_ready,
                args=(server, url),
                name="kindroid-dashboard-browser",
                daemon=True,
            )
            browser_thread.start()
        try:
            server.run()
        finally:
            worker.stop()
            worker_thread.join(timeout=30)
            if worker_thread.is_alive():
                logger.error("Relay worker did not stop within 30 seconds")
            if browser_thread is not None:
                browser_thread.join(timeout=1)
    return 0
