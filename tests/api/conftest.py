"""Dependency-injected fake application services for API contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from document_intelligence.api.app import create_app
from document_intelligence.api.contracts import (
    AssetPayload,
    Page,
    ReadinessReport,
    UploadInput,
)
from document_intelligence.config import Settings
from document_intelligence.models import (
    Answer,
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
    QueryRequest,
    SystemStatus,
    UploadReceipt,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def make_document(identifier: str = "document-1") -> Document:
    return Document(
        id=identifier,
        workspace_id="workspace-1",
        display_name="Quarterly report.pdf",
        status=DocumentStatus.READY,
        current_version_id=f"version-{identifier}",
        page_count=2,
        element_count=1,
        created_at=NOW,
        updated_at=NOW,
    )


def make_job(identifier: str = "job-1", *, kind: JobKind = JobKind.INGEST) -> IngestionJob:
    return IngestionJob(
        id=identifier,
        workspace_id="workspace-1",
        document_id="document-1",
        version_id="version-document-1",
        kind=kind,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )


def make_receipt(identifier: str = "document-1", *, sha256: str = "0" * 64) -> UploadReceipt:
    document = make_document(identifier)
    version = DocumentVersion(
        id=document.current_version_id,
        document_id=document.id,
        sha256=sha256,
        byte_size=12,
        parser_profile="pdf-v1",
        embedding_profile="deterministic-v1",
        source_key=f"workspace-1/{document.current_version_id}/{sha256}.pdf",
        created_at=NOW,
    )
    return UploadReceipt(document=document, version=version, job=make_job(f"job-{identifier}"))


class FakeServices:
    def __init__(self) -> None:
        self.document = make_document()
        self.job = make_job()
        self.upload_calls = 0
        self.uploaded_names: list[str] = []
        self.uploaded_bytes: list[bytes] = []
        self.asset_keys: list[str] = []
        self._upload_cache: dict[str, list[UploadReceipt]] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def readiness(self) -> ReadinessReport:
        return ReadinessReport(status="ready", checks={"database": True})

    def status(self) -> SystemStatus:
        return SystemStatus(
            status="ready",
            provider_mode="deterministic",
            embedding_provider="deterministic",
            document_count=1,
            ready_document_count=1,
            queued_job_count=1,
            running_job_count=0,
            ocr_available=False,
            demo_mode=True,
        )

    def accept_uploads(
        self, uploads: list[UploadInput] | tuple[UploadInput, ...], *, idempotency_key: str
    ) -> list[UploadReceipt]:
        self.upload_calls += 1
        if idempotency_key in self._upload_cache:
            return self._upload_cache[idempotency_key]
        receipts: list[UploadReceipt] = []
        for index, upload in enumerate(uploads, start=1):
            self.uploaded_names.append(upload.display_name)
            self.uploaded_bytes.append(upload.stream.read())
            receipts.append(make_receipt(f"uploaded-{index}", sha256=upload.sha256))
        self._upload_cache[idempotency_key] = receipts
        return receipts

    def list_documents(
        self,
        *,
        query: str | None,
        status: DocumentStatus | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> Page[Document]:
        del query, status, sort, limit, offset
        return Page(items=[self.document], total=1)

    def get_document(self, document_id: str) -> Document | None:
        return self.document if document_id == self.document.id else None

    def list_document_elements(
        self,
        document_id: str,
        *,
        modality: Modality | None,
        page_number: int | None,
        limit: int,
        offset: int,
    ) -> Page[ContentElement]:
        del modality, page_number, limit, offset
        if document_id != self.document.id:
            return Page(items=[], total=0)
        return Page(
            items=[
                ContentElement(
                    id="element-1",
                    workspace_id="workspace-1",
                    document_id=document_id,
                    version_id=self.document.current_version_id,
                    page_number=1,
                    modality=Modality.CHART,
                    content="Revenue increased.",
                    bbox=(0.1, 0.2, 0.8, 0.7),
                    extraction_method="deterministic-v1",
                )
            ],
            total=1,
        )

    def reprocess_document(self, document_id: str) -> IngestionJob:
        del document_id
        return self.job

    def delete_document(self, document_id: str) -> IngestionJob:
        del document_id
        return make_job("delete-job", kind=JobKind.DELETE)

    def list_jobs(self, *, status: JobStatus | None, limit: int, offset: int) -> Page[IngestionJob]:
        del status, limit, offset
        return Page(items=[self.job], total=1)

    def retry_job(self, job_id: str) -> IngestionJob:
        del job_id
        return self.job

    def list_conversations(self, *, limit: int, offset: int) -> Page[Conversation]:
        del limit, offset
        return Page(
            items=[
                Conversation(
                    id="conversation-1",
                    workspace_id="workspace-1",
                    title="Operations review",
                    document_ids=[self.document.id],
                    created_at=NOW,
                    updated_at=NOW,
                )
            ],
            total=1,
        )

    def answer(self, request: QueryRequest) -> Answer:
        return Answer(
            id="answer-1",
            conversation_id=request.conversation_id or "conversation-1",
            question=request.question,
            text="Revenue increased.",
            claims=[],
            citations=[],
            modalities_used=[],
            abstained=True,
            created_at=NOW,
        )

    def open_asset(self, asset_key: str) -> AssetPayload | None:
        self.asset_keys.append(asset_key)
        if asset_key != "workspace-1/version-1/page-1.png":
            return None
        return AssetPayload(
            stream=BytesIO(b"png-bytes"),
            media_type="image/png",
            content_length=9,
            etag='"asset-etag"',
        )

    def load_sample(self, *, idempotency_key: str) -> UploadReceipt:
        del idempotency_key
        return make_receipt("sample-document")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path / "data",
        max_file_bytes=1024,
        max_upload_batch=2,
    )


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


@pytest_asyncio.fixture
async def client(services: FakeServices, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services, settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client,
    ):
        yield test_client
