"""Worker-owned stimulus selection and local opt-in, separate from tick allocation."""
from __future__ import annotations

import json
import time

from .activity import ActivitySeverity
from .agency import Stimulus
from .memory import _id
from .public_sources import PublicSources

KINDS = ('catalog', 'memory', 'creative', 'interests', 'world_news', 'local_news')
QUERIES = {'music': 'Syncopation', 'mathematics': 'Tessellation', 'art': 'Negative space',
           'nature': 'Moss', 'astronomy': 'Cultural astronomy', 'history': 'Material culture',
           'language': 'Neologism', 'engineering': 'Biomimetics', 'cooking': 'Food pairing',
           'chemistry': 'Materials science', 'philosophy': 'Identity of indiscernibles', 'games': 'Cooperative game'}
CREATIVE = (
    ('Personal website', 'Imagine a small personal website about something you enjoy; what would belong there?'),
    ('Zine', 'Try imagining a one-page zine: words, images, or a collection of things worth sharing.'),
    ('Internet forum', 'Imagine a small discussion space around a shared curiosity. Would that appeal, or not?'),
    ('Short-form video', 'Imagine a short video explaining or showing something in your own style; no posting is implied.'),
    ('Commonplace book', 'Consider collecting a few discoveries in a private notebook, with no need to publish.'),
)


def defaults() -> dict:
    return {'sources': ['catalog', 'memory'], 'local_area': '', 'follow_through': False, 'research_limit': 2}


def validate_discovery(value: dict) -> dict:
    if set(value) != set(defaults()):
        raise ValueError('Discovery requires sources, local_area, follow_through and research_limit')
    sources = value['sources']
    if (not isinstance(sources, list) or not sources or len(sources) > len(KINDS)
            or any(not isinstance(s, str) or s not in KINDS for s in sources) or len(set(sources)) != len(sources)):
        raise ValueError('Choose at least one supported discovery source')
    area = value['local_area']
    if not isinstance(area, str) or len(area) > 80 or any(ord(c) < 32 or c in '<>\"' for c in area):
        raise ValueError('Local area must be a short public place name')
    if 'local_news' in sources and not area.strip():
        raise ValueError('Choose a public city or region before enabling local news')
    if type(value['follow_through']) is not bool or type(value['research_limit']) is not int or not 1 <= value['research_limit'] <= 20:
        raise ValueError('Follow-through requires a boolean and a daily research limit from 1 to 20')
    return {**value, 'sources': [s for s in KINDS if s in sources], 'local_area': area.strip()}


