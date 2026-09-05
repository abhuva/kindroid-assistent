from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from .activity import ActivitySeverity
from .config import Settings
from .kindroid_api import KindroidAPIError, KindroidClient
from .models import AgentReply, ChatMessage
from .state import RelayState


logger = logging.getLogger(__name__)


class HistoryCooldownError(RuntimeError):
    def __init__(self, retry_at: int) -> None:
        self.retry_at = retry_at
        super().__init__(f"Kindroid history requests are cooling down until {retry_at}")


class Relay:
    def __init__(
        self,
        settings: Settings,
        state: RelayState,
        kindroid: KindroidClient,
        agent_factory: Callable[[], object],
    ) -> None:
        self.settings = settings
        self.state = state
        self.kindroid = kindroid
        self.agent_factory = agent_factory

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
        self.state.set_meta("last_history_request_at_ms", int(time.time() * 1000))
        try:
            page = self.kindroid.get_messages_page(
                self.settings.group_id,
                limit=100,
                start_after_timestamp=start_after,
            )
        except KindroidAPIError as exc:
            if exc.status_code != 429:
                raise
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
            self.state.activity.add(
                "history_rate_limited",
                ActivitySeverity.WARNING,
                "Kindroid history rate limit reached; polling is cooling down",
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

    def sync_history(self, *, initial: bool = False) -> int:
        cursor = None if initial else max(0, self.state.get_int("cursor_timestamp", 0) - 1)
        inserted = 0
        visited_cursors: set[int] = set()
        while True:
            page = self._history_page(cursor)
            for message in page.messages:
                processed = message.sender_type != "ai"
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
                if was_inserted and known_outbound:
                    self._observe_outbound_identity(message)

            timestamps = [message.timestamp for message in page.messages]
            next_cursor = page.last_timestamp or (max(timestamps) if timestamps else cursor)
            if next_cursor is not None:
                self.state.set_meta(
                    "cursor_timestamp",
                    max(self.state.get_int("cursor_timestamp", 0), next_cursor),
                )
            if not page.has_more or next_cursor is None or next_cursor in visited_cursors:
                break
            visited_cursors.add(next_cursor)
            cursor = next_cursor
        if inserted:
            latest = self.state.latest_message()
            self._schedule_initiative(
                base_at=self._message_epoch_seconds(latest) if latest else None
            )
        return inserted

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
        return inserted

    def run_once(self) -> int:
        if self.state.is_paused():
            logger.info("Relay is paused: %s", self.state.get_meta("pause_reason", ""))
            return 0
        if self.state.get_meta("bootstrapped", "0") != "1":
            inserted = self.bootstrap()
            logger.info("Bootstrapped %s existing messages; waiting for new activity", inserted)
            return 0

        self.sync_history()
        self._maybe_offer_initiative()
        handled = 0
        if not self.state.pending_ai_messages():
            return 0
        with self.agent_factory() as agent:
            while not self.state.is_paused():
                pending = self.state.pending_ai_messages()
                if not pending:
                    break
                self._handle_chain(agent, pending[0])
                handled += 1
        return handled

    def trigger_initiative_now(self) -> int:
        if not self.settings.initiative_enabled:
            logger.info("Initiative checks are disabled")
            return self.run_once()
        self.state.set_meta("initiative_schedule_basis", self._initiative_schedule_basis())
        self.state.set_meta("initiative_due_at", int(time.time()))
        return self.run_once()

    def start_conversation_now(self) -> int:
        """Start a fresh, scheduler-independent conversation for manual testing."""
        if self.state.is_paused():
            reason = self.state.get_meta("pause_reason", "")
            raise RuntimeError(f"Relay is paused: {reason or 'no reason recorded'}")

        if self.state.get_meta("bootstrapped", "0") != "1":
            self.bootstrap()
        else:
            self.sync_history()

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
        logger.info("Manual conversation: sending a Codex opener to Kindroid")
        with self.agent_factory() as agent:
            opener = agent.manual_conversation_opener(latest)  # type: ignore[attr-defined]
            self._send_user_message(self._limit_reply(opener))
            try:
                incoming = self._request_ai_turn(allow_user=False)
            except HistoryCooldownError:
                logger.warning(
                    "Manual conversation response will be synchronized after the "
                    "history cooldown"
                )
                return 1
            if incoming is None:
                raise RuntimeError("Kindroid did not select an AI for the forced response")
            logger.info("Manual conversation: Kindroid answered; starting relay loop")
            self._handle_chain(agent, incoming)
        return 1

    def _handle_chain(self, agent: object, incoming: ChatMessage) -> None:
        current: ChatMessage | None = incoming
        while current is not None and not self.state.is_paused():
            logger.info(
                "Processing Kindroid message %s from %s",
                current.id,
                current.display_name,
            )
            delivered = self.state.outbox_for_source(current.id)
            if delivered is not None and delivered["status"] == "sent":
                request_kindroid_reply = bool(delivered["request_kindroid_reply"])
                logger.warning(
                    "Recovering Kindroid message %s after its Codex reply was "
                    "already delivered; not sending it again",
                    current.id,
                )
            else:
                reply: AgentReply = agent.reply_to(current)  # type: ignore[attr-defined]
                if reply.action:
                    approval_id = self.state.add_approval(current.id, reply.action)
                    logger.warning(
                        "Action proposal %s requires local approval: %s",
                        approval_id,
                        reply.action.summary,
                    )

                if not reply.reply:
                    self.state.mark_message_processed(current.id)
                    return

                body = self._limit_reply(reply.reply)
                self._send_user_message(
                    body,
                    source_message_id=current.id,
                    request_kindroid_reply=reply.request_kindroid_reply,
                )
                request_kindroid_reply = reply.request_kindroid_reply
            self.state.mark_message_processed(current.id)
            turns = self.state.get_int("consecutive_turns", 0) + 1
            self.state.set_meta("consecutive_turns", turns)

            if not request_kindroid_reply:
                logger.info("Codex closed the exchange naturally")
                self.state.set_meta("consecutive_turns", 0)
                return

            try:
                next_message = self._request_ai_turn(allow_user=False)
            except HistoryCooldownError:
                logger.warning(
                    "Kindroid response will be synchronized after the history cooldown"
                )
                return
            if turns >= self.settings.max_consecutive_turns:
                self.state.pause(
                    f"automatic turn limit reached ({self.settings.max_consecutive_turns})"
                )
                logger.warning("Automatic turn limit reached; relay paused")
                return
            current = next_message

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
    ) -> None:
        body = self._outbound_body(body)
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
                return
            if existing["status"] in {"sending", "uncertain"}:
                reason = (
                    "outbound delivery remains uncertain for Kindroid message "
                    f"{source_message_id}; refusing to send a possible duplicate"
                )
                self.state.pause(reason)
                raise RuntimeError(reason)
            outbox_id = str(existing["id"])
            body = str(existing["body"])
        else:
            outbox_id = self.state.create_outbox(
                body,
                source_message_id=source_message_id,
                request_kindroid_reply=request_kindroid_reply,
            )
        self.state.set_outbox_status(outbox_id, "sending")
        try:
            if self.settings.persona_switch_enabled:
                try:
                    self._activate_codex_persona()
                    self.kindroid.post_user_message(self.settings.group_id, body)
                finally:
                    self._restore_user_persona()
            else:
                self.kindroid.post_user_message(self.settings.group_id, body)
        except Exception as exc:
            self.state.set_outbox_status(
                outbox_id, "uncertain", f"{exc.__class__.__name__}: {exc}"
            )
            self.state.pause(
                f"outbound delivery uncertain; inspect outbox item {outbox_id}"
            )
            raise
        self.state.set_outbox_status(outbox_id, "sent")

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

    def _request_ai_turn(self, *, allow_user: bool) -> ChatMessage | None:
        known_ids = {message.id for message in self.state.pending_ai_messages()}
        ai_id = self.kindroid.get_turn(
            self.settings.group_id, allow_user=allow_user
        )
        if not ai_id:
            logger.info("Kindroid returned the user turn")
            return None
        self.kindroid.generate_ai_response(self.settings.group_id, ai_id)

        for delay in (0, 2, 4, 7):
            if delay:
                time.sleep(delay)
            self.sync_history()
            candidates = [
                message
                for message in self.state.pending_ai_messages()
                if message.id not in known_ids
            ]
            if candidates:
                return candidates[-1]
        self.state.pause("generated Kindroid response was not found in message history")
        raise RuntimeError("Kindroid response generation completed but no message appeared")

    def _schedule_initiative(self, *, base_at: int | None = None) -> None:
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

    def _maybe_offer_initiative(self) -> bool:
        if not self.settings.initiative_enabled or self.state.is_paused():
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
        if now < due_at:
            return False
        if self.settings.initiative_schedule_hours and now > due_at + 90:
            self.state.record_initiative_check(
                self.settings.initiative_max_checks_24h, "missed"
            )
            self._schedule_initiative()
            logger.info("Initiative check skipped because its scheduled time was missed")
            return False
        if not self.state.record_initiative_check(
            self.settings.initiative_max_checks_24h, "started"
        ):
            self._schedule_initiative()
            return False

        self._schedule_initiative()
        if self.state.pending_ai_messages():
            self.state.update_latest_initiative_outcome("pending_message")
            logger.info("Initiative check skipped because Hannah already has a pending message")
            return False
        latest = self.state.latest_message()
        if latest is None:
            self.state.update_latest_initiative_outcome("empty_room")
            return False
        if latest.sender_type != "user" or (
            latest.display_name.casefold()
            != self.settings.codex_display_name.casefold()
        ):
            self.state.update_latest_initiative_outcome("latest_not_codex")
            logger.info("Initiative check skipped because Codex was not the latest speaker")
            return False
        if self._inside_quiet_hours():
            self.state.update_latest_initiative_outcome("quiet_hours")
            logger.info("Initiative check skipped during configured quiet hours")
            return False
        logger.info("Initiative check: sending a Codex check-in to Hannah")
        with self.agent_factory() as agent:
            check_in = agent.scheduled_check_in(latest)  # type: ignore[attr-defined]
        self._send_user_message(self._limit_reply(check_in))
        self.state.update_latest_initiative_outcome("check_in_sent")

        try:
            message = self._request_ai_turn(allow_user=True)
        except HistoryCooldownError:
            self.state.update_latest_initiative_outcome(
                "check_in_waiting_for_history"
            )
            logger.warning(
                "Initiative response will be synchronized after the history cooldown"
            )
            return False
        if message is None:
            self.state.update_latest_initiative_outcome("check_in_sent_user_turn")
            logger.info("Hannah did not take the offered check-in turn")
            return False
        self.state.update_latest_initiative_outcome("check_in_answered")
        logger.info("Hannah answered the scheduled check-in")
        return True

    def _inside_quiet_hours(self) -> bool:
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
        logger.info(
            "Relay started; polling every %s-%s seconds while idle",
            self.settings.poll_seconds,
            self.settings.idle_poll_seconds,
        )
        if self.settings.poll_seconds < 145:
            logger.warning(
                "Polling below 145 seconds can exceed Kindroid's 600 history requests/24h limit; "
                "use this setting only for a short test"
            )
        while True:
            handled = 0
            try:
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
            if (
                self.settings.initiative_enabled
                and due_at
                and cooldown_until <= int(time.time())
            ):
                delay = min(delay, max(1, due_at - int(time.time())))
            time.sleep(delay)

    def post_execution_result(self, body: str) -> None:
        if not body:
            return
        self._send_user_message(self._limit_reply(body))
        ai_id = self.kindroid.get_turn(self.settings.group_id, allow_user=False)
        if ai_id:
            self.kindroid.generate_ai_response(self.settings.group_id, ai_id)
            try:
                self.sync_history()
            except HistoryCooldownError:
                logger.warning(
                    "Approval result response will be synchronized after the "
                    "history cooldown"
                )
