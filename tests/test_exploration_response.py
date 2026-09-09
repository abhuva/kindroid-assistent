import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kindroid_relay.agency import AgencyStore
from kindroid_relay.command_queue import CommandType, CommandStatus
from kindroid_relay.exploration_response import ResponseStore, ResponseInterpreter, validate_decision, VERSION
from kindroid_relay.models import ChatMessage
from kindroid_relay.memory_extractor import CodexExtractionRunner
from kindroid_relay.worker import RelayWorker
from kindroid_relay.backup import backup_database
from kindroid_relay.diagnostics import export_diagnostics
from kindroid_relay.state import RelayState
from test_agency import setup, command
from test_web import dashboard, MUTATION_HEADERS


@pytest.fixture(autouse=True)
def forbid_live_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unmocked Codex call is forbidden')
    monkeypatch.setattr(CodexExtractionRunner, 'run', forbidden)


class Interpreter:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def interpret(self, source, candidates, context):
        self.calls.append((source, candidates, context))
        outcome = next(self.outcomes)
        return {'classification': outcome, 'candidate_ids': [candidates[0]['id']] if outcome in {'choose', 'defer'} else [],
                'quote': source['body'], 'interpretation': 'Source expresses a response to the invitation, not identity adoption.',
                'alternative_title': 'Exploring tide pools' if outcome == 'alternative' else ''}


def scenario(tmp_path, monkeypatch, outcomes=('choose',), replies=('I would like the first one.',), **settings):
    config, state, remote, app = setup(tmp_path, exploration_auto_interpret=True, **settings)
    bodies = iter(replies)
    original = remote.generate_ai_response
    def generate(*args):
        original(*args)
        body = next(bodies)
        remote.messages[-1] = replace(remote.messages[-1], message=body)
        return body
    monkeypatch.setattr(remote, 'generate_ai_response', generate)
    interpreter = Interpreter(outcomes)
    app.exploration.interpreter = interpreter
    return config, state, remote, app, interpreter


def run_offer(state, app):
    record = command(state)
    state.commands.claim_next()
    result = app.start_exploration(record.id)
    state.commands.transition(record.id, CommandStatus.COMPLETED, result=result)
    return record.id


def test_choice_links_evidence_and_acknowledges_once(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        row = app.exploration.store.row(session, 0)
        assert row['classification'] == 'choose' and row['status'] == 'complete'
        assert len(remote.sent) == 2 and len(interpreter.calls) == 1
        assert remote.turn_requests == [True]  # acknowledgement never generates a reply
        assert 'Passing seed' in remote.sent[-1] and 'No research' in remote.sent[-1]
        assert state.connection.execute('SELECT COUNT(*) FROM candidate_responses').fetchone()[0] == 1
        detail = AgencyStore(state, 'g1').snapshot()
        linked = next(c for c in detail['candidates'] if c['responses'])
        assert linked['origin_actor'] == 'codex' and linked['responses'][0]['reaction'] == 'choose'
        assert detail['sessions'][0]['responses'][0]['decision']['quote'] == 'I would like the first one.'
        assert state.outbox_for_source(row['message_id'])['id'] == row['outbox_id']
        app.run_once()
        app.run_once()
        assert len(remote.sent) == 2 and len(interpreter.calls) == 1
        assert state.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0
        assert not state.get_meta('codex_thread_id') and not state.pending_ai_messages()


def test_alternative_creates_attributed_provisional_seed(tmp_path, monkeypatch):
    _, state, remote, app, _ = scenario(tmp_path, monkeypatch, outcomes=('alternative',), replies=('Could we explore tide pools instead?',))
    with state:
        session = run_offer(state, app)
        snap = AgencyStore(state, 'g1').snapshot()
        alternative = next(c for c in snap['candidates'] if c['origin_actor'] == 'hannah')
        assert alternative['summary'] == 'Exploring tide pools'
        assert alternative['responses'][0]['reaction'] == 'alternative'
        event = next(e for e in snap['sessions'][0]['events'] if e['kind'] == 'alternative_reported')
        assert event['actor'] == 'hannah' and event['statement_id']
        assert len(remote.sent) == 2 and len(snap['candidates']) == 4
        assert app.exploration.store.row(session, 0)['status'] == 'complete'


@pytest.mark.parametrize('outcome,body', [('decline', 'No thanks, none of these.'), ('defer', 'Perhaps the first one another day.')])
def test_decline_and_defer_do_not_pressure_a_reply(tmp_path, monkeypatch, outcome, body):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, outcomes=(outcome,), replies=(body,))
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'complete'
        assert len(remote.sent) == 1 and len(interpreter.calls) == 1
        assert state.connection.execute('SELECT COUNT(*) FROM candidate_responses').fetchone()[0] == int(outcome == 'defer')


