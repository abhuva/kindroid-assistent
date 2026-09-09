from pathlib import Path

import pytest

from kindroid_relay.config import ConfigurationError, Settings


def write_env(path: Path, extra: str = "") -> None:
    path.write_text(
        'KINDROID_API_KEY="kn_test-value"\n'
        'KINDROID_GROUP_ID="group"\n'
        + extra,
        encoding="utf-8",
    )


def test_dashboard_settings_have_safe_defaults(tmp_path: Path) -> None:
    env = tmp_path / "kindroid.env"
    write_env(env)

    settings = Settings.load(env)

    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 8765
    assert not settings.web_open_browser
    assert settings.web_poll_seconds == 2
    assert settings.poll_seconds == 300
    assert settings.idle_poll_seconds == 600
    assert settings.min_history_interval_seconds == 15
    assert settings.history_429_cooldown_seconds == 3600
    assert settings.activity_retention_days == 30
    assert settings.initiative_max_checks_24h == 50
    assert settings.initiative_idle_seconds == 300
    assert settings.worker_stale_seconds > settings.worker_heartbeat_seconds
    assert settings.log_max_bytes == 5_242_880
    assert settings.log_backup_count == 5
    assert settings.memory_enabled is False
    assert settings.exploration_enabled is False
    assert settings.exploration_max_sessions_24h == 3
    assert settings.memory_auto_capture is False
    assert settings.memory_max_messages_24h == 30
    assert settings.memory_model_path == ""
    assert settings.memory_extractor == "semantic"
    assert settings.memory_extraction_timeout_seconds == 45


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('RELAY_INITIATIVE_IDLE_SECONDS="59"\n', "at least 60"),
        ('RELAY_MEMORY_EXTRACTOR="unknown"\n', "semantic or local"),
        ('RELAY_MEMORY_EXTRACTION_TIMEOUT_SECONDS="121"\n', "at most 120"),
        ('RELAY_WEB_HOST="0.0.0.0"\n', "loopback"),
        ('RELAY_WEB_HOST="localhost"\n', "numeric loopback"),
        ('RELAY_WEB_HOST="::1"\n', "IPv4"),
        ('RELAY_WEB_PORT="65536"\n', "at most 65535"),
        (
            'RELAY_WORKER_HEARTBEAT_SECONDS="10"\n'
            'RELAY_WORKER_STALE_SECONDS="10"\n',
            "must exceed",
        ),
    ],
)
def test_unsafe_or_inconsistent_dashboard_settings_are_rejected(
    tmp_path: Path,
    line: str,
    message: str,
) -> None:
    env = tmp_path / "kindroid.env"
    write_env(env, line)

    with pytest.raises(ConfigurationError, match=message):
        Settings.load(env)