class DiscoveryStore:
    def __init__(self, state, group: str, public=None):
        self.state, self.db, self.group = state, state.connection, group
        self.public = public or PublicSources()

    def config(self) -> dict:
        row = self.db.execute('SELECT config_json FROM discovery_settings WHERE group_id=?', (self.group,)).fetchone()
        return json.loads(row[0]) if row else defaults()

    def save(self, value: dict, command_id: str) -> dict:
        value = validate_discovery(value)
        with self.db:
            self.db.execute('''INSERT INTO discovery_settings VALUES (?,?,?) ON CONFLICT(group_id)
                DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at''',
                (self.group, json.dumps(value), int(time.time())))
            self.db.execute('DELETE FROM discovery_cache WHERE group_id=?', (self.group,))
            self.state.activity.add('discovery_changed', ActivitySeverity.INFO,
                'Discovery settings saved; no immediate invitation or research', details={'command_id': command_id})
        return {'saved': True}

    def remember(self, kind: str, data: dict) -> str:
        sid = _id(self.group, kind, data['url'] or data['title'])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO discovery_sources VALUES (?,?,?,?,?)',
                            (sid, self.group, kind, json.dumps(data), data['retrieved_at']))
        return sid

    def source(self, sid: str):
        row = self.db.execute('SELECT * FROM discovery_sources WHERE id=? AND group_id=?', (sid, self.group)).fetchone()
        return {**json.loads(row['data_json']), 'kind': row['kind']} if row else None

    def news(self, kind: str, area: str) -> list[dict]:
        now = int(time.time())
        row = self.db.execute('SELECT * FROM discovery_cache WHERE group_id=? AND kind=?', (self.group, kind)).fetchone()
        if row and now < row['attempted_at'] + 3600:
            return json.loads(row['data_json'])
        try:
            result = self.public.news(area if kind == 'local_news' else '')
            outcome = 'available' if result else 'no_fresh_items'
        except Exception:
            result, outcome = [], 'source_unavailable'
        with self.db:
            self.db.execute('''INSERT INTO discovery_cache VALUES (?,?,?,?,?) ON CONFLICT(group_id,kind)
                DO UPDATE SET attempted_at=excluded.attempted_at,data_json=excluded.data_json,outcome=excluded.outcome''',
                (self.group, kind, now, json.dumps(result), outcome))
            self.state.activity.add('discovery_source', ActivitySeverity.INFO,
                f'{kind.replace("_", " ")}: {outcome.replace("_", " ")}', details={'outcome': outcome})
        return result

    def select(self, legacy: list[Stimulus], seed: str, previous: list[str]) -> tuple[list[Stimulus], dict]:
        if not self.db.execute('SELECT 1 FROM discovery_settings WHERE group_id=?', (self.group,)).fetchone():
            return legacy, {'selection_policy': 'legacy; discovery not configured'}
        config = self.config()
        pools = {k: [] for k in config['sources']}
        for item in legacy:
            kind = 'memory' if item.provider == 'agent-memory' else 'catalog'
            if kind in pools:
                pools[kind].append(item)
        if 'creative' in pools:
            for query, title in CREATIVE:
                sid = self.remember('creative', {'title': title, 'query': query, 'url': '', 'excerpt': '',
                    'published_at': None, 'retrieved_at': int(time.time())})
                pools['creative'].append(Stimulus('creative', title, 'creative_possibility', 'discovery', sid))
        if 'interests' in pools:
            # Only revisit explicitly selected, active candidates; rejection and generic enthusiasm do not qualify.
            rows = self.db.execute('''SELECT DISTINCT s.* FROM candidate_responses r
                JOIN passing_candidates c ON c.id=r.candidate_id JOIN agency_stimuli s ON s.id=c.stimulus_id
                JOIN memory_statements m ON m.id=r.statement_id
                WHERE s.group_id=? AND c.status='active' AND r.reaction IN ('choose','alternative')
                AND m.status='active' AND s.retrieved_at<? ORDER BY s.retrieved_at DESC LIMIT 12''',
                (self.group, int(time.time()) - 86400)).fetchall()
            for row in rows:
                pools['interests'].append(Stimulus(row['domain'], row['summary'], 'chosen_interest',
                    'chosen-candidate', row['id'], row['statement_id']))
        for kind in ('world_news', 'local_news'):
            if kind in pools:
                for data in self.news(kind, config['local_area']):
                    sid = self.remember(kind, data)
                    title = data['title'] + ' (published ' + time.strftime('%Y-%m-%d', time.gmtime(data['published_at'])) + ')'
                    pools[kind].append(Stimulus(kind, title, 'current_event', 'discovery', sid))
        # Equal source rotation across invitations, not a new set of timers.
        kinds = list(pools)
        offset = self.db.execute('SELECT COUNT(*) FROM exploration_sessions WHERE group_id=?', (self.group,)).fetchone()[0] % len(kinds)
        kinds = kinds[offset:] + kinds[:offset]
        for pool in pools.values():
            pool.sort(key=lambda s: (s.locator in previous, _id(seed, s.locator)))
        result = []
        while len(result) < 3:
            before = len(result)
            for kind in kinds:
                # Recent identical stimuli are suppressed, including declined offers.
                item = next((s for s in pools[kind] if s.locator not in previous
                             and s.locator not in {v.locator for v in result}), None)
                if item:
                    result.append(item)
                if len(result) == 3:
                    break
            if len(result) == before:
                break
        return result, {'source_mix': config['sources'], 'source_rotation': kinds,
                        'selection_policy': 'diverse sources; recent items suppressed'}

    def public_material(self, candidate_id: str) -> dict | None:
        row = self.db.execute('''SELECT s.* FROM passing_candidates c JOIN agency_stimuli s ON s.id=c.stimulus_id
            WHERE c.id=? AND c.group_id=?''', (candidate_id, self.group)).fetchone()
        if row is None:
            return None
        if row['provider'] == 'local-catalog':
            return {'title': row['summary'], 'query': QUERIES.get(row['domain'], ''), 'url': ''}
        if row['provider'] == 'discovery':
            return self.source(row['locator'])
        if row['provider'] == 'chosen-candidate':
            # Resolve one original candidate; never export its private memory quotation.
            original = self.db.execute('SELECT provider FROM agency_stimuli WHERE id=? AND group_id=?',
                                       (row['locator'], self.group)).fetchone()
            if original and original['provider'] in {'local-catalog', 'discovery'}:
                return self.public_material(row['locator'])
        return None

    def snapshot(self) -> dict:
        recent = []
        for row in self.db.execute('SELECT * FROM exploration_experiences WHERE group_id=? ORDER BY created_at DESC LIMIT 20', (self.group,)):
            item = dict(row)
            item['sources'] = json.loads(item.pop('result_json'))
            recent.append(item)
        return {'config': self.config(), 'experiences': recent,
                'sources': [dict(id=r['id'], kind=r['kind'], **json.loads(r['data_json'])) for r in self.db.execute(
                    'SELECT * FROM discovery_sources WHERE group_id=? ORDER BY retrieved_at DESC,rowid DESC LIMIT 12', (self.group,))],
                'feeds': [dict(r) for r in self.db.execute('SELECT kind,attempted_at,outcome FROM discovery_cache WHERE group_id=?', (self.group,))]}
