"""SQLite repository durability, deduplication, evidence, and lease tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from document_intelligence.database import Database
from document_intelligence.models import (
    Answer,
    AnswerClaim,
    Citation,
    ContentElement,
    DocumentStatus,
    JobStage,
    JobStatus,
    Modality,
)
from document_intelligence.repository import (
    LeaseLostError,
    PersistenceConflictError,
    Repository,
)
from document_intelligence.storage import FileStorage


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 7, 18, 12, 0, tzinfo=UTC))


@pytest.fixture
def repository(tmp_path: Path, clock: MutableClock) -> Repository:
    result = Repository(
        Database(tmp_path / "state.sqlite3"),
        lease_seconds=30,
        max_attempts=2,
        clock=clock,
    )
    result.initialize()
    return result


def stored_upload(
    repository: Repository,
    tmp_path: Path,
    *,
    payload: bytes = b"%PDF-1.7\nversion one",
    version_id: str = "version-1",
    document_id: str | None = None,
    idempotency_key: str | None = None,
):
    storage = FileStorage(tmp_path / "uploads", tmp_path / "artifacts")
    stored = storage.store_upload("workspace-1", version_id, payload)
    receipt = repository.accept_upload(
        "workspace-1",
        display_name="Quarterly report.pdf",
        sha256=stored.sha256,
        byte_size=stored.byte_size,
        mime_type="application/pdf",
        source_key=stored.key,
        parser_profile="pdf-v1",
        embedding_profile="deterministic-v1",
        document_id=document_id,
        version_id=version_id,
        idempotency_key=idempotency_key,
    )
    return storage, receipt


def test_database_uses_wal_foreign_keys_and_rolls_back(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()

    with database.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(RuntimeError), database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO workspaces (id, name, created_at, updated_at)
            VALUES ('rolled-back', 'Rolled back', 'now', 'now')
            """
        )
        raise RuntimeError("force rollback")
    with database.connection() as connection:
        assert (
            connection.execute("SELECT id FROM workspaces WHERE id = 'rolled-back'").fetchone()
            is None
        )


def test_upload_acceptance_deduplication_versioning_and_recreation(
    repository: Repository, tmp_path: Path, clock: MutableClock
) -> None:
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    _, first = stored_upload(repository, tmp_path, idempotency_key="upload-request-1")

    replay = repository.accept_upload(
        "workspace-1",
        display_name="Quarterly report.pdf",
        sha256=first.version.sha256,
        byte_size=first.version.byte_size,
        mime_type="application/pdf",
        source_key=first.version.source_key,
        parser_profile="pdf-v1",
        embedding_profile="deterministic-v1",
        version_id=first.version.id,
        idempotency_key="upload-request-1",
    )
    assert replay.duplicate
    assert replay.document.id == first.document.id

    with pytest.raises(PersistenceConflictError, match="idempotency"):
        repository.accept_upload(
            "workspace-1",
            display_name="Different.pdf",
            sha256=first.version.sha256,
            byte_size=first.version.byte_size,
            mime_type="application/pdf",
            source_key=first.version.source_key,
            parser_profile="pdf-v1",
            embedding_profile="deterministic-v1",
            version_id=first.version.id,
            idempotency_key="upload-request-1",
        )

    clock.advance(seconds=1)
    _, changed = stored_upload(
        repository,
        tmp_path,
        payload=b"%PDF-1.7\nversion two",
        version_id="version-2",
        document_id=first.document.id,
    )
    assert not changed.duplicate
    assert changed.document.current_version_id == "version-2"
    assert [version.id for version in repository.list_document_versions(first.document.id)] == [
        "version-2",
        "version-1",
    ]

    reopened = Repository(repository.database, clock=clock)
    assert reopened.get_document(first.document.id) == changed.document
    assert reopened.get_document_version("version-1") == first.version


