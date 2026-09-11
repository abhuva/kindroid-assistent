import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from kindroid_relay import memory_extractor as adapter
from kindroid_relay.memory import MemoryStore
from kindroid_relay.memory_extractor import SemanticObservationExtractor, SEMANTIC_VERSION, validate_extraction
from kindroid_relay.memory_service import MemoryService
from kindroid_relay.migrations import MIGRATIONS, apply_migrations, migrate_database, open_database
from kindroid_relay.models import ChatMessage
from kindroid_relay.state import RelayState
from test_relay import settings


def source(state, mid, text, timestamp=1, group='g1', sender='ai1'):
    state.add_message(group, ChatMessage(mid, sender, 'ai', 'Hannah', timestamp, text), processed=True)
    return dict(state.connection.execute('SELECT * FROM messages WHERE remote_id=?', (mid,)).fetchone())


def observation(row, *, key='person1', label='Alex', matches=None):
    return {'message_id': row['remote_id'], 'quote': row['body'], 'kind': 'experience',
            'interpretation': 'Speaker describes a formative experience, not a verified external fact.',
            'temporal_scope': 'Childhood; no claim of current applicability', 'uncertainty': 'Relationship inferred from context',
            'entities': [{'key': key, 'kind': 'person', 'label': label, 'aliases': [],
                          'relation': 'about', 'basis': 'inferred', 'possible_match_ids': matches or []}]}


class Runner:
    def __init__(self, observations):
        self.observations, self.calls = observations, []
    def run(self, envelope):
        self.calls.append(envelope)
        return {'observations': deepcopy(self.observations)}


def service(state, tmp_path, runner, **overrides):
    config = replace(settings(tmp_path), memory_enabled=True, **overrides)
    return MemoryService(state, config, extractor=SemanticObservationExtractor(None, runner=runner))


def test_semantic_capture_needs_no_manual_selection_and_restarts_without_replay(tmp_path):
    path = tmp_path / 'state.db'
    with RelayState(path) as state:
        a = source(state, 'a', 'Alex showed me the constellations as a child.')
        b = source(state, 'b', 'She made the night sky feel like somewhere I belonged.', 2)
        runner = Runner([observation(a), observation(b)])
        memory = service(state, tmp_path, runner)
        assert memory.capture()['observations'] == 2
        assert len(runner.calls) == 1  # one isolated batch, not one call per passage
        assert len(runner.calls[0]['targets']) == 2
        records = memory.store.search('constellations')['items']
        assert len(records) == 2
        assert {r['origin'] for r in records} == {'codex_extraction'}
        assert records[0]['entities'][0]['id'] == records[1]['entities'][0]['id']
        assert records[0]['entities'][0]['basis'] == 'inferred'
        assert records[0]['uncertainty']
        assert state.get_meta('codex_thread_id') is None
        for table in ('outbox', 'approvals', 'history_requests'):
            assert state.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    with RelayState(path) as state:
        assert service(state, tmp_path, runner).capture()['processed'] == 0
        assert len(runner.calls) == 1


@pytest.mark.parametrize('damage', ['invented_quote', 'context_source', 'authority_field', 'speaker_field',
                                     'unknown_entity', 'too_many', 'repeated_quote', 'inconsistent_entity'])
def test_untrusted_or_invalid_batch_is_rejected_atomically(tmp_path, damage):
    with RelayState(tmp_path / 'state.db') as state:
        a = source(state, 'a', 'Alex showed me the constellations.')
        b = source(state, 'b', 'This mattered deeply to me.', 2)
        items = [observation(a), observation(b)]
        if damage == 'invented_quote': items[1]['quote'] = 'Fabricated foundational preference'
        if damage == 'context_source': items[1]['message_id'] = 'not-a-target'
        if damage == 'authority_field': items[1]['approved'] = True
        if damage == 'speaker_field': items[1]['speaker'] = 'Hannah'
        if damage == 'unknown_entity': items[1]['entities'][0]['possible_match_ids'] = ['invented-entity']
        if damage == 'too_many': items *= 4
        if damage == 'repeated_quote': items[1] = deepcopy(items[0])
        if damage == 'inconsistent_entity': items[1]['entities'][0]['label'] = 'Different Alex'
        runner = Runner(items)
        memory = service(state, tmp_path, runner)
        assert memory.capture()['failed'] == 2
        assert state.connection.execute('SELECT COUNT(*) FROM memory_statements').fetchone()[0] == 0
        assert state.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 2
        assert memory.capture()['processed'] == 0
        assert len(runner.calls) == 1
        assert memory.status()['extractions'] == {'failed': 2}
        assert not state.is_paused()


