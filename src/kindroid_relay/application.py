from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
import time
from typing import Any

from .activity import ActivitySeverity, WorkerPhase
from .models import ExecutionResult
from .redaction import redact_text
from .relay import PhaseContext, Relay
from .state import RelayState
from .command_queue import CommandType, validate_payload


class ApprovalStateError(RuntimeError):
    pass


AgentFactory = Callable[[], AbstractContextManager[Any]]


class RelayApplication:
    """Reusable application operations shared by CLI and server workers."""

    def __init__(
        self,
        settings: Any,
        state: RelayState,
        kindroid: Any,
        agent_factory: AgentFactory,
        phase_context: PhaseContext | None = None,
    ) -> None:
        from .schedule import effective_settings
        settings = effective_settings(state, settings)
        self.settings = settings
        self.state = state
        self.agent_factory = agent_factory
        from .memory_service import MemoryService
        self.phase_context = phase_context or (lambda _phase, _note: nullcontext())
        self.memory = MemoryService(state, settings, phase_context=self.phase_context)
        self.relay = Relay(
            settings,
            state,
            kindroid,
            agent_factory,
            phase_context=self.phase_context,
        )
        from .exploration_response import ExplorationResponses
        self.exploration = ExplorationResponses(self)
        from .experience import Experiences
        from .discovery import DiscoveryStore
        self.experiences = Experiences(self)
        self.discovery = DiscoveryStore(state, settings.group_id)
        self.relay.context_operation = self._advance_context

    def _advance_context(self) -> bool:
        from .encounters import Encounters
        return (self.exploration.advance(synchronized=True) or self.experiences.advance()
                or Encounters(self.state, self.settings).finish_exploration(self))

    def bootstrap(
        self,
        *,
        respond_to_latest: bool = False,
        force: bool = False,
    ) -> int:
        return self.relay.bootstrap(
            respond_to_latest=respond_to_latest,
            force=force,
        )

    def run_once(self) -> int:
        if self.settings.room_mode == 'shared':
            from .shared import SharedConversation
            return int(SharedConversation(self).step())
        return self.relay.run_once()

    def sync_history(self, command_id: str) -> dict[str, Any]:
        """Worker-owned history read/reconciliation, even while paused; never reply."""
        from .kindroid_api import KindroidAPIError
        from .relay import HistoryCooldownError
        from .state import HistoryBudgetError
        from .turns import ReconciliationError, ReconciliationPending, reconciliation_diagnostic

        requests_before = self.state.history_request_count()
        def count_messages() -> int:
            return self.state.connection.execute(
                'SELECT COUNT(*) FROM messages WHERE group_id=?', (self.settings.group_id,)
            ).fetchone()[0]
        messages_before = count_messages()
        result: dict[str, Any] = {}
        try:
            if self.state.get_meta('bootstrapped', '0') != '1':
                self.relay.bootstrap()  # Initial import never schedules an old-chat reply.
                outcome = 'bootstrapped'
            else:
                self.relay.synchronize_context()
                outcome = 'synced'
        except HistoryCooldownError as exc:
            outcome = 'history_cooldown'
            result['retry_at'] = exc.retry_at
        except HistoryBudgetError:
            outcome = 'history_budget'
        except ReconciliationPending as exc:
            outcome = 'history_pending'
            result.update(reconciliation_diagnostic(exc))
        except ReconciliationError as exc:
            outcome = 'reconciliation_unresolved'
            result.update(reconciliation_diagnostic(exc))
        except KindroidAPIError:
            outcome = 'history_error'
        summaries = {
            'synced': 'Kindroid history synchronized; no reply requested',
            'bootstrapped': 'Initial Kindroid history imported without replaying old messages',
            'history_cooldown': 'History sync blocked by Kindroid API cooldown',
            'history_budget': 'History sync blocked by the rolling history-read budget',
            'history_pending': 'History fetched but a local message is not yet confirmed; sync remains incomplete',
            'reconciliation_unresolved': 'History fetched but delivery or generation remains ambiguous; inspect before resuming',
            'history_error': 'Kindroid history request failed; no automatic retry',
        }
        result.update(sync_outcome=outcome, messages_added=count_messages() - messages_before,
                      history_requests=max(0, self.state.history_request_count() - requests_before),
                      synced_at=self.state.get_int('context_synced_at', 0), paused=self.state.is_paused())
        self.state.activity.add('history_sync',
            ActivitySeverity.INFO if outcome in {'synced', 'bootstrapped'} else ActivitySeverity.WARNING,
            summaries[outcome] + ('; ' + result['reason_detail'] if 'reason_detail' in result else ''),
            details={'command_id': command_id, 'outcome': outcome,
                                        'request_count': result['history_requests']}, correlation_id=command_id)
        return result

    def trigger_initiative(self) -> dict[str, Any]:
        if self.settings.room_mode == 'shared':
            return {'handled': 0, 'initiative_outcome': 'shared_room', 'check_in_sent': False}
        handled = self.relay.trigger_initiative_now()
        outcome = self.relay.initiative_outcome or "unknown"
        return {
            "handled": handled,
            "initiative_outcome": outcome,
            "check_in_sent": outcome in {
                "check_in_sent", "check_in_answered", "check_in_sent_user_turn",
                "check_in_waiting_for_history",
            },
        }

    def start_conversation(self) -> int:
        if self.settings.room_mode == 'shared':
            import uuid
            from .shared import SharedConversation
            return int(SharedConversation(self).start(uuid.uuid4().hex)['outcome'] == 'started')
        return self.relay.start_conversation_now()

    def start_scheduled_exploration(self, command_id: str) -> dict[str, Any]:
        if self.settings.participant_scheduling:
            return self.start_scheduled_check_in(command_id)
        from .curiosity import CuriosityScheduler
        scheduler = CuriosityScheduler(self.state, self.settings)
        reason = scheduler.guard(command_id)
        if reason:
            scheduler.finish(command_id, reason)
            return {'scheduled_outcome': reason, 'invitations': 0}
        try:
            result = self.start_exploration(command_id, scheduled=True)
        except Exception:
            scheduler.finish(command_id, 'failed_inspect_delivery')
            raise
        outcome = result.get('scheduled_outcome', 'invitation_sent')
        scheduler.finish(command_id, outcome)
        return {**result, 'scheduled_outcome': outcome}

    def start_scheduled_check_in(self, command_id: str) -> dict[str, Any]:
        from .schedule import DailyScheduler
        from .relay import HistoryCooldownError
        scheduler = DailyScheduler(self.state, self.settings)
        reason = scheduler.guard(command_id)
        if reason:
            scheduler.finish(command_id, reason)
            return {'scheduled_outcome': reason, 'check_in_sent': False}
        self.relay.scheduled_check_in_guard = lambda: scheduler.guard(command_id, synchronized=True)
        try:
            def after_sync() -> bool:
                reason = scheduler.guard(command_id, synchronized=True)
                if reason:
                    self.relay._skip_initiative(reason, 'Daily tick skipped after synchronization', persist=False)
                return bool(reason)
            handled = self.relay.run_once(initiative_now=True, after_sync=after_sync)
            outcome = self.relay.initiative_outcome or 'unknown'
            result = {'handled': handled, 'scheduled_outcome': outcome,
                'check_in_sent': outcome.startswith('check_in_')}
        except HistoryCooldownError:
            result = {'scheduled_outcome': 'history_cooldown', 'check_in_sent': False}
        except Exception:
            scheduler.finish(command_id, 'failed_inspect_delivery')
            raise
        finally:
            self.relay.scheduled_check_in_guard = None
        scheduler.finish(command_id, result['scheduled_outcome'])
        return result

    def start_exploration(self, command_id: str, *, scheduled: bool = False) -> dict[str, Any]:
        if self.settings.room_mode == 'shared':
            raise ValueError('Shared rooms use companion sessions; exploration invitations belong to private encounters')
        from .agency import AgencyStore, invitation, select_stimuli
        if not self.settings.exploration_enabled:
            raise ValueError('Exploration is disabled pending operator acceptance')
        if self.state.is_paused():
            raise ValueError('Resume the relay before starting exploration')
        if self.state.get_meta('bootstrapped', '0') != '1':
            raise ValueError('Bootstrap the conversation before starting exploration')
        from .relay import HistoryCooldownError
        try:
            self.relay.synchronize_context()
        except HistoryCooldownError:
            if scheduled:
                return {'scheduled_outcome': 'history_cooldown', 'invitations': 0}
            raise
        if scheduled:
            from .curiosity import CuriosityScheduler
            reason = CuriosityScheduler(self.state, self.settings).guard(command_id, synchronized=True)
            if reason:
                return {'scheduled_outcome': reason, 'invitations': 0}
        if self.state.pending_ai_messages():
            raise ValueError('Process pending conversation messages before exploration')
        if self.relay._inside_quiet_hours():
            raise ValueError('Exploration is unavailable during quiet hours')
        store = AgencyStore(self.state, self.settings.group_id, self.settings.kindroid_display_name)
        previous = [r[0] for r in self.state.connection.execute(
            'SELECT locator FROM agency_stimuli WHERE group_id=? AND retrieved_at>? ORDER BY retrieved_at DESC,rowid DESC LIMIT 60',
            (self.settings.group_id, int(time.time()) - 604800))]
        context = self.memory.context('interest question curiosity', mode='explorer')
        memory = {'items': context['dynamic']['observations'], 'status': context['dynamic']['memory_status']}
        stimuli, inputs = select_stimuli(memory, command_id, previous, context['dynamic']['candidates'])
        stimuli, discovery_inputs = self.discovery.select(stimuli, command_id, previous)
        inputs.update(discovery_inputs)
        if not stimuli:
            self.state.activity.add('discovery_source', ActivitySeverity.INFO,
                'No fresh discovery stimuli; no invitation sent', details={'outcome': 'no_fresh_stimuli'})
            return {'scheduled_outcome': 'no_fresh_stimuli', 'invitations': 0}
        inputs['context_truncated'] = context['truncated']
        inputs['recent_message_ids'] = [m['remote_id'] for m in context['dynamic']['recent']]
        body = invitation(stimuli)
        if len(body) > self.settings.max_reply_chars:
            raise ValueError('Exploration invitation exceeds configured reply limit')
        if scheduled:
            reason = CuriosityScheduler(self.state, self.settings).guard(command_id, synchronized=True)
            if reason:
                return {'scheduled_outcome': reason, 'invitations': 0}
        store.prepare(command_id, stimuli, inputs, self.settings.exploration_max_sessions_24h)
        self.experiences.store.authorize_invitation(command_id)
        if self.settings.exploration_auto_interpret:
            self.exploration.store.enable(command_id, command_id)
        try:
            outbox_id = self.relay._send_user_message(body, request_kindroid_reply=False,
                outbox_created=lambda oid: store.bind_outbox(command_id, oid))
            incoming = self.relay._request_ai_turn(outbox_id=outbox_id, allow_user=True)
            store.finish(command_id, incoming.id if incoming else None)
            store.reconcile()  # No history reads; direct replies may still be provisional.
        except Exception:
            with self.state.connection:
                self.state.connection.execute("UPDATE exploration_sessions SET status=CASE WHEN outbox_id IS NULL THEN 'interrupted' ELSE 'uncertain' END WHERE id=?", (command_id,))
            self.state.pause('Exploration interrupted; inspect its session and delivery before resuming')
            raise
        self.exploration.advance()
        return {'session_id': command_id, 'status': store._session(command_id)['status'], 'invitations': 1}

    def interpret_exploration(self, command_id: str, session_id: str) -> dict[str, Any]:
        if not self.settings.exploration_enabled or self.state.is_paused():
            raise ValueError('Exploration must be enabled and relay resumed')
        self.exploration.store.enable(session_id, command_id)
        self.exploration.advance()
        row = self.exploration.store.row(session_id, 0)
        return {'session_id': session_id, 'response_status': row['status']}

    def set_initiative_limit(self, limit: int) -> dict[str, Any]:
        limit = validate_payload(CommandType.SET_INITIATIVE_LIMIT, {"limit": limit})["limit"]
        previous = self.state.initiative_limit(self.settings.initiative_max_checks_24h)
        with self.state.connection:
            self.state.connection.execute(
                "INSERT INTO metadata(key, value) VALUES('initiative_limit_override', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(limit),)
            )
            self.state.activity.add(
                "initiative_limit_changed", ActivitySeverity.INFO,
                f"Daily check-in limit changed from {previous} to {limit}",
                details={"previous_limit": previous, "limit": limit},
            )
        return {"limit": limit, "previous_limit": previous}

    def run_forever(self) -> None:
        self.relay.run_forever()

    def execute_approval(
        self,
        approval_id: str,
        *,
        post_result: bool = False,
    ) -> ExecutionResult:
        row = self.state.get_approval(approval_id)
        if row is None or row["status"] != "pending":
            raise ApprovalStateError("approval is missing or not pending")
        # Context acquisition is read-only and precedes the executing transition.
        # A cooldown or unresolved effect leaves the approval pending.
        self.relay.synchronize_context()
        self.state.update_approval(approval_id, "executing")
        self.state.activity.add(
            "approval_status",
            ActivitySeverity.INFO,
            "Approval execution started",
            details={"approval_id": approval_id, "status": "executing"},
            correlation_id=approval_id,
        )
        self.state.activity.add(
            "codex_started",
            ActivitySeverity.INFO,
            "Codex started approved execution",
            details={"approval_id": approval_id},
            correlation_id=approval_id,
        )
        try:
            with self.phase_context(
                WorkerPhase.RUNNING_CODEX, "Executing approved Codex action"
            ):
                with self.agent_factory() as agent:
                    result = agent.execute_approved(
                        row["id"],
                        row["summary"],
                        row["instruction"],
                        row["risk"],
                    )
        except Exception as exc:
            safe_error = redact_text(exc, maximum=500)
            self.state.update_approval(approval_id, "failed", safe_error)
            self.state.activity.add(
                "codex_failed",
                ActivitySeverity.ERROR,
                "Codex approved execution failed",
                details={
                    "approval_id": approval_id,
                    "error_type": exc.__class__.__name__,
                },
                correlation_id=approval_id,
            )
            self.state.activity.add(
                "approval_status",
                ActivitySeverity.ERROR,
                "Approval execution failed",
                details={"approval_id": approval_id, "status": "failed"},
                correlation_id=approval_id,
            )
            raise
        self.state.activity.add(
            "codex_completed",
            ActivitySeverity.INFO,
            "Codex approved execution completed",
            details={"approval_id": approval_id},
            correlation_id=approval_id,
        )
        status = "executed" if result.success else "failed"
        self.state.update_approval(approval_id, status, result.summary)
        self.state.activity.add(
            "approval_status",
            ActivitySeverity.INFO if result.success else ActivitySeverity.ERROR,
            f"Approval execution {status}",
            details={"approval_id": approval_id, "status": status},
            correlation_id=approval_id,
        )
        if post_result and result.group_reply:
            self.relay.post_execution_result(result.group_reply)
        return result


def pause_relay(state: RelayState, reason: str) -> dict[str, object]:
    was_paused = state.is_paused()
    state.pause(reason)
    if not was_paused:
        state.activity.add(
            "relay_paused",
            ActivitySeverity.WARNING,
            "Relay paused",
            details={"reason": reason},
        )
    return state.status()


def resume_relay(state: RelayState) -> dict[str, object]:
    was_paused = state.is_paused()
    state.resume()
    if was_paused:
        state.activity.add(
            "relay_resumed",
            ActivitySeverity.INFO,
            "Relay resumed",
        )
    return state.status()


def reject_approval(state: RelayState, approval_id: str) -> None:
    row = state.get_approval(approval_id)
    if row is None or row["status"] != "pending":
        raise ApprovalStateError("approval is missing or not pending")
    state.update_approval(approval_id, "rejected")
    state.activity.add(
        "approval_status",
        ActivitySeverity.INFO,
        "Approval rejected",
        details={"approval_id": approval_id, "status": "rejected"},
        correlation_id=approval_id,
    )
