import json
import time
from dataclasses import replace
from email.utils import formatdate

import pytest

from kindroid_relay.command_queue import CommandType, CommandStatus, validate_payload, CommandValidationError
from kindroid_relay.discovery import DiscoveryStore, defaults, validate_discovery, CREATIVE
from kindroid_relay.experience import ExperienceStore
from kindroid_relay.migrations import MIGRATIONS, apply_migrations, migrate_database, open_database
from kindroid_relay.public_sources import PublicSources, public_link
from kindroid_relay.state import RelayState
from kindroid_relay.worker import RelayWorker
from kindroid_relay.backup import backup_database
from kindroid_relay.diagnostics import export_diagnostics
from test_exploration_response import scenario, run_offer
from test_agency import command
from test_web import dashboard, MUTATION_HEADERS

ORIGINAL_PUBLIC_READ = PublicSources.read


@pytest.fixture(autouse=True)
def no_live_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unmocked public network call forbidden')
    monkeypatch.setattr(PublicSources, 'read', forbidden)


def configured(tmp_path, monkeypatch, *, outcome='choose', sources=None, follow=True):
    now = [int(time.time())]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    config, state, remote, app, interpreter = scenario(tmp_path, monkeypatch, outcomes=(outcome,),
        replies=('A private fixture choice.', 'That raised another question for me.'), curiosity_quiet_hours=None)
    record = command(state, CommandType.SET_DISCOVERY, {**defaults(), 'sources': sources or ['catalog'], 'follow_through': follow}, key='discovery')
    state.commands.claim_next()
    app.discovery.save({**defaults(), 'sources': sources or ['catalog'], 'follow_through': follow}, record.id)
    state.commands.transition(record.id, CommandStatus.COMPLETED)
    calls = []
    def research(topic):
        calls.append(topic)
        return [{'title': 'Fixture reference', 'excerpt': 'A short public reference excerpt.',
                 'url': 'https://en.wikipedia.org/?curid=1', 'retrieved_at': now[0], 'published_at': None}]
    monkeypatch.setattr(app.experiences.public, 'research', research)
    return config, state, remote, app, now, calls


def job(state, session):
    return state.connection.execute('SELECT * FROM exploration_experiences WHERE session_id=?', (session,)).fetchone()


@pytest.mark.parametrize('shared_schedule', [False, True])
def test_choice_research_result_reflection_roundtrip_without_replay(tmp_path, monkeypatch, shared_schedule):
    config, state, remote, app, now, calls = configured(tmp_path, monkeypatch)
    with state:
        if shared_schedule:
            from kindroid_relay.schedule import DailyScheduler, effective_settings
            daily = DailyScheduler(state, config)
            daily.save(daily.defaults(), 'shared-fixture')
            app.settings = app.relay.settings = effective_settings(state, config)
        session = run_offer(state, app)
        assert job(state, session)['status'] == 'queued'
        assert len(remote.sent) == 2 and not calls
        app.relay.synchronize_context()
        assert app.experiences.advance()
        assert job(state, session)['status'] == 'ready'
        assert len(calls) == 1 and 'private' not in calls[0]
        if not shared_schedule:
            assert not app.experiences.advance()  # Legacy inactivity rule remains.
            now[0] += 600
        assert app.experiences.advance()
        assert len(remote.sent) == 3 and remote.turn_requests == [True, True]
        assert 'Wikipedia' in remote.sent[-1] and 'https://en.wikipedia.org/' in remote.sent[-1]
        assert job(state, session)['status'] == 'awaiting_reflection'
        app.relay.synchronize_context()
        assert job(state, session)['status'] == 'complete'
        assert job(state, session)['reflection_statement_id']
        assert not state.pending_ai_messages()
        assert not app.experiences.advance()
        RelayWorker(config)._recover(state)
        assert not app.experiences.advance()
        assert len(remote.sent) == 3 and len(calls) == 1
        ack = app.exploration.store.row(session, 0)['outbox_id']
        assert state.outbox_for_source('local-user:' + ack)['id'] == job(state, session)['outbox_id']
        assert state.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0
        assert state.connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('outcome', ['decline', 'defer'])
def test_decline_or_defer_never_starts_research(tmp_path, monkeypatch, outcome):
    _, state, remote, app, _, calls = configured(tmp_path, monkeypatch, outcome=outcome)
    with state:
        session = run_offer(state, app)
        assert job(state, session)['status'] == 'cancelled'
        app.relay.synchronize_context()
        assert not app.experiences.advance()
        assert not calls and len(remote.sent) == 1


