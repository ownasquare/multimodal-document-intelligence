"""SQLite connection policy and forward-only schema migrations.

SQLite is the authoritative metadata store for the single-host application.  The
class in this module deliberately returns short-lived connections so callers do
not accidentally hold locks across parsing, embedding, or model-provider work.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

SCHEMA_VERSION = 1


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database was created by a newer application version."""


_MIGRATION_V1 = """
BEGIN IMMEDIATE;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL
);

CREATE TABLE schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 240),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'processing', 'ready', 'ready_with_warnings', 'failed',
        'deleting', 'deleted'
    )),
    current_version_id TEXT NOT NULL
        REFERENCES document_versions(id) DEFERRABLE INITIALLY DEFERRED,
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 1),
    element_count INTEGER NOT NULL DEFAULT 0 CHECK (element_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX ix_documents_workspace_recent
ON documents (workspace_id, updated_at DESC, id DESC);

CREATE INDEX ix_documents_workspace_status
ON documents (workspace_id, status, updated_at DESC, id DESC);

CREATE TABLE document_versions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL
        REFERENCES documents(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    mime_type TEXT NOT NULL CHECK (length(mime_type) BETWEEN 1 AND 120),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 1),
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 1),
    parser_profile TEXT NOT NULL CHECK (length(parser_profile) BETWEEN 1 AND 160),
    embedding_profile TEXT NOT NULL CHECK (length(embedding_profile) BETWEEN 1 AND 160),
    source_key TEXT NOT NULL CHECK (length(source_key) BETWEEN 1 AND 500),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    created_at TEXT NOT NULL,
    file_deleted_at TEXT,
    UNIQUE (document_id, ordinal),
    UNIQUE (document_id, sha256, parser_profile, embedding_profile)
);

CREATE INDEX ix_versions_workspace_fingerprint
ON document_versions (workspace_id, sha256, parser_profile, embedding_profile, created_at DESC);

CREATE INDEX ix_versions_document_recent
ON document_versions (document_id, ordinal DESC);

CREATE TABLE elements (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    modality TEXT NOT NULL CHECK (modality IN (
        'text', 'table', 'table_row', 'image', 'chart', 'diagram', 'ocr', 'page_summary'
    )),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 50000),
    bbox_json TEXT CHECK (bbox_json IS NULL OR json_valid(bbox_json)),
    asset_key TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    extraction_method TEXT NOT NULL CHECK (length(extraction_method) BETWEEN 1 AND 80),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_elements_version_page_modality
ON elements (version_id, page_number, modality, id);

CREATE INDEX ix_elements_document_version
ON elements (document_id, version_id, id);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('ingest', 'reprocess', 'delete')),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'cancelled'
    )),
    stage TEXT NOT NULL CHECK (stage IN (
        'queued', 'reading', 'extracting_text', 'extracting_tables', 'ocr',
        'understanding_visuals', 'indexing', 'verifying', 'deleting', 'complete'
    )),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    cancellation_requested INTEGER NOT NULL DEFAULT 0
        CHECK (cancellation_requested IN (0, 1)),
    available_at TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    CHECK (
        (status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'running'
    )
);

CREATE INDEX ix_jobs_lease_queue
ON jobs (status, available_at, created_at, id);

CREATE INDEX ix_jobs_document_recent
ON jobs (document_id, created_at DESC, id DESC);

CREATE INDEX ix_jobs_expired_leases
ON jobs (status, lease_expires_at)
WHERE status = 'running';

CREATE UNIQUE INDEX uq_jobs_active_version_kind
ON jobs (version_id, kind)
WHERE status IN ('queued', 'running');

CREATE TABLE upload_idempotency (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
    request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, idempotency_key)
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
    document_ids_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(document_ids_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX ix_conversations_workspace_recent
ON conversations (workspace_id, updated_at DESC, id DESC);

CREATE TABLE answers (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    question TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 4000),
    answer_text TEXT NOT NULL CHECK (length(answer_text) BETWEEN 1 AND 20000),
    modalities_json TEXT NOT NULL CHECK (json_valid(modalities_json)),
    abstained INTEGER NOT NULL CHECK (abstained IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX ix_answers_conversation_recent
ON answers (conversation_id, created_at DESC, id DESC);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 20000),
    answer_id TEXT REFERENCES answers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_messages_conversation_order
ON messages (conversation_id, created_at, id);

CREATE TABLE answer_versions (
    answer_id TEXT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    document_name TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    parser_profile TEXT NOT NULL,
    embedding_profile TEXT NOT NULL,
    PRIMARY KEY (answer_id, ordinal),
    UNIQUE (answer_id, document_id, version_id)
);

CREATE TABLE claims (
    answer_id TEXT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    claim_text TEXT NOT NULL CHECK (length(claim_text) BETWEEN 1 AND 4000),
    inference INTEGER NOT NULL CHECK (inference IN (0, 1)),
    PRIMARY KEY (answer_id, ordinal)
);

CREATE TABLE citations (
    id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    document_name TEXT NOT NULL,
    element_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    modality TEXT NOT NULL CHECK (modality IN (
        'text', 'table', 'table_row', 'image', 'chart', 'diagram', 'ocr', 'page_summary'
    )),
    excerpt TEXT NOT NULL CHECK (length(excerpt) BETWEEN 1 AND 4000),
    bbox_json TEXT CHECK (bbox_json IS NULL OR json_valid(bbox_json)),
    asset_url TEXT,
    available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    UNIQUE (answer_id, ordinal)
);

CREATE INDEX ix_citations_answer_order
ON citations (answer_id, ordinal);

CREATE INDEX ix_citations_document
ON citations (document_id, answer_id);

CREATE TABLE claim_citations (
    answer_id TEXT NOT NULL,
    claim_ordinal INTEGER NOT NULL,
    citation_id TEXT NOT NULL REFERENCES citations(id) ON DELETE CASCADE,
    citation_ordinal INTEGER NOT NULL CHECK (citation_ordinal >= 0),
    PRIMARY KEY (answer_id, claim_ordinal, citation_ordinal),
    FOREIGN KEY (answer_id, claim_ordinal)
        REFERENCES claims(answer_id, ordinal) ON DELETE CASCADE
);

INSERT INTO schema_migrations (version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT INTO schema_metadata (key, value, updated_at)
VALUES ('schema_version', '1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

PRAGMA user_version = 1;
COMMIT;
"""


class Database:
    """Create configured, short-lived SQLite connections and transactions."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._migration_lock = FileLock(
            f"{self.path}.migrate.lock", timeout=max(1.0, busy_timeout_ms / 1000)
        )

    def initialize(self) -> None:
        """Create the database and apply every known forward-only migration."""

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._migration_lock, self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(
                    "Database schema is newer than this application supports: "
                    f"found {version}, supports {SCHEMA_VERSION}"
                )
            if version < 1:
                connection.executescript(_MIGRATION_V1)

    def schema_version(self) -> int:
        """Return the database schema version without applying migrations."""

        with self.connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield one consistently configured connection and always close it."""

        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA temp_store = MEMORY")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Run a rollback-safe transaction on a fresh connection."""

        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
