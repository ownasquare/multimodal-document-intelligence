"""Credential-free Streamlit AppTest fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from document_intelligence.config import Settings
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
    QueryRequest,
    SystemStatus,
    UploadReceipt,
)

APP_PATH = Path(__file__).resolve().parents[2] / "src" / "document_intelligence" / "ui" / "app.py"
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def make_status(*, documents: int = 0) -> SystemStatus:
    return SystemStatus(
        status="ready",
        provider_mode="deterministic",
        embedding_provider="deterministic",
        document_count=documents,
        ready_document_count=documents,
        queued_job_count=0,
        running_job_count=0,
        ocr_available=False,
        demo_mode=True,
        warnings=[],
    )


def make_document(
    *,
    document_id: str = "doc-1",
    name: str = "northstar-q2-operations-review.pdf",
    status: DocumentStatus = DocumentStatus.READY,
) -> Document:
    return Document(
        id=document_id,
        workspace_id="workspace-default",
        display_name=name,
        status=status,
        current_version_id=f"version-{document_id}",
        page_count=8,
        element_count=34,
        warning_count=1 if status is DocumentStatus.READY_WITH_WARNINGS else 0,
        created_at=NOW,
        updated_at=NOW,
    )


def make_job(*, status: JobStatus = JobStatus.RUNNING) -> IngestionJob:
    return IngestionJob(
        id="job-1",
        workspace_id="workspace-default",
        document_id="doc-1",
        version_id="version-doc-1",
        kind=JobKind.INGEST,
        status=status,
        stage=JobStage.UNDERSTANDING_VISUALS,
        progress=0.65,
        attempt_count=1,
        created_at=NOW,
        updated_at=NOW,
    )


def make_answer() -> Answer:
    citation = Citation(
        id="citation-1",
        document_id="doc-1",
        version_id="version-doc-1",
        document_name="northstar-q2-operations-review.pdf",
        element_id="element-table-1",
        page_number=2,
        modality=Modality.TABLE_ROW,
        excerpt="South | Net revenue $2.0M | Target $2.7M | Variance -$0.7M",
    )
    return Answer(
        id="answer-1",
        conversation_id="conversation-1",
        question="Which region missed target by the most?",
        text="South missed its target by $0.7M.",
        claims=[AnswerClaim(text="South missed its target by $0.7M.", citation_ids=[citation.id])],
        citations=[citation],
        modalities_used=[Modality.TABLE_ROW],
        created_at=NOW,
    )


@dataclass
class FakeClient:
    documents: list[Document] = field(default_factory=list)
    jobs: list[IngestionJob] = field(default_factory=list)
    elements: list[ContentElement] = field(default_factory=list)
    asks: list[QueryRequest] = field(default_factory=list)
    sample_calls: int = 0
    upload_names: list[str] = field(default_factory=list)

    def status(self) -> SystemStatus:
        return make_status(documents=len(self.documents))

    def list_documents(
        self, *, query: str | None = None, status: str | None = None, sort: str = "recent"
    ) -> list[Document]:
        del sort
        result = list(self.documents)
        if query:
            result = [item for item in result if query.casefold() in item.display_name.casefold()]
        if status and status != "All":
            result = [item for item in result if item.status.value == status]
        return result

    def upload_documents(
        self, files: list[tuple[str, bytes, str]], *, idempotency_key: str
    ) -> list[UploadReceipt]:
        del idempotency_key
        self.upload_names.extend(name for name, _, _ in files)
        return [self._receipt(name, index) for index, (name, _, _) in enumerate(files)]

    def _receipt(self, name: str, index: int) -> UploadReceipt:
        document = make_document(
            document_id=f"uploaded-{index}", name=name, status=DocumentStatus.QUEUED
        )
        version = DocumentVersion(
            id=document.current_version_id,
            document_id=document.id,
            sha256="a" * 64,
            byte_size=1024,
            parser_profile="pdfplumber-v1",
            embedding_profile="deterministic-v1",
            source_key=f"workspace/{document.id}/source.pdf",
            created_at=NOW,
        )
        job = IngestionJob(
            id=f"job-uploaded-{index}",
            workspace_id=document.workspace_id,
            document_id=document.id,
            version_id=version.id,
            kind=JobKind.INGEST,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            created_at=NOW,
            updated_at=NOW,
        )
        return UploadReceipt(document=document, version=version, job=job)

    def load_sample(self) -> UploadReceipt:
        self.sample_calls += 1
        return self._receipt("northstar-q2-operations-review.pdf", 1)

    def get_document(self, document_id: str) -> Document:
        return next(item for item in self.documents if item.id == document_id)

    def list_elements(self, document_id: str) -> list[ContentElement]:
        return [item for item in self.elements if item.document_id == document_id]

    def reprocess_document(self, document_id: str) -> IngestionJob:
        del document_id
        return make_job(status=JobStatus.QUEUED)

    def delete_document(self, document_id: str) -> IngestionJob:
        del document_id
        return make_job(status=JobStatus.QUEUED)

    def list_jobs(self) -> list[IngestionJob]:
        return list(self.jobs)

    def retry_job(self, job_id: str) -> IngestionJob:
        del job_id
        return make_job(status=JobStatus.QUEUED)

    def list_conversations(self) -> list[Conversation]:
        return []

    def ask(self, request: QueryRequest) -> Answer:
        self.asks.append(request)
        return make_answer()

    def fetch_asset(self, asset_url: str) -> bytes:
        del asset_url
        return b""


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def app_test(tmp_path: Path, fake_client: FakeClient) -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=8)
    app.session_state["_docintel_client"] = fake_client
    app.session_state["_docintel_settings"] = Settings(
        environment="test",
        data_dir=tmp_path,
        demo_mode=True,
    )
    return app
