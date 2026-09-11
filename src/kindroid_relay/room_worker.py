"""Fair, serialized worker across isolated room stores."""
from contextlib import ExitStack
import time

from .activity import ActivitySeverity, WorkerPhase
from .encounters import Encounters
from .schedule import DailyScheduler, effective_settings
from .curiosity import CuriosityScheduler
from .shared import SharedConversation
from .state import RelayStateFactory
from .relay import HistoryCooldownError, Relay
from .turns import ReconciliationPending
from .worker import RelayWorker
from .quiet_hours import QuietHours, deferred_commands


class RoomWorker(RelayWorker):
    def __init__(self, rooms, **kwargs):
        super().__init__(rooms[0], **kwargs)
        self.rooms = rooms

    def run(self):
        with ExitStack() as stack:
            contexts = []
            for settings in self.rooms:
                state = stack.enter_context(RelayStateFactory(settings.state_path, settings.account_state_path).connect())
                stored_group = state.get_meta('group_id')
                groups = {r[0] for r in state.connection.execute('SELECT DISTINCT group_id FROM messages')}
                if (stored_group and stored_group != settings.group_id) or groups - {settings.group_id}:
                    raise ValueError('Room database belongs to another group')
                worker = RelayWorker(settings, wake_event=self.wake_event, stop_event=self.stop_event)
                worker._phase(state, WorkerPhase.STARTING)
                state.activity.prune(settings.activity_retention_days)
                worker._recover(state)
                app = stack.enter_context(worker._application(state))
                contexts.append([worker, app, state, 0.0])
            try:
                blocked_until = {}
                while not self.stop_event.is_set():
                    worked = False
                    for entry in contexts:
                        if self.stop_event.is_set():
                            break
                        worker, app, state, next_poll = entry
                        state.worker_status.heartbeat()
                        app.settings = effective_settings(state, worker.settings)
                        app.relay.settings = app.settings
                        try:
                            quiet = QuietHours(state, app.settings).blocked()
                            if worker._sync_startup_history(state, app):
                                worked = True
                                continue
                            command = state.commands.claim_next(exclude=deferred_commands() if quiet else frozenset())
                            if command:
                                worker._run_command(state, app, command)
                                entry[3] = time.time() + (0 if app.settings.room_mode == 'shared' else app.settings.poll_seconds)
                                worked = True
                                blocked_until.pop(worker.settings.room_key, None)
                                continue
                            if state.is_paused():
                                worker._phase(state, WorkerPhase.PAUSED)
                                continue
                            if quiet:
                                worker._phase(state, WorkerPhase.IDLE, note='Companion quiet hours; automatic work is waiting')
                                continue
                            if time.time() < blocked_until.get(worker.settings.room_key, 0):
                                continue
                            shared = SharedConversation(app)
                            encounters = Encounters(state, app.settings)
                            if encounters.tick() or (not app.settings.participant_scheduling and (
                                    shared.tick() or DailyScheduler(state, app.settings).tick() or CuriosityScheduler(state, app.settings).tick())):
                                worked = True
                                continue
                            if app.settings.room_mode == 'shared':
                                if time.time() >= entry[3]:
                                    worked = shared.step() or worked
                                continue
                            if state.has_active_conversation_work():
                                if state.get_int('history_cooldown_until') > time.time():
                                    continue
                                worker._phase(state, WorkerPhase.POLLING)
                                handled = app.run_once()
                                entry[3] = time.time() + worker._poll_delay(handled)
                                worked = True
                            else:
                                worker._phase(state, WorkerPhase.IDLE, note='Waiting for a scheduled check-in or manual start; chat monitoring is off')
                                choice = encounters.current()
                                next_encounter = choice['due_at'] if choice and choice['status'] in {'pending', 'queued', 'missed'} else None
                                if (app.settings.memory_enabled and (not next_encounter or
                                        time.time() + app.settings.memory_extraction_timeout_seconds < next_encounter)):
                                    try:
                                        app.memory.automatic_tick()
                                    except Exception:
                                        pass  # Optional capture cannot pause normal conversation.
                        except (HistoryCooldownError, ReconciliationPending):
                            entry[3] = max(time.time() + app.settings.poll_seconds,
                                           state.get_int('history_cooldown_until'))
                            blocked_until[worker.settings.room_key] = entry[3]
                            worker._phase(state, WorkerPhase.DEGRADED, note='Waiting for history allowance or confirmation')
                        except Exception as exc:
                            # A failed room cannot monopolize the worker or expose remote bodies.
                            if isinstance(app.relay, Relay):
                                app.relay._end_activity_session('failed_inspect_activity')
                            state.activity.add('room_work_failed', ActivitySeverity.ERROR,
                                'Room work failed; inspect its state before resuming',
                                details={'error_type': type(exc).__name__})
                            if not state.is_paused():
                                state.pause('Room work failed; inspect history, delivery and activity before resuming')
                            worker._phase(state, WorkerPhase.DEGRADED)
                    self.wake_event.wait(0.05 if worked else 1.0)
                    self.wake_event.clear()
            finally:
                for worker, app, state, _ in contexts:
                    worker._phase(state, WorkerPhase.STOPPING)
