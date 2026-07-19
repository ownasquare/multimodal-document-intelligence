"""Worker lease and verified deletion orchestration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from document_intelligence.database import Database
from document_intelligence.jobs import (
    DeletionVerificationError,
    JobCoordinator,
    VerifiedDeletionCoordinator,
)
from document_intelligence.models import JobKind, JobStatus
from document_intelligence.repository import Repository
from document_intelligence.storage import FileStorage


class FakeVectorIndex:
    def __init__(self, *, refuse_deletion: bool = False) -> None:
        self.versions: set[str] = set()
        self.refuse_deletion = refuse_deletion

    def delete_version(self, *, workspace_id: str, document_id: str, version_id: str) -> None:
        if not self.refuse_deletion:
            self.versions.discard(version_id)

    def version_exists(self, *, workspace_id: str, document_id: str, version_id: str) -> bool:
        return version_id in self.versions


def setup_document(tmp_path: Path):
    repository = Repository(Database(tmp_path / "state.sqlite3"))
    repository.initialize()
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    storage = FileStorage(tmp_path / "uploads", tmp_path / "artifacts")
    upload = storage.store_upload("workspace-1", "version-1", b"%PDF-1.7")
    storage.store_artifact("workspace-1", "version-1", "page-1", b"image")
    receipt = repository.accept_upload(
        "workspace-1",
        display_name="Report.pdf",
        sha256=upload.sha256,
        byte_size=upload.byte_size,
        mime_type="application/pdf",
        source_key=upload.key,
        parser_profile="pdf-v1",
        embedding_profile="deterministic-v1",
        version_id="version-1",
    )
    return repository, storage, receipt


def test_verified_deletion_removes_every_layer_before_success(tmp_path: Path) -> None:
    repository, storage, receipt = setup_document(tmp_path)
    ingest = repository.lease_next_job("ingest-worker")
    assert ingest is not None
    repository.complete_job(ingest.id, "ingest-worker")
    repository.mark_document_deleting(receipt.document.id)
    vectors = FakeVectorIndex()
    vectors.versions.add(receipt.version.id)
    lease = JobCoordinator(repository, owner="delete-worker").lease(kinds=(JobKind.DELETE,))
    assert lease is not None

    completed, readback = VerifiedDeletionCoordinator(repository, storage, vectors).execute(lease)

    assert completed.status is JobStatus.SUCCEEDED
    assert readback.verified
    assert repository.verify_document_deleted(receipt.document.id)
    assert storage.verify_version_absent("workspace-1", "version-1").verified


def test_deletion_fails_closed_when_vector_readback_still_finds_records(
    tmp_path: Path,
) -> None:
    repository, storage, receipt = setup_document(tmp_path)
    repository.mark_document_deleting(receipt.document.id)
    vectors = FakeVectorIndex(refuse_deletion=True)
    vectors.versions.add(receipt.version.id)
    lease = JobCoordinator(repository, owner="delete-worker").lease(kinds=(JobKind.DELETE,))
    assert lease is not None

    with pytest.raises(DeletionVerificationError, match="Vector records remain"):
        VerifiedDeletionCoordinator(repository, storage, vectors).execute(lease)

    document = repository.get_document(receipt.document.id, include_deleted=True)
    assert document is not None
    assert document.status.value == "deleting"
    assert storage.upload_exists(receipt.version.source_key)
