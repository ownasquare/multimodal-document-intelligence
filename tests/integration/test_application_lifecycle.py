from __future__ import annotations

from pathlib import Path

from document_intelligence.config import Settings
from document_intelligence.container import create_runtime
from document_intelligence.jobs import JobCoordinator
from document_intelligence.models import DocumentStatus, JobKind, JobStatus, QueryRequest


def _settings(data_dir: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=data_dir,
        enable_ocr=False,
        page_render_scale=0.75,
        provider_mode="deterministic",
        embedding_provider="deterministic",
    )


def test_complete_local_lifecycle_survives_restart_and_verifies_deletion(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "data")
    runtime = create_runtime(settings)
    receipt = runtime.application.load_sample(idempotency_key="sample-lifecycle-v1")
    duplicate = runtime.application.load_sample(idempotency_key="sample-lifecycle-v1")

    assert receipt.job.status is JobStatus.QUEUED
    assert duplicate.duplicate is True
    assert duplicate.document.id == receipt.document.id

    coordinator = JobCoordinator(runtime.repository, owner="test-worker", lease_seconds=30)
    lease = coordinator.lease(kinds=(JobKind.INGEST,))
    assert lease is not None
    result = runtime.ingestion.process(lease)

    assert result.job.status is JobStatus.SUCCEEDED
    prepared = runtime.application.get_document(receipt.document.id)
    assert prepared is not None
    assert prepared.status is DocumentStatus.READY_WITH_WARNINGS
    assert prepared.warning_count == 1
    assert prepared.page_count == 8
    assert prepared.element_count > 20
    assert (
        runtime.vector_index.count_version(prepared.workspace_id, prepared.current_version_id)
        == result.node_count
    )

    answer = runtime.application.answer(
        QueryRequest(
            question="What was Southeast net revenue?",
            document_ids=[prepared.id],
        )
    )
    assert answer.abstained is False
    assert answer.citations
    assert "1.8" in answer.text
    asset_url = next(citation.asset_url for citation in answer.citations if citation.asset_url)
    asset = runtime.application.open_asset(asset_url.removeprefix("/api/v1/assets/"))
    assert asset is not None
    assert asset.content_length and asset.content_length > 0

    restarted = create_runtime(settings)
    assert restarted.application.get_document(prepared.id) is not None
    assert restarted.application.list_conversations(limit=20, offset=0).total == 1

    reprocess_job = restarted.application.reprocess_document(prepared.id)
    reprocess_coordinator = JobCoordinator(
        restarted.repository, owner="restart-worker", lease_seconds=30
    )
    reprocess_lease = reprocess_coordinator.lease(kinds=(JobKind.REPROCESS,))
    assert reprocess_lease is not None
    assert reprocess_lease.job.id == reprocess_job.id
    assert restarted.ingestion.process(reprocess_lease).job.status is JobStatus.SUCCEEDED

    delete_job = restarted.application.delete_document(prepared.id)
    delete_lease = reprocess_coordinator.lease(kinds=(JobKind.DELETE,))
    assert delete_lease is not None
    assert delete_lease.job.id == delete_job.id
    finished, readback = restarted.deletion.execute(delete_lease)

    assert finished.status is JobStatus.SUCCEEDED
    assert readback.verified is True
    assert restarted.application.get_document(prepared.id) is None
    assert restarted.application.open_asset(asset_url.removeprefix("/api/v1/assets/")) is None
