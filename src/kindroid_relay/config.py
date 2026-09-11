from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_INITIATIVE_LIMIT = 50
DASHBOARD_INITIATIVE_LIMIT_MAX = 1000


class ConfigurationError(ValueError):
    pass


def _integer(values: dict[str, str], name: str, default: int, minimum: int) -> int:
    raw = values.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _web_host(values: dict[str, str]) -> str:
    raw = values.get("RELAY_WEB_HOST", "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ConfigurationError(
            "RELAY_WEB_HOST must be a numeric loopback address"
        ) from exc
    if not address.is_loopback:
        raise ConfigurationError("RELAY_WEB_HOST must be a loopback address")
    if address.version != 4:
        raise ConfigurationError("only IPv4 loopback is currently supported")
    return str(address)


def _boolean(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _quiet_hours(values: dict[str, str], name: str = "RELAY_INITIATIVE_QUIET_HOURS", default: str = "") -> tuple[int, int] | None:
    raw = values.get(name, default).strip()
    if not raw:
        return None
    try:
        start_raw, end_raw = raw.split("-", 1)

        def minutes(value: str) -> int:
            hour, minute = (int(part) for part in value.split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
            return hour * 60 + minute

        return minutes(start_raw), minutes(end_raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must look like 00:00-08:00"
        ) from exc


def _schedule_hours(values: dict[str, str], name: str = "RELAY_INITIATIVE_SCHEDULE_HOURS", default: str = "") -> tuple[int, ...] | None:
    raw = values.get(name, default).strip()
    if not raw:
        return None
    try:
        hours = tuple(sorted({int(part.strip()) for part in raw.split(",")}))
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be comma-separated hours"
        ) from exc
    if not hours or any(hour < 0 or hour > 23 for hour in hours):
        raise ConfigurationError(
            f"{name} entries must be between 0 and 23"
        )
    return hours


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    group_id: str
    base_url: str
    poll_seconds: int
    min_history_interval_seconds: int
    max_consecutive_turns: int
    max_history_requests_24h: int
    max_reply_chars: int
    initiative_enabled: bool
    initiative_interval_seconds: int
    initiative_schedule_hours: tuple[int, ...] | None
    initiative_jitter_seconds: int
    initiative_max_checks_24h: int
    initiative_quiet_hours: tuple[int, int] | None
    state_path: Path
    log_path: Path
    codex_model: str | None
    codex_cwd: Path
    approved_sandbox: str
    codex_display_name: str
    kindroid_display_name: str
    codex_message_prefix: str
    persona_switch_enabled: bool
    codex_persona_id: str
    codex_persona_gender: str
    codex_persona_backstory: str
    restore_persona_id: str
    restore_persona_name: str
    restore_persona_gender: str
    restore_persona_backstory: str
    prompt_path: Path
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_open_browser: bool = False
    web_poll_seconds: int = 2
    worker_heartbeat_seconds: int = 5
    worker_stale_seconds: int = 20
    idle_poll_seconds: int = 600
    history_429_cooldown_seconds: int = 3600
    activity_retention_days: int = 30
    log_max_bytes: int = 5_242_880
    log_backup_count: int = 5
    initiative_idle_seconds: int = 300
    memory_enabled: bool = False
    memory_auto_capture: bool = False
    memory_max_messages_24h: int = 30
    memory_model_path: str = ""
    memory_model_revision: str = "operator-local-v1"
    memory_extractor: str = "semantic"
    memory_extraction_timeout_seconds: int = 45
    exploration_enabled: bool = False
    exploration_auto_interpret: bool = True
    exploration_max_sessions_24h: int = 3
    curiosity_enabled: bool = False
    curiosity_schedule_hours: tuple[int, ...] | None = (10, 16)
    curiosity_interval_seconds: int = 21600
    curiosity_jitter_seconds: int = 1800
    curiosity_quiet_hours: tuple[int, int] | None = (1320, 480)
    curiosity_idle_seconds: int = 1800
    curiosity_max_attempts_24h: int = 2
    curiosity_max_interpretations_24h: int = 4
    curiosity_window_seconds: int = 180
    rooms_file: str = ""
    room_key: str = "primary"
    room_label: str = ""
    room_mode: str = "assistant"
    participants: tuple[tuple[str, str], ...] = ()
    participant_scheduling: bool = True
    account_state_path: Path | None = None
    cooperative_turns: bool = False
    shared_hour: int = 18
    shared_minute: int = 0
    shared_check_every: int = 6
    shared_max_messages: int = 60
    shared_max_seconds: int = 3600
    shared_turn_seconds: int = 30

    @classmethod
    def load(cls, env_file: str | Path = "kindroid.env") -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        file_values = {
            key: value
            for key, value in dotenv_values(Path(env_file)).items()
            if value is not None
        }
        values = {**file_values, **os.environ}
        api_key = values.get("KINDROID_API_KEY", "").strip()
        group_id = values.get("KINDROID_GROUP_ID", "").strip()
        if not api_key:
            raise ConfigurationError("KINDROID_API_KEY is missing")
        if not group_id:
            raise ConfigurationError("KINDROID_GROUP_ID is missing")
        if not api_key.startswith("kn_"):
            raise ConfigurationError("KINDROID_API_KEY must start with kn_")

        cwd_raw = values.get("RELAY_CODEX_CWD", "").strip()
        approved_sandbox = values.get(
            "RELAY_APPROVED_SANDBOX", "workspace-write"
        ).strip()
        if approved_sandbox not in {"workspace-write", "full-access"}:
            raise ConfigurationError(
                "RELAY_APPROVED_SANDBOX must be workspace-write or full-access"
            )

        persona_switch_enabled = _boolean(
            values, "RELAY_PERSONA_SWITCH_ENABLED", False
        )
        codex_persona_id = values.get("RELAY_CODEX_PERSONA_ID", "").strip()
        restore_persona_id = values.get("RELAY_RESTORE_PERSONA_ID", "").strip()
        codex_message_prefix = values.get(
            "RELAY_CODEX_MESSAGE_PREFIX", "[CODEX]: "
        )
        if not codex_message_prefix.strip():
            raise ConfigurationError("RELAY_CODEX_MESSAGE_PREFIX cannot be empty")
        if persona_switch_enabled and not codex_persona_id:
            raise ConfigurationError(
                "RELAY_CODEX_PERSONA_ID is required when persona switching is enabled"
            )
        if persona_switch_enabled and not restore_persona_id:
            raise ConfigurationError(
                "RELAY_RESTORE_PERSONA_ID is required when persona switching is enabled"
            )

        web_port = _integer(values, "RELAY_WEB_PORT", 8765, 1)
        if web_port > 65535:
            raise ConfigurationError("RELAY_WEB_PORT must be at most 65535")
        worker_heartbeat_seconds = _integer(
            values, "RELAY_WORKER_HEARTBEAT_SECONDS", 5, 1
        )
        worker_stale_seconds = _integer(
            values, "RELAY_WORKER_STALE_SECONDS", 20, 2
        )
        if worker_stale_seconds <= worker_heartbeat_seconds:
            raise ConfigurationError(
                "RELAY_WORKER_STALE_SECONDS must exceed "
                "RELAY_WORKER_HEARTBEAT_SECONDS"
            )

        memory_extractor = values.get("RELAY_MEMORY_EXTRACTOR", "semantic").strip()
        if memory_extractor not in {"semantic", "local"}:
            raise ConfigurationError("RELAY_MEMORY_EXTRACTOR must be semantic or local")
        memory_timeout = _integer(values, "RELAY_MEMORY_EXTRACTION_TIMEOUT_SECONDS", 45, 5)
        if memory_timeout > 120:
            raise ConfigurationError("RELAY_MEMORY_EXTRACTION_TIMEOUT_SECONDS must be at most 120")
        curiosity_interval = _integer(values, 'RELAY_CURIOSITY_INTERVAL_SECONDS', 21600, 3600)
        curiosity_jitter = _integer(values, 'RELAY_CURIOSITY_JITTER_SECONDS', 1800, 0)
        curiosity_window = _integer(values, 'RELAY_CURIOSITY_WINDOW_SECONDS', 180, 30)
        if curiosity_jitter >= curiosity_interval or curiosity_jitter > 86400:
            raise ConfigurationError('Curiosity jitter must be less than the interval and at most 86400 seconds')
        if curiosity_window > 900:
            raise ConfigurationError('Curiosity window must be at most 900 seconds')

        return cls(
            api_key=api_key,
            group_id=group_id,
            base_url=values.get(
                "KINDROID_BASE_URL", "https://api.kindroid.ai/v1"
            ).rstrip("/"),
            poll_seconds=_integer(values, "RELAY_POLL_SECONDS", 300, 30),
            min_history_interval_seconds=_integer(
                values, "RELAY_MIN_HISTORY_INTERVAL_SECONDS", 15, 0
            ),
            max_consecutive_turns=_integer(
                values, "RELAY_MAX_CONSECUTIVE_TURNS", 8, 1
            ),
            max_history_requests_24h=_integer(
                values, "RELAY_MAX_HISTORY_REQUESTS_24H", 500, 1
            ),
            max_reply_chars=_integer(values, "RELAY_MAX_REPLY_CHARS", 3500, 200),
            initiative_enabled=_boolean(
                values, "RELAY_INITIATIVE_ENABLED", True
            ),
            initiative_interval_seconds=_integer(
                values, "RELAY_INITIATIVE_INTERVAL_SECONDS", 600, 60
            ),
            initiative_schedule_hours=_schedule_hours(values),
            initiative_jitter_seconds=_integer(
                values, "RELAY_INITIATIVE_JITTER_SECONDS", 0, 0
            ),
            initiative_max_checks_24h=_integer(
                values, "RELAY_INITIATIVE_MAX_CHECKS_24H", DEFAULT_INITIATIVE_LIMIT, 1
            ),
            initiative_quiet_hours=_quiet_hours(values),
            initiative_idle_seconds=_integer(values, 'RELAY_INITIATIVE_IDLE_SECONDS', 300, 60),
            state_path=Path(
                values.get("RELAY_STATE_PATH", project_root / "var" / "relay.sqlite3")
            ).resolve(),
            log_path=Path(
                values.get("RELAY_LOG_PATH", project_root / "var" / "relay.log")
            ).resolve(),
            codex_model=values.get("RELAY_CODEX_MODEL", "").strip() or None,
            codex_cwd=Path(cwd_raw).resolve() if cwd_raw else project_root,
            approved_sandbox=approved_sandbox,
            codex_display_name=values.get(
                "RELAY_CODEX_DISPLAY_NAME", "Codex Test"
            ).strip(),
            kindroid_display_name=values.get(
                "RELAY_KINDROID_DISPLAY_NAME", "Kindroid"
            ).strip(),
            codex_message_prefix=codex_message_prefix,
            persona_switch_enabled=persona_switch_enabled,
            codex_persona_id=codex_persona_id,
            codex_persona_gender=values.get(
                "RELAY_CODEX_PERSONA_GENDER", "Nonbinary"
            ).strip(),
            codex_persona_backstory=values.get(
                "RELAY_CODEX_PERSONA_BACKSTORY", ""
            ).strip(),
            restore_persona_id=restore_persona_id,
            restore_persona_name=values.get(
                "RELAY_RESTORE_PERSONA_NAME", "Marc"
            ).strip(),
            restore_persona_gender=values.get(
                "RELAY_RESTORE_PERSONA_GENDER", "Male"
            ).strip(),
            restore_persona_backstory=values.get(
                "RELAY_RESTORE_PERSONA_BACKSTORY", ""
            ).strip(),
            prompt_path=Path(
                values.get("RELAY_PROMPT_PATH", project_root / "relay_prompt.md")
            ).resolve(),
            web_host=_web_host(values),
            web_port=web_port,
            web_open_browser=_boolean(values, "RELAY_WEB_OPEN_BROWSER", False),
            web_poll_seconds=_integer(values, "RELAY_WEB_POLL_SECONDS", 2, 1),
            worker_heartbeat_seconds=worker_heartbeat_seconds,
            worker_stale_seconds=worker_stale_seconds,
            idle_poll_seconds=_integer(
                values, "RELAY_IDLE_POLL_SECONDS", 600, 30
            ),
            history_429_cooldown_seconds=_integer(
                values, "RELAY_HISTORY_429_COOLDOWN_SECONDS", 3600, 60
            ),
            activity_retention_days=_integer(
                values, "RELAY_ACTIVITY_RETENTION_DAYS", 30, 1
            ),
            log_max_bytes=_integer(
                values, "RELAY_LOG_MAX_BYTES", 5_242_880, 65_536
            ),
            log_backup_count=_integer(
                values, "RELAY_LOG_BACKUP_COUNT", 5, 1
            ),
            memory_enabled=_boolean(values, "RELAY_MEMORY_ENABLED", False),
            exploration_enabled=False,  # Invitations are retired globally (ADR-025).
            exploration_auto_interpret=_boolean(values, "RELAY_EXPLORATION_AUTO_INTERPRET", True),
            exploration_max_sessions_24h=_integer(values, "RELAY_EXPLORATION_MAX_SESSIONS_24H", 3, 1),
            curiosity_enabled=_boolean(values, 'RELAY_CURIOSITY_ENABLED', False),
            curiosity_schedule_hours=_schedule_hours(values, 'RELAY_CURIOSITY_SCHEDULE_HOURS', '10,16'),
            curiosity_interval_seconds=curiosity_interval,
            curiosity_jitter_seconds=curiosity_jitter,
            curiosity_quiet_hours=_quiet_hours(values, 'RELAY_CURIOSITY_QUIET_HOURS', '22:00-08:00'),
            curiosity_idle_seconds=_integer(values, 'RELAY_CURIOSITY_IDLE_SECONDS', 1800, 300),
            curiosity_max_attempts_24h=_integer(values, 'RELAY_CURIOSITY_MAX_ATTEMPTS_24H', 2, 1),
            curiosity_max_interpretations_24h=_integer(values, 'RELAY_CURIOSITY_MAX_INTERPRETATIONS_24H', 4, 0),
            curiosity_window_seconds=curiosity_window,
            memory_auto_capture=_boolean(values, "RELAY_MEMORY_AUTO_CAPTURE", False),
            memory_max_messages_24h=_integer(values, "RELAY_MEMORY_MAX_MESSAGES_24H", 30, 1),
            memory_model_path=values.get("RELAY_MEMORY_MODEL_PATH", "").strip(),
            memory_model_revision=values.get("RELAY_MEMORY_MODEL_REVISION", "operator-local-v1").strip(),
            memory_extractor=memory_extractor,
            memory_extraction_timeout_seconds=memory_timeout,
            rooms_file=values.get("RELAY_ROOMS_FILE", "").strip(),
            participant_scheduling=True,  # Legacy opt-out is no longer a runtime mode.
        )

    def ensure_runtime_directories(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
