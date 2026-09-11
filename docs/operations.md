# Operations and recovery

This guide describes schema 3 and the combined dashboard host. The commands and
fake-adapter tests are implemented; real-browser Windows and disposable-group
release acceptance remain separate checks. Marc confirmed sign-in autostart works.

## Starting and restarting

Run `.\.venv\Scripts\kindroid-relay.exe serve` from the repository. The default
address is `http://127.0.0.1:8765`; automatic browser opening is optional. `run`
provides headless operation, not a second worker to launch alongside `serve`.

For a terminal-launched `serve`, intentional Ctrl+C is caught at the CLI boundary
after the host's worker cleanup. It prints `Relay shutdown requested.` and exits
without an asyncio/KeyboardInterrupt traceback. The worker's 30-second wait and
timeout warning are unchanged; that message alone does not prove an external
operation completed. Pause and let in-flight work finish before stopping. Automated
shutdown tests raise exceptions directly and never send Windows console signals.

The sign-in launcher intentionally has no terminal window. For a hidden instance,
use Windows Task Scheduler: select the installed Kindroid relay task, choose
**End**, wait until it is no longer Running, then **Run**. First Pause in the
dashboard and wait for any in-flight call to finish. Ending the task is not a
graceful cancellation; an interrupted operation may need reconciliation after
restart. Do not end all Python processes or remove a live relay's lock file.
Install/remove of autostart registration does not stop or start a running host.

After restart, reload the dashboard to get its new request token. Inspect recent
commands, activity, delivery health, and pause reason before Resume. Interrupted
side-effecting commands are marked interrupted and pause the relay. A startup
does not silently replay them. Never submit a new conversation just to resolve a
command whose outcome is still unknown.

## Controls and authority

Start conversation and Trigger check-in enqueue worker-owned requests, independent
of the browser's lifetime. Trigger bypasses due time, not quota, quiet hours, pause,
or other safeguards. Resume is queued and resets the automatic turn budget; it
does not clear uncertain delivery evidence. Pause is immediate for future work,
but cannot cancel an external call already in flight. The check-in limit is a
durable rolling-24-hour attempt limit, saved through the worker queue.

Approval Execute and Reject require local confirmation; Execute's post-result
choice controls whether a result is sent back to Kindroid. A chat message cannot
approve an action. Refresh, filters, pagination, and log display are local reads.
Retry same request preserves the idempotency key after a lost response; a fresh
submission with a new key is a separate command, not recovery.

The dashboard is loopback-only with Host validation, same-origin/CSRF checks,
bounded JSON input, and one external-effect worker. It assumes a trusted local
user/browser environment: it is not an authentication boundary against malware or
another process running as the same Windows user. Do not expose it through a proxy,
tunnel, or public bind address.

## Back up and inspect a restoration

1. Create a new online backup with `backup --output .\var\backup-before-change`.
   An existing directory is rejected. Keep its database and completion manifest
   together; a missing manifest means incomplete output.
2. Compare `Get-FileHash .\var\backup-before-change\state.sqlite3 -Algorithm SHA256`
   with `sha256` in `manifest.json`. Run `integrity --database` against that file.
   A hash detects accidental changes, not malicious replacement of both files.
3. For a restore drill, copy the **completed, closed backup** to a fresh staging
   directory, not over the active database. For example:

   ```powershell
   New-Item -ItemType Directory .\var\restore-review -ErrorAction Stop
   Copy-Item -LiteralPath .\var\backup-before-change\state.sqlite3 -Destination .\var\restore-review\state.sqlite3
   .\.venv\Scripts\kindroid-relay.exe integrity --database .\var\restore-review\state.sqlite3
   ```

4. Review the snapshot's schema/code compatibility, pause state, commands, outbox,
   approvals, generation turns, and Codex thread reference using read-only local
   inspection. Do not use `serve`, `run`, or `once` as an inspection command.
   Successful SQLite integrity does not mean the external conversation is aligned.
5. Before an actual switch, Pause and safely stop the host; prevent sign-in/restart
   from launching it during maintenance. Preserve the current database and any
   sidecars together. Do not mix a restored main file with an old `-wal` or `-shm`.
