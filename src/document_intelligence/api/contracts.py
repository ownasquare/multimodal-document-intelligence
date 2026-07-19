"""Narrow application-service contract consumed by the FastAPI boundary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

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


@dataclass(frozen=True, slots=True)
class Page[T]:
    """Service-layer page independent of HTTP serialization."""

    items: Sequence[T]
    total: int


class PageEnvelope[T](BaseModel):
    """Stable list response parsed by the Streamlit client."""

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ReadinessReport(BaseModel):
    """Local dependency checks; constructing this must not make paid provider calls."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    checks: dict[str, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UploadInput:
    """One bounded, validated PDF staged for synchronous application acceptance."""

    display_name: str
    content_type: str
    byte_size: int
    sha256: str
    stream: IO[bytes]


@dataclass(frozen=True, slots=True)
class AssetPayload:
    """An already-authorized derived image stream; original PDFs are never returned."""

    stream: IO[bytes]
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    content_length: int | None = None
    etag: str | None = None


@runtime_checkable
class ApplicationServices(Protocol):
    """Container-facing interface implemented outside the HTTP package."""

    def readiness(self) -> ReadinessReport: ...

    def status(self) -> SystemStatus: ...

    def accept_uploads(
        self, uploads: Sequence[UploadInput], *, idempotency_key: str
    ) -> Sequence[UploadReceipt]: ...

    def list_documents(
        self,
        *,
        query: str | None,
        status: DocumentStatus | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> Page[Document]: ...

    def get_document(self, document_id: str) -> Document | None: ...

    def list_document_elements(
        self,
        document_id: str,
        *,
        modality: Modality | None,
        page_number: int | None,
        limit: int,
        offset: int,
    ) -> Page[ContentElement]: ...

    def reprocess_document(self, document_id: str) -> IngestionJob: ...

    def delete_document(self, document_id: str) -> IngestionJob: ...

    def list_jobs(
        self, *, status: JobStatus | None, limit: int, offset: int
    ) -> Page[IngestionJob]: ...

    def retry_job(self, job_id: str) -> IngestionJob: ...

    def list_conversations(self, *, limit: int, offset: int) -> Page[Conversation]: ...

    def answer(self, request: QueryRequest) -> Answer: ...

    def open_asset(self, asset_key: str) -> AssetPayload | None: ...

    def load_sample(self, *, idempotency_key: str) -> UploadReceipt: ...
