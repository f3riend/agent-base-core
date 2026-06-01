"""Central database access, schema, and pooled connections."""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from queue import Empty, Queue

DB_PATH = "listener.db"
CONSUMER_NAME = "main"
DEFAULT_USER_ID = 3
POOL_SIZE = 8

# AI task status constants
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_RETRYING = "retrying"
TASK_DEAD_LETTER = "dead_letter"

# Workflow status constants
WORKFLOW_SCHEDULED = "scheduled"
WORKFLOW_RUNNING = "running"
WORKFLOW_COMPLETED = "completed"
WORKFLOW_CANCELLED = "cancelled"

VALID_TASK_TRANSITIONS = {
    TASK_PENDING: {TASK_RUNNING, TASK_CANCELLED},
    TASK_RUNNING: {
        TASK_COMPLETED,
        TASK_FAILED,
        TASK_CANCELLED,
        TASK_RETRYING,
    },
    TASK_FAILED: {TASK_RETRYING, TASK_DEAD_LETTER, TASK_CANCELLED},
    TASK_RETRYING: {
        TASK_RUNNING,
        TASK_FAILED,
        TASK_DEAD_LETTER,
        TASK_CANCELLED,
    },
    TASK_COMPLETED: set(),
    TASK_CANCELLED: set(),
    TASK_DEAD_LETTER: set(),
}


class _PooledConnection:
    """Wraps sqlite connection; close() returns to pool."""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._released = False

    def close(self):
        if not self._released:
            self._pool.release(self._conn)
            self._released = True

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


class ConnectionPool:
    """Thread-safe SQLite connection pool."""

    def __init__(self, db_path: str, size: int = POOL_SIZE):
        self.db_path = db_path
        self.size = size
        self._queue = Queue(maxsize=size)
        self._lock = threading.Lock()
        self._created = 0

    def _create_connection(self):
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def acquire(self):
        try:
            conn = self._queue.get_nowait()
        except Empty:
            with self._lock:
                if self._created < self.size:
                    conn = self._create_connection()
                    self._created += 1
                else:
                    conn = self._queue.get()
        return _PooledConnection(conn, self)

    def release(self, conn):
        try:
            self._queue.put_nowait(conn)
        except Exception:
            conn.close()

    @contextmanager
    def connection(self):
        pooled = self.acquire()
        try:
            yield pooled._conn
            pooled._conn.commit()
        except Exception:
            pooled._conn.rollback()
            raise
        finally:
            pooled.close()


_POOL = ConnectionPool(DB_PATH)


def get_pool() -> ConnectionPool:
    return _POOL


def get_db():
    """Acquire a pooled connection. Call conn.close() to return to pool."""
    return _POOL.acquire()


@contextmanager
def db_connection():
    """Preferred: context manager with auto commit/rollback."""
    with _POOL.connection() as conn:
        yield conn


def execute_query(sql, params=(), one=False):
    with db_connection() as conn:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute_write(sql, params=()):
    with db_connection() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


def now_iso():
    return datetime.utcnow().isoformat()