def test_failed_second_insert_rolls_back_all_memory_but_keeps_attempts(tmp_path, monkeypatch):
    with RelayState(tmp_path / 's.db') as state:
        a = source(state, 'a', 'A formative experience.')
        b = source(state, 'b', 'A second formative experience.', 2)
        memory = service(state, tmp_path, Runner([observation(a), observation(b)]))
        original = memory.store.save
        calls = 0
        def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise RuntimeError('PRIVATE EXCEPTION')
            return original(*args, **kwargs)
        monkeypatch.setattr(memory.store, 'save', failing)
        result = memory.capture()
        assert result['failed'] == 2
        assert 'PRIVATE' not in json.dumps(result)
        for table in ('memory_statements', 'memory_entities', 'memory_links', 'memory_evidence'):
            assert state.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_existing_same_name_people_are_suggestions_not_automatic_merges(tmp_path):
    with RelayState(tmp_path / 's.db') as state:
        memory = MemoryStore(state.connection, 'g1')
        ids = []
        for i in range(2):
            row = source(state, f'old{i}', f'Alex is person {i}.', i)
            sid = memory.save({'message_id': row['remote_id'], 'quote': row['body'], 'start': 0, 'kind': 'observation',
                'interpretation': '', 'temporal_scope': '', 'entities': [{'kind': 'person', 'label': 'Alex', 'aliases': []}]})
            ids.append(memory.detail(sid)['entities'][0]['id'])
            with state.connection:
                state.connection.execute('INSERT INTO memory_extractions VALUES (?,?,?,?)', (row['remote_id'], SEMANTIC_VERSION, 'completed', 0))
        new = source(state, 'new', 'Alex told me about the mountains.', 3)
        runner = Runner([observation(new, matches=ids)])
        svc = service(state, tmp_path, runner)
        assert svc.capture()['processed'] == 1
        assert len(runner.calls[0]['context_only']) == 2
        result = svc.store.search('mountains')['items'][0]
        assert result['entities'][0]['possible_match_ids'] == ids
        assert result['entities'][0]['identity_review_needed']
        assert result['entities'][0]['id'] not in ids


def test_budget_context_scope_unicode_and_input_bounds(tmp_path):
    with RelayState(tmp_path / 's.db') as state:
        source(state, 'other', 'OTHER GROUP PRIVATE', 0, group='g2')
        a = source(state, 'a', 'Zuhause ist für mich die gemeinsame Zeit. 🌱', 1, sender='ai')
        source(state, 'b', 'Unprocessed next message', 2)
        runner = Runner([observation(a, label='Gemeinsame Zeit')])
        memory = service(state, tmp_path, runner, memory_max_messages_24h=1)
        assert memory.capture()['processed'] == 1
        assert runner.calls[0]['targets'][0]['provisional'] is True
        assert 'OTHER GROUP' not in json.dumps(runner.calls)
        assert memory.capture()['status'] == 'budget_exhausted'
        assert len(runner.calls) == 1


def test_empty_response_is_valid_but_ambiguous_quotes_are_not(tmp_path):
    with RelayState(tmp_path / 's.db') as state:
        row = source(state, 'a', 'Hello! Hello!')
        item = observation(row)
        item['quote'] = 'Hello!'
        with pytest.raises(ValueError, match='uniquely'):
            validate_extraction({'observations': [item]}, [row], [])
        memory = service(state, tmp_path, Runner([]))
        assert memory.capture()['observations'] == 0
        assert memory.status()['extractions'] == {'completed': 1}


def test_automatic_semantic_tick_is_opt_in_and_cadence_limited(tmp_path):
    with RelayState(tmp_path / 's.db') as state:
        row = source(state, 'a', 'The stars were where I felt most at home.')
        runner = Runner([observation(row)])
        memory = service(state, tmp_path, runner)
        memory.automatic_tick()
        assert not runner.calls  # auto capture is independently opt-in
        memory = service(state, tmp_path, runner, memory_auto_capture=True)
        memory.automatic_tick()
        assert len(runner.calls) == 1
        source(state, 'b', 'Another message', 2)
        memory.automatic_tick()
        assert len(runner.calls) == 1
        assert memory.status()['pending_local_messages'] == 1


