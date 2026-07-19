"""Shared retrieval value objects with no dependency on a concrete vector store."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from document_intelligence.models import BoundingBox, Modality


class ModalityGroup(StrEnum):
    TEXT = "text"
    TABLE = "table"
    VISUAL = "visual"


def modality_group(modality: Modality) -> ModalityGroup:
    if modality in {Modality.TABLE, Modality.TABLE_ROW}:
        return ModalityGroup.TABLE
    if modality in {Modality.IMAGE, Modality.CHART, Modality.DIAGRAM}:
        return ModalityGroup.VISUAL
    return ModalityGroup.TEXT


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """Server-resolved query scope; only ready immutable versions may be supplied."""

    workspace_id: str
    ready_version_ids: tuple[str, ...]
    document_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        if any(not version_id for version_id in self.ready_version_ids):
            raise ValueError("ready version IDs cannot be empty")
        if any(not document_id for document_id in self.document_ids):
            raise ValueError("document IDs cannot be empty")


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """Bounded evidence returned from a project-owned index adapter."""

    record_id: str
    workspace_id: str
    document_id: str
    version_id: str
    document_name: str
    element_id: str
    page_number: int
    modality: Modality
    content: str
    bbox: BoundingBox | None = None
    asset_key: str | None = None
    units: str | None = None
    vector_score: float = 0.0
    lexical_score: float = 0.0
    final_score: float = 0.0
    metadata: dict[str, str | int | float | bool] = field(default_factory=dict)

    @property
    def group(self) -> ModalityGroup:
        return modality_group(self.modality)
