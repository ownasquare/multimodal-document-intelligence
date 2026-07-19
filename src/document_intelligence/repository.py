"""Transactional repositories for durable document-intelligence state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import cast
from uuid import uuid4

from document_intelligence.database import Database
from document_intelligence.models import (
    Answer,
    AnswerClaim,
    Citation,
    ContentElement,
    Conversation,
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    JobKind,
    JobStage,
    JobStatus,
    Modality,
    UploadReceipt,
    Workspace,
    utc_now,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_MAX_PAGE_SIZE = 200

_DOCUMENT_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.QUEUED: frozenset(
        {DocumentStatus.PROCESSING, DocumentStatus.FAILED, DocumentStatus.DELETING}
    ),
    DocumentStatus.PROCESSING: frozenset(
        {
            DocumentStatus.READY,
            DocumentStatus.READY_WITH_WARNINGS,
            DocumentStatus.FAILED,
            DocumentStatus.DELETING,
        }
    ),
    DocumentStatus.READY: frozenset({DocumentStatus.QUEUED, DocumentStatus.DELETING}),
    DocumentStatus.READY_WITH_WARNINGS: frozenset({DocumentStatus.QUEUED, DocumentStatus.DELETING}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.QUEUED, DocumentStatus.DELETING}),
    DocumentStatus.DELETING: frozenset({DocumentStatus.DELETED}),
    DocumentStatus.DELETED: frozenset(),
}

_STAGE_RANK = {
    JobStage.QUEUED: 0,
    JobStage.READING: 1,
    JobStage.EXTRACTING_TEXT: 2,
    JobStage.EXTRACTING_TABLES: 3,
    JobStage.OCR: 4,
    JobStage.UNDERSTANDING_VISUALS: 5,
    JobStage.INDEXING: 6,
    JobStage.VERIFYING: 7,
    JobStage.COMPLETE: 8,
}


class RepositoryError(RuntimeError):
    """Base class for safe, domain-level persistence failures."""


class RecordNotFoundError(RepositoryError):
    """Raised when a required durable record does not exist."""


class PersistenceConflictError(RepositoryError):
    """Raised when an idempotency or ownership contract conflicts."""


class InvalidStateTransitionError(RepositoryError):
    """Raised when a durable state machine would move backward or skip safety."""


class LeaseLostError(RepositoryError):
    """Raised when a worker mutates work it no longer owns."""


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: str
    conversation_id: str
    role: str
    content: str
    answer_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DeletionReadback:
    document_id: str
    status: DocumentStatus
    active_element_count: int
    available_citation_count: int
    undeleted_version_count: int

    @property
    def verified(self) -> bool:
        return (
            self.status is DocumentStatus.DELETED
            and self.active_element_count == 0
            and self.available_citation_count == 0
            and self.undeleted_version_count == 0
        )


class Repository:
    """Expose short, explicit transactions instead of leaking SQL to services."""

    def __init__(
        self,
        database: Database,
        *,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.database = database
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self._clock = clock

    def initialize(self) -> None:
        self.database.initialize()

    @staticmethod
    def new_id() -> str:
        """Return one server-generated identifier accepted by every domain model."""

        return uuid4().hex

    def create_workspace(self, name: str, *, workspace_id: str | None = None) -> Workspace:
        identifier = self._identifier(workspace_id or self.new_id(), "workspace_id")
        normalized_name = self._text(name, "name", maximum=120)
        now = self._now_text()
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO workspaces (id, name, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (identifier, normalized_name, now, now),
                )
                row = self._one(
                    connection.execute(
                        "SELECT * FROM workspaces WHERE id = ?", (identifier,)
                    ).fetchone()
                )
                return self._workspace(row)
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflictError("workspace identifier is already in use") from exc

    def ensure_workspace(self, workspace_id: str, *, name: str = "My workspace") -> Workspace:
        identifier = self._identifier(workspace_id, "workspace_id")
        existing = self.get_workspace(identifier)
        if existing is not None:
            return existing
        try:
            return self.create_workspace(name, workspace_id=identifier)
        except PersistenceConflictError:
            raced = self.get_workspace(identifier)
            if raced is None:
                raise
            return raced

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        identifier = self._identifier(workspace_id, "workspace_id")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (identifier,)
            ).fetchone()
        return self._workspace(row) if row is not None else None

    def list_workspaces(self, *, limit: int = 50, offset: int = 0) -> list[Workspace]:
        self._pagination(limit, offset)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workspaces
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [self._workspace(row) for row in rows]

    def set_metadata(self, key: str, value: str) -> None:
        normalized_key = self._text(key, "key", maximum=120)
        normalized_value = self._text(value, "value", maximum=4_000)
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO schema_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (normalized_key, normalized_value, now),
            )

    def get_metadata(self, key: str) -> str | None:
        normalized_key = self._text(key, "key", maximum=120)
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = ?", (normalized_key,)
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def find_duplicate_document(
        self,
        workspace_id: str,
        *,
        sha256: str,
        parser_profile: str,
        embedding_profile: str,
    ) -> tuple[Document, DocumentVersion] | None:
        workspace = self._identifier(workspace_id, "workspace_id")
        digest = self._digest(sha256)
        parser = self._text(parser_profile, "parser_profile", maximum=160)
        embedding = self._text(embedding_profile, "embedding_profile", maximum=160)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT d.*, v.id AS matched_version_id
                FROM documents AS d
                JOIN document_versions AS v ON v.id = d.current_version_id
                WHERE d.workspace_id = ? AND d.status <> 'deleted'
                  AND v.sha256 = ? AND v.parser_profile = ? AND v.embedding_profile = ?
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT 1
                """,
                (workspace, digest, parser, embedding),
            ).fetchone()
            if row is None:
                return None
            version_row = self._one(
                connection.execute(
                    "SELECT * FROM document_versions WHERE id = ?",
                    (row["matched_version_id"],),
                ).fetchone()
            )
        return self._document(row), self._version(version_row)

    def accept_upload(
        self,
        workspace_id: str,
        *,
        display_name: str,
        sha256: str,
        byte_size: int,
        mime_type: str,
        source_key: str,
        parser_profile: str,
        embedding_profile: str,
        document_id: str | None = None,
        version_id: str | None = None,
        job_id: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
    ) -> UploadReceipt:
        """Commit accepted upload metadata and its queued job in one transaction."""

        workspace = self._identifier(workspace_id, "workspace_id")
        name = self._text(display_name, "display_name", maximum=240)
        digest = self._digest(sha256)
        if byte_size < 1:
            raise ValueError("byte_size must be positive")
        media_type = self._text(mime_type, "mime_type", maximum=120)
        parser = self._text(parser_profile, "parser_profile", maximum=160)
        embedding = self._text(embedding_profile, "embedding_profile", maximum=160)
        source_parts = self._source_key(source_key)
        if source_parts[0] != workspace:
            raise ValueError("source_key workspace does not match workspace_id")
        version_identifier = self._identifier(version_id or source_parts[1], "version_id")
        if source_parts[1] != version_identifier:
            raise ValueError("source_key version does not match version_id")
        requested_document_id = (
            self._identifier(document_id, "document_id") if document_id is not None else None
        )
        job_identifier = self._identifier(job_id or self.new_id(), "job_id")
        attempts = max_attempts or self.max_attempts
        if attempts < 1:
            raise ValueError("max_attempts must be positive")
        idempotency = (
            self._text(idempotency_key, "idempotency_key", minimum=8, maximum=200)
            if idempotency_key is not None
            else None
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "workspace_id": workspace,
                    "document_id": requested_document_id,
                    "display_name": name,
                    "sha256": digest,
                    "byte_size": byte_size,
                    "mime_type": media_type,
                    "parser_profile": parser,
                    "embedding_profile": embedding,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        now = self._now_text()

        with self.database.transaction(immediate=True) as connection:
            self._require_workspace(connection, workspace)
            if idempotency is not None:
                existing_idempotency = connection.execute(
                    """
                    SELECT * FROM upload_idempotency
                    WHERE workspace_id = ? AND idempotency_key = ?
                    """,
                    (workspace, idempotency),
                ).fetchone()
                if existing_idempotency is not None:
                    if existing_idempotency["request_fingerprint"] != fingerprint:
                        raise PersistenceConflictError(
                            "idempotency key was already used for a different upload"
                        )
                    return self._receipt_from_ids(
                        connection,
                        str(existing_idempotency["document_id"]),
                        str(existing_idempotency["version_id"]),
                        str(existing_idempotency["job_id"]),
                        duplicate=True,
                    )

            document_row: sqlite3.Row | None = None
            if requested_document_id is not None:
                document_row = connection.execute(
                    "SELECT * FROM documents WHERE id = ?", (requested_document_id,)
                ).fetchone()
                if document_row is None:
                    raise RecordNotFoundError("document does not exist")
                if document_row["workspace_id"] != workspace:
                    raise RecordNotFoundError("document does not exist in this workspace")
                if document_row["status"] in {
                    DocumentStatus.DELETING.value,
                    DocumentStatus.DELETED.value,
                }:
                    raise InvalidStateTransitionError("deleted documents cannot accept versions")
            else:
                document_row = connection.execute(
                    """
                    SELECT d.*
                    FROM documents AS d
                    JOIN document_versions AS v ON v.id = d.current_version_id
                    WHERE d.workspace_id = ? AND d.status <> 'deleted'
                      AND v.sha256 = ? AND v.parser_profile = ? AND v.embedding_profile = ?
                    ORDER BY d.updated_at DESC, d.id DESC
                    LIMIT 1
                    """,
                    (workspace, digest, parser, embedding),
                ).fetchone()

            if document_row is not None:
                duplicate_version = connection.execute(
                    """
                    SELECT * FROM document_versions
                    WHERE document_id = ? AND sha256 = ?
                      AND parser_profile = ? AND embedding_profile = ?
                    ORDER BY ordinal DESC LIMIT 1
                    """,
                    (document_row["id"], digest, parser, embedding),
                ).fetchone()
                if duplicate_version is not None:
                    latest_job = connection.execute(
                        """
                        SELECT * FROM jobs WHERE version_id = ?
                        ORDER BY created_at DESC, id DESC LIMIT 1
                        """,
                        (duplicate_version["id"],),
                    ).fetchone()
                    if latest_job is None:
                        raise RepositoryError("duplicate version is missing its durable job")
                    receipt = UploadReceipt(
                        document=self._document(document_row),
                        version=self._version(duplicate_version),
                        job=self._job(latest_job),
                        duplicate=True,
                    )
                    if idempotency is not None:
                        self._insert_idempotency(
                            connection, workspace, idempotency, fingerprint, receipt, now
                        )
                    return receipt

            if document_row is None:
                document_identifier = self._identifier(
                    requested_document_id or self.new_id(), "document_id"
                )
                ordinal = 1
                kind = JobKind.INGEST
                connection.execute(
                    """
                    INSERT INTO documents (
                        id, workspace_id, display_name, status, current_version_id,
                        page_count, element_count, warning_count, created_at, updated_at
                    ) VALUES (?, ?, ?, 'queued', ?, NULL, 0, 0, ?, ?)
                    """,
                    (document_identifier, workspace, name, version_identifier, now, now),
                )
            else:
                document_identifier = str(document_row["id"])
                ordinal = int(
                    self._one(
                        connection.execute(
                            """
                            SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
                            FROM document_versions WHERE document_id = ?
                            """,
                            (document_identifier,),
                        ).fetchone()
                    )["next_ordinal"]
                )
                kind = JobKind.REPROCESS

            try:
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        id, workspace_id, document_id, ordinal, sha256, mime_type,
                        byte_size, page_count, parser_profile, embedding_profile,
                        source_key, warning_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?)
                    """,
                    (
                        version_identifier,
                        workspace,
                        document_identifier,
                        ordinal,
                        digest,
                        media_type,
                        byte_size,
                        parser,
                        embedding,
                        source_key,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceConflictError(
                    "version identifier or content is already in use"
                ) from exc

            if document_row is not None:
                connection.execute(
                    """
                    UPDATE documents
                    SET display_name = ?, status = 'queued', current_version_id = ?,
                        page_count = NULL, element_count = 0, warning_count = 0,
                        updated_at = ?, deleted_at = NULL
                    WHERE id = ?
                    """,
                    (name, version_identifier, now, document_identifier),
                )

            connection.execute(
                """
                INSERT INTO jobs (
                    id, workspace_id, document_id, version_id, kind, status, stage,
                    progress, attempt_count, max_attempts, cancellation_requested,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', 'queued', 0, 0, ?, 0, ?, ?, ?)
                """,
                (
                    job_identifier,
                    workspace,
                    document_identifier,
                    version_identifier,
                    kind.value,
                    attempts,
                    now,
                    now,
                    now,
                ),
            )
            receipt = self._receipt_from_ids(
                connection,
                document_identifier,
                version_identifier,
                job_identifier,
                duplicate=False,
            )
            if idempotency is not None:
                self._insert_idempotency(
                    connection, workspace, idempotency, fingerprint, receipt, now
                )
            return receipt

    def get_document(self, document_id: str, *, include_deleted: bool = False) -> Document | None:
        identifier = self._identifier(document_id, "document_id")
        with self.database.connection() as connection:
            if include_deleted:
                row = connection.execute(
                    "SELECT * FROM documents WHERE id = ?", (identifier,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM documents WHERE id = ? AND status <> 'deleted'",
                    (identifier,),
                ).fetchone()
        return self._document(row) if row is not None else None

    def list_documents(
        self,
        workspace_id: str,
        *,
        status: DocumentStatus | None = None,
        query: str | None = None,
        sort: str = "recent",
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[Document]:
        workspace = self._identifier(workspace_id, "workspace_id")
        self._pagination(limit, offset)
        if sort not in {"recent", "oldest", "name", "name_desc"}:
            raise ValueError("sort must be recent, oldest, name, or name_desc")
        status_value = status.value if status is not None else None
        search_pattern: str | None = None
        if query is not None and query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            search_pattern = f"%{escaped}%"
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM documents
                WHERE workspace_id = ?
                  AND (? = 1 OR status <> 'deleted')
                  AND (? IS NULL OR status = ?)
                  AND (? IS NULL OR display_name LIKE ? ESCAPE '\\')
                ORDER BY
                  CASE WHEN ? = 'recent' THEN updated_at END DESC,
                  CASE WHEN ? = 'recent' THEN id END DESC,
                  CASE WHEN ? = 'oldest' THEN updated_at END ASC,
                  CASE WHEN ? = 'oldest' THEN id END ASC,
                  CASE WHEN ? = 'name' THEN display_name END COLLATE NOCASE ASC,
                  CASE WHEN ? = 'name' THEN id END ASC,
                  CASE WHEN ? = 'name_desc' THEN display_name END COLLATE NOCASE DESC,
                  CASE WHEN ? = 'name_desc' THEN id END DESC
                LIMIT ? OFFSET ?
                """,
                (
                    workspace,
                    int(include_deleted),
                    status_value,
                    status_value,
                    search_pattern,
                    search_pattern,
                    sort,
                    sort,
                    sort,
                    sort,
                    sort,
                    sort,
                    sort,
                    sort,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [self._document(row) for row in rows]

    def count_documents(
        self,
        workspace_id: str,
        *,
        status: DocumentStatus | None = None,
        query: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        """Count documents using the same scope and search rules as ``list_documents``."""

        workspace = self._identifier(workspace_id, "workspace_id")
        status_value = status.value if status is not None else None
        search_pattern: str | None = None
        if query is not None and query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            search_pattern = f"%{escaped}%"
        with self.database.connection() as connection:
            row = self._one(
                connection.execute(
                    """
                    SELECT COUNT(*) AS total FROM documents
                    WHERE workspace_id = ?
                      AND (? = 1 OR status <> 'deleted')
                      AND (? IS NULL OR status = ?)
                      AND (? IS NULL OR display_name LIKE ? ESCAPE '\\')
                    """,
                    (
                        workspace,
                        int(include_deleted),
                        status_value,
                        status_value,
                        search_pattern,
                        search_pattern,
                    ),
                ).fetchone()
            )
        return int(row["total"])

    def document_status_counts(self, workspace_id: str) -> dict[DocumentStatus, int]:
        workspace = self._identifier(workspace_id, "workspace_id")
        counts = {status: 0 for status in DocumentStatus}
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS total FROM documents
                WHERE workspace_id = ? GROUP BY status
                """,
                (workspace,),
            ).fetchall()
        for row in rows:
            counts[DocumentStatus(str(row["status"]))] = int(row["total"])
        return counts

    def get_document_version(self, version_id: str) -> DocumentVersion | None:
        identifier = self._identifier(version_id, "version_id")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM document_versions WHERE id = ?", (identifier,)
            ).fetchone()
        return self._version(row) if row is not None else None

    def list_document_versions(self, document_id: str) -> list[DocumentVersion]:
        identifier = self._identifier(document_id, "document_id")
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM document_versions WHERE document_id = ?
                ORDER BY ordinal DESC
                """,
                (identifier,),
            ).fetchall()
        return [self._version(row) for row in rows]

    def update_version_analysis(
        self, version_id: str, *, page_count: int, warning_count: int = 0
    ) -> DocumentVersion:
        identifier = self._identifier(version_id, "version_id")
        if page_count < 1:
            raise ValueError("page_count must be positive")
        if warning_count < 0:
            raise ValueError("warning_count must not be negative")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE document_versions SET page_count = ?, warning_count = ?
                WHERE id = ? AND file_deleted_at IS NULL
                """,
                (page_count, warning_count, identifier),
            )
            if changed.rowcount != 1:
                raise RecordNotFoundError("active document version does not exist")
            connection.execute(
                """
                UPDATE documents
                SET page_count = ?, warning_count = ?, updated_at = ?
                WHERE current_version_id = ? AND status <> 'deleted'
                """,
                (page_count, warning_count, now, identifier),
            )
            row = self._one(
                connection.execute(
                    "SELECT * FROM document_versions WHERE id = ?", (identifier,)
                ).fetchone()
            )
        return self._version(row)

    def replace_elements(self, version_id: str, elements: Sequence[ContentElement]) -> int:
        """Atomically replace one retryable version's authoritative element inventory."""

        identifier = self._identifier(version_id, "version_id")
        if len({element.id for element in elements}) != len(elements):
            raise ValueError("element identifiers must be unique")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT * FROM document_versions WHERE id = ? AND file_deleted_at IS NULL",
                (identifier,),
            ).fetchone()
            if version is None:
                raise RecordNotFoundError("active document version does not exist")
            for element in elements:
                if (
                    element.version_id != identifier
                    or element.document_id != version["document_id"]
                    or element.workspace_id != version["workspace_id"]
                ):
                    raise ValueError("element scope does not match its document version")
            connection.execute("DELETE FROM elements WHERE version_id = ?", (identifier,))
            connection.executemany(
                """
                INSERT INTO elements (
                    id, workspace_id, document_id, version_id, page_number, modality,
                    content, bbox_json, asset_key, confidence, extraction_method,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        element.id,
                        element.workspace_id,
                        element.document_id,
                        element.version_id,
                        element.page_number,
                        element.modality.value,
                        element.content,
                        self._json(element.bbox) if element.bbox is not None else None,
                        element.asset_key,
                        element.confidence,
                        element.extraction_method,
                        self._json(element.metadata),
                        now,
                    )
                    for element in elements
                ],
            )
            connection.execute(
                """
                UPDATE documents SET element_count = ?, updated_at = ?
                WHERE current_version_id = ? AND status <> 'deleted'
                """,
                (len(elements), now, identifier),
            )
        return len(elements)

    def get_element(self, element_id: str) -> ContentElement | None:
        identifier = self._identifier(element_id, "element_id")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM elements WHERE id = ?", (identifier,)
            ).fetchone()
        return self._element(row) if row is not None else None

    def get_active_element_by_asset_key(
        self, workspace_id: str, asset_key: str
    ) -> ContentElement | None:
        """Resolve only assets referenced by an active document's current evidence."""

        workspace = self._identifier(workspace_id, "workspace_id")
        key = self._text(asset_key, "asset_key", maximum=1000)
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT e.* FROM elements AS e
                JOIN documents AS d
                  ON d.id = e.document_id AND d.current_version_id = e.version_id
                WHERE e.workspace_id = ? AND e.asset_key = ?
                  AND d.status NOT IN ('deleting', 'deleted')
                ORDER BY e.page_number, e.id LIMIT 1
                """,
                (workspace, key),
            ).fetchone()
        return self._element(row) if row is not None else None

    def list_elements(
        self,
        version_id: str,
        *,
        modalities: Sequence[Modality] | None = None,
        page_number: int | None = None,
        limit: int = 10_000,
    ) -> list[ContentElement]:
        identifier = self._identifier(version_id, "version_id")
        if limit < 1 or limit > 50_000:
            raise ValueError("limit must be between 1 and 50000")
        modality_values = (
            self._json([modality.value for modality in modalities]) if modalities else None
        )
        if page_number is not None and page_number < 1:
            raise ValueError("page_number must be positive")
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM elements
                WHERE version_id = ?
                  AND (? IS NULL OR modality IN (SELECT value FROM json_each(?)))
                  AND (? IS NULL OR page_number = ?)
                ORDER BY page_number, id LIMIT ?
                """,
                (
                    identifier,
                    modality_values,
                    modality_values,
                    page_number,
                    page_number,
                    limit,
                ),
            ).fetchall()
        return [self._element(row) for row in rows]

    def get_job(self, job_id: str) -> IngestionJob | None:
        identifier = self._identifier(job_id, "job_id")
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
        return self._job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        workspace_id: str | None = None,
        document_id: str | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IngestionJob]:
        self._pagination(limit, offset)
        workspace = (
            self._identifier(workspace_id, "workspace_id") if workspace_id is not None else None
        )
        document = self._identifier(document_id, "document_id") if document_id is not None else None
        status_value = status.value if status is not None else None
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE (? IS NULL OR workspace_id = ?)
                  AND (? IS NULL OR document_id = ?)
                  AND (? IS NULL OR status = ?)
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (
                    workspace,
                    workspace,
                    document,
                    document,
                    status_value,
                    status_value,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [self._job(row) for row in rows]

    def job_status_counts(self, workspace_id: str) -> dict[JobStatus, int]:
        workspace = self._identifier(workspace_id, "workspace_id")
        counts = {status: 0 for status in JobStatus}
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS total FROM jobs
                WHERE workspace_id = ? GROUP BY status
                """,
                (workspace,),
            ).fetchall()
        for row in rows:
            counts[JobStatus(str(row["status"]))] = int(row["total"])
        return counts

    def recover_expired_jobs(self) -> int:
        """Requeue interrupted work or fail it at the configured attempt ceiling."""

        now = self._now()
        with self.database.transaction(immediate=True) as connection:
            return self._recover_expired_jobs(connection, now)

    def lease_next_job(
        self,
        owner: str,
        *,
        lease_seconds: int | None = None,
        kinds: Sequence[JobKind] | None = None,
    ) -> IngestionJob | None:
        """Atomically recover expired work and lease the oldest eligible job."""

        normalized_owner = self._text(owner, "owner", maximum=120)
        duration = lease_seconds or self.lease_seconds
        if duration < 1:
            raise ValueError("lease_seconds must be positive")
        now = self._now()
        now_text = self._datetime_text(now)
        expires_text = self._datetime_text(now + timedelta(seconds=duration))
        with self.database.transaction(immediate=True) as connection:
            self._recover_expired_jobs(connection, now)
            kind_values = self._json([kind.value for kind in kinds]) if kinds else None
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                  AND cancellation_requested = 0
                  AND attempt_count < max_attempts
                  AND available_at <= ?
                  AND (? IS NULL OR kind IN (SELECT value FROM json_each(?)))
                ORDER BY available_at, created_at, id LIMIT 1
                """,
                (now_text, kind_values, kind_values),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?,
                    error_code = NULL, error_message = NULL, updated_at = ?
                WHERE id = ? AND status = 'queued' AND cancellation_requested = 0
                  AND attempt_count < max_attempts
                """,
                (normalized_owner, expires_text, now_text, now_text, row["id"]),
            )
            if changed.rowcount != 1:
                return None
            leased = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            )
            return self._job(leased)

    def heartbeat_job(
        self,
        job_id: str,
        owner: str,
        *,
        lease_seconds: int | None = None,
    ) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        normalized_owner = self._text(owner, "owner", maximum=120)
        duration = lease_seconds or self.lease_seconds
        if duration < 1:
            raise ValueError("lease_seconds must be positive")
        now = self._now()
        now_text = self._datetime_text(now)
        expires = self._datetime_text(now + timedelta(seconds=duration))
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at > ? AND cancellation_requested = 0
                """,
                (expires, now_text, now_text, identifier, normalized_owner, now_text),
            )
            if changed.rowcount != 1:
                raise LeaseLostError("job lease is no longer active")
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def advance_job(
        self,
        job_id: str,
        owner: str,
        *,
        stage: JobStage,
        progress: float,
    ) -> IngestionJob:
        """Persist forward-only stage/progress before expensive work begins."""

        identifier = self._identifier(job_id, "job_id")
        normalized_owner = self._text(owner, "owner", maximum=120)
        if not 0 <= progress < 1:
            raise ValueError("running job progress must be at least 0 and less than 1")
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            current = self._owned_job(connection, identifier, normalized_owner, now_text)
            current_stage = JobStage(str(current["stage"]))
            kind = JobKind(str(current["kind"]))
            if kind is JobKind.DELETE:
                if stage not in {JobStage.DELETING, JobStage.VERIFYING}:
                    raise InvalidStateTransitionError("delete jobs may only delete and verify")
            elif stage is JobStage.DELETING:
                raise InvalidStateTransitionError("ingestion jobs cannot enter deletion")
            if stage is not JobStage.DELETING:
                current_rank = _STAGE_RANK.get(current_stage, 0)
                requested_rank = _STAGE_RANK.get(stage)
                if requested_rank is None or requested_rank < current_rank:
                    raise InvalidStateTransitionError("job stages cannot move backward")
            if progress < float(current["progress"]):
                raise InvalidStateTransitionError("job progress cannot move backward")
            connection.execute(
                """
                UPDATE jobs SET stage = ?, progress = ?, updated_at = ?
                WHERE id = ?
                """,
                (stage.value, progress, now_text, identifier),
            )
            if kind is not JobKind.DELETE:
                connection.execute(
                    """
                    UPDATE documents SET status = 'processing', updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (now_text, current["document_id"]),
                )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def complete_job(self, job_id: str, owner: str) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        normalized_owner = self._text(owner, "owner", maximum=120)
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            current = self._owned_job(connection, identifier, normalized_owner, now_text)
            kind = JobKind(str(current["kind"]))
            connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', stage = 'complete', progress = 1,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    cancellation_requested = 0, error_code = NULL, error_message = NULL,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (now_text, now_text, identifier),
            )
            if kind is not JobKind.DELETE:
                version = self._one(
                    connection.execute(
                        """
                        SELECT page_count, warning_count FROM document_versions WHERE id = ?
                        """,
                        (current["version_id"],),
                    ).fetchone()
                )
                element_count = int(
                    self._one(
                        connection.execute(
                            "SELECT COUNT(*) AS total FROM elements WHERE version_id = ?",
                            (current["version_id"],),
                        ).fetchone()
                    )["total"]
                )
                ready_status = (
                    DocumentStatus.READY_WITH_WARNINGS.value
                    if int(version["warning_count"]) > 0
                    else DocumentStatus.READY.value
                )
                connection.execute(
                    """
                    UPDATE documents
                    SET status = ?, page_count = ?, warning_count = ?, element_count = ?,
                        updated_at = ?
                    WHERE id = ? AND current_version_id = ?
                      AND status NOT IN ('deleting', 'deleted')
                    """,
                    (
                        ready_status,
                        version["page_count"],
                        version["warning_count"],
                        element_count,
                        now_text,
                        current["document_id"],
                        current["version_id"],
                    ),
                )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def fail_job(
        self,
        job_id: str,
        owner: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: float = 0,
    ) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        normalized_owner = self._text(owner, "owner", maximum=120)
        code = self._error_code(error_code)
        message = self._safe_error_message(error_message)
        if retry_delay_seconds < 0 or retry_delay_seconds > 3_600:
            raise ValueError("retry_delay_seconds must be between 0 and 3600")
        now = self._now()
        now_text = self._datetime_text(now)
        available = self._datetime_text(now + timedelta(seconds=retry_delay_seconds))
        with self.database.transaction(immediate=True) as connection:
            current = self._owned_job(connection, identifier, normalized_owner, now_text)
            should_retry = (
                retryable
                and not bool(current["cancellation_requested"])
                and int(current["attempt_count"]) < int(current["max_attempts"])
            )
            if should_retry:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', stage = 'queued', progress = 0,
                        lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                        available_at = ?, error_code = ?, error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (available, code, message, now_text, identifier),
                )
            else:
                final_status = (
                    JobStatus.CANCELLED.value
                    if bool(current["cancellation_requested"])
                    else JobStatus.FAILED.value
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, error_code = ?, error_message = ?,
                        updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (final_status, code, message, now_text, now_text, identifier),
                )
                if current["kind"] != JobKind.DELETE.value:
                    connection.execute(
                        """
                        UPDATE documents SET status = 'failed', updated_at = ?
                        WHERE id = ? AND status NOT IN ('deleting', 'deleted')
                        """,
                        (now_text, current["document_id"]),
                    )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def request_job_cancellation(self, job_id: str) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (identifier,)
            ).fetchone()
            if current is None:
                raise RecordNotFoundError("job does not exist")
            status = JobStatus(str(current["status"]))
            if status is JobStatus.QUEUED:
                connection.execute(
                    """
                    UPDATE jobs SET status = 'cancelled', cancellation_requested = 1,
                        updated_at = ?, finished_at = ? WHERE id = ?
                    """,
                    (now_text, now_text, identifier),
                )
                if current["kind"] != JobKind.DELETE.value:
                    connection.execute(
                        """
                        UPDATE documents SET status = 'failed', updated_at = ?
                        WHERE id = ? AND status NOT IN ('deleting', 'deleted')
                        """,
                        (now_text, current["document_id"]),
                    )
            elif status is JobStatus.RUNNING:
                connection.execute(
                    """
                    UPDATE jobs SET cancellation_requested = 1, updated_at = ? WHERE id = ?
                    """,
                    (now_text, identifier),
                )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def job_cancellation_requested(self, job_id: str, owner: str | None = None) -> bool:
        identifier = self._identifier(job_id, "job_id")
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise RecordNotFoundError("job does not exist")
        if owner is not None and row["lease_owner"] != owner:
            raise LeaseLostError("job is not leased by this worker")
        return bool(row["cancellation_requested"])

    def acknowledge_job_cancellation(self, job_id: str, owner: str) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        normalized_owner = self._text(owner, "owner", maximum=120)
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            current = self._owned_job(
                connection, identifier, normalized_owner, now_text, allow_cancelled=True
            )
            if not bool(current["cancellation_requested"]):
                raise InvalidStateTransitionError("job cancellation was not requested")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (now_text, now_text, identifier),
            )
            if current["kind"] != JobKind.DELETE.value:
                connection.execute(
                    """
                    UPDATE documents SET status = 'failed', updated_at = ?
                    WHERE id = ? AND status NOT IN ('deleting', 'deleted')
                    """,
                    (now_text, current["document_id"]),
                )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def retry_job(self, job_id: str) -> IngestionJob:
        identifier = self._identifier(job_id, "job_id")
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (identifier,)
            ).fetchone()
            if current is None:
                raise RecordNotFoundError("job does not exist")
            if current["status"] not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
                raise InvalidStateTransitionError("only failed or cancelled jobs may be retried")
            active = connection.execute(
                """
                SELECT id FROM jobs WHERE version_id = ? AND kind = ?
                  AND status IN ('queued', 'running') AND id <> ?
                LIMIT 1
                """,
                (current["version_id"], current["kind"], identifier),
            ).fetchone()
            if active is not None:
                raise PersistenceConflictError("equivalent work is already active")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', stage = 'queued', progress = 0, attempt_count = 0,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    cancellation_requested = 0, available_at = ?, error_code = NULL,
                    error_message = NULL, updated_at = ?, finished_at = NULL
                WHERE id = ?
                """,
                (now_text, now_text, identifier),
            )
            if current["kind"] == JobKind.DELETE.value:
                connection.execute(
                    "UPDATE documents SET status = 'deleting', updated_at = ? WHERE id = ?",
                    (now_text, current["document_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE documents SET status = 'queued', updated_at = ?
                    WHERE id = ? AND status NOT IN ('deleting', 'deleted')
                    """,
                    (now_text, current["document_id"]),
                )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (identifier,)).fetchone()
            )
        return self._job(row)

    def queue_reprocess(self, document_id: str) -> IngestionJob:
        """Idempotently queue the current immutable version for renewed analysis."""

        identifier = self._identifier(document_id, "document_id")
        now_text = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (identifier,)
            ).fetchone()
            if document is None:
                raise RecordNotFoundError("document does not exist")
            status = DocumentStatus(str(document["status"]))
            if status in {DocumentStatus.DELETING, DocumentStatus.DELETED}:
                raise InvalidStateTransitionError("deleted documents cannot be reprocessed")
            active = connection.execute(
                """
                SELECT * FROM jobs
                WHERE document_id = ? AND version_id = ? AND kind = 'reprocess'
                  AND status IN ('queued', 'running')
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (identifier, document["current_version_id"]),
            ).fetchone()
            if active is not None:
                return self._job(active)
            if status not in {
                DocumentStatus.READY,
                DocumentStatus.READY_WITH_WARNINGS,
                DocumentStatus.FAILED,
            }:
                raise InvalidStateTransitionError(
                    "only ready or failed documents may be reprocessed"
                )

            job_id = self.new_id()
            connection.execute(
                """
                INSERT INTO jobs (
                    id, workspace_id, document_id, version_id, kind, status, stage,
                    progress, attempt_count, max_attempts, cancellation_requested,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reprocess', 'queued', 'queued',
                          0, 0, ?, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    document["workspace_id"],
                    identifier,
                    document["current_version_id"],
                    self.max_attempts,
                    now_text,
                    now_text,
                    now_text,
                ),
            )
            connection.execute(
                """
                UPDATE documents SET status = 'queued', updated_at = ? WHERE id = ?
                """,
                (now_text, identifier),
            )
            row = self._one(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )
        return self._job(row)

    def mark_document_deleting(
        self,
        document_id: str,
        *,
        job_id: str | None = None,
        max_attempts: int | None = None,
    ) -> IngestionJob:
        """Remove a document from retrieval immediately and queue verified deletion."""

        identifier = self._identifier(document_id, "document_id")
        requested_job_id = self._identifier(job_id or self.new_id(), "job_id")
        attempts = max_attempts or self.max_attempts
        if attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (identifier,)
            ).fetchone()
            if document is None:
                raise RecordNotFoundError("document does not exist")
            if document["status"] == DocumentStatus.DELETED.value:
                raise InvalidStateTransitionError("document is already deleted")
            existing = connection.execute(
                """
                SELECT * FROM jobs WHERE document_id = ? AND kind = 'delete'
                  AND status IN ('queued', 'running')
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (identifier,),
            ).fetchone()
            if existing is not None:
                return self._job(existing)
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', cancellation_requested = 1,
                    updated_at = ?, finished_at = ?
                WHERE document_id = ? AND kind IN ('ingest', 'reprocess')
                  AND status = 'queued'
                """,
                (now, now, identifier),
            )
            connection.execute(
                """
                UPDATE jobs SET cancellation_requested = 1, updated_at = ?
                WHERE document_id = ? AND kind IN ('ingest', 'reprocess')
                  AND status = 'running'
                """,
                (now, identifier),
            )
            connection.execute(
                "UPDATE documents SET status = 'deleting', updated_at = ? WHERE id = ?",
                (now, identifier),
            )
            connection.execute(
                """
                INSERT INTO jobs (
                    id, workspace_id, document_id, version_id, kind, status, stage,
                    progress, attempt_count, max_attempts, cancellation_requested,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'delete', 'queued', 'queued', 0, 0, ?, 0, ?, ?, ?)
                """,
                (
                    requested_job_id,
                    document["workspace_id"],
                    identifier,
                    document["current_version_id"],
                    attempts,
                    now,
                    now,
                    now,
                ),
            )
            row = self._one(
                connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (requested_job_id,)
                ).fetchone()
            )
        return self._job(row)

    def finalize_document_deletion(
        self,
        document_id: str,
        *,
        artifacts_verified: bool,
        vectors_verified: bool,
    ) -> DeletionReadback:
        """Tombstone a document only after both external deletion readbacks pass."""

        if not artifacts_verified or not vectors_verified:
            raise InvalidStateTransitionError(
                "document deletion requires verified file and vector absence"
            )
        identifier = self._identifier(document_id, "document_id")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (identifier,)
            ).fetchone()
            if document is None:
                raise RecordNotFoundError("document does not exist")
            if document["status"] not in {
                DocumentStatus.DELETING.value,
                DocumentStatus.DELETED.value,
            }:
                raise InvalidStateTransitionError("document was not marked for deletion")
            connection.execute("DELETE FROM elements WHERE document_id = ?", (identifier,))
            connection.execute(
                """
                UPDATE document_versions SET file_deleted_at = COALESCE(file_deleted_at, ?)
                WHERE document_id = ?
                """,
                (now, identifier),
            )
            connection.execute(
                """
                UPDATE citations SET available = 0, asset_url = NULL
                WHERE document_id = ?
                """,
                (identifier,),
            )
            connection.execute(
                """
                UPDATE documents
                SET status = 'deleted', page_count = NULL, element_count = 0,
                    warning_count = 0, updated_at = ?, deleted_at = ?
                WHERE id = ?
                """,
                (now, now, identifier),
            )
        return self.deletion_readback(identifier)

    def deletion_readback(self, document_id: str) -> DeletionReadback:
        identifier = self._identifier(document_id, "document_id")
        with self.database.connection() as connection:
            document = connection.execute(
                "SELECT status FROM documents WHERE id = ?", (identifier,)
            ).fetchone()
            if document is None:
                raise RecordNotFoundError("document does not exist")
            element_count = int(
                self._one(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM elements WHERE document_id = ?",
                        (identifier,),
                    ).fetchone()
                )["total"]
            )
            citation_count = int(
                self._one(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS total FROM citations
                        WHERE document_id = ? AND (available = 1 OR asset_url IS NOT NULL)
                        """,
                        (identifier,),
                    ).fetchone()
                )["total"]
            )
            undeleted_versions = int(
                self._one(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS total FROM document_versions
                        WHERE document_id = ? AND file_deleted_at IS NULL
                        """,
                        (identifier,),
                    ).fetchone()
                )["total"]
            )
        return DeletionReadback(
            document_id=identifier,
            status=DocumentStatus(str(document["status"])),
            active_element_count=element_count,
            available_citation_count=citation_count,
            undeleted_version_count=undeleted_versions,
        )

    def verify_document_deleted(self, document_id: str) -> bool:
        return self.deletion_readback(document_id).verified

    def create_conversation(
        self,
        workspace_id: str,
        *,
        title: str = "New conversation",
        document_ids: Sequence[str] = (),
        conversation_id: str | None = None,
    ) -> Conversation:
        workspace = self._identifier(workspace_id, "workspace_id")
        identifier = self._identifier(conversation_id or self.new_id(), "conversation_id")
        normalized_title = self._text(title, "title", maximum=160)
        normalized_documents = self._identifier_list(document_ids, "document_id")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            self._require_workspace(connection, workspace)
            self._validate_document_scope(connection, workspace, normalized_documents)
            try:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id, workspace_id, title, document_ids_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        workspace,
                        normalized_title,
                        self._json(normalized_documents),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceConflictError("conversation identifier is already in use") from exc
            row = self._one(
                connection.execute(
                    "SELECT * FROM conversations WHERE id = ?", (identifier,)
                ).fetchone()
            )
        return self._conversation(row)

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        identifier = self._identifier(conversation_id, "conversation_id")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (identifier,)
            ).fetchone()
        return self._conversation(row) if row is not None else None

    def list_conversations(
        self, workspace_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        workspace = self._identifier(workspace_id, "workspace_id")
        self._pagination(limit, offset)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversations WHERE workspace_id = ?
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (workspace, limit, offset),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def count_conversations(self, workspace_id: str) -> int:
        workspace = self._identifier(workspace_id, "workspace_id")
        with self.database.connection() as connection:
            row = self._one(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM conversations WHERE workspace_id = ?",
                    (workspace,),
                ).fetchone()
            )
        return int(row["total"])

    def update_conversation_scope(
        self, conversation_id: str, document_ids: Sequence[str]
    ) -> Conversation:
        identifier = self._identifier(conversation_id, "conversation_id")
        normalized_documents = self._identifier_list(document_ids, "document_id")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (identifier,)
            ).fetchone()
            if conversation is None:
                raise RecordNotFoundError("conversation does not exist")
            self._validate_document_scope(
                connection, str(conversation["workspace_id"]), normalized_documents
            )
            connection.execute(
                """
                UPDATE conversations SET document_ids_json = ?, updated_at = ? WHERE id = ?
                """,
                (self._json(normalized_documents), now, identifier),
            )
            row = self._one(
                connection.execute(
                    "SELECT * FROM conversations WHERE id = ?", (identifier,)
                ).fetchone()
            )
        return self._conversation(row)

    def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        answer_id: str | None = None,
        message_id: str | None = None,
    ) -> MessageRecord:
        identifier = self._identifier(conversation_id, "conversation_id")
        if role not in {"user", "assistant", "system"}:
            raise ValueError("role must be user, assistant, or system")
        normalized_content = self._text(content, "content", maximum=20_000)
        normalized_answer_id = (
            self._identifier(answer_id, "answer_id") if answer_id is not None else None
        )
        message_identifier = self._identifier(message_id or self.new_id(), "message_id")
        now = self._now_text()
        with self.database.transaction(immediate=True) as connection:
            if (
                connection.execute(
                    "SELECT id FROM conversations WHERE id = ?", (identifier,)
                ).fetchone()
                is None
            ):
                raise RecordNotFoundError("conversation does not exist")
            connection.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, answer_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_identifier,
                    identifier,
                    role,
                    normalized_content,
                    normalized_answer_id,
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, identifier)
            )
            row = self._one(
                connection.execute(
                    "SELECT * FROM messages WHERE id = ?", (message_identifier,)
                ).fetchone()
            )
        return self._message(row)

    def list_messages(
        self, conversation_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[MessageRecord]:
        identifier = self._identifier(conversation_id, "conversation_id")
        self._pagination(limit, offset)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM messages WHERE conversation_id = ?
                ORDER BY created_at, id LIMIT ? OFFSET ?
                """,
                (identifier, limit, offset),
            ).fetchall()
        return [self._message(row) for row in rows]

    def persist_answer(
        self,
        answer: Answer,
        *,
        document_versions: Sequence[DocumentVersion] = (),
    ) -> Answer:
        """Persist an answer, claims, evidence snapshots, and messages atomically."""

        if len({citation.id for citation in answer.citations}) != len(answer.citations):
            raise ValueError("citation identifiers must be unique")
        citation_ids = {citation.id for citation in answer.citations}
        for claim in answer.claims:
            unknown = set(claim.citation_ids) - citation_ids
            if unknown:
                raise ValueError("claim references a citation outside this answer")
        if not answer.abstained and answer.claims and not answer.citations:
            raise ValueError("supported claims require citation snapshots")

        created_at = self._datetime_text(answer.created_at)
        message_time = self._now()
        user_message_time = self._datetime_text(message_time)
        assistant_message_time = self._datetime_text(message_time + timedelta(microseconds=1))
        with self.database.transaction(immediate=True) as connection:
            conversation_row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (answer.conversation_id,)
            ).fetchone()
            if conversation_row is None:
                raise RecordNotFoundError("conversation does not exist")
            workspace_id = str(conversation_row["workspace_id"])
            scoped_document_ids = set(json.loads(str(conversation_row["document_ids_json"])))
            validated_citations: list[Citation] = []
            version_rows: dict[str, sqlite3.Row] = {}

            for citation in answer.citations:
                element = connection.execute(
                    """
                    SELECT e.*, d.display_name, d.status AS document_status,
                           v.id AS snapshot_version_id, v.file_deleted_at, v.sha256,
                           v.parser_profile, v.embedding_profile
                    FROM elements AS e
                    JOIN documents AS d ON d.id = e.document_id
                    JOIN document_versions AS v ON v.id = e.version_id
                    WHERE e.id = ?
                    """,
                    (citation.element_id,),
                ).fetchone()
                if element is None:
                    raise PersistenceConflictError("citation element is not available")
                if (
                    element["workspace_id"] != workspace_id
                    or element["document_id"] != citation.document_id
                    or element["version_id"] != citation.version_id
                    or int(element["page_number"]) != citation.page_number
                    or element["modality"] != citation.modality.value
                    or element["file_deleted_at"] is not None
                    or element["document_status"]
                    not in {
                        DocumentStatus.READY.value,
                        DocumentStatus.READY_WITH_WARNINGS.value,
                        DocumentStatus.PROCESSING.value,
                    }
                ):
                    raise PersistenceConflictError("citation scope or provenance is invalid")
                if scoped_document_ids and citation.document_id not in scoped_document_ids:
                    raise PersistenceConflictError("citation is outside the conversation scope")
                authoritative_bbox = (
                    tuple(json.loads(str(element["bbox_json"])))
                    if element["bbox_json"] is not None
                    else None
                )
                if citation.bbox != authoritative_bbox:
                    raise PersistenceConflictError("citation bounding box is not authoritative")
                validated_citations.append(
                    Citation(
                        id=citation.id,
                        document_id=citation.document_id,
                        version_id=citation.version_id,
                        document_name=str(element["display_name"]),
                        element_id=citation.element_id,
                        page_number=citation.page_number,
                        modality=citation.modality,
                        excerpt=citation.excerpt,
                        bbox=citation.bbox,
                        asset_url=citation.asset_url,
                        available=True,
                    )
                )
                version_rows[citation.version_id] = element

            for version in document_versions:
                row = connection.execute(
                    """
                    SELECT v.*, v.id AS snapshot_version_id,
                           d.display_name, d.status AS document_status
                    FROM document_versions AS v
                    JOIN documents AS d ON d.id = v.document_id
                    WHERE v.id = ?
                    """,
                    (version.id,),
                ).fetchone()
                if row is None or row["workspace_id"] != workspace_id:
                    raise PersistenceConflictError("answer version is outside the workspace")
                if row["document_id"] != version.document_id or row["sha256"] != version.sha256:
                    raise PersistenceConflictError("answer version snapshot is not authoritative")
                if scoped_document_ids and version.document_id not in scoped_document_ids:
                    raise PersistenceConflictError("answer version is outside conversation scope")
                version_rows[version.id] = row

            try:
                connection.execute(
                    """
                    INSERT INTO answers (
                        id, conversation_id, question, answer_text, modalities_json,
                        abstained, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        answer.id,
                        answer.conversation_id,
                        answer.question,
                        answer.text,
                        self._json([modality.value for modality in answer.modalities_used]),
                        int(answer.abstained),
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistenceConflictError("answer identifier is already in use") from exc

            connection.executemany(
                """
                INSERT INTO citations (
                    id, answer_id, ordinal, document_id, version_id, document_name,
                    element_id, page_number, modality, excerpt, bbox_json, asset_url, available
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        citation.id,
                        answer.id,
                        ordinal,
                        citation.document_id,
                        citation.version_id,
                        citation.document_name,
                        citation.element_id,
                        citation.page_number,
                        citation.modality.value,
                        citation.excerpt,
                        self._json(citation.bbox) if citation.bbox is not None else None,
                        citation.asset_url,
                        int(citation.available),
                    )
                    for ordinal, citation in enumerate(validated_citations)
                ],
            )
            connection.executemany(
                """
                INSERT INTO claims (answer_id, ordinal, claim_text, inference)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (answer.id, ordinal, claim.text, int(claim.inference))
                    for ordinal, claim in enumerate(answer.claims)
                ],
            )
            connection.executemany(
                """
                INSERT INTO claim_citations (
                    answer_id, claim_ordinal, citation_id, citation_ordinal
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (answer.id, claim_ordinal, citation_id, citation_ordinal)
                    for claim_ordinal, claim in enumerate(answer.claims)
                    for citation_ordinal, citation_id in enumerate(claim.citation_ids)
                ],
            )
            ordered_versions = sorted(
                version_rows.values(), key=lambda row: (str(row["document_id"]), str(row["id"]))
            )
            connection.executemany(
                """
                INSERT INTO answer_versions (
                    answer_id, ordinal, document_id, version_id, document_name,
                    sha256, parser_profile, embedding_profile
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        answer.id,
                        ordinal,
                        row["document_id"],
                        row["snapshot_version_id"],
                        row["display_name"],
                        row["sha256"],
                        row["parser_profile"],
                        row["embedding_profile"],
                    )
                    for ordinal, row in enumerate(ordered_versions)
                ],
            )
            connection.executemany(
                """
                INSERT INTO messages (id, conversation_id, role, content, answer_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        self.new_id(),
                        answer.conversation_id,
                        "user",
                        answer.question,
                        answer.id,
                        user_message_time,
                    ),
                    (
                        self.new_id(),
                        answer.conversation_id,
                        "assistant",
                        answer.text,
                        answer.id,
                        assistant_message_time,
                    ),
                ],
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (assistant_message_time, answer.conversation_id),
            )
        persisted = self.get_answer(answer.id)
        if persisted is None:
            raise RepositoryError("persisted answer could not be read back")
        return persisted

    def get_answer(self, answer_id: str) -> Answer | None:
        identifier = self._identifier(answer_id, "answer_id")
        with self.database.connection() as connection:
            answer_row = connection.execute(
                "SELECT * FROM answers WHERE id = ?", (identifier,)
            ).fetchone()
            if answer_row is None:
                return None
            citation_rows = connection.execute(
                "SELECT * FROM citations WHERE answer_id = ? ORDER BY ordinal",
                (identifier,),
            ).fetchall()
            claim_rows = connection.execute(
                "SELECT * FROM claims WHERE answer_id = ? ORDER BY ordinal",
                (identifier,),
            ).fetchall()
            link_rows = connection.execute(
                """
                SELECT claim_ordinal, citation_id FROM claim_citations
                WHERE answer_id = ? ORDER BY claim_ordinal, citation_ordinal
                """,
                (identifier,),
            ).fetchall()
        citation_links: dict[int, list[str]] = {}
        for row in link_rows:
            citation_links.setdefault(int(row["claim_ordinal"]), []).append(str(row["citation_id"]))
        return Answer(
            id=str(answer_row["id"]),
            conversation_id=str(answer_row["conversation_id"]),
            question=str(answer_row["question"]),
            text=str(answer_row["answer_text"]),
            claims=[
                AnswerClaim(
                    text=str(row["claim_text"]),
                    citation_ids=citation_links.get(int(row["ordinal"]), []),
                    inference=bool(row["inference"]),
                )
                for row in claim_rows
            ],
            citations=[self._citation(row) for row in citation_rows],
            modalities_used=[
                Modality(value) for value in json.loads(str(answer_row["modalities_json"]))
            ],
            abstained=bool(answer_row["abstained"]),
            created_at=self._parse_datetime(str(answer_row["created_at"])),
        )

    def list_answers(
        self, conversation_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[Answer]:
        identifier = self._identifier(conversation_id, "conversation_id")
        self._pagination(limit, offset)
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM answers WHERE conversation_id = ?
                ORDER BY created_at, id LIMIT ? OFFSET ?
                """,
                (identifier, limit, offset),
            ).fetchall()
        answers: list[Answer] = []
        for row in rows:
            answer = self.get_answer(str(row["id"]))
            if answer is not None:
                answers.append(answer)
        return answers

    def _recover_expired_jobs(self, connection: sqlite3.Connection, now: datetime) -> int:
        now_text = self._datetime_text(now)
        expired = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running' AND lease_expires_at <= ?
            ORDER BY lease_expires_at, id
            """,
            (now_text,),
        ).fetchall()
        for job in expired:
            if bool(job["cancellation_requested"]):
                status = JobStatus.CANCELLED.value
                finished_at: str | None = now_text
            elif int(job["attempt_count"]) >= int(job["max_attempts"]):
                status = JobStatus.FAILED.value
                finished_at = now_text
            else:
                status = JobStatus.QUEUED.value
                finished_at = None
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = CASE WHEN ? = 'queued' THEN 'queued' ELSE stage END,
                    progress = CASE WHEN ? = 'queued' THEN 0 ELSE progress END,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    available_at = ?, error_code = 'lease_expired',
                    error_message = 'The previous worker stopped before completing this job.',
                    updated_at = ?, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (status, status, status, now_text, now_text, finished_at, job["id"]),
            )
            if status == JobStatus.FAILED.value and job["kind"] != JobKind.DELETE.value:
                connection.execute(
                    """
                    UPDATE documents SET status = 'failed', updated_at = ?
                    WHERE id = ? AND status NOT IN ('deleting', 'deleted')
                    """,
                    (now_text, job["document_id"]),
                )
        return len(expired)

    def _owned_job(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        owner: str,
        now_text: str,
        *,
        allow_cancelled: bool = False,
    ) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone(),
        )
        if row is None:
            raise RecordNotFoundError("job does not exist")
        if (
            row["status"] != JobStatus.RUNNING.value
            or row["lease_owner"] != owner
            or row["lease_expires_at"] is None
            or str(row["lease_expires_at"]) <= now_text
            or (bool(row["cancellation_requested"]) and not allow_cancelled)
        ):
            raise LeaseLostError("job lease is no longer active")
        return row

    def _receipt_from_ids(
        self,
        connection: sqlite3.Connection,
        document_id: str,
        version_id: str,
        job_id: str,
        *,
        duplicate: bool,
    ) -> UploadReceipt:
        document = self._one(
            connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        )
        version = self._one(
            connection.execute(
                "SELECT * FROM document_versions WHERE id = ?", (version_id,)
            ).fetchone()
        )
        job = self._one(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        return UploadReceipt(
            document=self._document(document),
            version=self._version(version),
            job=self._job(job),
            duplicate=duplicate,
        )

    @staticmethod
    def _insert_idempotency(
        connection: sqlite3.Connection,
        workspace_id: str,
        idempotency_key: str,
        fingerprint: str,
        receipt: UploadReceipt,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO upload_idempotency (
                workspace_id, idempotency_key, request_fingerprint,
                document_id, version_id, job_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                idempotency_key,
                fingerprint,
                receipt.document.id,
                receipt.version.id,
                receipt.job.id,
                now,
            ),
        )

    @staticmethod
    def _require_workspace(connection: sqlite3.Connection, workspace_id: str) -> None:
        if (
            connection.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
            is None
        ):
            raise RecordNotFoundError("workspace does not exist")

    @staticmethod
    def _validate_document_scope(
        connection: sqlite3.Connection, workspace_id: str, document_ids: Sequence[str]
    ) -> None:
        for document_id in document_ids:
            row = connection.execute(
                "SELECT workspace_id, status FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if (
                row is None
                or row["workspace_id"] != workspace_id
                or row["status"] in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}
            ):
                raise RecordNotFoundError("conversation document is not available")

    @staticmethod
    def _one(row: sqlite3.Row | None) -> sqlite3.Row:
        if row is None:
            raise RepositoryError("durable state failed readback")
        return row

    @staticmethod
    def _workspace(row: sqlite3.Row) -> Workspace:
        return Workspace(
            id=str(row["id"]),
            name=str(row["name"]),
            created_at=Repository._parse_datetime(str(row["created_at"])),
            updated_at=Repository._parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            display_name=str(row["display_name"]),
            status=DocumentStatus(str(row["status"])),
            current_version_id=str(row["current_version_id"]),
            page_count=int(row["page_count"]) if row["page_count"] is not None else None,
            element_count=int(row["element_count"]),
            warning_count=int(row["warning_count"]),
            created_at=Repository._parse_datetime(str(row["created_at"])),
            updated_at=Repository._parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _version(row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            sha256=str(row["sha256"]),
            mime_type=str(row["mime_type"]),
            byte_size=int(row["byte_size"]),
            page_count=int(row["page_count"]) if row["page_count"] is not None else None,
            parser_profile=str(row["parser_profile"]),
            embedding_profile=str(row["embedding_profile"]),
            source_key=str(row["source_key"]),
            warning_count=int(row["warning_count"]),
            created_at=Repository._parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _element(row: sqlite3.Row) -> ContentElement:
        bbox = (
            tuple(float(value) for value in json.loads(str(row["bbox_json"])))
            if row["bbox_json"] is not None
            else None
        )
        return ContentElement(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            document_id=str(row["document_id"]),
            version_id=str(row["version_id"]),
            page_number=int(row["page_number"]),
            modality=Modality(str(row["modality"])),
            content=str(row["content"]),
            bbox=bbox,
            asset_key=str(row["asset_key"]) if row["asset_key"] is not None else None,
            confidence=float(row["confidence"]),
            extraction_method=str(row["extraction_method"]),
            metadata=dict(json.loads(str(row["metadata_json"]))),
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> IngestionJob:
        return IngestionJob(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            document_id=str(row["document_id"]),
            version_id=str(row["version_id"]),
            kind=JobKind(str(row["kind"])),
            status=JobStatus(str(row["status"])),
            stage=JobStage(str(row["stage"])),
            progress=float(row["progress"]),
            attempt_count=int(row["attempt_count"]),
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
            lease_expires_at=(
                Repository._parse_datetime(str(row["lease_expires_at"]))
                if row["lease_expires_at"] is not None
                else None
            ),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_message=(str(row["error_message"]) if row["error_message"] is not None else None),
            created_at=Repository._parse_datetime(str(row["created_at"])),
            updated_at=Repository._parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            title=str(row["title"]),
            document_ids=list(json.loads(str(row["document_ids_json"]))),
            created_at=Repository._parse_datetime(str(row["created_at"])),
            updated_at=Repository._parse_datetime(str(row["updated_at"])),
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            role=str(row["role"]),
            content=str(row["content"]),
            answer_id=str(row["answer_id"]) if row["answer_id"] is not None else None,
            created_at=Repository._parse_datetime(str(row["created_at"])),
        )

    @staticmethod
    def _citation(row: sqlite3.Row) -> Citation:
        bbox = (
            tuple(float(value) for value in json.loads(str(row["bbox_json"])))
            if row["bbox_json"] is not None
            else None
        )
        return Citation(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            version_id=str(row["version_id"]),
            document_name=str(row["document_name"]),
            element_id=str(row["element_id"]),
            page_number=int(row["page_number"]),
            modality=Modality(str(row["modality"])),
            excerpt=str(row["excerpt"]),
            bbox=bbox,
            asset_url=str(row["asset_url"]) if row["asset_url"] is not None else None,
            available=bool(row["available"]),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("repository clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _now_text(self) -> str:
        return self._datetime_text(self._now())

    @staticmethod
    def _datetime_text(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        if _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{field} must be a safe identifier")
        return value

    @staticmethod
    def _identifier_list(values: Sequence[str], field: str) -> list[str]:
        if len(values) > 200:
            raise ValueError(f"{field} list may contain at most 200 identifiers")
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            identifier = Repository._identifier(value, field)
            if identifier not in seen:
                seen.add(identifier)
                result.append(identifier)
        return result

    @staticmethod
    def _digest(value: str) -> str:
        normalized = value.lower()
        if _HASH_PATTERN.fullmatch(normalized) is None:
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        return normalized

    @staticmethod
    def _text(
        value: str,
        field: str,
        *,
        minimum: int = 1,
        maximum: int,
    ) -> str:
        normalized = value.strip()
        if not minimum <= len(normalized) <= maximum or "\x00" in normalized:
            raise ValueError(f"{field} must contain between {minimum} and {maximum} characters")
        return normalized

    @staticmethod
    def _source_key(value: str) -> tuple[str, str, str]:
        if len(value) > 500 or value.startswith("/") or "\\" in value:
            raise ValueError("source_key must be a managed relative POSIX key")
        parsed = PurePosixPath(value)
        if parsed.as_posix() != value or len(parsed.parts) != 3:
            raise ValueError("source_key must contain workspace, version, and content key")
        if any(part in {"", ".", ".."} for part in parsed.parts):
            raise ValueError("source_key contains an unsafe component")
        filename = PurePosixPath(parsed.parts[2])
        if _HASH_PATTERN.fullmatch(filename.stem) is None or not filename.suffix:
            raise ValueError("source_key filename must be content addressed")
        return str(parsed.parts[0]), str(parsed.parts[1]), str(parsed.parts[2])

    @staticmethod
    def _error_code(value: str) -> str:
        normalized = value.strip().lower()
        if _ERROR_CODE_PATTERN.fullmatch(normalized) is None:
            raise ValueError("error_code must be a stable lowercase identifier")
        return normalized

    @staticmethod
    def _safe_error_message(value: str) -> str:
        normalized = " ".join(value.split())
        normalized = re.sub(r"(?:/[A-Za-z0-9._-]+){2,}", "[path]", normalized)
        normalized = re.sub(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{8,}\b", "[redacted]", normalized)
        if not normalized:
            return "The job did not complete."
        return normalized[:500]

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if limit < 1 or limit > _MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {_MAX_PAGE_SIZE}")
        if offset < 0:
            raise ValueError("offset must not be negative")
