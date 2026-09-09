"""Versioned, observational garden. No promotion, consent parsing or execution.

Passing records mirror source candidates transactionally. Shared memory remains
the authority for evidence and its corrections, rather than copied quotations.
Protected layers have storage support only until manual bootstrap is reviewed.
"""
from __future__ import annotations

import json
import sqlite3
import time
from enum import Enum
from typing import Any

from .memory import MemoryStore


class Layer(str, Enum):
    ROOTS = 'roots'
    GROWING = 'growing'
    PASSING = 'passing'


class Ownership(str, Enum):
    SELF = 'self'
    SHARED = 'shared'
    INFRASTRUCTURE = 'infrastructure'


class ItemStatus(str, Enum):
    ACTIVE = 'active'
    PAUSED = 'paused'
    ARCHIVED = 'archived'
    SUPERSEDED = 'superseded'


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError('invalid bounded garden text')
    return value.strip()


def validate_correction(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'item_id', 'expected_version', 'annotation', 'reason'}:
        raise ValueError('garden correction accepts only item, version, annotation and reason')
    _text(data['item_id'], 128)
    _text(data['annotation'], 1000, empty=True)
    _text(data['reason'], 500)
    if type(data['expected_version']) is not int or data['expected_version'] < 1:
        raise ValueError('expected version is required')
    return data


