import logging
from pathlib import Path
from types import SimpleNamespace

from kindroid_relay.cli import _file_log_handler


def test_file_log_handler_rotates_at_configured_size(tmp_path: Path) -> None:
    config = SimpleNamespace(
        log_path=tmp_path / "relay.log",
        log_max_bytes=128,
        log_backup_count=2,
    )
    handler = _file_log_handler(config)  # type: ignore[arg-type]
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        for _ in range(20):
            handler.emit(
                logging.LogRecord(
                    "test",
                    logging.INFO,
                    __file__,
                    1,
                    "a sufficiently long log record",
                    (),
                    None,
                )
            )
    finally:
        handler.close()

    assert config.log_path.exists()
    assert config.log_path.with_name(config.log_path.name + ".1").exists()