def test_private_alternative_needs_explicit_public_topic_and_no_cross_group_approval(tmp_path, monkeypatch):
    _, state, _, app, _, calls = configured(tmp_path, monkeypatch, outcome='alternative')
    with state:
        session = run_offer(state, app)
        assert job(state, session)['status'] == 'needs_public_topic'
        assert job(state, session)['topic'] == ''
        app.relay.synchronize_context()
        assert not app.experiences.advance() and not calls
        record = command(state, CommandType.APPROVE_PUBLIC_TOPIC, {'session_id': session, 'topic': 'Tide pools'}, key='approve-topic')
        with pytest.raises(ValueError):
            ExperienceStore(state, 'other').approve_topic(session, 'Tide pools', record.id)
        app.experiences.store.approve_topic(session, 'Tide pools', record.id)
        with pytest.raises(ValueError):
            app.experiences.store.approve_topic(session, 'Different topic', record.id)
        app.experiences.advance()
        assert calls == ['Tide pools']


def test_upgrade_or_later_enable_does_not_backfill_old_invitation(tmp_path, monkeypatch):
    _, state, _, app, _, calls = configured(tmp_path, monkeypatch, follow=False)
    with state:
        session = run_offer(state, app)
        assert job(state, session) is None
        record = command(state, CommandType.SET_DISCOVERY, {**defaults(), 'follow_through': True}, key='enable-later')
        app.discovery.save({**defaults(), 'follow_through': True}, record.id)
        app.relay.synchronize_context()
        assert not app.experiences.advance() and not calls


@pytest.mark.parametrize('block', ['pause', 'disabled', 'dead_time', 'retracted', 'archived', 'expired', 'budget'])
def test_experience_safety_gates(tmp_path, monkeypatch, block):
    _, state, remote, app, now, calls = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        if block == 'pause': state.pause('Fixture')
        if block == 'disabled':
            record = command(state, CommandType.SET_DISCOVERY, defaults(), key='disable')
            app.discovery.save(defaults(), record.id)
        if block == 'dead_time':
            from kindroid_relay.schedule import DailyScheduler
            daily = DailyScheduler(state, app.settings)
            daily.save({**daily.defaults(), 'dead_hours': list(range(24))}, 'fixture')
        if block == 'retracted':
            with state.connection:
                state.connection.execute("UPDATE memory_statements SET status='retracted' WHERE id=?", (app.exploration.store.row(session, 0)['statement_id'],))
        if block == 'archived':
            with state.connection:
                state.connection.execute("UPDATE passing_candidates SET status='archived' WHERE id=?", (job(state, session)['candidate_id'],))
        if block == 'expired': now[0] += 86401
        if block == 'budget':
            record = command(state, CommandType.SET_DISCOVERY, {**defaults(), 'follow_through': True, 'research_limit': 1}, key='limit')
            app.discovery.save({**defaults(), 'follow_through': True, 'research_limit': 1}, record.id)
            with state.connection:
                state.connection.execute('UPDATE exploration_experiences SET started_at=? WHERE session_id=?', (now[0], session))
        app.experiences.advance()
        assert not calls and len(remote.sent) == 2


@pytest.mark.parametrize('stage', ['research', 'send', 'generation'])
def test_failed_or_uncertain_experience_never_retries(tmp_path, monkeypatch, stage):
    config, state, remote, app, now, calls = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        def failed(*args, **kwargs):
            raise TimeoutError('PRIVATE-NETWORK-ERROR')
        if stage == 'research': monkeypatch.setattr(app.experiences.public, 'research', failed)
        app.experiences.advance()
        if stage != 'research':
            now[0] += 600
            monkeypatch.setattr(remote, 'post_user_message' if stage == 'send' else 'generate_ai_response', failed)
            app.experiences.advance()
        assert job(state, session)['status'] == ('failed' if stage == 'research' else 'uncertain')
        assert 'PRIVATE-NETWORK-ERROR' not in job(state, session)['note']
        assert state.is_paused() is (stage != 'research')
        RelayWorker(config)._recover(state)
        assert not app.experiences.advance()
        assert len(calls) <= 1
        if stage != 'research':
            close = command(state, CommandType.CLOSE_EXPLORATION, {'session_id': session, 'reason': 'Inspected fixture'}, key='close')
            app.exploration.store.agency.close(close.id, session, 'Inspected fixture')
            assert state.is_paused()