def init_db():
    with db_connection() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,
                email               TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS listener_state (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                consumer_name       TEXT UNIQUE,
                last_cursor         INTEGER DEFAULT 0,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                rule_name           TEXT NOT NULL,
                natural_language    TEXT,
                dsl                 TEXT NOT NULL,
                enabled             INTEGER DEFAULT 1,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE(user_id, rule_name)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_executions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id             INTEGER,
                tool_name           TEXT,
                input_payload       TEXT,
                output_payload      TEXT,
                status              TEXT,
                duration_ms         INTEGER,
                error               TEXT,
                created_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                id                  INTEGER PRIMARY KEY,
                user_id             INTEGER DEFAULT 1,
                name                TEXT,
                owner               TEXT,
                instagram           TEXT,
                status              TEXT DEFAULT 'active',
                created_at          TEXT,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id                  INTEGER PRIMARY KEY,
                store_id            INTEGER,
                user_id             INTEGER DEFAULT 1,
                name                TEXT,
                price               REAL,
                stock               INTEGER,
                sales               INTEGER,
                category            TEXT,
                status              TEXT DEFAULT 'active',
                created_at          TEXT,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS ai_tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                task_type           TEXT,
                entity_type         TEXT,
                entity_id           INTEGER,
                workflow_id         INTEGER,
                status              TEXT,
                payload             TEXT,
                ai_result           TEXT,
                selected_tool       TEXT,
                retry_count         INTEGER DEFAULT 0,
                error               TEXT,
                created_at          TEXT,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id                  INTEGER PRIMARY KEY,
                store_id            INTEGER,
                item_id             INTEGER,
                quantity            INTEGER,
                status              TEXT,
                created_at          TEXT,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS workflow_instances (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                workflow_name       TEXT,
                entity_type         TEXT,
                entity_id           INTEGER,
                status              TEXT,
                scheduled_at        TEXT,
                cancelled_reason    TEXT,
                created_at          TEXT,
                updated_at          TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS automation_logs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                event_id            INTEGER DEFAULT 0,
                rule_name           TEXT,
                workflow_id         INTEGER,
                task_id             INTEGER,
                matched             INTEGER DEFAULT 1,
                ai_decision         TEXT,
                selected_tool       TEXT,
                tool_input          TEXT,
                tool_output         TEXT,
                execution_status    TEXT DEFAULT 'pending',
                retry_count         INTEGER DEFAULT 0,
                failed_reason       TEXT,
                latency_ms          INTEGER DEFAULT 0,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS rule_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                rule_name           TEXT,
                natural_language    TEXT NOT NULL,
                generated_dsl       TEXT,
                full_rules_snapshot TEXT,
                action              TEXT NOT NULL,
                conflicts_json      TEXT,
                validation_status   TEXT,
                validation_errors   TEXT,
                applied             INTEGER DEFAULT 0,
                created_at          TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS planner_proposals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                event_id            INTEGER,
                event_name          TEXT,
                entity_type         TEXT,
                entity_id           INTEGER,
                proposal_json       TEXT NOT NULL,
                applied             INTEGER DEFAULT 0,
                apply_result        TEXT,
                created_at          TEXT NOT NULL
            )
        """)

        _ensure_column(c, "stores", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "items", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "items", "status", "TEXT DEFAULT 'active'")
        _ensure_column(c, "stores", "status", "TEXT DEFAULT 'active'")
        _ensure_column(c, "ai_tasks", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "ai_tasks", "retry_count", "INTEGER DEFAULT 0")
        _ensure_column(c, "ai_tasks", "error", "TEXT")
        _ensure_column(c, "ai_tasks", "next_retry_at", "TEXT")
        _ensure_column(c, "workflow_instances", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "automation_logs", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "rule_history", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "planner_proposals", "user_id", "INTEGER DEFAULT 1")
        _ensure_column(c, "workflow_instances", "metadata", "TEXT")

        c.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                proposal_id         INTEGER,
                event_id            INTEGER,
                workflow_name       TEXT,
                proposal_json       TEXT NOT NULL,
                status              TEXT DEFAULT 'pending',
                risk_level          TEXT DEFAULT 'medium',
                confidence          REAL,
                reason              TEXT,
                feedback            TEXT,
                edited_proposal_json TEXT,
                approved_by         TEXT,
                approval_type       TEXT DEFAULT 'generic_approval',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS planner_memory (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                event_id            INTEGER,
                event_name          TEXT,
                entity_type         TEXT,
                entity_id           INTEGER,
                decision            TEXT,
                workflow_name       TEXT,
                tools_json          TEXT,
                outcome             TEXT DEFAULT 'pending',
                reason              TEXT,
                confidence          REAL,
                priority            TEXT,
                context_snapshot    TEXT,
                plan_json           TEXT,
                created_at          TEXT NOT NULL
            )
        """)

        _ensure_column(c, "approval_requests", "proposal_hash", "TEXT")
        _ensure_column(c, "approval_requests", "approval_type", "TEXT DEFAULT 'generic_approval'")

        # ----- Tur 2: structured_rules versioning -----
        _ensure_column(c, "structured_rules", "version", "INTEGER DEFAULT 1")
        _ensure_column(c, "structured_rules", "parent_rule_id", "INTEGER")
        _ensure_column(c, "structured_rules", "is_current", "INTEGER DEFAULT 1")
        _ensure_column(c, "structured_rules", "supersedes_at", "TEXT")

        # ----- Tur 3: rule learning + suggestions -----
        # health_score runtime success'ten gelir (parse_confidence parser'dan).
        # 0..1 aralığında, default 0.7 (yeni kural için tarafsız zemin).
        _ensure_column(c, "structured_rules", "health_score", "REAL DEFAULT 0.7")
        _ensure_column(c, "structured_rules", "last_outcome", "TEXT")
        _ensure_column(c, "structured_rules", "success_count", "INTEGER DEFAULT 0")
        _ensure_column(c, "structured_rules", "failure_count", "INTEGER DEFAULT 0")
        _ensure_column(c, "structured_rules", "cancel_count", "INTEGER DEFAULT 0")

        # ----- LangGraph structured rules (Phase R: rule rewrite) -----
        # `structured_rules` holds the operator's parsed NL rule. Each row
        # carries the original Turkish prompt + the canonical Pydantic
        # serialization (rule_json). The listener matches incoming events
        # against rows where enabled=1.
        c.execute("""
            CREATE TABLE IF NOT EXISTS structured_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                org_id          INTEGER,
                name            TEXT NOT NULL,
                natural_language TEXT NOT NULL,
                rule_json       TEXT NOT NULL,
                trigger_event   TEXT NOT NULL,
                enabled         INTEGER DEFAULT 1,
                last_fired_at   TEXT,
                fire_count      INTEGER DEFAULT 0,
                parse_confidence REAL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_struct_rules_event "
            "ON structured_rules (trigger_event, enabled)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_struct_rules_user "
            "ON structured_rules (user_id, enabled)"
        )

        # `rule_executions` — one row per (rule_id, event_id) graph instance.
        # `thread_id` is the LangGraph checkpoint thread; resume operations
        # use it to load the paused state.
        c.execute("""
            CREATE TABLE IF NOT EXISTS rule_executions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                rule_id         INTEGER NOT NULL,
                event_id        INTEGER,
                event_type      TEXT,
                thread_id       TEXT NOT NULL UNIQUE,
                status          TEXT NOT NULL DEFAULT 'running',
                current_node    TEXT,
                approval_id     INTEGER,
                started_at      TEXT NOT NULL,
                ended_at        TEXT,
                error           TEXT,
                trace_summary   TEXT
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_executions_status "
            "ON rule_executions (status, started_at DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_executions_rule "
            "ON rule_executions (rule_id, started_at DESC)"
        )

        # `graph_node_traces` — humanized per-node observability for the UI.
        c.execute("""
            CREATE TABLE IF NOT EXISTS graph_node_traces (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id    INTEGER NOT NULL,
                node_name       TEXT NOT NULL,
                node_status     TEXT NOT NULL,
                summary         TEXT,
                details_json    TEXT,
                started_at      TEXT NOT NULL,
                ended_at        TEXT,
                duration_ms     INTEGER
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_node_traces_exec "
            "ON graph_node_traces (execution_id, id)"
        )

        # ----- Multi-tenant org / role / API key layer (Phase E) -----
        # `orgs` is the workspace boundary. `org_members` is the
        # membership join with role. `api_keys` stores the SHA-256 hash
        # of issued keys — the raw key value is shown ONCE at creation
        # time and never persisted.
        c.execute("""
            CREATE TABLE IF NOT EXISTS orgs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                slug            TEXT NOT NULL UNIQUE,
                plan            TEXT NOT NULL DEFAULT 'free',
                created_at      TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS org_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id          INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                role            TEXT NOT NULL DEFAULT 'viewer',
                joined_at       TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_members_org_user "
            "ON org_members (org_id, user_id)"
        )
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_org_members_unique "
            "ON org_members (org_id, user_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_members_user "
            "ON org_members (user_id)"
        )
        c.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id          INTEGER NOT NULL,
                name            TEXT NOT NULL,
                key_hash        TEXT NOT NULL,
                scope           TEXT NOT NULL DEFAULT 'read',
                status          TEXT NOT NULL DEFAULT 'active',
                last_used_at    TEXT,
                expires_at      TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash "
            "ON api_keys (key_hash)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_org "
            "ON api_keys (org_id, status)"
        )

        # ----- Campaign lifecycle (Phase C) -----
        # `campaigns` holds the operator-facing entity that survives across
        # a workflow run. `campaign_metrics` is append-only — one row per
        # measurement snapshot (impressions/clicks/conversions/spend).
        c.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                name            TEXT NOT NULL,
                channel         TEXT NOT NULL,
                intent          TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'draft',
                scheduled_at    TEXT,
                started_at      TEXT,
                ended_at        TEXT,
                budget          REAL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaigns_user_status "
            "ON campaigns (user_id, status)"
        )

        c.execute("""
            CREATE TABLE IF NOT EXISTS campaign_metrics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER NOT NULL,
                ts              TEXT NOT NULL,
                impressions     INTEGER DEFAULT 0,
                clicks          INTEGER DEFAULT 0,
                conversions     INTEGER DEFAULT 0,
                spend           REAL DEFAULT 0
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_metrics_ts "
            "ON campaign_metrics (campaign_id, ts DESC)"
        )

        # ----- Social credentials (encrypted-at-rest) -----
        # Stores OAuth tokens / API keys for connected social and
        # commerce accounts. The token blob is Fernet-encrypted using
        # APP_SECRET_KEY; this table never stores plaintext credentials.
        # The unique index prevents duplicate accounts per (user, provider, handle).
        c.execute("""
            CREATE TABLE IF NOT EXISTS social_credentials (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id               INTEGER NOT NULL,
                provider              TEXT NOT NULL,
                account_handle        TEXT NOT NULL,
                encrypted_token_blob  TEXT NOT NULL,
                scope                 TEXT,
                token_expires_at      TEXT,
                status                TEXT DEFAULT 'active',
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_creds_user_provider "
            "ON social_credentials (user_id, provider)"
        )
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_creds_unique "
            "ON social_credentials (user_id, provider, account_handle) "
            "WHERE status = 'active'"
        )

        # ----- Orchestration traces (Phase 7) -----
        # Dual-write target for observability._emit when persist=True.
        # The dashboard reads from this to render the AI reasoning feed.
        c.execute("""
            CREATE TABLE IF NOT EXISTS orchestration_traces (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER DEFAULT 1,
                event_id            INTEGER,
                workflow_id         INTEGER,
                task_id             INTEGER,
                trace_tag           TEXT NOT NULL,
                summary             TEXT,
                details_json        TEXT,
                created_at          TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_traces_tag_created ON orchestration_traces (trace_tag, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_traces_user_created ON orchestration_traces (user_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_traces_event ON orchestration_traces (event_id)")

        # ----- Idempotency: partial unique indexes -----
        # An active workflow with the same (user, name, entity) tuple
        # cannot exist twice in scheduled/running state. SELECT-then-INSERT
        # races now resolve via IntegrityError → existing row returned.
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_active_unique
            ON workflow_instances (user_id, workflow_name, entity_type, entity_id)
            WHERE status IN ('scheduled', 'running')
        """)

        # A pending approval with the same proposal_hash collapses into one.
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_pending_unique
            ON approval_requests (user_id, proposal_hash)
            WHERE status = 'pending' AND proposal_hash IS NOT NULL
        """)

        # ----- Composite indexes for hot paths -----
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_tasks_status_user ON ai_tasks (status, user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_tasks_retry ON ai_tasks (status, next_retry_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status_scheduled ON workflow_instances (status, scheduled_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_workflows_user ON workflow_instances (user_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_logs_user_created ON automation_logs (user_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_planner_memory_user_entity ON planner_memory (user_id, entity_type, entity_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_exec_task ON tool_executions (task_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_approval_user_status ON approval_requests (user_id, status, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_planner_proposals_user ON planner_proposals (user_id, created_at DESC)")

        row = conn.execute(
            "SELECT id FROM users WHERE id=?",
            (DEFAULT_USER_ID,),
        ).fetchone()
        if not row:
            ts = now_iso()
            conn.execute(
                """
                INSERT INTO users (id, name, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (DEFAULT_USER_ID, "Default Tenant", "default@local", ts, ts),
            )

        row = conn.execute(
            "SELECT * FROM listener_state WHERE consumer_name=?",
            (CONSUMER_NAME,),
        ).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO listener_state (consumer_name, last_cursor, updated_at)
                VALUES (?, ?, ?)
                """,
                (CONSUMER_NAME, 0, now_iso()),
            )



def _ensure_column(cursor, table, column, definition):
    columns = {
        row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        cursor.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def get_cursor():
    row = execute_query(
        "SELECT last_cursor FROM listener_state WHERE consumer_name=?",
        (CONSUMER_NAME,),
        one=True,
    )
    return row["last_cursor"] if row else 0


def set_cursor(cursor: int):
    execute_write(
        """
        UPDATE listener_state
        SET last_cursor=?, updated_at=?
        WHERE consumer_name=?
        """,
        (cursor, now_iso(), CONSUMER_NAME),
    )