def test_element_inventory_and_atomic_lease_recovery(
    repository: Repository, tmp_path: Path, clock: MutableClock
) -> None:
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    _, receipt = stored_upload(repository, tmp_path)
    element = ContentElement(
        id="element-1",
        workspace_id="workspace-1",
        document_id=receipt.document.id,
        version_id=receipt.version.id,
        page_number=1,
        modality=Modality.TABLE_ROW,
        content="North | $4.2M",
        bbox=(0.1, 0.2, 0.8, 0.4),
        confidence=0.98,
        extraction_method="pdfplumber-table-v1",
        metadata={"unit": "USD millions"},
    )
    repository.update_version_analysis(receipt.version.id, page_count=2)
    assert repository.replace_elements(receipt.version.id, [element]) == 1

    first_lease = repository.lease_next_job("worker-a", lease_seconds=30)
    assert first_lease is not None
    assert first_lease.attempt_count == 1
    assert repository.lease_next_job("worker-b") is None
    repository.advance_job(first_lease.id, "worker-a", stage=JobStage.READING, progress=0.1)

    clock.advance(seconds=31)
    assert repository.recover_expired_jobs() == 1
    recovered = repository.lease_next_job("worker-b", lease_seconds=30)
    assert recovered is not None
    assert recovered.id == first_lease.id
    assert recovered.attempt_count == 2
    with pytest.raises(LeaseLostError):
        repository.heartbeat_job(first_lease.id, "worker-a")

    repository.advance_job(recovered.id, "worker-b", stage=JobStage.INDEXING, progress=0.8)
    completed = repository.complete_job(recovered.id, "worker-b")
    assert completed.status is JobStatus.SUCCEEDED
    document = repository.get_document(receipt.document.id)
    assert document is not None
    assert document.status is DocumentStatus.READY
    assert document.element_count == 1
    assert repository.get_element("element-1") == element


def test_answer_claims_and_citations_survive_repository_recreation(
    repository: Repository, tmp_path: Path
) -> None:
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    _, receipt = stored_upload(repository, tmp_path)
    element = ContentElement(
        id="element-1",
        workspace_id="workspace-1",
        document_id=receipt.document.id,
        version_id=receipt.version.id,
        page_number=1,
        modality=Modality.CHART,
        content="Revenue increased from $4.2M to $5.1M.",
        bbox=(0.1, 0.2, 0.8, 0.7),
        asset_key="workspace-1/version-1/page-1/digest.png",
        extraction_method="vision-v1",
    )
    repository.update_version_analysis(receipt.version.id, page_count=1)
    repository.replace_elements(receipt.version.id, [element])
    lease = repository.lease_next_job("worker-a")
    assert lease is not None
    repository.advance_job(lease.id, "worker-a", stage=JobStage.VERIFYING, progress=0.9)
    repository.complete_job(lease.id, "worker-a")
    conversation = repository.create_conversation("workspace-1", document_ids=[receipt.document.id])
    answer = Answer(
        id="answer-1",
        conversation_id=conversation.id,
        question="What happened to revenue?",
        text="Revenue increased to $5.1M.",
        claims=[AnswerClaim(text="Revenue increased.", citation_ids=["citation-1"])],
        citations=[
            Citation(
                id="citation-1",
                document_id=receipt.document.id,
                version_id=receipt.version.id,
                document_name="ignored caller label",
                element_id=element.id,
                page_number=1,
                modality=Modality.CHART,
                excerpt="Revenue increased from $4.2M to $5.1M.",
                bbox=element.bbox,
                asset_url="/assets/page-1",
            )
        ],
        modalities_used=[Modality.CHART],
    )

    persisted = repository.persist_answer(answer, document_versions=[receipt.version])
    reopened = Repository(repository.database)

    assert reopened.get_answer(answer.id) == persisted
    assert persisted.citations[0].document_name == "Quarterly report.pdf"
    assert [message.role for message in reopened.list_messages(conversation.id)] == [
        "user",
        "assistant",
    ]


def test_document_list_search_sort_and_status_counts(
    repository: Repository, tmp_path: Path
) -> None:
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    _, receipt = stored_upload(repository, tmp_path)

    assert repository.list_documents("workspace-1", query="Quarterly") == [receipt.document]
    assert repository.list_documents("workspace-1", query="Missing") == []
    assert repository.list_documents("workspace-1", sort="name") == [receipt.document]
    assert repository.document_status_counts("workspace-1")[DocumentStatus.QUEUED] == 1
    assert repository.job_status_counts("workspace-1")[JobStatus.QUEUED] == 1
    assert hashlib.sha256(b"%PDF-1.7\nversion one").hexdigest() == receipt.version.sha256
