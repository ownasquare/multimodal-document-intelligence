"""Upload, ingestion, managed-asset, reprocess, and retry integration proof."""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from llama_index.core.schema import TextNode
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from document_intelligence.database import Database
from document_intelligence.ingestion.pipeline import IngestionPipeline
from document_intelligence.ingestion.upload import UploadService
from document_intelligence.jobs import JobCoordinator
from document_intelligence.models import DocumentStatus, JobKind, JobStatus, Modality
from document_intelligence.parsers import InvalidPDFError, PDFParser, PDFParserOptions
from document_intelligence.parsers.base import (
    ElementModality,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    sha256_file,
)
from document_intelligence.providers import (
    DeterministicEmbeddingProvider,
    DeterministicVisualProvider,
)
from document_intelligence.repository import InvalidStateTransitionError, Repository
from document_intelligence.retrieval import ChromaVectorIndex, HybridRetriever, RetrievalScope
from document_intelligence.storage import FileStorage

pytestmark = pytest.mark.integration


def _pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    canvas.setTitle("Northstar Quarterly Review")
    canvas.drawString(72, 730, "Northstar quarterly revenue review")
    canvas.drawString(72, 705, "Q2 revenue increased 14 percent to $4.8 million.")
    canvas.drawString(72, 680, "Customer retention improved during the same quarter.")
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


class _IdSequence:
    def __init__(self, *values: str) -> None:
        self.values = iter(values)

    def __call__(self) -> str:
        return next(self.values)


def _services(
    tmp_path: Path,
    *,
    id_factory: Callable[[], str] | None = None,
) -> tuple[
    Repository,
    FileStorage,
    PDFParser,
    DeterministicEmbeddingProvider,
    UploadService,
]:
    repository = Repository(Database(tmp_path / "state.sqlite3"), max_attempts=3)
    repository.initialize()
    repository.create_workspace("Workspace", workspace_id="workspace-1")
    storage = FileStorage(tmp_path / "uploads", tmp_path / "artifacts")
    parser = PDFParser(
        PDFParserOptions(
            enable_ocr=False,
            minimum_native_characters=1,
            max_file_bytes=2 * 1024 * 1024,
        )
    )
    embeddings = DeterministicEmbeddingProvider()
    uploads = UploadService(
        repository,
        storage,
        parser,
        parser_profile="pdf-v1",
        embedding_profile=embeddings.profile,
        max_file_bytes=2 * 1024 * 1024,
        id_factory=id_factory,
    )
    return repository, storage, parser, embeddings, uploads


def test_upload_parse_index_ready_and_reprocess_current_immutable_version(
    tmp_path: Path,
) -> None:
    repository, storage, parser, embeddings, uploads = _services(
        tmp_path, id_factory=_IdSequence("version-1")
    )
    receipt = uploads.accept(
        "workspace-1",
        display_name="../Northstar\x00 Quarterly.pdf",
        data=_pdf_bytes(),
        idempotency_key="upload-request-1",
    )
    index = ChromaVectorIndex(
        tmp_path / "chroma",
        embeddings,
        parser_profile=receipt.version.parser_profile,
    )
    pipeline = IngestionPipeline(repository, storage, parser, index)
    coordinator = JobCoordinator(repository, owner="worker-1")
    lease = coordinator.lease(kinds=(JobKind.INGEST,))

    assert lease is not None
    result = pipeline.process(lease)

    assert result.job.status is JobStatus.SUCCEEDED
    assert result.element_count > 0
    assert result.node_count >= result.element_count
    document = repository.get_document(receipt.document.id)
    assert document is not None
    assert document.status in {DocumentStatus.READY, DocumentStatus.READY_WITH_WARNINGS}
    elements = repository.list_elements(receipt.version.id)
    assert elements
    assert all(element.asset_key for element in elements)
    assert all(storage.artifact_exists(element.asset_key or "") for element in elements)
    assert all(element.metadata["asset_role"] == "page_render" for element in elements)
    assert all(element.metadata["original_asset_filename"].endswith(".png") for element in elements)
    assert index.count_version("workspace-1", receipt.version.id) == result.node_count

    hits = HybridRetriever(index).retrieve(
        "How much did Q2 revenue increase?",
        RetrievalScope(
            workspace_id="workspace-1",
            ready_version_ids=(receipt.version.id,),
            document_ids=(receipt.document.id,),
        ),
    )
    assert hits
    assert any("$4.8 million" in hit.content for hit in hits)

    original_source = receipt.version.source_key
    reprocess_job = uploads.reprocess_document(receipt.document.id)
    repeated_request = uploads.reprocess_document(receipt.document.id)
    assert reprocess_job.kind is JobKind.REPROCESS
    assert repeated_request.id == reprocess_job.id
    reprocess_lease = coordinator.lease(kinds=(JobKind.REPROCESS,))
    assert reprocess_lease is not None
    reprocessed = pipeline.process(reprocess_lease)
    assert reprocessed.job.status is JobStatus.SUCCEEDED
    assert repository.get_document_version(receipt.version.id).source_key == original_source  # type: ignore[union-attr]
    assert len(repository.list_document_versions(receipt.document.id)) == 1