def test_interrupted_lookup_is_terminal_and_sending_recovery_pauses(tmp_path, monkeypatch):
    config, state, _, app, _, calls = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        with state.connection:
            state.connection.execute("UPDATE exploration_experiences SET status='researching' WHERE session_id=?", (session,))
        RelayWorker(config)._recover(state)
        assert job(state, session)['status'] == 'failed' and not state.is_paused()
        with state.connection:
            state.connection.execute("UPDATE exploration_experiences SET status='sending' WHERE session_id=?", (session,))
        RelayWorker(config)._recover(state)
        assert job(state, session)['status'] == 'uncertain' and state.is_paused() and not calls


def test_news_failure_is_cached_and_creative_stimuli_still_work(tmp_path, monkeypatch):
    _, state, _, app, _, _ = configured(tmp_path, monkeypatch, sources=['world_news', 'creative'])
    calls = []
    def failed(*args):
        calls.append(True)
        raise TimeoutError('PRIVATE-FEED-ERROR')
    monkeypatch.setattr(app.discovery.public, 'news', failed)
    with state:
        session = run_offer(state, app)
        rows = state.connection.execute('SELECT provider,selection_kind FROM agency_stimuli WHERE session_id=?', (session,)).fetchall()
        assert rows and all(r['selection_kind'] == 'creative_possibility' for r in rows)
        app.discovery.news('world_news', '')
        assert len(calls) == 1
        assert app.discovery.snapshot()['feeds'][0]['outcome'] == 'source_unavailable'


def test_discovery_repeat_suppression_and_no_sources_is_a_skip(tmp_path, monkeypatch):
    _, state, remote, app, _, _ = configured(tmp_path, monkeypatch, sources=['creative'], follow=False)
    with state:
        first, _ = app.discovery.select([], 'fixture', [])
        second, _ = app.discovery.select([], 'fixture2', [s.locator for s in first])
        assert len(first) == 3 and len(second) == len(CREATIVE) - 3
        assert not set(s.locator for s in first).intersection(s.locator for s in second)
        third, _ = app.discovery.select([], 'fixture3', [s.locator for s in first + second])
        assert third == [] and not remote.sent


@pytest.mark.parametrize('change', [{'sources': []}, {'sources': ['shell']}, {'sources': ['local_news']},
    {'follow_through': 'yes'}, {'research_limit': True}, {'research_limit': 21}, {'local_area': '<private>'}])
def test_discovery_validation(change):
    with pytest.raises(CommandValidationError):
        validate_payload(CommandType.SET_DISCOVERY, {**defaults(), **change})


def test_api_is_read_only_and_settings_are_worker_queued(tmp_path):
    _, factory, _, client = dashboard(tmp_path)
    assert client.get('/api/v1/discovery').json()['config'] == defaults()
    body = {'idempotency_key': 'sources', 'payload': {**defaults(), 'sources': ['creative']}}
    assert client.post('/api/v1/settings/discovery', json=body).status_code == 403
    first = client.post('/api/v1/settings/discovery', json=body, headers=MUTATION_HEADERS)
    assert first.status_code == 202
    assert client.post('/api/v1/settings/discovery', json=body, headers=MUTATION_HEADERS).json()['id'] == first.json()['id']
    with factory.connect() as state:
        assert DiscoveryStore(state, 'g1').config() == defaults()
        assert not state.connection.execute('SELECT 1 FROM outbox').fetchone()


