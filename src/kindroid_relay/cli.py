from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .application import (
    ApprovalStateError,
    RelayApplication,
    pause_relay,
    reject_approval,
    resume_relay,
)
from .codex_agent import CodexAgent
from .backup import BACKUP_NOTICE, BackupError, backup_database, check_integrity
from .config import ConfigurationError, Settings
from .diagnostics import DiagnosticError, REVIEW_NOTICE, export_diagnostics
from .kindroid_api import KindroidClient
from .server import serve_dashboard
from .state import (
    ProcessLock,
    RelayAlreadyRunning,
    RelayState,
    process_lock_is_active,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kindroid-relay",
        description="Approval-gated Kindroid group chat relay for Codex",
    )
    parser.add_argument("--env-file", default="kindroid.env")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap", help="Record existing history without replaying it")
    bootstrap.add_argument("--respond-to-latest", action="store_true")
    bootstrap.add_argument("--force", action="store_true")

    sub.add_parser("once", help="Run one relay cycle")
    sub.add_parser("trigger", help="Run the scheduled initiative check now")
    sub.add_parser(
        "start-conversation",
        help="Start a complete conversation now, independent of initiative scheduling",
    )
    sub.add_parser("run", help="Run continuously")
    sub.add_parser("serve", help="Run the relay with its local web dashboard")
    sub.add_parser("status", help="Show local relay state")
    diagnostics = sub.add_parser("diagnostics", help="Export bounded, privacy-filtered local diagnostics")
    diagnostics.add_argument("--output", required=True, type=Path, help="New JSON destination; never overwritten")
    backup = sub.add_parser("backup", help="Create a private online SQLite backup")
    backup.add_argument("--output", required=True, type=Path, help="New backup directory; parent must exist")
    integrity = sub.add_parser("integrity", help="Check SQLite integrity without migration or relay work")
    integrity.add_argument("--database", type=Path, help="Check a separate database without loading relay configuration")

    pause = sub.add_parser("pause", help="Pause automatic replies")
    pause.add_argument("reason", nargs="?", default="paused locally")
    sub.add_parser("resume", help="Resume with a fresh automatic turn budget")

    approvals = sub.add_parser("approvals", help="List action proposals")
    approvals.add_argument("--all", action="store_true")

    approve = sub.add_parser("approve", help="Execute one locally reviewed proposal")
    approve.add_argument("approval_id")
    approve.add_argument("--post-result", action="store_true")

    reject = sub.add_parser("reject", help="Reject one action proposal")
    reject.add_argument("approval_id")
    return parser


def _configure_logging(settings: Settings) -> None:
    settings.ensure_runtime_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            _file_log_handler(settings),
        ],
    )
    # httpx logs full query strings, including conversation identifiers.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _file_log_handler(settings: Settings) -> RotatingFileHandler:
    return RotatingFileHandler(
        settings.log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )


def _agent_factory(settings: Settings, state: RelayState):
    return lambda: CodexAgent(
        state,
        cwd=settings.codex_cwd,
        prompt_path=settings.prompt_path,
        model=settings.codex_model,
        approved_sandbox=settings.approved_sandbox,
        memory_settings=settings,
    )


def _print_status(state: RelayState) -> None:
    status = state.status()
    for key in ("initiative_due_at", "last_initiative_check_at"):
        timestamp = int(status[key])
        status[key.replace("_at", "_local")] = (
            datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")
            if timestamp
            else ""
        )
    print(json.dumps(status, indent=2, sort_keys=True))


def _list_approvals(state: RelayState, include_all: bool) -> None:
    rows = state.list_approvals(None if include_all else "pending")
    if not rows:
        print("No matching approvals.")
        return
    for row in rows:
        print(
            f"{row['id']}  {row['status']}  risk={row['risk']}\n"
            f"  {row['summary']}\n"
            f"  instruction: {row['instruction']}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "integrity" and args.database is not None:
        return _print_integrity(args.database)
    try:
        settings = Settings.load(args.env_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "integrity":
        return _print_integrity(settings.state_path)
    if args.command == "backup":
        try:
            output = backup_database(settings.state_path, args.output)
        except BackupError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Backup saved to {output}")
        print(BACKUP_NOTICE)
        return 0
    if args.command == "diagnostics":
        try:
            output = export_diagnostics(settings.state_path, settings.log_path, args.output)
        except DiagnosticError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Diagnostics saved to {output}")
        print(REVIEW_NOTICE)
        return 0
    _configure_logging(settings)

    if args.command == "serve":
        try:
            return serve_dashboard(settings)
        except RelayAlreadyRunning as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except Exception as exc:
            logging.getLogger(__name__).exception("Server failed")
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    lock_path = settings.state_path.with_suffix(".lock")
    try:
        with RelayState(settings.state_path) as state:
            if args.command == "status":
                _print_status(state)
                return 0
            if args.command == "pause":
                pause_relay(state, args.reason)
                _print_status(state)
                return 0
            if args.command == "resume":
                if process_lock_is_active(lock_path):
                    print(
                        "The relay server is running; use its dashboard to resume.",
                        file=sys.stderr,
                    )
                    return 3
                resume_relay(state)
                _print_status(state)
                return 0
            if args.command == "approvals":
                _list_approvals(state, args.all)
                return 0
            if args.command == "reject":
                if process_lock_is_active(lock_path):
                    print(
                        "The relay server is running; use its dashboard to reject approvals.",
                        file=sys.stderr,
                    )
                    return 3
                try:
                    reject_approval(state, args.approval_id)
                except ApprovalStateError:
                    print("Approval is missing or not pending.", file=sys.stderr)
                    return 2
                print(f"Rejected {args.approval_id}.")
                return 0

            with ProcessLock(lock_path):
                with KindroidClient(settings.api_key, settings.base_url) as kindroid:
                    application = RelayApplication(
                        settings,
                        state,
                        kindroid,
                        _agent_factory(settings, state),
                    )
                    if args.command == "bootstrap":
                        count = application.bootstrap(
                            respond_to_latest=args.respond_to_latest,
                            force=args.force,
                        )
                        print(f"Bootstrapped {count} new history messages.")
                        _print_status(state)
                        return 0
                    if args.command == "once":
                        count = application.run_once()
                        print(f"Handled {count} conversation chain(s).")
                        _print_status(state)
                        return 0
                    if args.command == "trigger":
                        result = application.trigger_initiative()
                        print(f"Manual check-in outcome: {result['initiative_outcome']}; "
                              f"handled {result['handled']} conversation chain(s).")
                        _print_status(state)
                        return 0
                    if args.command == "start-conversation":
                        count = application.start_conversation()
                        print(f"Manual conversation completed {count} conversation chain(s).")
                        _print_status(state)
                        return 0
                    if args.command == "run":
                        try:
                            application.run_forever()
                        except KeyboardInterrupt:
                            print("Relay stopped.")
                        return 0
                    if args.command == "approve":
                        try:
                            result = application.execute_approval(
                                args.approval_id,
                                post_result=args.post_result,
                            )
                        except ApprovalStateError:
                            print("Approval is missing or not pending.", file=sys.stderr)
                            return 2
                        print(result.summary)
                        return 0
    except RelayAlreadyRunning as exc:
        print(
            f"{exc}. The relay server may be running; use the local dashboard.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:
        logging.getLogger(__name__).exception("Command failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_integrity(path: Path) -> int:
    try:
        print(json.dumps(check_integrity(path), indent=2, sort_keys=True))
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
