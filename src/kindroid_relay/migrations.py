from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path


logger = logging.getLogger(__name__)

Migration = tuple[int, Callable[[sqlite3.Connection], None]]


class SchemaVersionError(RuntimeError):
    pass


BASE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        remote_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        display_name TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        body TEXT NOT NULL,
        processed INTEGER NOT NULL DEFAULT 0,
        received_at INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_pending
        ON messages(processed, timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        body_hash TEXT NOT NULL,
        source_message_id TEXT,
        request_kindroid_reply INTEGER,
        status TEXT NOT NULL,
        error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outbox_hash ON outbox(body_hash)",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        source_message_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        instruction TEXT NOT NULL,
        risk TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS history_requests (
        requested_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS initiative_checks (
        checked_at INTEGER NOT NULL,
        outcome TEXT NOT NULL
    )
    """,
)


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _migration_1(connection: sqlite3.Connection) -> None:
    for statement in BASE_SCHEMA_STATEMENTS:
        connection.execute(statement)

    outbox_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(outbox)").fetchall()
    }
    if "source_message_id" not in outbox_columns:
        connection.execute("ALTER TABLE outbox ADD COLUMN source_message_id TEXT")
    if "request_kindroid_reply" not in outbox_columns:
        connection.execute(
            "ALTER TABLE outbox ADD COLUMN request_kindroid_reply INTEGER"
        )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_source_message "
        "ON outbox(source_message_id) WHERE source_message_id IS NOT NULL"
    )
    connection.execute(
        "UPDATE messages SET sender_type = sender WHERE sender_type = ''"
    )


RUNTIME_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE commands (
        id TEXT PRIMARY KEY,
        command_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (
            status IN (
                'queued', 'running', 'completed', 'failed',
                'interrupted', 'cancelled'
            )
        ),
        requested_at INTEGER NOT NULL,
        started_at INTEGER,
        completed_at INTEGER,
        result_json TEXT,
        error TEXT
    )
    """,
    "CREATE INDEX idx_commands_queue ON commands(status, requested_at, id)",
    "CREATE INDEX idx_commands_recent ON commands(requested_at DESC, id DESC)",
    """
    CREATE TABLE activity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (
            severity IN ('debug', 'info', 'warning', 'error')
        ),
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        correlation_id TEXT,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX idx_activity_events_created
        ON activity_events(created_at, id)
    """,
    """
    CREATE TABLE worker_status (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        phase TEXT NOT NULL,
        phase_started_at INTEGER NOT NULL,
        heartbeat_at INTEGER NOT NULL,
        active_command_id TEXT,
        note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(active_command_id) REFERENCES commands(id)
    )
    """,
)


def _migration_2(connection: sqlite3.Connection) -> None:
    for statement in RUNTIME_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _migration_3(connection: sqlite3.Connection) -> None:
    # Keep legacy primary keys stable: outbox, approvals, and activity refer to
    # them. New locally captured messages acquire a separate remote alias later.
    connection.execute("ALTER TABLE messages ADD COLUMN kindroid_id TEXT")
    connection.execute("UPDATE messages SET kindroid_id = remote_id")
    connection.execute("CREATE UNIQUE INDEX idx_messages_kindroid_id ON messages(kindroid_id)")
    connection.execute("ALTER TABLE messages ADD COLUMN sync_after INTEGER")
    connection.execute("""
        CREATE TABLE generation_turns (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            ai_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            outbox_id TEXT NOT NULL UNIQUE REFERENCES outbox(id),
            anchor_message_id TEXT NOT NULL REFERENCES messages(remote_id),
            status TEXT NOT NULL CHECK(status IN ('generating', 'received', 'uncertain', 'reconciled')),
            after_timestamp INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)


def _migration_4(connection: sqlite3.Connection) -> None:
    # Evidence is authoritative; graph/search data is not identity or approval.
    statements = (
        """CREATE TABLE memory_statements (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, kind TEXT NOT NULL,
            interpretation TEXT NOT NULL, origin TEXT NOT NULL, version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','disputed','superseded','retracted')),
            temporal_scope TEXT NOT NULL, created_at INTEGER NOT NULL,
            UNIQUE(id, group_id))""",
        """CREATE TABLE memory_evidence (
            statement_id TEXT NOT NULL REFERENCES memory_statements(id),
            message_id TEXT NOT NULL REFERENCES messages(remote_id),
            start_offset INTEGER NOT NULL CHECK(start_offset >= 0), quote TEXT NOT NULL,
            speaker_snapshot TEXT NOT NULL, PRIMARY KEY(statement_id, message_id, start_offset))""",
        """CREATE TABLE memory_entities (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, kind TEXT NOT NULL,
            label TEXT NOT NULL, aliases_json TEXT NOT NULL, UNIQUE(id, group_id))""",
        """CREATE TABLE memory_links (
            statement_id TEXT NOT NULL, entity_id TEXT NOT NULL, group_id TEXT NOT NULL,
            relation TEXT NOT NULL, origin TEXT NOT NULL,
            FOREIGN KEY(statement_id, group_id) REFERENCES memory_statements(id, group_id),
            FOREIGN KEY(entity_id, group_id) REFERENCES memory_entities(id, group_id),
            PRIMARY KEY(statement_id, entity_id, relation))""",
        """CREATE TABLE memory_revisions (
            command_id TEXT PRIMARY KEY, statement_id TEXT NOT NULL REFERENCES memory_statements(id),
            operation TEXT NOT NULL, reason TEXT NOT NULL, before_json TEXT NOT NULL,
            after_json TEXT NOT NULL, created_at INTEGER NOT NULL)""",
        """CREATE TABLE memory_extractions (
            message_id TEXT NOT NULL REFERENCES messages(remote_id), version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
            attempted_at INTEGER NOT NULL, PRIMARY KEY(message_id, version))""",
        """CREATE TABLE memory_vectors (
            statement_id TEXT NOT NULL REFERENCES memory_statements(id), model_version TEXT NOT NULL,
            text_hash TEXT NOT NULL, vector_json TEXT NOT NULL,
            PRIMARY KEY(statement_id, model_version))""",
        "CREATE INDEX memory_scope ON memory_statements(group_id, status, created_at)",
        "CREATE INDEX memory_entity_links ON memory_links(entity_id, statement_id)",
        "CREATE INDEX memory_evidence_source ON memory_evidence(message_id)",
        "CREATE INDEX memory_extraction_budget ON memory_extractions(attempted_at)",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_5(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE memory_statements ADD COLUMN uncertainty TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE memory_statements ADD COLUMN extraction_model TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE memory_entities ADD COLUMN possible_matches_json TEXT NOT NULL DEFAULT '[]'")
    connection.execute("ALTER TABLE memory_links ADD COLUMN basis TEXT NOT NULL DEFAULT 'selected'")


def _migration_6(connection: sqlite3.Connection) -> None:
    for sql in (
        """CREATE TABLE exploration_sessions (
            id TEXT PRIMARY KEY REFERENCES commands(id), group_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode='explorer'), version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('prepared','sending','awaiting_sync','observed','silent','uncertain','interrupted','closed')),
            budget INTEGER NOT NULL CHECK(budget=1), seed TEXT NOT NULL,
            inputs_json TEXT NOT NULL, created_at INTEGER NOT NULL,
            outbox_id TEXT UNIQUE REFERENCES outbox(id),
            response_message_id TEXT REFERENCES messages(remote_id),
            UNIQUE(id, group_id))""",
        """CREATE TABLE agency_events (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, session_id TEXT,
            actor TEXT NOT NULL CHECK(actor IN ('hannah','marc','codex','external','system','unknown')),
            origin TEXT NOT NULL CHECK(origin IN ('explicit_statement','conversational_observation','offered_stimulus','research_result','repeated_return','user_suggestion','system_candidate')),
            kind TEXT NOT NULL, version TEXT NOT NULL, created_at INTEGER NOT NULL,
            statement_id TEXT, command_id TEXT REFERENCES commands(id),
            details_json TEXT NOT NULL,
            FOREIGN KEY(session_id,group_id) REFERENCES exploration_sessions(id,group_id),
            FOREIGN KEY(statement_id,group_id) REFERENCES memory_statements(id,group_id),
            UNIQUE(id,group_id))""",
        """CREATE TABLE agency_stimuli (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, session_id TEXT NOT NULL,
            event_id TEXT NOT NULL, provider TEXT NOT NULL, locator TEXT NOT NULL,
            retrieved_at INTEGER NOT NULL, summary TEXT NOT NULL,
            domain TEXT NOT NULL, selection_kind TEXT NOT NULL,
            statement_id TEXT,
            FOREIGN KEY(session_id,group_id) REFERENCES exploration_sessions(id,group_id),
            FOREIGN KEY(event_id,group_id) REFERENCES agency_events(id,group_id),
            FOREIGN KEY(statement_id,group_id) REFERENCES memory_statements(id,group_id),
            UNIQUE(id,group_id))""",
        """CREATE TABLE passing_candidates (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, stimulus_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','archived')),
            FOREIGN KEY(stimulus_id,group_id) REFERENCES agency_stimuli(id,group_id))""",
        "CREATE INDEX exploration_scope ON exploration_sessions(group_id,created_at)",
        "CREATE INDEX agency_trail ON agency_events(group_id,session_id,created_at)",
    ):
        connection.execute(sql)


def _migration_7(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE exploration_responses (
        session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK(step IN (0,1)), group_id TEXT NOT NULL,
        authorized_by TEXT NOT NULL REFERENCES commands(id),
        message_id TEXT REFERENCES messages(remote_id),
        status TEXT NOT NULL CHECK(status IN ('awaiting_source','pending','interpreting','ready','sending','awaiting_reply','complete','failed','uncertain','needs_review')),
        classification TEXT NOT NULL DEFAULT '', decision_json TEXT NOT NULL DEFAULT '{}',
        source_hash TEXT NOT NULL DEFAULT '', statement_id TEXT,
        followup_kind TEXT NOT NULL DEFAULT '', outbox_id TEXT UNIQUE REFERENCES outbox(id),
        version TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        PRIMARY KEY(session_id,step),
        FOREIGN KEY(session_id,group_id) REFERENCES exploration_sessions(id,group_id),
        FOREIGN KEY(statement_id,group_id) REFERENCES memory_statements(id,group_id))''')
    connection.execute('''CREATE TABLE candidate_responses (
        candidate_id TEXT NOT NULL REFERENCES passing_candidates(id), session_id TEXT NOT NULL,
        step INTEGER NOT NULL, reaction TEXT NOT NULL CHECK(reaction IN ('choose','defer','alternative')),
        statement_id TEXT NOT NULL REFERENCES memory_statements(id),
        PRIMARY KEY(candidate_id,session_id,step),
        FOREIGN KEY(session_id,step) REFERENCES exploration_responses(session_id,step))''')
    connection.execute('CREATE INDEX exploration_response_work ON exploration_responses(group_id,status,created_at)')


def _migration_8(connection: sqlite3.Connection) -> None:
    for sql in (
        'CREATE UNIQUE INDEX passing_candidate_scope ON passing_candidates(id,group_id)',
        '''CREATE TABLE garden_items (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
            layer TEXT NOT NULL CHECK(layer IN ('roots','growing','passing')),
            ownership TEXT NOT NULL CHECK(ownership IN ('self','shared','infrastructure')),
            candidate_id TEXT UNIQUE REFERENCES passing_candidates(id),
            current_version INTEGER NOT NULL CHECK(current_version > 0),
            UNIQUE(id,group_id),
            FOREIGN KEY(candidate_id,group_id) REFERENCES passing_candidates(id,group_id),
            FOREIGN KEY(id,current_version) REFERENCES garden_versions(item_id,version)
                DEFERRABLE INITIALLY DEFERRED)''',
        '''CREATE TABLE garden_versions (
            item_id TEXT NOT NULL REFERENCES garden_items(id), version INTEGER NOT NULL CHECK(version>0),
            status TEXT NOT NULL CHECK(status IN ('active','paused','archived','superseded')),
            content TEXT NOT NULL, annotation TEXT NOT NULL, rationale TEXT NOT NULL,
            actor TEXT NOT NULL CHECK(actor IN ('hannah','marc','codex','external','system','unknown')),
            origin TEXT NOT NULL, created_at INTEGER NOT NULL,
            group_id TEXT NOT NULL, event_id TEXT, statement_id TEXT,
            command_id TEXT REFERENCES commands(id),
            PRIMARY KEY(item_id,version), UNIQUE(item_id,command_id),
            FOREIGN KEY(item_id,group_id) REFERENCES garden_items(id,group_id),
            FOREIGN KEY(event_id,group_id) REFERENCES agency_events(id,group_id),
            FOREIGN KEY(statement_id,group_id) REFERENCES memory_statements(id,group_id))''',
        '''CREATE TABLE garden_relationships (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation IN ('related','contradicts','supports','supersedes')),
            rationale TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('marc','system')), created_at INTEGER NOT NULL,
            CHECK(source_id != target_id),
            FOREIGN KEY(source_id,group_id) REFERENCES garden_items(id,group_id),
            FOREIGN KEY(target_id,group_id) REFERENCES garden_items(id,group_id))''',
        '''CREATE TABLE garden_projects (
            id TEXT PRIMARY KEY, group_id TEXT NOT NULL, title TEXT NOT NULL,
            outcome TEXT NOT NULL, next_step TEXT NOT NULL, stopping_condition TEXT NOT NULL,
            created_at INTEGER NOT NULL, UNIQUE(id,group_id))''',
        '''CREATE TABLE garden_project_items (
            project_id TEXT NOT NULL, item_id TEXT NOT NULL, group_id TEXT NOT NULL,
            PRIMARY KEY(project_id,item_id),
            FOREIGN KEY(project_id,group_id) REFERENCES garden_projects(id,group_id),
            FOREIGN KEY(item_id,group_id) REFERENCES garden_items(id,group_id))''',
        '''CREATE TABLE garden_attention (
            project_id TEXT NOT NULL, entry_id TEXT NOT NULL, group_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('allocate','consume','pause','complete','abandon','resume')),
            unit TEXT NOT NULL CHECK(unit IN ('minutes','tool_calls','none')), amount INTEGER NOT NULL CHECK(amount>=0),
            result TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL CHECK(actor IN ('marc','system')),
            created_at INTEGER NOT NULL, PRIMARY KEY(project_id,entry_id),
            FOREIGN KEY(project_id,group_id) REFERENCES garden_projects(id,group_id))''',
        'CREATE INDEX garden_scope ON garden_items(group_id,layer,id)',
        'CREATE INDEX garden_version_history ON garden_versions(item_id,version)',
    ):
        connection.execute(sql)
    for table in ('garden_versions', 'garden_relationships', 'garden_projects', 'garden_project_items', 'garden_attention'):
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'garden history is immutable'); END''')
    connection.execute('''CREATE TRIGGER garden_items_no_delete BEFORE DELETE ON garden_items
        BEGIN SELECT RAISE(ABORT,'garden items cannot be deleted'); END''')
    connection.execute('''CREATE TRIGGER garden_items_identity_fixed BEFORE UPDATE ON garden_items
        WHEN NEW.id != OLD.id OR NEW.group_id != OLD.group_id OR NEW.layer != OLD.layer
        OR NEW.ownership != OLD.ownership OR NEW.candidate_id IS NOT OLD.candidate_id
        OR NEW.current_version != OLD.current_version+1
        BEGIN SELECT RAISE(ABORT,'garden identity changes require future confirmation'); END''')
    # Preserve existing candidates as a versioned baseline, not a new identity claim.
    # Earlier archive/reaction events remain available in their original agency trail.
    connection.execute('''INSERT INTO garden_items
        SELECT c.id,c.group_id,'passing','self',c.id,1 FROM passing_candidates c''')
    connection.execute('''INSERT INTO garden_versions
        SELECT c.id,1,c.status,s.summary,'','Imported existing Passing candidate; prior history remains in exploration.',
        e.actor,'migration_baseline',s.retrieved_at,c.group_id,s.event_id,s.statement_id,NULL
        FROM passing_candidates c JOIN agency_stimuli s ON s.id=c.stimulus_id
        JOIN agency_events e ON e.id=s.event_id''')


def _migration_9(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE curiosity_schedule (
        group_id TEXT PRIMARY KEY, basis TEXT NOT NULL, next_due_at INTEGER,
        updated_at INTEGER NOT NULL)''')
    connection.execute('''CREATE TABLE curiosity_slots (
        id TEXT PRIMARY KEY, group_id TEXT NOT NULL, basis TEXT NOT NULL,
        scheduled_at INTEGER NOT NULL, recorded_at INTEGER NOT NULL, budget_at INTEGER NOT NULL,
        outcome TEXT NOT NULL, command_id TEXT UNIQUE REFERENCES commands(id),
        reserved_interpretations INTEGER NOT NULL CHECK(reserved_interpretations IN (0,2)),
        UNIQUE(group_id,scheduled_at))''')
    connection.execute('CREATE INDEX curiosity_budget ON curiosity_slots(group_id,recorded_at)')


def _migration_10(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE daily_schedule (
        group_id TEXT PRIMARY KEY, revision TEXT NOT NULL, config_json TEXT NOT NULL,
        next_due_at INTEGER, updated_at INTEGER NOT NULL)''')
    connection.execute('''CREATE TABLE daily_slots (
        id TEXT PRIMARY KEY, group_id TEXT NOT NULL, revision TEXT NOT NULL,
        wall_slot TEXT NOT NULL, scheduled_at INTEGER NOT NULL, activity TEXT NOT NULL,
        outcome TEXT NOT NULL, command_id TEXT UNIQUE REFERENCES commands(id),
        UNIQUE(group_id,wall_slot))''')
    connection.execute('CREATE INDEX daily_slots_time ON daily_slots(group_id,scheduled_at)')


def _migration_11(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE discovery_settings (
        group_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at INTEGER NOT NULL)''')
    connection.execute('''CREATE TABLE discovery_sources (
        id TEXT PRIMARY KEY, group_id TEXT NOT NULL, kind TEXT NOT NULL,
        data_json TEXT NOT NULL, retrieved_at INTEGER NOT NULL, UNIQUE(id,group_id))''')
    connection.execute('''CREATE TABLE discovery_cache (
        group_id TEXT NOT NULL, kind TEXT NOT NULL, attempted_at INTEGER NOT NULL,
        data_json TEXT NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(group_id,kind))''')
    connection.execute('''CREATE TABLE exploration_experiences (
        session_id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('eligible','queued','researching','ready','sending',
            'awaiting_reflection','complete','failed','uncertain','needs_public_topic','needs_review','cancelled')),
        candidate_id TEXT REFERENCES passing_candidates(id), response_step INTEGER,
        topic TEXT NOT NULL DEFAULT '', authorized_by TEXT NOT NULL REFERENCES commands(id),
        created_at INTEGER NOT NULL, started_at INTEGER, result_json TEXT NOT NULL DEFAULT '[]',
        outbox_id TEXT UNIQUE REFERENCES outbox(id), reflection_message_id TEXT REFERENCES messages(remote_id),
        reflection_statement_id TEXT REFERENCES memory_statements(id), note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(session_id,group_id) REFERENCES exploration_sessions(id,group_id),
        FOREIGN KEY(session_id,response_step) REFERENCES exploration_responses(session_id,step))''')
    connection.execute('CREATE INDEX experience_work ON exploration_experiences(group_id,status,created_at)')


MIGRATIONS: tuple[Migration, ...] = (
    (1, _migration_1),
    (2, _migration_2),
    (3, _migration_3),
    (4, _migration_4),
    (5, _migration_5),
    (6, _migration_6),
    (7, _migration_7),
    (8, _migration_8),
    (9, _migration_9),
    (10, _migration_10),
    (11, _migration_11),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]


def current_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def require_current_schema(connection: sqlite3.Connection) -> None:
    current = current_schema_version(connection)
    if current != LATEST_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} does not match required version "
            f"{LATEST_SCHEMA_VERSION}"
        )


def _validate_registry(migrations: Sequence[Migration]) -> None:
    versions = [version for version, _ in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError("migration versions must be contiguous and start at 1")


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> list[int]:
    _validate_registry(migrations)
    current = current_schema_version(connection)
    latest = migrations[-1][0] if migrations else 0
    if current > latest:
        raise SchemaVersionError(
            f"database schema version {current} is newer than supported version {latest}"
        )

    applied: list[int] = []
    for version, migration in migrations:
        if version <= current:
            continue
        logger.info("Applying database migration %s", version)
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration(connection)
            connection.execute(f"PRAGMA user_version={version}")
            connection.commit()
        except Exception:
            connection.rollback()
            logger.exception("Database migration %s failed", version)
            raise
        logger.info("Database migration %s completed", version)
        applied.append(version)
        current = version
    return applied


def migrate_database(
    path: Path,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> list[int]:
    connection = open_database(path)
    try:
        return apply_migrations(connection, migrations)
    finally:
        connection.close()
