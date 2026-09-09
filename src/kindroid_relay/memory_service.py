"""Bounded capture/indexing. Semantic capture uses isolated Codex extraction turns,
never Kindroid requests or the persistent conversation thread.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from functools import lru_cache
from contextlib import nullcontext
from typing import Any

from .memory import EXTRACTOR_VERSION, MemoryStore, _id, validate_memory_payload


class LocalEmbedder:
    """Optional SentenceTransformer; preinstalled local model only, never downloads."""
    def __init__(self, path: str, revision: str):
        self.path = Path(path)
        self.version = f'local-sentence-transformer:{_id(str(self.path), revision)}'
        self.model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self.model is None:
            if not self.path.is_absolute() or str(self.path).startswith(('\\\\', '//')) or not self.path.is_dir():
                raise ValueError('local embedding model is unavailable')
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(str(self.path), local_files_only=True,
                                             trust_remote_code=False, device='cpu')
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=1)
def local_embedder(path: str, revision: str) -> LocalEmbedder:
    return LocalEmbedder(path, revision)


class LocalObservationExtractor:
    """Conservative v1 candidate selector, not a claim-understanding model.

    Exact sentence spans only. No invented entities or automatic identity merging.
    Explicitly named themes become separate topic candidates for operator review.
    """
    version = EXTRACTOR_VERSION
    rules = (
        ('reported_root', r'\b(root|foundational|fundamental|core value)\b'),
        ('interest', r'\b(interested|curious|interest|love|enjoy|explore)\b'),
        ('commitment', r'\b(promise|committed|commitment|will remember)\b'),
        ('experience', r'\b(remember|visited|experienced|grew up)\b'),
        ('offer', r'\b(can help|would you like|offer)\b'),
        ('question', r'\b(wonder|unfinished|question)\b'),
    )

    def extract(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        observations = []
        # Work and input bounded; long messages remain available for manual selection.
        for match in re.finditer(r'[^.!?\n]+[.!?]?', source['body'][:8000]):
            passage = match.group().strip()
            if not 8 <= len(passage) <= 2000:
                continue
            for kind, pattern in self.rules:
                theme = re.search(pattern, passage, re.IGNORECASE)
                if theme:
                    observations.append({
                        'message_id': source['remote_id'], 'quote': passage,
                        'start': match.start() + len(match.group()) - len(match.group().lstrip()),
                        'kind': kind, 'interpretation': '', 'temporal_scope': 'As stated at source time; current applicability unverified',
                        'entities': [{'kind': 'topic', 'label': theme.group().casefold(), 'aliases': []}],
                    })
                    break
            if len(observations) == 3:
                break
        return observations


class MemoryService:
    def __init__(self, state: Any, settings: Any, *, extractor: Any = None, embedder: Any = None, phase_context: Any = None):
        self.state, self.settings = state, settings
        self.store = MemoryStore(state.connection, settings.group_id)
        from .memory_extractor import SemanticObservationExtractor
        self.extractor = extractor or (SemanticObservationExtractor(settings.codex_model, settings.memory_extraction_timeout_seconds)
                                      if settings.memory_extractor == 'semantic' else LocalObservationExtractor())
        self.version = getattr(self.extractor, 'version', EXTRACTOR_VERSION)
        self.phase_context = phase_context or (lambda *_args: nullcontext())
        path = getattr(settings, 'memory_model_path', '')
        self.embedder = embedder or (local_embedder(path, settings.memory_model_revision) if path else None)

    def status(self) -> dict[str, Any]:
        rows = self.state.connection.execute(
            'SELECT x.status, COUNT(*) AS count FROM memory_extractions x JOIN messages m ON m.remote_id=x.message_id '
            'WHERE m.group_id=? AND x.version=? GROUP BY x.status', (self.settings.group_id, self.version))
        pending = self.state.connection.execute(
            'SELECT COUNT(*) FROM messages m WHERE m.group_id=? AND NOT EXISTS '
            '(SELECT 1 FROM memory_extractions x WHERE x.message_id=m.remote_id AND x.version=?)',
            (self.settings.group_id, self.version)).fetchone()[0]
        return {'enabled': self.settings.memory_enabled, 'auto_capture': self.settings.memory_auto_capture,
                'pending_local_messages': pending,
                'extractor': self.version, 'extractions': {r['status']: r['count'] for r in rows},
                'last_capture': self.state.get_meta('memory_last_capture:' + self.settings.group_id, ''),
                'semantic': 'local_model_configured' if self.embedder else 'not_configured_lexical_fallback'}

    def command(self, operation: str, payload: dict[str, Any], command_id: str) -> dict[str, Any]:
        validate_memory_payload(operation, payload)
        if operation == 'save':
            return {'statement_id': self.store.save(payload)}
        if operation in {'revise', 'link', 'evidence'}:
            return self.store.revise(operation, payload, command_id)
        if not self.settings.memory_enabled:
            raise ValueError('agent memory must be enabled before extraction or indexing')
        if operation == 'extract':
            return self.capture(payload['limit'])
        return self.reindex(payload['limit'])

    def capture(self, limit: int = 3) -> dict[str, Any]:
        if not self.settings.memory_enabled:
            return {'status': 'disabled', 'processed': 0}
        validate_memory_payload('extract', {'limit': limit})
        if hasattr(self.extractor, 'extract_batch'):
            return self._semantic_capture(limit)
        db = self.state.connection
        now = int(time.time())
        # An interrupted read-only extraction can be marked failed; no chat is replayed.
        with db:
            db.execute("UPDATE memory_extractions SET status='failed' WHERE status='running'")
        spent = db.execute('SELECT COUNT(*) FROM memory_extractions WHERE attempted_at>?', (now - 86400,)).fetchone()[0]
        remaining = max(0, min(limit, 10, self.settings.memory_max_messages_24h - spent))
        rows = db.execute(
            'SELECT m.* FROM messages m WHERE m.group_id=? AND NOT EXISTS '
            '(SELECT 1 FROM memory_extractions x WHERE x.message_id=m.remote_id AND x.version=?) '
            'ORDER BY m.timestamp, m.remote_id LIMIT ?', (self.settings.group_id, EXTRACTOR_VERSION, remaining)).fetchall()
        processed, failed = 0, 0
        for row in rows:
            with db:
                db.execute('INSERT INTO memory_extractions VALUES (?,?,?,?)',
                           (row['remote_id'], EXTRACTOR_VERSION, 'running', now))
            try:
                candidates = self.extractor.extract(dict(row))
                if not isinstance(candidates, list) or len(candidates) > 3:
                    raise ValueError('invalid extraction batch')
                for candidate in candidates:
                    if candidate.get('message_id') != row['remote_id']:
                        raise ValueError('extractor changed source message')
                    self.store.save(candidate, origin='system_selection', version=EXTRACTOR_VERSION)
                status = 'completed'
                processed += 1
            except Exception:
                # No raw model/source text in error events or command results.
                status = 'failed'
                failed += 1
            with db:
                db.execute('UPDATE memory_extractions SET status=? WHERE message_id=? AND version=?',
                           (status, row['remote_id'], EXTRACTOR_VERSION))
        return {'processed': processed, 'failed': failed, 'status': 'budget_exhausted' if not remaining else 'completed'}

    def _semantic_capture(self, limit: int) -> dict[str, Any]:
        from .activity import WorkerPhase
        from .memory_extractor import validate_extraction
        db, group = self.state.connection, self.settings.group_id
        now = int(time.time())
        # Claim and charge before model I/O. Restart never repeats an interrupted batch.
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE memory_extractions SET status='failed' WHERE status='running' AND message_id IN "
                       '(SELECT remote_id FROM messages WHERE group_id=?)', (group,))
            spent = db.execute('SELECT COUNT(*) FROM memory_extractions WHERE attempted_at>?', (now - 86400,)).fetchone()[0]
            remaining = max(0, min(limit, 3, self.settings.memory_max_messages_24h - spent))
            sources = [dict(r) for r in db.execute(
                'SELECT m.* FROM messages m WHERE m.group_id=? AND NOT EXISTS '
                '(SELECT 1 FROM memory_extractions x WHERE x.message_id=m.remote_id AND x.version=?) '
                'ORDER BY m.timestamp,m.remote_id LIMIT ?', (group, self.version, remaining))]
            for source in sources:
                db.execute('INSERT INTO memory_extractions VALUES (?,?,?,?)', (source['remote_id'], self.version, 'running', now))
        if not sources:
            return {'status': 'budget_exhausted' if not remaining else 'completed', 'processed': 0, 'failed': 0}
        first = sources[0]
        source_fields = ('group_id', 'body', 'sender', 'sender_type', 'display_name', 'kindroid_id', 'timestamp')
        fingerprints = {row['remote_id']: _id({k: row[k] for k in source_fields}) for row in sources}
        context = [dict(r) for r in db.execute(
            'SELECT * FROM messages WHERE group_id=? AND (timestamp<? OR (timestamp=? AND remote_id<?)) '
            'ORDER BY timestamp DESC, remote_id DESC LIMIT 6',
            (group, first['timestamp'], first['timestamp'], first['remote_id']))][::-1]
        for row in context:
            row['body'] = row['body'][:1000]
        truncated = sum(len(row['body']) > 4000 for row in sources)
        sources = [{**row, 'body': row['body'][:4000]} for row in sources]
        # Bounded lexical entry points are hints only, never an identity merge.
        candidates = []
        source_text = ' '.join(s['body'] for s in sources).casefold()
        candidate_chars = 0
        for row in db.execute('SELECT * FROM memory_entities WHERE group_id=? ORDER BY id LIMIT 500', (group,)):
            aliases = json.loads(row['aliases_json'])
            if not any(name.casefold() in source_text for name in [row['label'], *aliases]):
                continue
            candidate = {key: row[key] for key in ('id', 'kind', 'label')}
            candidate['aliases'] = aliases
            candidate_chars += len(json.dumps(candidate))
            if candidate_chars > 4000 or len(candidates) >= 12:
                break
            candidates.append(candidate)
        try:
            with self.phase_context(WorkerPhase.RUNNING_CODEX, 'semantic memory extraction'):
                items = self.extractor.extract_batch(sources, context, candidates)
            # Validate even injected/custom adapters, before any durable observation.
            clean_items = [{k: v for k, v in item.items() if k != 'start'} for item in items]
            items = validate_extraction({'observations': clean_items}, sources, candidates)
            with db:
                db.execute('BEGIN IMMEDIATE')
                for original in sources:
                    current = db.execute('SELECT * FROM messages WHERE remote_id=? AND group_id=?',
                                         (original['remote_id'], group)).fetchone()
                    if current is None or _id({k: current[k] for k in source_fields}) != fingerprints[original['remote_id']]:
                        raise ValueError('source changed during semantic extraction')
                model = getattr(getattr(self.extractor, 'runner', None), 'last_model', None) or self.settings.codex_model or 'runtime_default'
                if not isinstance(model, str) or len(model) > 128:
                    raise ValueError('invalid extraction model metadata')
                self.store.save_semantic(items, self.version, _id(self.version, [s['remote_id'] for s in sources]), model=model)
                for source in sources:
                    db.execute("UPDATE memory_extractions SET status='completed' WHERE message_id=? AND version=?",
                               (source['remote_id'], self.version))
            result = {'status': 'completed', 'processed': len(sources), 'failed': 0, 'observations': len(items), 'truncated_sources': truncated}
        except Exception:
            with db:
                for source in sources:
                    db.execute("UPDATE memory_extractions SET status='failed' WHERE message_id=? AND version=?",
                               (source['remote_id'], self.version))
            result = {'status': 'failed', 'processed': 0, 'failed': len(sources), 'truncated_sources': truncated}
        self.state.set_meta('memory_last_capture:' + group, json.dumps(result))
        return result

    def reindex(self, limit: int = 10) -> dict[str, Any]:
        if self.embedder is None:
            return {'status': 'not_configured_lexical_fallback', 'indexed': 0}
        items, truncated = self.store.corpus()
        selected = []
        for item in items:
            text = self.store.searchable(item)
            existing = self.state.connection.execute(
                'SELECT text_hash FROM memory_vectors WHERE statement_id=? AND model_version=?',
                (item['id'], self.embedder.version)).fetchone()
            if existing is None or existing['text_hash'] != _id(text):
                selected.append((item['id'], text))
            if len(selected) >= min(limit, 10):
                break
        if not selected:
            return {'status': 'current', 'indexed': 0, 'truncated': truncated}
        try:
            vectors = self.embedder.encode([text for _, text in selected])
            from .memory import cosine
            if len(vectors) != len(selected):
                raise ValueError('embedding count mismatch')
            for vector in vectors:
                if len(vector) > 4096:
                    raise ValueError('embedding too large')
                cosine(vector, vector)
            with self.state.connection:
                for (statement_id, text), vector in zip(selected, vectors):
                    self.state.connection.execute('INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?)',
                        (statement_id, self.embedder.version, _id(text), json.dumps(vector)))
            return {'status': 'indexed', 'indexed': len(selected), 'truncated': truncated}
        except Exception:
            return {'status': 'unavailable_lexical_fallback', 'indexed': 0}

    def automatic_tick(self) -> None:
        if not self.settings.memory_enabled or not self.settings.memory_auto_capture:
            return
        key = 'memory_next_capture:' + self.settings.group_id
        if self.state.get_int(key, 0) > time.time():
            return
        self.state.set_meta(key, int(time.time()) + 600)
        self.capture(3)

    def context(self, query: str, *, mode: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        if mode is not None:
            from .agency import Mode, mode_context
            selected_mode = Mode(mode)
            memory = self.context(query)
            if selected_mode == Mode.EXPLORER and self.settings.memory_enabled:
                # A question need not literally contain the English word "question".
                # These are historical observations, not verified unfinished tasks.
                ids = self.state.connection.execute("SELECT id FROM memory_statements WHERE group_id=? AND status='active' AND kind IN ('question','interest') ORDER BY kind='question' DESC,created_at DESC,id LIMIT 3",
                                                    (self.settings.group_id,)).fetchall()
                existing = {item['id'] for item in memory.get('items', [])}
                additions = []
                for row in ids:
                    if row['id'] not in existing:
                        item = self.store.detail(row['id'])
                        item.pop('revisions', None)
                        item['retrieval_reason'] = ['attributed question/interest; current applicability unverified']
                        additions.append(item)
                memory['items'] = additions + memory.get('items', [])
            return mode_context(self.state, self.settings.group_id, memory, selected_mode, session_id)
        if not self.settings.memory_enabled:
            return {'status': 'disabled', 'items': []}
        try:
            return self.store.search(query[:500], embedder=self.embedder)
        except Exception:
            return {'status': 'unavailable', 'items': [], 'context_complete': False}
