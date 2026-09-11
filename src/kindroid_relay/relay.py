from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import datetime, timedelta
from typing import Any, cast

from .activity import ActivitySeverity, WorkerPhase
from .config import Settings
from .kindroid_api import KindroidAPIError, KindroidClient
from .models import AgentReply, ChatMessage, InvalidAgentReply
from .redaction import redact_text
from .state import NON_SPENDING_INITIATIVE_OUTCOMES, RelayState
from .turns import EmptyGeneratedReply, ReconciliationError, reconciliation_diagnostic


logger = logging.getLogger(__name__)


class HistoryCooldownError(RuntimeError):
    def __init__(self, retry_at: int) -> None:
        self.retry_at = retry_at
        super().__init__(f"Kindroid history requests are cooling down until {retry_at}")


PhaseContext = Callable[[WorkerPhase, str], AbstractContextManager[None]]


class Relay:
    def __init__(
        self,
        settings: Settings,
        state: RelayState,
        kindroid: KindroidClient,
        agent_factory: Callable[[], object],
        phase_context: PhaseContext | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.kindroid = kindroid
        self.agent_factory = agent_factory
        self.phase_context = phase_context or (lambda _phase, _note: nullcontext())
        self.initiative_outcome: str | None = None
        self._context_synced_at = 0.0
        self.context_operation: Callable[[], bool] | None = None
        self.chosen_encounter = False
        self.encounter_opener = None
        self.history_requests_attempted = 0
        self.history_rejections = 0

    def _event(
        self,
        kind: str,
        severity: ActivitySeverity,
        summary: str,
        *,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.state.activity.add(
            kind,
            severity,
            summary,
            details=details,
            correlation_id=correlation_id,
        )

    def _start_activity_session(self, summary):
        import uuid
        if not self.state.get_meta('activity_session'):
            self.state.set_meta('activity_session', uuid.uuid4().hex)
            self._event('session_started', ActivitySeverity.INFO, summary)

    def _end_activity_session(self, outcome):
        if self.state.get_meta('activity_session'):
            self._event('session_ended', ActivitySeverity.INFO, 'Conversation session ended', details={'outcome': outcome})
            self.state.set_meta('activity_session', '')

    def _pause(self, reason: str, summary: str) -> None:
        was_paused = self.state.is_paused()
        self.state.pause(reason)
        if not was_paused:
            self._event(
                "relay_paused",
                ActivitySeverity.WARNING,
                summary,
                details={"reason": reason},
            )
        self._end_activity_session('paused_inspect_activity')

    def _mark_message_processed(self, message_id: str) -> None:
        self.state.mark_message_processed(message_id)
        self._event(
            "message_processed",
            ActivitySeverity.INFO,
            "Kindroid message processed",
            details={"message_id": message_id},
            correlation_id=message_id,
        )

    def _call_codex(
        self,
        operation: str,
        callback: Callable[[], Any],
        *,
        details: dict[str, Any],
        correlation_id: str,
    ) -> Any:
        self._event(
            "codex_started",
            ActivitySeverity.INFO,
            f"Codex started {operation}",
            details=details,
            correlation_id=correlation_id,
        )
        try:
            with self.phase_context(WorkerPhase.RUNNING_CODEX, f"Codex {operation}"):
                result = callback()
        except Exception as exc:
            self._event(
                "codex_failed",
                ActivitySeverity.ERROR,
                f"Codex {operation} failed",
                details={**details, "error_type": exc.__class__.__name__},
                correlation_id=correlation_id,
            )
            raise
        self._event(
            "codex_completed",
            ActivitySeverity.INFO,
            f"Codex completed {operation}",
            details=details,
            correlation_id=correlation_id,
        )
        return result

    @contextmanager
    def _agent_session(self, correlation_id: str) -> Iterator[Any]:
        entered = False
        try:
            with self.phase_context(
                WorkerPhase.RUNNING_CODEX,
                "Codex conversation session",
            ):
                with self.agent_factory() as agent:  # type: ignore[attr-defined]
                    entered = True
                    yield agent
        except Exception as exc:
            if not entered:
                self._event(
                    "codex_failed",
                    ActivitySeverity.ERROR,
                    "Codex session failed to open",
                    details={
                        "message_id": correlation_id,
                        "error_type": exc.__class__.__name__,
                    },
                    correlation_id=correlation_id,
                )
            raise

    def _history_page(self, start_after: int | None):
        now = int(time.time())
        cooldown_until = self.state.get_int("history_cooldown_until", 0)
        if cooldown_until > now:
            raise HistoryCooldownError(cooldown_until)
        minimum = self.settings.min_history_interval_seconds
        if minimum:
            last_request_ms = self.state.get_int("last_history_request_at_ms", 0)
            wait = minimum - ((time.time() * 1000 - last_request_ms) / 1000)
            if wait > 0:
                logger.info("Waiting %.1fs before the next history request", wait)
                time.sleep(wait)
        self.state.record_history_request(self.settings.max_history_requests_24h)
        self.history_requests_attempted += 1
        self.state.set_meta("last_history_request_at_ms", int(time.time() * 1000))
        try:
            with self.phase_context(
                WorkerPhase.WAITING_FOR_KINDROID,
                "Synchronizing Kindroid history",
            ):
                page = self.kindroid.get_messages_page(
                    self.settings.group_id,
                    limit=100,
                    start_after_timestamp=start_after,
                )
        except KindroidAPIError as exc:
            if exc.status_code != 429:
                raise
            self.history_rejections += 1
            streak = self.state.get_int("history_rate_limit_streak", 0) + 1
            if exc.retry_after_seconds is not None:
                delay = max(1, exc.retry_after_seconds)
            else:
                delay = min(
                    self.settings.history_429_cooldown_seconds * (2 ** (streak - 1)),
                    86400,
                )
            limited_at = int(time.time())
            retry_at = limited_at + delay
            self.state.set_meta("history_last_429_at", limited_at)
            self.state.set_meta("history_cooldown_until", retry_at)
            self.state.set_meta("history_rate_limit_streak", streak)
            # An explicit read/recovery while already paused must preserve its
            # session and uncertainty evidence, even when the server rejects it.
            if not self.state.is_paused():
                with self.state.connection:
                    self.state.connection.execute(
                        "UPDATE shared_sessions SET status='ended',ended_at=?,reason=? WHERE status='active'",
                        (limited_at, 'history_rate_limit'),
                    )
                self._end_activity_session('failed_history_rate_limit')
                self._pause('Kindroid rejected a history read (HTTP 429); inspect before resuming',
                            'Session failed because Kindroid rejected a history read')
            self.state.activity.add(
                "history_rate_limited",
                ActivitySeverity.ERROR,
                "Kindroid rejected history access; session stopped, inspect before resuming",
                details={
                    "cooldown_until": retry_at,
                    "retry_after_seconds": delay,
                    "request_count": self.state.history_request_count(),
                },
            )
            logger.warning(
                "Kindroid history rate limit reached; next history request after %s",
                datetime.fromtimestamp(retry_at).astimezone().isoformat(),
            )
            raise HistoryCooldownError(retry_at) from exc
        self.state.set_meta("history_cooldown_until", 0)
        self.state.set_meta("history_rate_limit_streak", 0)
        return page

    def fetch_history(self, cursor: int | None) -> list[ChatMessage]:
        """Fetch a complete window without importing or linking it."""
        visited_cursors: set[int] = set()
        history: dict[str, ChatMessage] = {}
        # Complete pagination before associating replies: a later page may
        # contain another identical candidate. Never hold SQLite over HTTP.
        while True:
            page = self._history_page(cursor)
            history.update((m.id, m) for m in page.messages if m.id)
            timestamps = [message.timestamp for message in page.messages]
            next_cursor = page.last_timestamp or (max(timestamps) if timestamps else cursor)
            if not page.has_more:
                break
            if next_cursor is None or next_cursor in visited_cursors or (cursor is not None and next_cursor <= cursor):
                raise RuntimeError("Kindroid history pagination did not advance; synchronization deferred")
            visited_cursors.add(next_cursor)
            cursor = next_cursor
        return sorted(history.values(), key=lambda m: m.timestamp)

    def sync_history(self, *, initial: bool = False) -> int:
        cursor = None if initial else max(0, self.state.turns.sync_cursor(
            self.settings.group_id, self.state.get_int("cursor_timestamp", 0)
        ) - 1)
        messages = self.fetch_history(cursor)
        inserted = 0
        previously_known = {row[0] for row in self.state.connection.execute(
            "SELECT kindroid_id FROM messages WHERE kindroid_id IS NOT NULL"
        )}
        try:
            reconciled = self.state.turns.reconcile(self.settings.group_id, messages)
            # Do not ingest potential copies as new events until every local
            # effect has an unambiguous remote association. Retry on a later
            # scheduled sync, never by polling here or repeating generation.
            self.state.turns.require_reconciled(self.settings.group_id)
        except ReconciliationError as exc:
            detail = reconciliation_diagnostic(exc)['reason_detail']
            self._pause("Kindroid history reconciliation is unresolved: " + detail,
                        "Relay paused for unresolved history reconciliation: " + detail)
            raise
        for message in messages:
            if message.id not in reconciled:
                # Retain blank history records, but never treat them as chat turns.
                processed = message.sender_type != "ai" or not message.message.strip()
                known_outbound = (
                    message.sender_type == "user"
                    and self.state.is_known_outbound(message.message)
                )
                if known_outbound:
                    processed = True
                was_inserted = self.state.add_message(
                    self.settings.group_id, message, processed=processed
                )
                inserted += int(was_inserted)
                if was_inserted:
                    self._event(
                        "message_received",
                        ActivitySeverity.INFO,
                        f"Message received from {message.display_name}",
                        details={
                            "message_id": message.id,
                            "display_name": message.display_name,
                            "sender_type": message.sender_type,
                        },
                        correlation_id=message.id,
                    )
                if was_inserted and known_outbound:
                    self._observe_outbound_identity(message)
            elif message.id not in previously_known and message.sender_type == "user":
                local = self.state.connection.execute(
                    "SELECT * FROM messages WHERE kindroid_id = ?", (message.id,)
                ).fetchone()
                self._observe_outbound_identity(self.state._row_to_message(local))
        if messages:
            self.state.set_meta("cursor_timestamp", max(
                self.state.get_int("cursor_timestamp", 0), max(m.timestamp for m in messages)
            ))
        if self.settings.room_mode == 'shared':
            # Record the last nonblank message in provider order, not provisional
            # local timestamps. The shared worker consumes this only after sync
            # has confirmed every local effect and imported the full window.
            tail = next((m for m in reversed(messages) if m.message.strip()), None)
            if tail is not None:
                row = self.state.connection.execute(
                    'SELECT remote_id FROM messages WHERE group_id=? AND kindroid_id=?',
                    (self.settings.group_id, tail.id)).fetchone()
                self.state.set_meta('shared_history_tail', row[0])
        if inserted:
            latest = self.state.latest_message()
            self._schedule_initiative(
                base_at=self._message_epoch_seconds(latest) if latest else None
            )
        return inserted

    def synchronize_context(self) -> None:
        """Explicit boundary before work needing current shared context."""
        self.sync_history()
        try:
            self.state.turns.require_reconciled(self.settings.group_id)
        except ReconciliationError:
            self._pause("Kindroid generation remains uncertain; inspect conversation before resuming", "Relay paused for uncertain Kindroid generation")
            raise
        self._context_synced_at = time.monotonic()
        self.state.set_meta("context_synced_at", int(time.time()))
        # Consume exploration replies before the ordinary, tool-capable chat loop.
        from .agency import AgencyStore
        AgencyStore(self.state, self.settings.group_id, self.settings.kindroid_display_name).reconcile()
        from .exploration_response import ResponseStore
        ResponseStore(self.state, self.settings.group_id, self.settings.kindroid_display_name).reconcile()
        from .experience import ExperienceStore
        ExperienceStore(self.state, self.settings.group_id, self.settings.kindroid_display_name).reconcile()

    def bootstrap(self, *, respond_to_latest: bool = False, force: bool = False) -> int:
        if self.state.get_meta("bootstrapped", "0") == "1" and not force:
            return 0
        inserted = self.sync_history(initial=True)
        self.state.mark_all_messages_processed()
        if respond_to_latest:
            latest = self.state.latest_ai_message()
            if latest:
                self.state.mark_message_processed(latest.id, False)
        self.state.set_meta("bootstrapped", True)
        self.state.set_meta("group_id", self.settings.group_id)
        self.state.set_meta("context_synced_at", int(time.time()))
        self._context_synced_at = time.monotonic()
        return inserted

    def run_once(self, *, initiative_now: bool = False, after_sync: Callable[[], bool] | None = None) -> int:
        self.initiative_outcome = None
        from .quiet_hours import QuietHours
        if QuietHours(self.state, self.settings).blocked():
            self.initiative_outcome = 'quiet_hours'
            return 0
        if self.state.is_paused():
            logger.info("Relay is paused: %s", self.state.get_meta("pause_reason", ""))
            if initiative_now:
                self._skip_initiative("paused", "Check-in skipped because the relay is paused", persist=False)
            return 0
        just_bootstrapped = False
        if self.state.get_meta("bootstrapped", "0") != "1":
            inserted = self.bootstrap()
            logger.info("Bootstrapped %s existing messages without replaying old chat", inserted)
            if not (initiative_now and self.settings.participant_scheduling):
                if initiative_now:
                    self._skip_initiative("bootstrapped", "Check-in skipped while initial history was bootstrapped; trigger again to check in", persist=False)
                return 0
            # No idle bootstrap now: the first explicit/scheduled check-in owns
            # this initial read and can continue without a redundant second sync.
            just_bootstrapped = True

        if not just_bootstrapped and not (self.settings.cooperative_turns and self.state.pending_ai_messages()
                and not self.state.turns.has_uncertain()
                and time.monotonic() - self._context_synced_at < self.settings.poll_seconds):
            self.synchronize_context()
        after_sync = after_sync or self.context_operation
        if after_sync is not None and after_sync():
            return 0
        self._maybe_offer_initiative(force_due=initiative_now)
        handled = 0
        pending = self.state.pending_ai_messages()
        if not pending:
            return 0
        with self._agent_session(pending[0].id) as agent:
            while not self.state.is_paused() and not QuietHours(self.state, self.settings).blocked():
                pending = self.state.pending_ai_messages()
                if not pending:
                    break
                self._handle_chain(agent, pending[0])
                handled += 1
                if self.settings.cooperative_turns:
                    break
        return handled

    def trigger_initiative_now(self) -> int:
        # Carry intent through synchronization, which may reschedule initiative.
        # Only the due-time gate is bypassed, never the operational safeguards.
        return self.run_once(initiative_now=True)

    def start_conversation_now(self) -> int:
        """Start a fresh, scheduler-independent conversation for manual testing."""
        from .quiet_hours import QuietHours
        if QuietHours(self.state, self.settings).blocked():
            return 0
        if self.state.is_paused():
            reason = self.state.get_meta("pause_reason", "")
            raise RuntimeError(f"Relay is paused: {reason or 'no reason recorded'}")

        if self.state.get_meta("bootstrapped", "0") != "1":
            self.bootstrap()
            self.state.turns.require_reconciled(self.settings.group_id)
            self._context_synced_at = time.monotonic()
        else:
            self.synchronize_context()

        pending = self.state.pending_ai_messages()
        if pending:
            raise RuntimeError(
                "Cannot start a fresh conversation while Kindroid messages are "
                f"pending ({len(pending)}); run 'kindroid-relay once' first"
            )

        latest = self.state.latest_message()
        if latest is None:
            raise RuntimeError("Cannot start a conversation because the group history is empty")

        self.state.set_meta("consecutive_turns", 0)
        self.state.set_meta('encounter_asked', 0)
        self.state.set_meta('encounter_choice_received', 0)
        logger.info("Manual conversation: sending a Codex opener to Kindroid")
        self._start_activity_session('Manual conversation session started')
        from .encounters import Encounters
        Encounters(self.state, self.settings).retry_later()
        with self._agent_session(latest.id) as agent:
            opener = cast(
                str,
                self._call_codex(
                    "manual conversation opener",
                    lambda: agent.manual_conversation_opener(latest),  # type: ignore[attr-defined]
                    details={"message_id": latest.id},
                    correlation_id=latest.id,
                ),
            )
            outbox_id = self._send_user_message(self._limit_reply(opener))
            try:
                incoming = self._request_ai_turn(outbox_id=outbox_id, allow_user=False)
            except HistoryCooldownError:
                logger.info("Manual conversation reply will be recovered after the history cooldown")
                return 1
            if incoming is None:
                raise RuntimeError("Kindroid did not select an AI for the forced response")
            logger.info("Manual conversation: Kindroid answered; starting relay loop")
            self._handle_chain(agent, incoming)
        return 1

    def _handle_chain(self, agent: object, incoming: ChatMessage) -> None:
        current: ChatMessage | None = incoming
        while current is not None and not self.state.is_paused():
            from .quiet_hours import QuietHours
            if QuietHours(self.state, self.settings).blocked():
                return
            self._start_activity_session('Conversation session started')
            # A history cooldown can end a chain immediately after generation,
            # before the usual post-generation limit check runs.
            if self.state.get_int("consecutive_turns", 0) >= self.settings.max_consecutive_turns:
                self._pause(
                    f"automatic turn limit reached ({self.settings.max_consecutive_turns})",
                    "Relay paused at the automatic turn limit",
                )
                return
            # Direct replies already provide the next event. Only refresh the
            # remote context when a long exchange crosses the activity interval.
            if time.monotonic() - self._context_synced_at >= self.settings.poll_seconds:
                self.synchronize_context()
                pending = self.state.pending_ai_messages()
                current = pending[0] if pending else None
                if current is None:
                    return
            logger.info(
                "Processing Kindroid message %s from %s",
                current.id,
                current.display_name,
            )
            delivered = self.state.outbox_for_source(current.id)
            decision_reason = "stored_decision" if delivered is not None else "model_decision"
            if delivered is not None and delivered["status"] == "sent":
                outbox_id = str(delivered["id"])
                request_kindroid_reply = bool(delivered["request_kindroid_reply"])
                logger.warning(
                    "Recovering Kindroid message %s after its Codex reply was "
                    "already delivered; not sending it again",
                    current.id,
                )
            else:
                try:
                    reply = cast(
                        AgentReply,
                        self._call_codex(
                            "reply",
                            lambda: agent.reply_to(current),  # type: ignore[attr-defined]
                            details={"message_id": current.id},
                            correlation_id=current.id,
                        ),
                    )
                except InvalidAgentReply:
                    if not self.settings.participant_scheduling:
                        raise
                    from .encounters import Encounters
                    Encounters(self.state, self.settings).retry_later()
                    self._mark_message_processed(current.id)
                    self.state.set_meta('consecutive_turns', 0)
                    self._end_activity_session('invalid_agent_reply')
                    return
                if self.settings.participant_scheduling:
                    reply = self._encounter_reply(current, reply)
                if reply.action:
                    approval_id = self.state.add_approval(current.id, reply.action)
                    self._event(
                        "approval_created",
                        ActivitySeverity.WARNING,
                        "Approval proposal created",
                        details={
                            "approval_id": approval_id,
                            "source_message_id": current.id,
                            "risk": reply.action.risk,
                        },
                        correlation_id=approval_id,
                    )
                    logger.warning(
                        "Action proposal %s requires local approval: %s",
                        approval_id,
                        reply.action.summary,
                    )

                if not reply.reply:
                    self._mark_message_processed(current.id)
                    self._end_activity_session('no_reply')
                    return

                body = self._outbound_body(self._limit_reply(reply.reply))
                request_kindroid_reply = reply.request_kindroid_reply
                if delivered is not None:
                    # Recovery follows the durable decision, never reinterprets an
                    # old delivered/pending message to create a surprise extra turn.
                    request_kindroid_reply = bool(delivered["request_kindroid_reply"])
                elif not request_kindroid_reply and any(mark in body for mark in "?\uff1f\u061f"):
                    # Conservative fallback, not semantic question classification.
                    # Persist before sending so recovery keeps the corrected flag.
                    request_kindroid_reply = True
                    decision_reason = "question_requires_reply"
                outbox_id = self._send_user_message(
                    body,
                    source_message_id=current.id,
                    request_kindroid_reply=request_kindroid_reply,
                )
            self._mark_message_processed(current.id)
            turns = self.state.get_int("consecutive_turns", 0) + 1
            self.state.set_meta("consecutive_turns", turns)

            summary = (
                f"{self.settings.kindroid_display_name} reply requested: corrected question/stop mismatch"
                if decision_reason == "question_requires_reply" else
                f"{self.settings.kindroid_display_name} reply requested by Codex" if request_kindroid_reply else
                f"Exchange ended: Codex requested no {self.settings.kindroid_display_name} reply"
            )
            logger.info(summary)
            self._event(
                "reply_turn_decision", ActivitySeverity.INFO, summary,
                details={"source_message_id": current.id, "outbox_id": outbox_id,
                         "outcome": "reply_requested" if request_kindroid_reply else "no_reply_requested",
                         "reason": decision_reason},
                correlation_id=outbox_id,
            )

            if not request_kindroid_reply:
                self.state.set_meta("consecutive_turns", 0)
                self._end_activity_session('completed')
                return

            try:
                next_message = self._request_ai_turn(outbox_id=outbox_id, allow_user=False)
            except HistoryCooldownError:
                logger.info("Kindroid reply will be recovered after the history cooldown")
                return
            if turns >= self.settings.max_consecutive_turns:
                reason = (
                    "automatic turn limit reached "
                    f"({self.settings.max_consecutive_turns})"
                )
                self._pause(reason, "Relay paused at the automatic turn limit")
                logger.warning("Automatic turn limit reached; relay paused")
                return
            current = next_message
            if current is None:
                self._end_activity_session('companion_did_not_take_turn')
            if self.settings.cooperative_turns:
                return

    def _encounter_reply(self, current: ChatMessage, reply: AgentReply) -> AgentReply:
        from dataclasses import replace
        from .encounters import Encounters
        encounters = Encounters(self.state, self.settings)
        receipt = ''
        if reply.next_encounter is not None:
            try:
                receipt = encounters.choose(current, reply.next_encounter)
            except (ValueError, TypeError, OverflowError):
                # Do not publish a model's unsupported scheduling promise.
                row = encounters.retry_later()
                note = Encounters.receipt(row) if row else ''
                return replace(reply, reply='I could not save that timing. ' + note,
                               request_kindroid_reply=False, explore=False)
            self.state.set_meta('encounter_choice_received', 1)
            reply = replace(reply, reply=receipt + '\n\n' + reply.reply)
        if (not reply.request_kindroid_reply and not self.state.get_int('encounter_asked')
                and not self.state.get_int('encounter_choice_received')):
            self.state.set_meta('encounter_asked', 1)
            encounters.retry_later()
            return replace(reply, reply=reply.reply + '\n\nWhen would you like our next check-in?',
                           request_kindroid_reply=True)
        if self.state.get_int('encounter_asked') and not self.state.get_int('encounter_choice_received'):
            row = encounters.retry_later()
            return replace(reply, reply=Encounters.receipt(row), request_kindroid_reply=False, explore=False)
        return reply

    def _limit_reply(self, reply: str) -> str:
        maximum = self.settings.max_reply_chars
        if len(reply) <= maximum:
            return reply
        shortened = reply[: maximum - 1].rsplit(" ", 1)[0].rstrip()
        return shortened + "..."

    def _outbound_body(self, reply: str) -> str:
        prefix = self.settings.codex_message_prefix
        if reply.startswith(prefix):
            return self._limit_reply(reply)
        available = self.settings.max_reply_chars - len(prefix)
        if available < 4:
            raise RuntimeError("Codex message prefix leaves no room for a reply")
        if len(reply) > available:
            shortened = reply[: available - 1].rsplit(" ", 1)[0].rstrip()
            reply = shortened + "..."
        return prefix + reply

    def _activate_codex_persona(self) -> None:
        self.kindroid.activate_user_persona(
            self.settings.codex_persona_id,
            name=self.settings.codex_display_name,
            gender=self.settings.codex_persona_gender,
            backstory=self.settings.codex_persona_backstory,
        )

    def _restore_user_persona(self) -> None:
        self.kindroid.activate_user_persona(
            self.settings.restore_persona_id,
            name=self.settings.restore_persona_name,
            gender=self.settings.restore_persona_gender,
            backstory=self.settings.restore_persona_backstory,
        )

    def _send_user_message(
        self,
        body: str,
        *,
        source_message_id: str | None = None,
        request_kindroid_reply: bool | None = None,
        outbox_created: Callable[[str], None] | None = None,
    ) -> str:
        body = self._outbound_body(body)
        if self.state.turns.has_uncertain():
            self._pause("Kindroid generation remains uncertain; synchronize before sending", "Relay paused for uncertain Kindroid generation")
            raise ReconciliationError("Kindroid generation remains uncertain; synchronize before sending")
        existing = (
            self.state.outbox_for_source(source_message_id)
            if source_message_id is not None
            else None
        )
        if existing is not None:
            if existing["status"] == "sent":
                logger.warning(
                    "Skipping an already delivered reply for Kindroid message %s",
                    source_message_id,
                )
                return str(existing["id"])
            if existing["status"] in {"sending", "uncertain"}:
                reason = (
                    "outbound delivery remains uncertain for Kindroid message "
                    f"{source_message_id}; refusing to send a possible duplicate"
                )
                self._pause(reason, "Relay paused for uncertain outbound delivery")
                raise RuntimeError(reason)
            outbox_id = str(existing["id"])
            body = str(existing["body"])
        else:
            outbox_id = self.state.create_outbox(
                body,
                source_message_id=source_message_id,
                request_kindroid_reply=request_kindroid_reply,
            )
            self._event(
                "outbound_created",
                ActivitySeverity.INFO,
                "Outbound Kindroid message created",
                details={
                    "outbox_id": outbox_id,
                    **(
                        {"source_message_id": source_message_id}
                        if source_message_id
                        else {}
                    ),
                },
                correlation_id=outbox_id,
            )
        if outbox_created is not None:
            # Link the session before the first external effect, including crashes.
            outbox_created(outbox_id)
        if outbox_created is not None and self.state.is_paused():
            raise RuntimeError("Relay paused before outbound delivery")
        self.state.set_outbox_status(outbox_id, "sending")
        outbound_details = {
            "outbox_id": outbox_id,
            **(
                {"source_message_id": source_message_id}
                if source_message_id
                else {}
            ),
        }
        self._event(
            "outbound_sending",
            ActivitySeverity.INFO,
            "Sending message to Kindroid",
            details=outbound_details,
            correlation_id=outbox_id,
        )
        try:
            with self.phase_context(WorkerPhase.SENDING, "Sending message to Kindroid"):
                if self.settings.persona_switch_enabled:
                    try:
                        self._activate_codex_persona()
                        self.kindroid.post_user_message(self.settings.group_id, body)
                    finally:
                        self._restore_user_persona()
                else:
                    self.kindroid.post_user_message(self.settings.group_id, body)
            self.state.turns.record_sent(
                outbox_id, self.settings.group_id, self.settings.codex_display_name,
                self.state.get_int("cursor_timestamp", 0),
            )
        except Exception as exc:
            self.state.set_outbox_status(
                outbox_id,
                "uncertain",
                f"{exc.__class__.__name__}: {redact_text(exc, maximum=500)}",
            )
            self._event(
                "outbound_uncertain",
                ActivitySeverity.ERROR,
                "Outbound Kindroid delivery is uncertain",
                details={
                    **outbound_details,
                    "error_type": exc.__class__.__name__,
                },
                correlation_id=outbox_id,
            )
            self._pause(
                f"outbound delivery uncertain; inspect outbox item {outbox_id}",
                "Relay paused for uncertain outbound delivery",
            )
            raise
        self._schedule_initiative()
        self._event(
            "outbound_sent",
            ActivitySeverity.INFO,
            "Outbound Kindroid message sent",
            details=outbound_details,
            correlation_id=outbox_id,
        )
        return outbox_id

    def _observe_outbound_identity(self, stored: ChatMessage) -> None:
        self.state.set_meta("last_identity_observed_at", int(time.time()))
        self.state.set_meta("last_identity_observed_name", stored.display_name)
        expected = self.settings.codex_display_name.casefold()
        if stored.display_name.casefold() != expected:
            self.state.set_meta("last_identity_fallback_at", int(time.time()))
            self.state.set_meta("last_identity_fallback_name", stored.display_name)
            logger.warning(
                "Persona switch did not control stored attribution: expected %r, "
                "stored as %r; continuing because the message has the Codex prefix",
                self.settings.codex_display_name,
                stored.display_name,
            )
            self._event(
                "identity_fallback",
                ActivitySeverity.WARNING,
                "Stored outbound identity differed; Codex prefix remains authoritative",
                details={
                    "message_id": stored.id,
                    "display_name": stored.display_name,
                },
                correlation_id=stored.id,
            )

    def _request_ai_turn(self, *, outbox_id: str, allow_user: bool) -> ChatMessage | None:
        existing = self.state.turns.for_outbox(outbox_id)
        if existing is not None:
            if existing["status"] in {"received", "reconciled"}:
                return self.state.turns.message(existing["id"])
            raise ReconciliationError("Kindroid generation remains uncertain; synchronize before continuing")
        if self.state.turns.has_uncertain():
            raise ReconciliationError("Kindroid generation remains uncertain; synchronize before continuing")
        with self.phase_context(
            WorkerPhase.WAITING_FOR_KINDROID,
            "Requesting the next Kindroid turn",
        ):
            ai_id = self.kindroid.get_turn(
                self.settings.group_id, allow_user=allow_user
            )
            if not ai_id:
                logger.info("Kindroid returned the user turn")
                return None
            if self.settings.participants and ai_id not in {person[0] for person in self.settings.participants}:
                self._pause('Kindroid selected a participant outside this room configuration',
                            'Relay paused for unexpected speaker selection')
                raise ReconciliationError('Kindroid selected an unexpected participant; check room configuration')
            display_name = self.state.turns.expected_display_name(
                self.settings.group_id, ai_id, self.settings.kindroid_display_name
            )
            turn_id = self.state.turns.begin(self.settings.group_id, ai_id,
                display_name, outbox_id,
                self.state.get_int("cursor_timestamp", 0))
            failure_stage = "request"
            try:
                body = self.kindroid.generate_ai_response(self.settings.group_id, ai_id)
                failure_stage = "capture"
                message = self.state.turns.complete(turn_id, body)
            except Exception as exc:
                self.state.turns.mark_uncertain(turn_id)
                if isinstance(exc, EmptyGeneratedReply):
                    self._event(
                        "generation_history_fallback", ActivitySeverity.WARNING,
                        "Kindroid returned no reply text; synchronizing history once",
                        details={"outbox_id": outbox_id, "outcome": "started"},
                        correlation_id=outbox_id,
                    )
                    try:
                        # Generation has already happened. Fetch the existing
                        # reply through the budgeted history boundary, never by
                        # repeating generation or polling in a loop.
                        self.synchronize_context()
                    except HistoryCooldownError:
                        self._event(
                            "generation_history_fallback", ActivitySeverity.WARNING,
                            "Reply recovery deferred until the history cooldown ends",
                            details={"outbox_id": outbox_id, "outcome": "cooldown"},
                            correlation_id=outbox_id,
                        )
                        raise
                    except Exception as recovery_error:
                        recovery_type = type(recovery_error).__name__
                        if recovery_type not in {
                            "ReconciliationError", "ReconciliationPending", "KindroidAPIError",
                            "OperationalError", "IntegrityError", "RuntimeError",
                        }:
                            recovery_type = "UnexpectedError"
                        self._event(
                            "generation_history_fallback", ActivitySeverity.ERROR,
                            "History did not safely confirm the generated reply",
                            details={"outbox_id": outbox_id, "outcome": "failed",
                                     "error_type": recovery_type},
                            correlation_id=outbox_id,
                        )
                        # Keep uncertainty and report the original cause below.
                    else:
                        message = self.state.turns.message(turn_id)
                        self._event(
                            "generation_history_fallback", ActivitySeverity.INFO,
                            "Existing Kindroid reply recovered from history",
                            details={"outbox_id": outbox_id, "outcome": "recovered",
                                     "message_id": message.id},
                            correlation_id=outbox_id,
                        )
                        self._event(
                            "message_received", ActivitySeverity.INFO,
                            "Kindroid reply received through history recovery",
                            details={"message_id": message.id, "sender_type": "ai"},
                            correlation_id=message.id,
                        )
                        self._schedule_initiative()
                        return message
                error_type = type(exc).__name__
                if error_type not in {
                    "KindroidAPIError", "EmptyGeneratedReply", "ReconciliationError",
                    "OperationalError", "IntegrityError", "DatabaseError",
                    "RuntimeError", "ValueError", "TypeError", "OSError",
                    "TimeoutError",
                }:
                    error_type = "UnexpectedError"
                if isinstance(exc, EmptyGeneratedReply):
                    cause = "empty response after successful HTTP request"
                elif isinstance(exc, KindroidAPIError):
                    cause = (f"HTTP {exc.status_code}" if exc.status_code is not None
                             else "network request failed")
                else:
                    cause = f"{failure_stage} failed: {error_type}"
                details = {"outbox_id": outbox_id, "error_type": error_type,
                           "failure_stage": failure_stage}
                if isinstance(exc, KindroidAPIError):
                    details["http_status"] = exc.status_code
                self._event("generation_uncertain", ActivitySeverity.ERROR,
                            f"Kindroid generation uncertain: {cause}",
                            details=details, correlation_id=outbox_id)
                self._pause(f"Kindroid generation outcome is uncertain ({cause}); synchronize history before continuing",
                            "Relay paused for uncertain Kindroid generation")
                # Retain only allow-listed type/stage/status; never the raw
                # exception, response body, URL, headers, or request content.
                raise ReconciliationError(f"Kindroid generation outcome is uncertain ({cause})") from None
        self._event("message_received", ActivitySeverity.INFO, "Kindroid reply received directly",
                    details={"message_id": message.id, "sender_type": "ai"}, correlation_id=message.id)
        self._schedule_initiative()
        return message

    def _schedule_initiative(self, *, base_at: int | None = None) -> None:
        if self.settings.participant_scheduling:
            return
        from .schedule import saved_schedule
        if saved_schedule(self.state, self.settings.group_id) is not None:
            return
        if not self.settings.initiative_enabled:
            return
        if self.settings.initiative_schedule_hours:
            due_at = self._next_fixed_due(
                datetime.now().astimezone(), self.settings.initiative_schedule_hours
            )
            basis = "fixed_hours_v1:" + ",".join(
                str(hour) for hour in self.settings.initiative_schedule_hours
            )
            self.state.set_meta("initiative_due_at", due_at)
            self.state.set_meta("initiative_schedule_basis", basis)
            return
        jitter = self.settings.initiative_jitter_seconds
        offset = random.SystemRandom().randint(-jitter, jitter) if jitter else 0
        delay = max(60, self.settings.initiative_interval_seconds + offset)
        now = int(time.time())
        base = base_at if base_at is not None and base_at <= now + 300 else now
        self.state.set_meta("initiative_due_at", base + delay)
        self.state.set_meta("initiative_schedule_basis", "message_timestamp_v1")

    @staticmethod
    def _next_fixed_due(now: datetime, hours: tuple[int, ...]) -> int:
        for hour in hours:
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return int(candidate.timestamp())
        tomorrow = now + timedelta(days=1)
        candidate = tomorrow.replace(
            hour=hours[0], minute=0, second=0, microsecond=0
        )
        return int(candidate.timestamp())

    def _initiative_schedule_basis(self) -> str:
        if self.settings.initiative_schedule_hours:
            return "fixed_hours_v1:" + ",".join(
                str(hour) for hour in self.settings.initiative_schedule_hours
            )
        return "message_timestamp_v1"

    @staticmethod
    def _message_epoch_seconds(message: ChatMessage) -> int | None:
        timestamp = message.timestamp
        if timestamp <= 0:
            return None
        # Kindroid currently returns milliseconds, but accept seconds as well.
        return timestamp // 1000 if timestamp > 10_000_000_000 else timestamp

    def _set_initiative_outcome(self, outcome: str) -> None:
        self.initiative_outcome = outcome
        if outcome in NON_SPENDING_INITIATIVE_OUTCOMES:
            self.state.record_initiative_check(self.state.initiative_limit(self.settings.initiative_max_checks_24h), outcome)
        else:
            self.state.update_latest_initiative_outcome(outcome)

    def _skip_initiative(self, outcome: str, summary: str, *, persist: bool = True) -> bool:
        self.initiative_outcome = outcome
        if persist:
            self._set_initiative_outcome(outcome)
        logger.info(summary)
        self._event("initiative_skipped", ActivitySeverity.INFO, summary, details={"outcome": outcome})
        return False

    def _maybe_offer_initiative(self, *, force_due: bool = False) -> bool:
        from .encounters import Encounters
        if (self.settings.room_mode == 'shared' or
                (Encounters(self.state, self.settings).controls_schedule() and not self.chosen_encounter and not force_due)):
            if force_due:
                self._skip_initiative('participant_schedule', 'The selected next encounter governs this room', persist=False)
            return False
        from .schedule import saved_schedule
        if not force_due and saved_schedule(self.state, self.settings.group_id) is not None:
            return False
        if not self.settings.initiative_enabled or self.state.is_paused():
            if force_due:
                outcome = "paused" if self.state.is_paused() else "disabled"
                return self._skip_initiative(outcome, f"Check-in skipped: {outcome}", persist=False)
            return False
        now = int(time.time())
        if (
            self.state.get_meta("initiative_schedule_basis", "")
            != self._initiative_schedule_basis()
        ):
            latest = self.state.latest_message()
            base_at = self._message_epoch_seconds(latest) if latest else None
            self._schedule_initiative(base_at=base_at)
        due_at = self.state.get_int("initiative_due_at", 0)
        if not due_at:
            latest = self.state.latest_message()
            self._schedule_initiative(
                base_at=self._message_epoch_seconds(latest) if latest else None
            )
            due_at = self.state.get_int("initiative_due_at", 0)
        if not force_due and now < due_at:
            return False
        if not force_due and self.settings.initiative_schedule_hours and now > due_at + 90:
            self.state.record_initiative_check(
                self.state.initiative_limit(self.settings.initiative_max_checks_24h), "missed"
            )
            self._schedule_initiative()
            self.initiative_outcome = "missed"
            logger.info("Initiative check skipped because its scheduled time was missed")
            self._event(
                "initiative_skipped",
                ActivitySeverity.INFO,
                "Initiative check skipped because its scheduled time was missed",
                details={"outcome": "missed"},
            )
            return False
        self._event(
            "initiative_due",
            ActivitySeverity.INFO,
            "Initiative check became due",
        )
        self._schedule_initiative()
        if self.state.pending_ai_messages():
            self._set_initiative_outcome("pending_message")
            logger.info("Initiative check skipped because %s already has a pending message", self.settings.kindroid_display_name)
            self._event(
                "initiative_skipped",
                ActivitySeverity.INFO,
                f"Initiative check skipped because a message from {self.settings.kindroid_display_name} is pending",
                details={"outcome": "pending_message"},
            )
            return False
        latest = self.state.latest_message()
        if latest is None:
            self._set_initiative_outcome("empty_room")
            self._event(
                "initiative_skipped",
                ActivitySeverity.INFO,
                "Initiative check skipped because the room is empty",
                details={"outcome": "empty_room"},
            )
            return False
        # Initiation is independent of turn ownership. A processed companion reply
        # or a Marc message can end a conversation just as a Codex reply can.
        # Keep the idle check separate from pending-message/delivery safety.
        if self.state.connection.execute(
            "SELECT 1 FROM outbox WHERE status IN ('pending','sending','uncertain') LIMIT 1"
        ).fetchone():
            return self._skip_initiative("delivery_unresolved", "Check-in skipped because delivery remains unresolved")
        timestamp = self._message_epoch_seconds(latest)
        if not self.chosen_encounter and self.settings.initiative_idle_seconds > 0 and (timestamp is None or timestamp + self.settings.initiative_idle_seconds > now):
            return self._skip_initiative("recent_conversation", "Check-in skipped because conversation activity is recent or its time is unknown")
        if self._inside_quiet_hours():
            self._set_initiative_outcome("quiet_hours")
            logger.info("Initiative check skipped during configured quiet hours")
            self._event(
                "initiative_skipped",
                ActivitySeverity.INFO,
                "Initiative check skipped during quiet hours",
                details={"outcome": "quiet_hours"},
            )
            return False
        guard = getattr(self, 'scheduled_check_in_guard', None)
        if guard and (reason := guard()):
            return self._skip_initiative(reason, 'Scheduled check-in no longer eligible', persist=False)
        # Reserve only once eligibility checks pass, before Codex/remote work.
        # Failed or uncertain attempts remain charged to bound repeated failures.
        if not self.state.record_initiative_check(
            self.state.initiative_limit(self.settings.initiative_max_checks_24h), "started"
        ):
            return self._skip_initiative("daily_budget", "Initiative check skipped because its daily budget was exhausted", persist=False)
        self.state.set_meta('encounter_asked', 0)
        self.state.set_meta('encounter_choice_received', 0)
        logger.info("Initiative check: sending a Codex check-in")
        self._start_activity_session('Check-in session started')
        from .encounters import Encounters
        if self.encounter_opener:
            check_in = self.encounter_opener
        else:
            with self._agent_session(latest.id) as agent:
                check_in = cast(
                    str,
                    self._call_codex(
                        "scheduled check-in",
                        lambda: agent.scheduled_check_in(latest),  # type: ignore[attr-defined]
                        details={"message_id": latest.id},
                        correlation_id=latest.id,
                    ),
                )
        if guard and (reason := guard()):
            return self._skip_initiative(reason, 'Scheduled check-in expired during preparation', persist=False)
        Encounters(self.state, self.settings).retry_later()
        outbox_id = self._send_user_message(self._limit_reply(check_in))
        self._set_initiative_outcome("check_in_sent")
        self._event(
            "initiative_offered",
            ActivitySeverity.INFO,
            f"Scheduled check-in offered to {self.settings.kindroid_display_name}",
            details={"outcome": "check_in_sent"},
        )

        try:
            message = self._request_ai_turn(outbox_id=outbox_id, allow_user=True)
        except HistoryCooldownError:
            self._set_initiative_outcome("check_in_waiting_for_history")
            return False
        if message is None:
            self._set_initiative_outcome("check_in_sent_user_turn")
            logger.info("%s did not take the offered check-in turn", self.settings.kindroid_display_name)
            self._event(
                "initiative_declined",
                ActivitySeverity.INFO,
                f"{self.settings.kindroid_display_name} did not take the offered check-in turn",
                details={"outcome": "user_turn"},
            )
            self._end_activity_session('companion_did_not_take_turn')
            return False
        self._set_initiative_outcome("check_in_answered")
        logger.info("%s answered the scheduled check-in", self.settings.kindroid_display_name)
        self._event(
            "initiative_answered",
            ActivitySeverity.INFO,
            f"{self.settings.kindroid_display_name} answered the scheduled check-in",
            details={"message_id": message.id, "outcome": "answered"},
            correlation_id=message.id,
        )
        return True

    def _inside_quiet_hours(self) -> bool:
        if self.settings.participant_scheduling:
            from .quiet_hours import QuietHours
            return QuietHours(self.state, self.settings).blocked()
        quiet = self.settings.initiative_quiet_hours
        if quiet is None:
            return False
        start, end = quiet
        current = datetime.now().hour * 60 + datetime.now().minute
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def run_forever(self) -> None:
        logger.info("Relay started; passive chat monitoring is off")
        while True:
            handled = 0
            try:
                due = self.state.get_int('initiative_due_at')
                if self.state.has_active_conversation_work() or (
                        not self.settings.participant_scheduling and self.settings.initiative_enabled
                        and due and due <= time.time()):
                    handled = self.run_once()
            except KeyboardInterrupt:
                raise
            except HistoryCooldownError as exc:
                delay = max(1, exc.retry_at - int(time.time()))
                logger.info("History polling is cooling down for %s seconds", delay)
            except Exception:
                logger.exception("Relay cycle failed")
                delay = self.settings.idle_poll_seconds
            else:
                delay = (
                    self.settings.poll_seconds
                    if handled
                    else self.settings.idle_poll_seconds
                )
            due_at = self.state.get_int("initiative_due_at", 0)
            cooldown_until = self.state.get_int("history_cooldown_until", 0)
            from .schedule import saved_schedule
            if (
                self.settings.initiative_enabled and saved_schedule(self.state, self.settings.group_id) is None
                and due_at
                and cooldown_until <= int(time.time())
            ):
                delay = min(delay, max(1, due_at - int(time.time())))
            time.sleep(delay)

    def post_execution_result(self, body: str) -> None:
        if not body:
            return
        if self.settings.room_mode == 'shared':
            self._send_user_message(self._limit_reply(body),
                outbox_created=lambda oid: self.state.set_meta('shared_result_outbox', oid))
            return  # The shared session owns continuation and speaker selection.
        self._start_activity_session('Approved result conversation started')
        outbox_id = self._send_user_message(self._limit_reply(body))
        try:
            self._request_ai_turn(outbox_id=outbox_id, allow_user=False)
        except HistoryCooldownError:
            logger.info("Approval result reply will be recovered after the history cooldown")
