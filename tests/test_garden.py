import json
import sqlite3
from types import SimpleNamespace

import pytest

from kindroid_relay.agency import AgencyStore, select_stimuli
from kindroid_relay.garden import GardenStore, validate_correction
from kindroid_relay.command_queue import CommandStore, CommandType, CommandStatus, CommandValidationError, validate_payload
from kindroid_relay.migrations import MIGRATIONS, apply_migrations, open_database, migrate_database
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from kindroid_relay.backup import backup_database, check_integrity
from kindroid_relay.diagnostics import export_diagnostics
from test_web import dashboard, MUTATION_HEADERS
from test_exploration_response import scenario, run_offer


def populate(state, group='g1', key='offer'):
    command, _ = CommandStore(state.connection).enqueue(CommandType.START_EXPLORATION, {}, idempotency_key=key)
    stimuli, inputs = select_stimuli({'items': []}, key, [])
    AgencyStore(state, group).prepare(command.id, stimuli, inputs, 100)
    CommandStore(state.connection).claim_next()
    CommandStore(state.connection).transition(command.id, CommandStatus.COMPLETED)
    return GardenStore(state.connection, group), command


def correction(state, item, text='Needs review', key='correct'):
    data = {'item_id': item['id'], 'expected_version': item['current_version'], 'annotation': text, 'reason': 'Operator correction'}
    command, _ = state.commands.enqueue(CommandType.GARDEN_CORRECT, data, idempotency_key=key)
    return command, data


def protected_fixture(store, item_id, layer='roots', ownership='shared', content='Fixture only'):
    """Not a runtime bootstrap: exact roots still require manual review outside tests."""
    with store.db:
        store.db.execute('INSERT INTO garden_items VALUES (?,?,?,?,NULL,1)', (item_id, store.group, layer, ownership))
        store._version(item_id, 1, status='active', content=content, annotation='', rationale='Manual bootstrap fixture',
                       actor='marc', origin='manual_bootstrap_fixture')


