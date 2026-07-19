"""Domain and API contracts shared across the system."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
BoundingBox = tuple[float, float, float, float]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class JobKind(StrEnum):
    INGEST = "ingest"
    REPROCESS = "reprocess"
    DELETE = "delete"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(StrEnum):
    QUEUED = "queued"
    READING = "reading"
    EXTRACTING_TEXT = "extracting_text"
    EXTRACTING_TABLES = "extracting_tables"
    OCR = "ocr"
    UNDERSTANDING_VISUALS = "understanding_visuals"
    INDEXING = "indexing"
    VERIFYING = "verifying"
    DELETING = "deleting"
    COMPLETE = "complete"


class Modality(StrEnum):
    TEXT = "text"
    TABLE = "table"
    TABLE_ROW = "table_row"
    IMAGE = "image"
    CHART = "chart"
    DIAGRAM = "diagram"
    OCR = "ocr"
    PAGE_SUMMARY = "page_summary"


class Workspace(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DocumentVersion(StrictModel):
    id: Identifier
    document_id: Identifier
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = "application/pdf"
    byte_size: int = Field(ge=1)
    page_count: int | None = Field(default=None, ge=1)
    parser_profile: str
    embedding_profile: str
    source_key: str
    warning_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class Document(StrictModel):
    id: Identifier
    workspace_id: Identifier
    display_name: str = Field(min_length=1, max_length=240)
    status: DocumentStatus
    current_version_id: Identifier
    page_count: int | None = Field(default=None, ge=1)
    element_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ContentElement(StrictModel):
    id: Identifier
    workspace_id: Identifier
    document_id: Identifier
    version_id: Identifier
    page_number: int = Field(ge=1)
    modality: Modality
    content: str = Field(min_length=1, max_length=50000)
    bbox: BoundingBox | None = None
    asset_key: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    extraction_method: str = Field(min_length=1, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: BoundingBox | None) -> BoundingBox | None:
        if value is None:
            return None
        left, top, right, bottom = value
        if not all(0.0 <= coordinate <= 1.0 for coordinate in value):
            raise ValueError("bbox coordinates must be normalized between 0 and 1")
        if left >= right or top >= bottom:
            raise ValueError("bbox must have positive width and height")
        return value


class IngestionJob(StrictModel):
    id: Identifier
    workspace_id: Identifier
    document_id: Identifier
    version_id: Identifier
    kind: JobKind
    status: JobStatus
    stage: JobStage
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    attempt_count: int = Field(default=0, ge=0)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Citation(StrictModel):
    id: Identifier
    document_id: Identifier
    version_id: Identifier
    document_name: str
    element_id: Identifier
    page_number: int = Field(ge=1)
    modality: Modality
    excerpt: str = Field(min_length=1, max_length=4000)
    bbox: BoundingBox | None = None
    asset_url: str | None = None
    available: bool = True


class AnswerClaim(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[Identifier] = Field(min_length=1, max_length=10)
    inference: bool = False


class Answer(StrictModel):
    id: Identifier
    conversation_id: Identifier
    question: str = Field(min_length=1, max_length=4000)
    text: str = Field(min_length=1, max_length=20000)
    claims: list[AnswerClaim]
    citations: list[Citation]
    modalities_used: list[Modality]
    abstained: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class Conversation(StrictModel):
    id: Identifier
    workspace_id: Identifier
    title: str = Field(min_length=1, max_length=160)
    document_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QueryRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: Identifier | None = None
    document_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    top_k: int = Field(default=10, ge=2, le=50)


class UploadReceipt(StrictModel):
    document: Document
    version: DocumentVersion
    job: IngestionJob
    duplicate: bool = False


class SystemStatus(StrictModel):
    status: Literal["ready", "working", "needs_setup", "degraded"]
    provider_mode: str
    embedding_provider: str
    document_count: int = Field(ge=0)
    ready_document_count: int = Field(ge=0)
    queued_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    ocr_available: bool
    demo_mode: bool
    warnings: list[str] = Field(default_factory=list)