class GardenStore:
    def __init__(self, db: sqlite3.Connection, group_id: str):
        self.db, self.group = db, group_id

    def _item(self, item_id: str) -> dict[str, Any]:
        row = self.db.execute('''SELECT i.*,v.status,v.content,v.annotation,v.actor,v.origin,v.created_at
            FROM garden_items i JOIN garden_versions v ON v.item_id=i.id AND v.version=i.current_version
            WHERE i.id=? AND i.group_id=?''', (item_id, self.group)).fetchone()
        if row is None:
            raise ValueError('garden item not found in this conversation')
        return dict(row)

    def _version(self, item_id: str, version: int, *, status: str, content: str,
                 annotation: str, rationale: str, actor: str, origin: str,
                 event_id: str | None = None, statement_id: str | None = None,
                 command_id: str | None = None) -> None:
        self.db.execute('''INSERT INTO garden_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (item_id, version, status, content, annotation, rationale, actor, origin,
             int(time.time()), self.group, event_id, statement_id, command_id))

    def capture_candidate(self, candidate_id: str, *, event_id: str | None = None,
                          command_id: str | None = None, reason: str = '') -> None:
        """Caller owns the candidate transaction; never commits independently."""
        source = self.db.execute('''SELECT c.status,s.summary,s.event_id,s.statement_id,e.actor,e.origin
            FROM passing_candidates c JOIN agency_stimuli s ON s.id=c.stimulus_id
            JOIN agency_events e ON e.id=s.event_id
            WHERE c.id=? AND c.group_id=?''', (candidate_id, self.group)).fetchone()
        if source is None:
            raise ValueError('candidate not found')
        existing = self.db.execute('SELECT 1 FROM garden_items WHERE id=?', (candidate_id,)).fetchone()
        if existing is None:
            self.db.execute("INSERT INTO garden_items VALUES (?,?,'passing','self',?,1)",
                            (candidate_id, self.group, candidate_id))
            self._version(candidate_id, 1, status=source['status'], content=source['summary'], annotation='',
                          rationale='Provisional seed from exploration; not an adopted preference.',
                          actor=source['actor'], origin=source['origin'], event_id=source['event_id'],
                          statement_id=source['statement_id'])
        elif command_id:
            item = self._item(candidate_id)
            if self.db.execute('SELECT 1 FROM garden_versions WHERE item_id=? AND command_id=?',
                               (candidate_id, command_id)).fetchone():
                return
            self._version(candidate_id, item['current_version'] + 1, status=source['status'],
                          content=item['content'], annotation=item['annotation'], rationale=reason,
                          actor='marc', origin='administrative_status', event_id=event_id, command_id=command_id)
            self.db.execute('UPDATE garden_items SET current_version=current_version+1 WHERE id=?', (candidate_id,))

    def correct(self, command_id: str, data: dict[str, Any]) -> dict[str, Any]:
        validate_correction(data)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            item = self._item(data['item_id'])
            prior = self.db.execute('SELECT version FROM garden_versions WHERE item_id=? AND command_id=?',
                                    (item['id'], command_id)).fetchone()
            if prior:
                return {'item_id': item['id'], 'version': prior['version']}
            command = self.db.execute("SELECT command_type,payload_json FROM commands WHERE id=?", (command_id,)).fetchone()
            if (command is None or command['command_type'] != 'garden_correct'
                    or json.loads(command['payload_json']) != data):
                raise ValueError('correction requires the matching local command')
            if item['current_version'] != data['expected_version']:
                raise ValueError('garden version changed; inspect before correcting')
            version = item['current_version'] + 1
            self._version(item['id'], version, status=item['status'], content=item['content'],
                          annotation=data['annotation'], rationale=data['reason'], actor='marc',
                          origin='administrative_annotation', command_id=command_id)
            self.db.execute('UPDATE garden_items SET current_version=? WHERE id=?', (version, item['id']))
        return {'item_id': item['id'], 'version': version}

    def list_items(self, layer: str = '', status: str = '', after: str = '', limit: int = 30) -> dict[str, Any]:
        if layer:
            Layer(layer)
        if status:
            ItemStatus(status)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid garden page size')
        _text(after, 128, empty=True)
        rows = [dict(r) for r in self.db.execute('''SELECT i.*,v.status,v.content,v.annotation,
            v.created_at AS last_version_at,first.actor AS origin_actor,first.origin,
            COALESCE((SELECT MAX(e.created_at) FROM agency_events e JOIN agency_stimuli s ON s.session_id=e.session_id
                WHERE s.id=i.candidate_id),v.created_at) AS last_interaction_at
            FROM garden_items i JOIN garden_versions v ON v.item_id=i.id AND v.version=i.current_version
            JOIN garden_versions first ON first.item_id=i.id AND first.version=1
            WHERE i.group_id=? AND (?='' OR i.layer=?) AND (?='' OR v.status=?) AND i.id>?
            ORDER BY i.id LIMIT ?''', (self.group, layer, layer, status, status, after, limit + 1))]
        items = rows[:limit]
        counts = {r['layer']: r['n'] for r in self.db.execute('''SELECT layer,COUNT(*) n
            FROM garden_items WHERE group_id=? GROUP BY layer''', (self.group,))}
        return {'items': items, 'has_more': len(rows) > limit,
                'next_after': items[-1]['id'] if items else after,
                'counts': {layer.value: counts.get(layer.value, 0) for layer in Layer},
                'policy': 'Observational only. No promotion, prompt projection or execution. Roots require reviewed manual bootstrap.'}

    def detail(self, item_id: str, after: int = 0, limit: int = 30) -> dict[str, Any]:
        item = self._item(item_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid garden history page')
        rows = [dict(r) for r in self.db.execute('''SELECT * FROM garden_versions
            WHERE item_id=? AND version>? ORDER BY version LIMIT ?''', (item_id, after, limit + 1))]
        versions = rows[:limit]
        memory = MemoryStore(self.db, self.group)
        for version in versions:
            version['observation'] = memory.detail(version['statement_id']) if version['statement_id'] else None
        item.update(versions=versions, has_more=len(rows) > limit,
                    next_after=versions[-1]['version'] if versions else after)
        source = self.db.execute('SELECT session_id,provider,locator,selection_kind FROM agency_stimuli WHERE id=? AND group_id=?',
                                 (item['candidate_id'], self.group)).fetchone()
        item['source'] = dict(source) if source else None
        item['responses'] = []
        if item['candidate_id']:
            for row in self.db.execute('''SELECT cr.reaction,cr.statement_id,r.message_id,r.status,r.classification,
                r.created_at,r.step,r.session_id FROM candidate_responses cr JOIN exploration_responses r
                ON r.session_id=cr.session_id AND r.step=cr.step
                WHERE cr.candidate_id=? AND r.group_id=? ORDER BY r.created_at,r.step LIMIT 10''', (item['candidate_id'], self.group)):
                reaction = dict(row)
                reaction['observation'] = memory.detail(reaction['statement_id'])
                item['responses'].append(reaction)
        item['relationships'] = [dict(r) for r in self.db.execute('''SELECT * FROM garden_relationships
            WHERE group_id=? AND (source_id=? OR target_id=?) ORDER BY created_at,id LIMIT 101''', (self.group, item_id, item_id))]
        item['relationships_truncated'] = len(item['relationships']) > 100
        item['relationships'] = item['relationships'][:100]
        item['projects'] = [self.project(r['project_id']) for r in self.db.execute('''SELECT project_id
            FROM garden_project_items WHERE item_id=? AND group_id=? ORDER BY project_id LIMIT 21''', (item_id, self.group))]
        item['projects_truncated'] = len(item['projects']) > 20
        item['projects'] = item['projects'][:20]
        return item

    def trail(self, item_id: str, after: int = 0, limit: int = 30) -> dict[str, Any]:
        """Paginate original exploration events, including pre-migration history."""
        item = self._item(item_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid garden trail page')
        rows = [dict(r) for r in self.db.execute('''SELECT e.rowid AS sequence,e.* FROM agency_events e
            JOIN agency_stimuli s ON s.session_id=e.session_id AND s.group_id=e.group_id
            WHERE s.id=? AND e.group_id=? AND e.rowid>? ORDER BY e.rowid LIMIT ?''',
            (item['candidate_id'], self.group, after, limit + 1))]
        items = rows[:limit]
        memory = MemoryStore(self.db, self.group)
        for event in items:
            event['details'] = json.loads(event.pop('details_json'))
            event['observation'] = memory.detail(event['statement_id']) if event['statement_id'] else None
        return {'items': items, 'has_more': len(rows) > limit,
                'next_after': items[-1]['sequence'] if items else after}

    def create_project(self, project_id: str, item_ids: list[str], *, title: str, outcome: str,
                       next_step: str, stopping_condition: str) -> None:
        """Local planning storage only; no endpoint, scheduler, tools or authority grant."""
        for value in (project_id, title, outcome, next_step, stopping_condition):
            _text(value, 500)
        if not item_ids or len(item_ids) > 10 or len(set(item_ids)) != len(item_ids):
            raise ValueError('project requires 1..10 distinct Growing items')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if any(self._item(i)['layer'] != Layer.GROWING for i in item_ids):
                raise ValueError('projects require Growing items')
            self.db.execute('INSERT INTO garden_projects VALUES (?,?,?,?,?,?,?)',
                (project_id, self.group, title, outcome, next_step, stopping_condition, int(time.time())))
            self.db.executemany('INSERT INTO garden_project_items VALUES (?,?,?)', [(project_id, i, self.group) for i in item_ids])

    def project(self, project_id: str) -> dict[str, Any]:
        row = self.db.execute('SELECT * FROM garden_projects WHERE id=? AND group_id=?', (project_id, self.group)).fetchone()
        if row is None:
            raise ValueError('project not found in this conversation')
        result = dict(row)
        result['status'] = 'planned'
        latest = self.db.execute("SELECT kind FROM garden_attention WHERE project_id=? AND kind IN ('pause','resume','complete','abandon') ORDER BY rowid DESC LIMIT 1", (project_id,)).fetchone()
        if latest:
            result['status'] = {'pause': 'paused', 'resume': 'active', 'complete': 'completed', 'abandon': 'abandoned'}[latest[0]]
        result['budgets'] = [dict(r) for r in self.db.execute('''SELECT unit,
            SUM(CASE WHEN kind='allocate' THEN amount ELSE 0 END) AS allocated,
            SUM(CASE WHEN kind='consume' THEN amount ELSE 0 END) AS consumed FROM garden_attention
            WHERE project_id=? AND unit!='none' GROUP BY unit''', (project_id,))]
        result['history'] = [dict(r) for r in self.db.execute('SELECT * FROM garden_attention WHERE project_id=? ORDER BY rowid DESC LIMIT 101', (project_id,))]
        result['history_truncated'] = len(result['history']) > 100
        result['history'] = list(reversed(result['history'][:100]))
        return result

    def record_attention(self, project_id: str, entry_id: str, *, kind: str, unit: str = 'none',
                         amount: int = 0, result: str = '', reason: str, actor: str = 'system') -> None:
        _text(entry_id, 128)
        _text(reason, 500)
        _text(result, 2000, empty=True)
        if actor not in {'marc', 'system'} or kind not in {'allocate','consume','pause','resume','complete','abandon'}:
            raise ValueError('invalid attention event')
        if type(amount) is not int or not 0 <= amount <= 10000:
            raise ValueError('attention amount outside bounds')
        if (kind in {'allocate','consume'} and (unit not in {'minutes','tool_calls'} or amount == 0)
                or kind not in {'allocate','consume'} and (unit != 'none' or amount != 0)):
            raise ValueError('invalid attention unit')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            project = self.project(project_id)
            prior = self.db.execute('SELECT * FROM garden_attention WHERE project_id=? AND entry_id=?', (project_id, entry_id)).fetchone()
            if prior:
                if any(prior[k] != v for k, v in dict(kind=kind, unit=unit, amount=amount, result=result, reason=reason, actor=actor).items()):
                    raise ValueError('attention event ID conflict')
                return
            if project['status'] in {'completed','abandoned'}:
                raise ValueError('project already stopped')
            if kind == 'allocate':
                allocated = next((b['allocated'] for b in project['budgets'] if b['unit'] == unit), 0)
                if allocated + amount > 10000:
                    raise ValueError('total attention allocation exceeds 10000 units')
            if kind == 'consume':
                budget = next((b for b in project['budgets'] if b['unit'] == unit), None)
                if project['status'] == 'paused' or budget is None or budget['consumed'] + amount > budget['allocated']:
                    raise ValueError('attention budget unavailable')
            if kind == 'resume' and project['status'] != 'paused':
                raise ValueError('only a paused project can resume')
            self.db.execute('INSERT INTO garden_attention VALUES (?,?,?,?,?,?,?,?,?,?)',
                (project_id, entry_id, self.group, kind, unit, amount, result, reason, actor, int(time.time())))


def public_garden(value: Any) -> Any:
    """Redact credential-shaped text at the HTTP boundary, retaining private evidence locally."""
    from .redaction import redact_text
    if isinstance(value, dict):
        return {k: public_garden(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public_garden(v) for v in value]
    return redact_text(value, maximum=max(2000, len(value) + 1)) if isinstance(value, str) else value