def test_unclear_gets_one_clarification_then_source_backed_choice(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, outcomes=('unclear', 'choose'),
                                                  replies=('That sounds nice.', 'I meant the first option.'))
    with state:
        session = run_offer(state, app)
        assert len(remote.sent) == 2
        assert remote.turn_requests == [True, True]
        assert not state.pending_ai_messages()
        app.run_once()
        assert len(remote.sent) == 3 and len(interpreter.calls) == 2
        assert app.exploration.store.row(session, 1)['classification'] == 'choose'
        assert interpreter.calls[1][2]['earlier_response'] == 'That sounds nice.'
        app.run_once()
        assert len(remote.sent) == 3 and not state.pending_ai_messages()


def test_second_ambiguity_stops_and_clarification_alternative_has_own_source(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, outcomes=('unclear', 'unclear'), replies=('Maybe.', 'Still unsure.'))
    with state:
        session = run_offer(state, app)
        app.run_once()
        assert app.exploration.store.row(session, 1)['status'] == 'needs_review'
        assert len(remote.sent) == 2 and len(interpreter.calls) == 2


def test_alternative_from_clarification_keeps_hannah_attribution(tmp_path, monkeypatch):
    _, state, _, app, _ = scenario(tmp_path, monkeypatch, outcomes=('unclear', 'alternative'), replies=('Maybe.', 'Actually, tide pools instead.'))
    with state:
        session = run_offer(state, app)
        app.run_once()
        row = app.exploration.store.row(session, 1)
        assert row['classification'] == 'alternative' and row['status'] == 'complete'
        evidence = state.connection.execute('SELECT message_id,quote FROM memory_evidence WHERE statement_id=?', (row['statement_id'],)).fetchone()
        assert evidence['message_id'] == row['message_id'] and evidence['quote'] == 'Actually, tide pools instead.'


def test_old_sessions_require_explicit_interpret_command_and_never_resend_invitation(tmp_path, monkeypatch):
    _, state, remote, app = setup(tmp_path)
    app.exploration.interpreter = Interpreter(('choose',))
    with state:
        session = run_offer(state, app)
        app.run_once()
        assert app.exploration.store.row(session, 0) is None
        record = command(state, CommandType.INTERPRET_EXPLORATION, {'session_id': session}, key='interpret')
        app.interpret_exploration(record.id, session)
        assert len(remote.sent) == 2
        app.interpret_exploration(record.id, session)
        assert len(remote.sent) == 2


@pytest.mark.parametrize('mutate', [
    lambda d: {**d, 'candidate_ids': ['other-session-candidate']},
    lambda d: {**d, 'quote': 'fabricated'},
    lambda d: {**d, 'classification': 'promote_root'},
    lambda d: {**d, 'execute': 'publish'},
    lambda d: {**d, 'candidate_ids': []},
])
def test_invalid_model_output_fails_atomically_without_retry(tmp_path, monkeypatch, mutate):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = interpreter.interpret
    interpreter.interpret = lambda *args: mutate(original(*args))
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'failed'
        assert state.connection.execute('SELECT COUNT(*) FROM candidate_responses').fetchone()[0] == 0
        assert len(remote.sent) == 1
        app.run_once()
        assert len(interpreter.calls) == 1


