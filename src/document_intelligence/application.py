"""Product-facing application services shared by FastAPI and local tests."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from document_intelligence.answering.service import AnswerService
from document_intelligence.api.contracts import (
    AssetPayload,
    Page,
    ReadinessReport,
    UploadInput,
)
from document_intelligence.config import Settings
from document_intelligence.ingestion.upload import UploadService
from document_intelligence.models import (
    Answer,
    ContentElement,
    Conversation,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Modality,
    QueryRequest,
    SystemStatus,
    UploadReceipt,
)
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.repository import (
    InvalidStateTransitionError,
    RecordNotFoundError,
    Repository,
)
from document_intelligence.retrieval.models import RetrievalScope
from document_intelligence.storage import FileStorage

_READY_STATUSES = frozenset({DocumentStatus.READY, DocumentStatus.READY_WITH_WARNINGS})
_PAGE_SIZE = 200


class DocumentApplication:
    """One-workspace application facade with no HTTP or process-global state."""

    def __init__(
        self,
        *,
        settings: Settings,
        workspace_id: str,
        repository: Repository,
        storage: FileStorage,
        uploads: UploadService,
        answers: AnswerService,
        ocr_processor: OCRProcessor,
        sample_path: Path,
    ) -> None:
        self.settings = settings
        self.workspace_id = workspace_id
        self.repository = repository
        self.storage = storage
        self.uploads = uploads
        self.answers = answers
        self.ocr_processor = ocr_processor
        self.sample_path = sample_path

    def readiness(self) -> ReadinessReport:
        checks: dict[str, bool] = {}
        warnings: list[str] = []
        try:
            checks["database"] = self.repository.get_workspace(self.workspace_id) is not None
        except Exception:
            checks["database"] = False
        checks["uploads"] = self.settings.uploads_dir.is_dir()
        checks["artifacts"] = self.settings.artifacts_dir.is_dir()
        checks["vector_store"] = self.settings.chroma_dir.is_dir()
        if self.settings.demo_mode:
            checks["sample"] = self.sample_path.is_file()
        if self.settings.enable_ocr and not self.ocr_processor.available:
            warnings.append(
                self.ocr_processor.unavailable_reason
                or "OCR is unavailable; born-digital documents remain usable."
            )
        return ReadinessReport(
            status="ready" if checks and all(checks.values()) else "not_ready",
            checks=checks,
            warnings=warnings,
        )

    def status(self) -> SystemStatus:
        document_counts = self.repository.document_status_counts(self.workspace_id)
        job_counts = self.repository.job_status_counts(self.workspace_id)
        document_count = sum(
            count for state, count in document_counts.items() if state is not DocumentStatus.DELETED
        )
        ready_count = sum(document_counts[state] for state in _READY_STATUSES)
        queued = job_counts[JobStatus.QUEUED]
        running = job_counts[JobStatus.RUNNING]
        warnings = list(self.readiness().warnings)
        if job_counts[JobStatus.FAILED]:
            warnings.append("Some document jobs need attention in Activity.")
        if queued or running:
            status = "working"
        elif document_count == 0:
            status = "needs_setup"
        elif ready_count == 0 and document_counts[DocumentStatus.FAILED]:
            status = "degraded"
        else:
            status = "ready"
        return SystemStatus(
            status=status,
            provider_mode=self.settings.provider_mode,
            embedding_provider=self.settings.embedding_provider,
            document_count=document_count,
            ready_document_count=ready_count,
            queued_job_count=queued,
            running_job_count=running,
            ocr_available=self.ocr_processor.available,
            demo_mode=self.settings.demo_mode,
            warnings=warnings,
        )

    def accept_uploads(
        self, uploads: Sequence[UploadInput], *, idempotency_key: str
    ) -> Sequence[UploadReceipt]:
        if not uploads:
            raise ValueError("at least one PDF is required")
        if len(uploads) > self.settings.max_upload_batch:
            raise ValueError("upload batch exceeds the configured limit")
        receipts: list[UploadReceipt] = []
        base_key = idempotency_key.strip()[:140]
        for index, upload in enumerate(uploads):
            if upload.byte_size < 1 or upload.byte_size > self.settings.max_file_bytes:
                raise ValueError("upload size is outside the configured limit")
            if upload.content_type != "application/pdf":
                raise ValueError("only application/pdf uploads are accepted")
            seek = getattr(upload.stream, "seek", None)
            if callable(seek):
                seek(0)
            receipt = self.uploads.accept(
                self.workspace_id,
                display_name=upload.display_name,
                data=upload.stream,
                mime_type=upload.content_type,
                idempotency_key=f"{base_key}:{index}:{upload.sha256[:16]}",
            )
            if (
                receipt.version.sha256 != upload.sha256
                or receipt.version.byte_size != upload.byte_size
            ):
                raise ValueError("accepted upload metadata did not match the request")
            receipts.append(receipt)
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
        items = self.repository.list_documents(
            self.workspace_id,
            query=query,
            status=status,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        total = self.repository.count_documents(
            self.workspace_id,
            query=query,
            status=status,
        )
        return Page(items=items, total=total)

    def get_document(self, document_id: str) -> Document | None:
        document = self.repository.get_document(document_id)
        if document is None or document.workspace_id != self.workspace_id:
            return None
        return document

    def list_document_elements(
        self,
        document_id: str,
        *,
        modality: Modality | None,
        page_number: int | None,
        limit: int,
        offset: int,
    ) -> Page[ContentElement]:
        document = self.get_document(document_id)
        if document is None:
            raise RecordNotFoundError("document does not exist")
        elements = self.repository.list_elements(
            document.current_version_id,
            modalities=[modality] if modality else None,
            page_number=page_number,
            limit=50_000,
        )
        return Page(items=elements[offset : offset + limit], total=len(elements))

    def reprocess_document(self, document_id: str) -> IngestionJob:
        document = self.get_document(document_id)
        if document is None:
            raise RecordNotFoundError("document does not exist")
        return self.uploads.reprocess_document(document.id)

    def delete_document(self, document_id: str) -> IngestionJob:
        document = self.get_document(document_id)
        if document is None:
            raise RecordNotFoundError("document does not exist")
        return self.repository.mark_document_deleting(document.id)

    def list_jobs(self, *, status: JobStatus | None, limit: int, offset: int) -> Page[IngestionJob]:
        items = self.repository.list_jobs(
            workspace_id=self.workspace_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        counts = self.repository.job_status_counts(self.workspace_id)
        total = counts[status] if status is not None else sum(counts.values())
        return Page(items=items, total=total)

    def retry_job(self, job_id: str) -> IngestionJob:
        job = self.repository.get_job(job_id)
        if job is None or job.workspace_id != self.workspace_id:
            raise RecordNotFoundError("job does not exist")
        return self.repository.retry_job(job.id)

    def list_conversations(self, *, limit: int, offset: int) -> Page[Conversation]:
        return Page(
            items=self.repository.list_conversations(self.workspace_id, limit=limit, offset=offset),
            total=self.repository.count_conversations(self.workspace_id),
        )

    def answer(self, request: QueryRequest) -> Answer:
        if len(request.question) > self.settings.max_question_characters:
            raise ValueError("question exceeds the configured limit")
        documents = self._query_documents(request.document_ids)
        document_ids = [document.id for document in documents]
        if request.conversation_id is None:
            conversation = self.repository.create_conversation(
                self.workspace_id,
                title=_conversation_title(request.question),
                document_ids=document_ids,
            )
        else:
            existing = self.repository.get_conversation(request.conversation_id)
            if existing is None or existing.workspace_id != self.workspace_id:
                raise RecordNotFoundError("conversation does not exist")
            conversation = existing
            if conversation.document_ids != document_ids:
                conversation = self.repository.update_conversation_scope(
                    conversation.id, document_ids
                )
        scope = RetrievalScope(
            workspace_id=self.workspace_id,
            ready_version_ids=tuple(document.current_version_id for document in documents),
            document_ids=tuple(document_ids),
        )
        return self.answers.answer(
            request.question,
            conversation_id=conversation.id,
            scope=scope,
            top_k=min(request.top_k, self.settings.retrieval_top_k),
        )

    def open_asset(self, asset_key: str) -> AssetPayload | None:
        if self.repository.get_active_element_by_asset_key(self.workspace_id, asset_key) is None:
            return None
        try:
            path = self.storage.resolve_artifact_key(asset_key)
        except (OSError, ValueError):
            return None
        media_type: Literal["image/png", "image/jpeg", "image/webp"] | None
        suffix = path.suffix.casefold()
        if suffix == ".png":
            media_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        elif suffix == ".webp":
            media_type = "image/webp"
        else:
            media_type = None
        if media_type is None:
            return None
        content = path.read_bytes()
        return AssetPayload(
            stream=io.BytesIO(content),
            media_type=media_type,
            content_length=len(content),
            etag=hashlib.sha256(content).hexdigest(),
        )

    def load_sample(self, *, idempotency_key: str) -> UploadReceipt:
        if not self.settings.demo_mode:
            raise InvalidStateTransitionError("demo mode is disabled")
        if not self.sample_path.is_file():
            raise RecordNotFoundError("sample document is unavailable")
        with self.sample_path.open("rb") as source:
            return self.uploads.accept(
                self.workspace_id,
                display_name="Northstar Q2 Operations Review.pdf",
                data=source,
                mime_type="application/pdf",
                idempotency_key=idempotency_key,
            )

    def close(self) -> None:
        """Compatibility hook for FastAPI lifespan; resources are short-lived handles."""

    def _query_documents(self, requested_ids: Sequence[str]) -> list[Document]:
        if requested_ids:
            documents: list[Document] = []
            for document_id in dict.fromkeys(requested_ids):
                document = self.get_document(document_id)
                if document is None:
                    raise RecordNotFoundError("document does not exist")
                if document.status not in _READY_STATUSES:
                    raise InvalidStateTransitionError(
                        "questions require documents that have finished preparation"
                    )
                documents.append(document)
            return documents
        return [
            document for document in self._all_documents() if document.status in _READY_STATUSES
        ]

    def _all_documents(self) -> list[Document]:
        documents: list[Document] = []
        offset = 0
        while True:
            page = self.repository.list_documents(
                self.workspace_id,
                sort="oldest",
                limit=_PAGE_SIZE,
                offset=offset,
            )
            documents.extend(page)
            if len(page) < _PAGE_SIZE:
                return documents
            offset += len(page)


def _conversation_title(question: str) -> str:
    normalized = " ".join(question.split())
    if len(normalized) <= 160:
        return normalized
    return f"{normalized[:157].rstrip()}..."