def test_migration_preserves_existing_state_and_backup_privacy(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    db = open_database(path); apply_migrations(db, MIGRATIONS[:10])
    db.execute("INSERT INTO metadata VALUES ('fixture','retained')"); db.commit(); db.close()
    assert migrate_database(path) == [11, 12, 13]
    with RelayState(path) as state:
        assert state.get_meta('fixture') == 'retained'
        assert not state.connection.execute('SELECT 1 FROM exploration_experiences').fetchone()
    config, state, _, app, _, _ = configured(tmp_path / 'new', monkeypatch)
    with state:
        session = run_offer(state, app)
        target = backup_database(config.state_path, tmp_path / 'backup')
        output = export_diagnostics(config.state_path, config.log_path, tmp_path / 'diagnostics.json')
        assert 'A private fixture choice' not in output.read_text()
    with RelayState(target / 'state.sqlite3') as restored:
        assert job(restored, session)['status'] == 'queued'


def test_public_adapters_bound_and_validate_source_content(monkeypatch):
    public = PublicSources()
    now = int(time.time())
    rss = f'<rss><channel><item><title>News &amp; culture</title><link>https://www.bbc.co.uk/news/fixture</link><pubDate>{formatdate(now, usegmt=True)}</pubDate><description>&lt;b&gt;Public snippet&lt;/b&gt;</description></item></channel></rss>'.encode()
    monkeypatch.setattr(public, 'read', lambda *a: rss)
    assert public.news()[0]['excerpt'] == 'Public snippet'
    monkeypatch.setattr(public, 'read', lambda *a: b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>')
    with pytest.raises(ValueError): public.news()
    monkeypatch.setattr(public, 'read', lambda *a: json.dumps({'query': {'pages': [{'pageid': 4, 'title': 'Reference', 'extract': 'x' * 2000}]}}).encode())
    result = public.research('Public topic')
    assert len(result[0]['excerpt']) == 450 and result[0]['url'].endswith('=4')
    with pytest.raises(ValueError): public.research('x' * 181)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://localhost/x', 'https://127.0.0.1/x', 'https://user:pass@example.com/x'])
def test_public_link_rejects_unsafe_targets(url):
    with pytest.raises(ValueError): public_link(url)


@pytest.mark.parametrize('case', ['ok', 'redirect', 'rate_limit', 'oversized', 'deadline', 'private_target'])
def test_public_transport_has_fixed_destinations_bounds_and_no_retries(monkeypatch, case):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import kindroid_relay.public_sources as module
    clients, reads = [], []
    def client(**kwargs):
        clients.append(kwargs)
        def stream(method, url, **options):
            reads.append((method, url, options))
            response = SimpleNamespace(status_code=302 if case == 'redirect' else 429 if case == 'rate_limit' else 200,
                iter_bytes=lambda n: iter([b'x' * (262145 if case == 'oversized' else 5)]))
            return nullcontext(response)
        return nullcontext(SimpleNamespace(stream=stream))
    monkeypatch.setattr(module.httpx, 'Client', client)
    if case == 'deadline':
        times = iter([0, 16]); monkeypatch.setattr(module.time, 'monotonic', lambda: next(times))
    url = 'https://127.0.0.1/private' if case == 'private_target' else 'https://en.wikipedia.org/w/api.php'
    if case == 'ok':
        assert ORIGINAL_PUBLIC_READ(PublicSources(), url) == b'xxxxx'
    else:
        with pytest.raises(ValueError): ORIGINAL_PUBLIC_READ(PublicSources(), url)
    assert len(reads) == (0 if case == 'private_target' else 1)
    if clients:
        assert clients[0]['trust_env'] is False and clients[0]['follow_redirects'] is False
        assert clients[0]['timeout'] == 5


def test_optional_reflection_silence_is_terminal(tmp_path, monkeypatch):
    _, state, remote, app, now, _ = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context(); app.experiences.advance(); now[0] += 600
        monkeypatch.setattr(remote, 'get_turn', lambda *a, **kw: None)
        app.experiences.advance()
        assert job(state, session)['status'] == 'complete'
        assert not job(state, session)['reflection_message_id']
        assert len(remote.sent) == 3


def test_changed_choice_source_cancels_without_public_reads(tmp_path, monkeypatch):
    _, state, _, app, _, calls = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        with state.connection:
            state.connection.execute("UPDATE messages SET body='Changed choice' WHERE remote_id=?", (app.exploration.store.row(session, 0)['message_id'],))
        assert app.experiences.advance()
        assert job(state, session)['status'] == 'cancelled' and not calls


@pytest.mark.parametrize('status,blocked', [('researching', True), ('sending', True), ('uncertain', True),
    ('awaiting_reflection', True), ('failed', False), ('needs_review', False), ('needs_public_topic', False)])
def test_experience_uncertainty_blocks_but_review_only_does_not(tmp_path, monkeypatch, status, blocked):
    _, state, _, app, _, _ = configured(tmp_path, monkeypatch)
    with state:
        session = run_offer(state, app)
        app.relay.synchronize_context()
        with state.connection:
            state.connection.execute('UPDATE exploration_experiences SET status=? WHERE session_id=?', (status, session))
        assert app.exploration.store.agency.has_unresolved_work() is blocked