def test_source_change_during_interpretation_rolls_back(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = interpreter.interpret
    def change(source, *args):
        result = original(source, *args)
        with state.connection:
            state.connection.execute('UPDATE messages SET body=? WHERE remote_id=?', ('Changed source', source['remote_id']))
        return result
    interpreter.interpret = change
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'failed'
        assert not state.connection.execute('SELECT 1 FROM candidate_responses').fetchone()
        assert len(remote.sent) == 1


def test_unknown_speaker_and_oversized_response_are_not_interpreted(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, replies=('x' * 8001,))
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'needs_review'
        assert interpreter.calls == [] and len(remote.sent) == 1


def test_interrupted_acknowledgement_never_replays(tmp_path, monkeypatch):
    config, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = remote.post_user_message
    def uncertain(*args):
        if remote.sent:
            raise TimeoutError('PRIVATE-FAILURE-FIXTURE')
        return original(*args)
    monkeypatch.setattr(remote, 'post_user_message', uncertain)
    with state:
        session = run_offer(state, app)
        row = app.exploration.store.row(session, 0)
        assert row['status'] == 'uncertain' and row['outbox_id']
        assert state.outbox_for_source(row['message_id'])['status'] == 'uncertain'
        assert state.is_paused()
        assert 'PRIVATE-FAILURE-FIXTURE' not in row['note']
        RelayWorker(config)._recover(state)
        app.run_once()
        assert len(remote.sent) == 1 and len(interpreter.calls) == 1


def test_pause_after_interpretation_retains_choice_without_sending(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = interpreter.interpret
    def pause(*args):
        result = original(*args)
        state.pause('fixture')
        return result
    interpreter.interpret = pause
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'ready'
        assert len(remote.sent) == 1
        state.resume()
        app.run_once()
        assert len(remote.sent) == 2 and len(interpreter.calls) == 1


def test_interpret_api_is_worker_owned_and_idempotent(tmp_path):
    _, factory, _, client = dashboard(tmp_path, exploration_enabled=True)
    payload = {'idempotency_key': 'interpret', 'payload': {'session_id': 'old-session'}}
    assert client.post('/api/v1/exploration/interpret', json=payload).status_code == 403
    first = client.post('/api/v1/exploration/interpret', json=payload, headers=MUTATION_HEADERS)
    assert first.status_code == 202
    assert client.post('/api/v1/exploration/interpret', json=payload, headers=MUTATION_HEADERS).json()['id'] == first.json()['id']
    with factory.connect() as state:
        assert state.connection.execute('SELECT COUNT(*) FROM exploration_responses').fetchone()[0] == 0
        assert state.connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_backup_keeps_interpretations_and_diagnostics_exclude_quotes(tmp_path, monkeypatch):
    private = 'PRIVATE-RESPONSE-FIXTURE: I choose the first option.'
    config, state, _, app, _ = scenario(tmp_path, monkeypatch, replies=(private,))
    with state:
        session = run_offer(state, app)
        destination = backup_database(config.state_path, tmp_path / 'backup')
        exported = export_diagnostics(config.state_path, config.log_path, tmp_path / 'diagnostics.json')
        assert private not in exported.read_text(encoding='utf-8')
    with RelayState(destination / 'state.sqlite3') as restored:
        assert json.loads(ResponseStore(restored, 'g1', 'Hannah').row(session, 0)['decision_json'])['quote'] == private
        assert restored.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_response_interpreter_uses_isolated_runtime_contract(tmp_path, monkeypatch):
    from test_relay import settings
    interpreter = ResponseInterpreter(settings(tmp_path))
    assert interpreter.runner.instructions.startswith('Codex is a separate capability provider')
    assert interpreter.runner.schema['additionalProperties'] is False
    assert set(interpreter.runner.schema['properties']['classification']['enum']) == {'choose', 'decline', 'defer', 'alternative', 'unclear'}
    captured = {}
    def run(envelope):
        captured.update(envelope)
        return {'classification': 'unclear'}
    monkeypatch.setattr(interpreter.runner, 'run', run)
    interpreter.interpret({'remote_id': 'source', 'sender': 'ai1', 'display_name': 'Hannah', 'body': 'Unclear'}, [], {})
    assert captured['response']['body'] == 'Unclear'
    assert 'context_only' in captured


def test_role_only_speaker_is_attributed_without_review(tmp_path, monkeypatch):
    _, state, remote, app = setup(tmp_path)
    app.exploration.interpreter = Interpreter(('choose',))
    with state:
        session = run_offer(state, app)
        remote.messages[-1] = replace(remote.messages[-1], sender='ai')
        app.relay.synchronize_context()
        record = command(state, CommandType.INTERPRET_EXPLORATION, {'session_id': session}, key='interpret')
        app.interpret_exploration(record.id, session)
        assert app.exploration.store.row(session, 0)['status'] == 'complete'
        assert len(app.exploration.interpreter.calls) == 1 and len(remote.sent) == 2
        app.run_once()
        assert len(remote.sent) == 2  # never repeat the acknowledgement


@pytest.mark.parametrize('configured_name', ['Hannah', 'Kindroid'])
@pytest.mark.parametrize('outcome', ['choose', 'alternative', 'unclear'])
@pytest.mark.parametrize('empty_generation', [False, True])
def test_generic_ai_history_auto_flow_keeps_hannah_provenance(tmp_path, monkeypatch, configured_name, outcome, empty_generation):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch,
        outcomes=(outcome, 'alternative'), replies=('The first sounds interesting.', 'Tide pools instead.'),
        kindroid_display_name=configured_name)
    original = remote.generate_ai_response
    def generate(*args):
        body = original(*args)
        remote.messages[-1] = replace(remote.messages[-1], sender='ai')
        return '' if empty_generation else body
    monkeypatch.setattr(remote, 'generate_ai_response', generate)
    with state:
        # Model the deployed endpoint: all confirmed AI history uses the role.
        remote.messages[0] = replace(remote.messages[0], sender='ai')
        with state.connection:
            state.connection.execute("UPDATE messages SET sender='ai' WHERE sender_type='ai'")
        session = run_offer(state, app)
        app.run_once()
        step = int(outcome == 'unclear')
        assert app.exploration.store.row(session, step)['status'] == 'complete'
        assert len(interpreter.calls) == 1 + step
        assert len(remote.sent) == 2 + step
        events = state.connection.execute("SELECT actor,kind FROM agency_events WHERE session_id=?", (session,)).fetchall()
        assert [e['actor'] for e in events if e['kind'] == 'response_observed'] == ['hannah']
        if outcome != 'choose':
            assert [e['actor'] for e in events if e['kind'] == 'alternative_reported'] == ['hannah']
        source = app.exploration.store.source(app.exploration.store.row(session, step))
        assert source['sender'] == 'ai' and source['kindroid_id']  # raw metadata not rewritten
        assert state.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0
        app.run_once()
        assert len(remote.sent) == 2 + step


@pytest.mark.parametrize('conflict', ['other_name', 'other_id', 'user', 'codex', 'empty_sender',
                                    'wrong_group', 'wrong_id', 'wrong_name', 'configured_conflict',
                                    'unconfirmed', 'unlinked', 'unreconciled'])
def test_role_only_attribution_rejects_conflicts(tmp_path, monkeypatch, conflict):
    _, state, remote, app = setup(tmp_path)
    with state:
        session = run_offer(state, app)
        remote.messages[-1] = replace(remote.messages[-1], sender='ai')
        app.relay.synchronize_context()
        agency = app.exploration.store.agency
        turn = state.turns.for_outbox(agency._session(session)['outbox_id'])
        source = dict(state.connection.execute('SELECT * FROM messages WHERE remote_id=?', (turn['id'],)).fetchone())
        assert agency.trusted_response_speaker(source, turn['ai_id'])
        if conflict in {'other_name', 'other_id'}:
            state.add_message('g1', ChatMessage('conflict', 'ai' if conflict == 'other_name' else 'ai2',
                'ai', 'Another companion' if conflict == 'other_name' else 'Hannah', 1, 'Fixture'), processed=True)
        elif conflict == 'user': source['sender_type'] = 'user'
        elif conflict == 'codex': source['body'] = '  [CODEX]: Not Hannah'
        elif conflict == 'empty_sender': source['sender'] = ''
        elif conflict == 'wrong_group': source['group_id'] = 'elsewhere'
        elif conflict == 'wrong_id': source['sender'] = 'ai2'
        elif conflict == 'wrong_name': source['display_name'] = 'Another companion'
        elif conflict == 'configured_conflict': agency.hannah_name = 'Another companion'
        elif conflict == 'unconfirmed': source['kindroid_id'] = None
        elif conflict == 'unlinked': source['remote_id'] = 'unrelated'
        elif conflict == 'unreconciled':
            with state.connection:
                state.connection.execute("UPDATE generation_turns SET status='received' WHERE id=?", (turn['id'],))
        assert not agency.trusted_response_speaker(source, turn['ai_id'])


def test_role_only_review_does_not_replay_old_sessions(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, replies=('x' * 8001,))
    with state:
        session = run_offer(state, app)
        app.run_once()
        assert app.exploration.store.row(session, 0)['status'] == 'needs_review'
        assert not interpreter.calls and len(remote.sent) == 1


def test_silence_and_silent_clarification_are_terminal(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, outcomes=('unclear',), replies=('Maybe.',))
    original = remote.get_turn
    def turn(*args, **kwargs):
        return original(*args, **kwargs) if len(remote.sent) == 1 else None
    monkeypatch.setattr(remote, 'get_turn', turn)
    with state:
        session = run_offer(state, app)
        assert app.exploration.store.row(session, 0)['status'] == 'complete'
        assert app.exploration.store.row(session, 1) is None
        app.run_once()
        assert len(remote.sent) == 2 and len(interpreter.calls) == 1


def test_later_conversation_retains_choice_without_stale_ack(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = interpreter.interpret
    def conversation_moves(*args):
        decision = original(*args)
        timestamp = state.latest_message().timestamp + 1
        state.add_message('g1', ChatMessage('later', 'user', 'user', 'Marc', timestamp, 'Different subject now.'), processed=True)
        return decision
    interpreter.interpret = conversation_moves
    with state:
        session = run_offer(state, app)
        row = app.exploration.store.row(session, 0)
        assert row['status'] == 'needs_review' and row['classification'] == 'choose'
        assert state.connection.execute('SELECT COUNT(*) FROM candidate_responses').fetchone()[0] == 1
        assert len(remote.sent) == 1


def test_cross_group_cannot_enable_existing_response(tmp_path, monkeypatch):
    _, state, _, app, _ = scenario(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        with pytest.raises(ValueError, match='conversation'):
            ResponseStore(state, 'other', 'Hannah').enable(session, session)


def test_followup_interruption_recovery_pauses_and_retains_links(tmp_path, monkeypatch):
    config, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        with state.connection:
            state.connection.execute("UPDATE exploration_responses SET status='sending' WHERE session_id=?", (session,))
        RelayWorker(config)._recover(state)
        assert state.is_paused()
        assert app.exploration.store.row(session, 0)['status'] == 'uncertain'
        state.resume()
        app.run_once()  # confirms the previously sent acknowledgement, not a resend
        assert app.exploration.store.row(session, 0)['status'] == 'complete'
        assert len(remote.sent) == 2 and len(interpreter.calls) == 1


def test_model_failure_is_sanitized_and_does_not_pause_chat(tmp_path, monkeypatch):
    _, state, remote, app, _ = scenario(tmp_path, monkeypatch)
    def failed(*args):
        raise TimeoutError('PRIVATE-MODEL-ERROR')
    app.exploration.interpreter.interpret = failed
    with state:
        session = run_offer(state, app)
        row = app.exploration.store.row(session, 0)
        assert row['status'] == 'failed' and 'PRIVATE-MODEL-ERROR' not in row['note']
        assert not state.is_paused() and len(remote.sent) == 1


def test_retracted_interpretation_is_not_acknowledged_after_resume(tmp_path, monkeypatch):
    _, state, remote, app, interpreter = scenario(tmp_path, monkeypatch)
    original = interpreter.interpret
    def pause(*args):
        result = original(*args)
        state.pause('fixture correction')
        return result
    interpreter.interpret = pause
    with state:
        session = run_offer(state, app)
        row = app.exploration.store.row(session, 0)
        app.exploration.store.agency.memory.revise('revise', {
            'statement_id': row['statement_id'], 'status': 'retracted', 'interpretation': 'Wrong interpretation',
            'temporal_scope': '', 'reason': 'Operator reviewed source'}, 'correction')
        state.resume()
        app.run_once()
        assert app.exploration.store.row(session, 0)['status'] == 'needs_review'
        assert len(remote.sent) == 1
        candidate = next(c for c in AgencyStore(state, 'g1').snapshot()['candidates'] if c['responses'])
        assert candidate['responses'][0]['observation_status'] == 'retracted'


def test_closing_uncertain_clarification_consumes_late_reply(tmp_path, monkeypatch):
    _, state, remote, app, _ = scenario(tmp_path, monkeypatch, outcomes=('unclear',), replies=('Maybe.', 'The first one.'))
    with state:
        session = run_offer(state, app)
        record = command(state, CommandType.CLOSE_EXPLORATION, {'session_id': session, 'reason': 'Inspected'}, key='close')
        app.exploration.store.agency.close(record.id, **record.payload)
        clarification = state.connection.execute('SELECT id FROM generation_turns WHERE outbox_id=?',
            (app.exploration.store.row(session, 0)['outbox_id'],)).fetchone()[0]
        state.mark_message_processed(clarification, False)
        app.run_once()
        assert state.pending_ai_messages() == []
        assert app.exploration.store.row(session, 1) is None and len(remote.sent) == 2