def test_sdk_boundary_is_ephemeral_restricted_and_does_not_expose_config(tmp_path, monkeypatch):
    calls = {}
    class Client:
        def __init__(self, config): calls['config'] = config
        def start(self): pass
        def initialize(self): pass
        def request(self, method, params):
            assert method == 'config/read'
            return {'config': {'mcp_servers': {'server.with.dots': {'secret': 'NEVER IN PROMPT'}},
                               'plugins': {'plugin@example': {}}, 'apps': {'app1': {}}}}
        def thread_start(self, params):
            calls['start'] = params
            return SimpleNamespace(thread=SimpleNamespace(id='isolated-memory'))
        def close(self): calls['closed'] = True
    class Thread:
        def __init__(self, client, thread_id): assert thread_id == 'isolated-memory'
        def run(self, prompt, **kwargs):
            calls['prompt'], calls['run'] = prompt, kwargs
            return SimpleNamespace(final_response='{"observations": []}')
    monkeypatch.setattr(adapter, 'CodexClient', Client)
    monkeypatch.setattr(adapter, 'Thread', Thread)
    assert adapter.CodexExtractionRunner('existing-configured-model').run({'targets': []}) == {'observations': []}
    assert calls['closed']
    assert calls['start']['ephemeral'] is True
    assert calls['start']['model'] == 'existing-configured-model'
    assert calls['start']['sandbox'] == 'read-only'
    config = calls['start']['config']
    assert config['mcp_servers']['server.with.dots']['enabled'] is False
    assert config['plugins']['plugin@example']['enabled'] is False
    assert config['apps']['app1']['enabled'] is False
    assert config['web_search'] == 'disabled'
    assert config['features.shell_tool'] is False
    assert 'NEVER IN PROMPT' not in calls['prompt']
    assert calls['run']['approval_mode'] == adapter.ApprovalMode.deny_all
    assert 'negation' in calls['start']['developerInstructions']
    assert 'permission' in calls['start']['developerInstructions']


def test_sdk_deadline_closes_only_owned_client_and_rejects_late_result(monkeypatch):
    closed = []
    class Client:
        def __init__(self, config): pass
        def start(self): pass
        def initialize(self): raise AssertionError('Must not initialize after expiry')
        def close(self): closed.append(True)
    class ImmediateTimer:
        def __init__(self, seconds, callback): self.callback = callback
        def start(self): self.callback()
        def cancel(self): pass
        def join(self, timeout): pass
    monkeypatch.setattr(adapter, 'CodexClient', Client)
    monkeypatch.setattr(adapter.threading, 'Timer', ImmediateTimer)
    with pytest.raises(TimeoutError): adapter.CodexExtractionRunner(None).run({})
    assert closed


def test_migration_five_preserves_existing_observations(tmp_path):
    path = tmp_path / 'old.db'
    db = open_database(path)
    apply_migrations(db, MIGRATIONS[:4])
    with db:
        db.execute("INSERT INTO memory_statements VALUES ('s','g1','observation','retained','marc_selection','manual-v1','disputed','past',1)")
        db.execute("INSERT INTO memory_entities VALUES ('e','g1','person','Alex','[]')")
        db.execute("INSERT INTO memory_links VALUES ('s','e','g1','mentions','marc_selection')")
    db.close()
    assert migrate_database(path) == [5, 6, 7, 8, 9, 10, 11, 12, 13]
    with RelayState(path) as state:
        item = MemoryStore(state.connection, 'g1').detail('s')
        assert item['interpretation'] == 'retained' and item['status'] == 'disputed'
        assert item['uncertainty'] == ''
        assert item['entities'][0]['possible_match_ids'] == []
        assert item['entities'][0]['basis'] == 'selected'
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_source_metadata_changed_during_model_call_rejects_batch(tmp_path):
    with RelayState(tmp_path / 's.db') as state:
        row = source(state, 'a', 'A formative memory with Alex.')
        class ChangingRunner:
            def run(self, envelope):
                with state.connection:
                    state.connection.execute("UPDATE messages SET display_name='Different companion' WHERE remote_id='a'")
                return {'observations': [observation(row)]}
        memory = service(state, tmp_path, ChangingRunner())
        assert memory.capture()['failed'] == 1
        assert state.connection.execute('SELECT COUNT(*) FROM memory_statements').fetchone()[0] == 0