def test_upload_duplicate_and_invalid_pdf_leave_no_orphan_version_files(tmp_path: Path) -> None:
    identifiers = _IdSequence("version-1", "version-duplicate", "version-invalid")
    repository, storage, _, _, uploads = _services(tmp_path, id_factory=identifiers)
    payload = _pdf_bytes()
    first = uploads.accept(
        "workspace-1",
        display_name="Northstar.pdf",
        data=payload,
        idempotency_key="upload-request-1",
    )
    replay = uploads.accept(
        "workspace-1",
        display_name="Northstar.pdf",
        data=payload,
        idempotency_key="upload-request-1",
    )

    assert replay.duplicate
    assert replay.version.id == first.version.id
    assert storage.verify_version_absent("workspace-1", "version-duplicate").verified

    with pytest.raises(InvalidPDFError):
        uploads.accept(
            "workspace-1",
            display_name="not-a-pdf.pdf",
            data=b"not a PDF",
        )
    assert storage.verify_version_absent("workspace-1", "version-invalid").verified
    assert len(repository.list_documents("workspace-1")) == 1


class _FixtureParser:
    def __init__(self) -> None:
        self.last_artifact_dir: Path | None = None

    def parse(self, source_path: Path, *, artifact_dir: Path) -> ParsedDocument:
        self.last_artifact_dir = artifact_dir
        page_path = artifact_dir / "Human Named Page 1.png"
        crop_path = artifact_dir / "Revenue Chart Crop.png"
        Image.new("RGB", (320, 240), "white").save(page_path)
        Image.new("RGB", (160, 100), "white").save(crop_path)
        elements = [
            ParsedElement(
                page_number=1,
                modality=ElementModality.CHART,
                content="Revenue chart for Q2",
                extraction_method="fixture-chart",
                bbox=(0.1, 0.1, 0.8, 0.6),
                confidence=0.9,
                asset_path=crop_path,
                metadata={"visual_kind": "line_chart"},
            ),
            ParsedElement(
                page_number=1,
                modality=ElementModality.TABLE_ROW,
                content="Q2 | $4.8 million | 14 percent increase",
                extraction_method="fixture-table",
                bbox=(0.1, 0.65, 0.8, 0.8),
                confidence=0.98,
                metadata={"units": "USD millions"},
            ),
        ]
        return ParsedDocument(
            source_path=source_path,
            sha256=sha256_file(source_path),
            pages=[
                ParsedPage(
                    page_number=1,
                    width=612,
                    height=792,
                    native_text="Q2 revenue",
                    elements=elements,
                    page_asset_path=page_path,
                )
            ],
        )


class _FailOnceIndex:
    def __init__(self, delegate: ChromaVectorIndex) -> None:
        self.delegate = delegate
        self.fail_next = True

    def upsert(self, nodes: Sequence[TextNode]) -> int:
        written = self.delegate.upsert(nodes)
        if self.fail_next:
            self.fail_next = False
            raise OSError("simulated transient vector failure")
        return written

    def delete_version(self, workspace_id: str, version_id: str) -> int:
        return self.delegate.delete_version(workspace_id, version_id)

    def count_version(self, workspace_id: str, version_id: str) -> int:
        return self.delegate.count_version(workspace_id, version_id)


def test_partial_index_failure_cleans_authoritative_state_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    repository, storage, _, embeddings, uploads = _services(
        tmp_path, id_factory=_IdSequence("version-1")
    )
    receipt = uploads.accept(
        "workspace-1",
        display_name="Northstar.pdf",
        data=_pdf_bytes(),
    )
    parser = _FixtureParser()
    delegate = ChromaVectorIndex(tmp_path / "chroma", embeddings, parser_profile="pdf-v1")
    index = _FailOnceIndex(delegate)
    pipeline = IngestionPipeline(
        repository,
        storage,
        parser,
        index,
        visual_provider=DeterministicVisualProvider(),
    )
    coordinator = JobCoordinator(repository, owner="worker-1")
    first_lease = coordinator.lease(kinds=(JobKind.INGEST,))

    assert first_lease is not None
    first = pipeline.process(first_lease)

    assert first.job.status is JobStatus.QUEUED
    assert first.job.error_code == "storage_unavailable"
    assert repository.list_elements(receipt.version.id) == []
    assert delegate.count_version("workspace-1", receipt.version.id) == 0
    assert storage.upload_exists(receipt.version.source_key)
    assert parser.last_artifact_dir is not None
    assert not parser.last_artifact_dir.exists()

    retry_lease = coordinator.lease(kinds=(JobKind.INGEST,))
    assert retry_lease is not None
    second = pipeline.process(retry_lease)

    assert second.job.status is JobStatus.SUCCEEDED
    elements = repository.list_elements(receipt.version.id)
    assert len(elements) == 3
    chart = next(element for element in elements if element.extraction_method == "fixture-chart")
    table = next(element for element in elements if element.modality is Modality.TABLE_ROW)
    assert chart.asset_key != table.asset_key
    assert chart.metadata["asset_role"] == "element_crop"
    assert table.metadata["asset_role"] == "page_render"
    assert chart.metadata["original_asset_filename"] == "Revenue Chart Crop.png"
    assert table.metadata["original_asset_filename"] == "Human Named Page 1.png"
    assert all(storage.artifact_exists(element.asset_key or "") for element in elements)


def test_reprocess_rejects_documents_that_are_still_ingesting(tmp_path: Path) -> None:
    _, _, _, _, uploads = _services(tmp_path, id_factory=_IdSequence("version-1"))
    receipt = uploads.accept(
        "workspace-1",
        display_name="Northstar.pdf",
        data=_pdf_bytes(),
    )

    with pytest.raises(InvalidStateTransitionError, match="ready or failed"):
        uploads.reprocess_document(receipt.document.id)