6. Account for effects completed **after** the snapshot: restoring loses their
   deduplication and approval execution records. Keep the restored state paused
   and reconcile with the newer local evidence and remote conversation before
   authorizing any execution. If evidence is ambiguous, stop for operator review;
   there is no automatic restore-and-resume command or force-retry shortcut.
7. Only after that review should the configured state path be switched to the
   reviewed copy. A code rollback needs a compatible pre-upgrade snapshot, not an
   attempted schema downgrade. Keep the previous state recoverable.

Backing up a live relay does not pause it. If the backup must represent a quiet
boundary, first Pause and wait until work in flight finishes. Both backup and
integrity use read-only source connections and do not migrate older databases.
They may need access to the live database's SQLite sidecars; filesystem permissions
still apply. Backups are not sanitized diagnostic bundles and must remain private.

## Troubleshooting

| Symptom | Safe next step |
|---|---|
| Port already in use | Check whether the existing relay/dashboard is already running. Stop only that identified instance or choose another loopback port; do not launch a second worker against the same database. |
| Process lock remains | The lock's PID must be checked without sending a signal. A live owner means use its dashboard; do not delete its lock. Startup handles a confirmed dead owner. |
| Worker stale or degraded | Stale can mean a long synchronous call; inspect phase and activity first. A crashed worker leaves the dashboard readable but queued work will not execute. Pause if possible, then safely restart and review recovery. |
| Migration failure | Preserve the database, sidecars, previous revision, and error evidence. Do not recreate the database or edit its schema version. Check a separate backup and use compatible code. |
| Integrity/backup failure | Missing, corrupt, busy, full, or slow storage fails closed. Preserve the source. A folder without a completion manifest is not a valid backup. Resolve contention/capacity and use a new destination; do not repeatedly resume the relay. |
| Database busy request | The API returns sanitized `503 database_busy` without exposing raw SQLite errors or forcing a connection close. Do not assume the command was queued. After contention clears, retry the same idempotency key and inspect recent commands. Do not invent a new key to bypass uncertainty. |
| Interrupted command | Inspect its linked outbox/approval/generation records and remote outcome. Restart marks it interrupted, not safe to execute again. |
| Uncertain outbound delivery | The message might already exist in Kindroid. Keep paused and inspect the exact outbox/source linkage. Never automatically resend or clear uncertainty merely because the HTTP call timed out. |
| Hannah replied but response capture was empty | One budgeted history synchronization attempts to recover the existing reply. Missing/ambiguous matches pause; history 429 defers recovery until cooldown. Do not generate again to recover a reply that already exists. |
| History quota/429 | Local usage only warns and resets at 00:00 UTC daily. Actual server 429 fails the session and pauses its room; observe the cooldown, sync/reconcile, then resume separately. `Retry-After` or durable exponential fallback and minimum spacing remain; local reset does not clear them. |
| Codex failed before replying | Inspect the failed command/activity. No opener was sent if the opener failed before send. A later-chain failure may follow earlier successful effects, so inspect linkage before any manual rerun. |
| Persona restoration failed | Delivery is uncertain even if the message was posted. Check the active persona in Kindroid and restore Marc's persona locally as needed before resuming. Do not resend the message to fix identity. |
| Stored persona name differs | `[CODEX]:` remains authoritative attribution; cosmetic names do not prove a separate model speaker role. Inspect the fallback warning rather than changing account-global configuration experimentally. |

Use `diagnostics --output <new-file.json>` for a bounded, privacy-filtered report,
reviewed before sharing. It omits free-form error text and cannot replace private
local investigation or a backup. Do not share the environment file, authentication,
raw network captures, or unredacted backups/logs.

## Remaining release acceptance

Local tests cover real HTTP with fake adapters; they do not authorize real external
effects or replace visual/native-dialog acceptance. Still required before tagging:
Windows terminal/browser/timezone/shutdown/headless checks, all controls against an
explicitly approved disposable Kindroid group, a final quiet-boundary live backup,
and the full dependency/license, logging, mutation-endpoint, and tracked-secret
release review. Self-change and observational-agency milestones remain deferred
until the dashboard release gate is satisfied.