def test_candidates_are_atomic_versioned_seeds_with_no_promotion(tmp_path):
    with RelayState(tmp_path / 'state.db') as state:
        store, _ = populate(state)
        items = store.list_items()
        assert items['counts'] == {'roots': 0, 'growing': 0, 'passing': 3}
        assert {i['origin_actor'] for i in items['items']} == {'codex'}
        item = items['items'][0]
        assert store.detail(item['id'])['source']['provider'] == 'local-catalog'
        assert store.trail(item['id'])['items']
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert state.connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_failed_candidate_capture_rolls_back_invitation_preparation(tmp_path, monkeypatch):
    with RelayState(tmp_path / 'state.db') as state:
        def fail(*args, **kwargs):
            raise ValueError('fixture capture failure')
        monkeypatch.setattr(GardenStore, 'capture_candidate', fail)
        with pytest.raises(ValueError):
            populate(state)
        for table in ('passing_candidates', 'agency_stimuli', 'exploration_sessions', 'garden_items'):
            assert state.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_upgrade_preserves_old_candidate_status_and_original_trail(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    db = open_database(path)
    apply_migrations(db, MIGRATIONS[:7])
    with monkeypatch.context() as patch:
        patch.setattr(GardenStore, 'capture_candidate', lambda *args, **kwargs: None)
        populate(SimpleNamespace(connection=db))
    candidate = db.execute('SELECT id FROM passing_candidates ORDER BY id').fetchone()[0]
    with db:
        db.execute("UPDATE passing_candidates SET status='archived' WHERE id=?", (candidate,))
    db.close()
    assert migrate_database(path) == [8, 9, 10, 11, 12, 13]
    assert migrate_database(path) == []
    with RelayState(path) as state:
        store = GardenStore(state.connection, 'g1')
        detail = store.detail(candidate)
        assert detail['status'] == 'archived'
        assert detail['versions'][0]['origin'] == 'migration_baseline'
        assert store.trail(candidate)['items']
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_archive_restore_and_annotations_keep_immutable_history(tmp_path):
    path = tmp_path / 'state.db'
    with RelayState(path) as state:
        store, _ = populate(state)
        item = store.list_items()['items'][0]
        cmd, data = correction(state, item)
        assert store.correct(cmd.id, data)['version'] == 2
        assert store.correct(cmd.id, data)['version'] == 2
        for status in ('archived', 'active'):
            payload = {'candidate_id': item['id'], 'status': status, 'reason': f'Operator {status}'}
            cmd, _ = state.commands.enqueue(CommandType.REVIEW_CANDIDATE, payload, idempotency_key=status)
            AgencyStore(state, 'g1').revise_candidate(cmd.id, **payload)
            AgencyStore(state, 'g1').revise_candidate(cmd.id, **payload)
        detail = store.detail(item['id'])
        assert [v['status'] for v in detail['versions']] == ['active', 'active', 'archived', 'active']
        assert detail['annotation'] == 'Needs review'
        assert detail['content'] == item['content']
        assert [v['actor'] for v in detail['versions']] == ['codex', 'marc', 'marc', 'marc']
        for sql in ("UPDATE garden_versions SET content='replacement'", 'DELETE FROM garden_versions',
                    'DELETE FROM garden_items', "UPDATE garden_items SET layer='roots'",
                    "UPDATE garden_items SET ownership='shared'", 'UPDATE garden_items SET current_version=999'):
            with pytest.raises(sqlite3.IntegrityError), state.connection:
                state.connection.execute(sql)
    with RelayState(path) as state:
        assert GardenStore(state.connection, 'g1').detail(item['id'])['current_version'] == 4


def test_correction_rejects_stale_version_and_identity_fields(tmp_path):
    with RelayState(tmp_path / 'state.db') as state:
        store, _ = populate(state)
        item = store.list_items()['items'][0]
        first, data = correction(state, item)
        second, stale = correction(state, item, 'Concurrent', 'second')
        store.correct(first.id, data)
        with pytest.raises(ValueError, match='version changed'):
            store.correct(second.id, stale)
        for field in ('layer', 'ownership', 'status', 'actor', 'content', 'approval'):
            with pytest.raises(CommandValidationError):
                validate_payload(CommandType.GARDEN_CORRECT, {**data, field: 'roots'})
        with pytest.raises(ValueError):
            validate_correction({**data, 'reason': ' '})
        assert len(store.detail(item['id'])['versions']) == 2


def test_group_isolation_and_pagination(tmp_path):
    with RelayState(tmp_path / 'state.db') as state:
        store, _ = populate(state)
        other, _ = populate(state, 'g2', 'other')
        first = store.list_items(limit=1)
        second = store.list_items(after=first['next_after'], limit=2)
        assert first['has_more'] and not second['has_more']
        assert len({i['id'] for i in first['items'] + second['items']}) == 3
        item = first['items'][0]
        with pytest.raises(ValueError):
            other.detail(item['id'])
        with pytest.raises(ValueError):
            other.trail(item['id'])
        protected_fixture(store, 'root', ownership='shared')
        protected_fixture(store, 'contrary-root', content='Contradictory fixture')
        with state.connection:
            state.connection.execute("INSERT INTO garden_relationships VALUES ('rel','g1','root','contrary-root','contradicts','Unresolved','marc',1)")
        assert store.detail('root')['relationships'][0]['relation'] == 'contradicts'
        assert store.list_items(layer='roots')['counts']['roots'] == 2
        with pytest.raises(sqlite3.IntegrityError), state.connection:
            state.connection.execute("UPDATE garden_items SET ownership='self' WHERE id='root'")
        with pytest.raises(sqlite3.IntegrityError), state.connection:
            state.connection.execute("INSERT INTO garden_relationships VALUES ('bad','g2',?,'root','related','Bad','system',1)", (other.list_items()['items'][0]['id'],))
        page = store.trail(item['id'], limit=1)
        assert page['has_more']
        assert store.trail(item['id'], after=page['next_after'])['items'][0]['sequence'] > page['next_after']


def test_response_links_reuse_live_evidence_without_promoting(tmp_path, monkeypatch):
    _, state, remote, app, _ = scenario(tmp_path, monkeypatch, outcomes=('alternative',), replies=('Could we explore tide pools instead?',))
    with state:
        run_offer(state, app)
        store = GardenStore(state.connection, 'g1')
        item = next(i for i in store.list_items()['items'] if i['origin_actor'] == 'hannah')
        detail = store.detail(item['id'])
        assert detail['layer'] == 'passing'
        response = detail['responses'][0]
        assert response['reaction'] == 'alternative'
        assert response['observation']['evidence'][0]['quote'] == 'Could we explore tide pools instead?'
        with state.connection:
            state.connection.execute("UPDATE memory_statements SET status='retracted' WHERE id=?", (response['statement_id'],))
        assert store.detail(item['id'])['responses'][0]['observation']['status'] == 'retracted'
        assert len(remote.sent) == 2  # garden capture adds no sends or generations


def test_project_attention_is_bounded_idempotent_and_independent_of_interest(tmp_path):
    with RelayState(tmp_path / 'state.db') as state:
        store, _ = populate(state)
        seed_id = store.list_items()['items'][0]['id']
        args = {'title': 'Fixture project', 'outcome': 'A sketch', 'next_step': 'Consider options', 'stopping_condition': 'Budget exhausted'}
        with pytest.raises(ValueError, match='Growing'):
            store.create_project('bad', [seed_id], **args)
        protected_fixture(store, 'growing', layer='growing', ownership='self')
        store.create_project('project', ['growing'], **args)
        store.record_attention('project', 'allocate', kind='allocate', unit='minutes', amount=5, reason='Bounded plan', actor='marc')
        store.record_attention('project', 'use', kind='consume', unit='minutes', amount=2, reason='Fixture work', result='Fixture output')
        store.record_attention('project', 'use', kind='consume', unit='minutes', amount=2, reason='Fixture work', result='Fixture output')
        with pytest.raises(ValueError, match='conflict'):
            store.record_attention('project', 'use', kind='consume', unit='minutes', amount=3, reason='Changed')
        with pytest.raises(ValueError, match='budget'):
            store.record_attention('project', 'over', kind='consume', unit='minutes', amount=4, reason='Too much')
        store.record_attention('project', 'pause', kind='pause', reason='Pause is not disinterest')
        assert store.detail('growing')['status'] == 'active'
        with pytest.raises(ValueError):
            store.record_attention('project', 'paused-use', kind='consume', unit='minutes', amount=1, reason='Paused')
        store.record_attention('project', 'resume', kind='resume', reason='Ready')
        store.record_attention('project', 'stop', kind='abandon', reason='Fixture stopped', result='Partial result')
        project = store.project('project')
        assert project['status'] == 'abandoned'
        assert project['budgets'] == [{'unit': 'minutes', 'allocated': 5, 'consumed': 2}]
        with pytest.raises(ValueError):
            store.record_attention('project', 'late', kind='allocate', unit='minutes', amount=2, reason='After stop')
        with pytest.raises(sqlite3.IntegrityError), state.connection:
            state.connection.execute('DELETE FROM garden_attention')


def test_api_is_read_only_except_worker_owned_reasoned_correction(tmp_path):
    config, factory, wake, client = dashboard(tmp_path)
    with factory.connect() as state:
        store, _ = populate(state)
        item = store.list_items()['items'][0]
        state.pause('Maintenance')
    assert client.get('/api/v1/garden').json()['counts']['passing'] == 3
    assert client.get('/api/v1/garden?layer=invalid').status_code == 422
    assert client.get('/api/v1/garden?limit=101').status_code == 422
    assert client.get('/api/v1/garden/missing').status_code == 404
    assert client.get('/api/v1/garden/' + item['id']).json()['current_version'] == 1
    payload = {'item_id': item['id'], 'expected_version': 1, 'annotation': 'Operator note kn_fixture-secret-123', 'reason': 'Fixture correction'}
    body = {'idempotency_key': 'correction', 'payload': payload}
    assert client.post('/api/v1/garden/corrections', json=body).status_code == 403
    first = client.post('/api/v1/garden/corrections', json=body, headers=MUTATION_HEADERS)
    assert first.status_code == 202
    assert client.post('/api/v1/garden/corrections', json=body, headers=MUTATION_HEADERS).json()['id'] == first.json()['id']
    with factory.connect() as state:
        store = GardenStore(state.connection, 'g1')
        assert store.detail(item['id'])['current_version'] == 1
        # Dispatch only the local correction; no real adapter is instantiated.
        command = state.commands.get(first.json()['id'])
        worker = RelayWorker(config)
        worker._dispatch(SimpleNamespace(state=state, settings=config), command)
        assert state.is_paused()
        assert store.detail(item['id'])['current_version'] == 2
    assert 'kn_fixture-secret' not in client.get('/api/v1/garden').text
    assert 'kn_fixture-secret' not in client.get('/api/v1/garden/' + item['id']).text
    assert client.post('/api/v1/garden/' + item['id'] + '/promote', json={}, headers=MUTATION_HEADERS).status_code == 404


def test_backup_and_diagnostics_keep_garden_private(tmp_path):
    config, factory, _, _ = dashboard(tmp_path)
    with factory.connect() as state:
        store, _ = populate(state)
        cmd, data = correction(state, store.list_items()['items'][0], 'Private garden marker')
        store.correct(cmd.id, data)
    backup = tmp_path / 'backup'
    backup_database(config.state_path, backup)
    assert check_integrity(backup / 'state.sqlite3')['integrity'] == 'ok'
    with RelayState(backup / 'state.sqlite3') as state:
        assert 'Private garden marker' in json.dumps(GardenStore(state.connection, 'g1').list_items())
    output = tmp_path / 'diagnostics.json'
    export_diagnostics(config.state_path, config.log_path, output)
    assert 'Private garden marker' not in output.read_text(encoding='utf8')
