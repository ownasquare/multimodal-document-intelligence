"""Narrow, project-owned contracts for model providers.

Provider implementations intentionally know nothing about persistence, HTTP routes, or
LlamaIndex.  This keeps external SDK behavior behind a small, deterministic test surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from document_intelligence.models import Identifier, Modality, StrictModel


class ProviderError(RuntimeError):
    """A sanitized provider failure safe to surface in application logs."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class VisualDescription(StrictModel):
    """Structured observations produced for an image, chart, or diagram."""

    summary: str = Field(min_length=1, max_length=8000)
    modality: Modality
    observed_text: list[str] = Field(default_factory=list, max_length=100)
    observed_facts: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_visual_modality(self) -> VisualDescription:
        allowed = {Modality.IMAGE, Modality.CHART, Modality.DIAGRAM}
        if self.modality not in allowed:
            raise ValueError("visual descriptions require an image, chart, or diagram modality")
        return self


class ProviderEvidence(StrictModel):
    """Bounded, server-selected evidence passed to an answer provider."""

    id: Identifier
    workspace_id: Identifier
    document_id: Identifier
    version_id: Identifier
    document_name: str = Field(min_length=1, max_length=240)
    element_id: Identifier
    page_number: int = Field(ge=1)
    modality: Modality
    content: str = Field(min_length=1, max_length=12000)
    retrieval_score: float = Field(default=0.0, ge=0.0)
    asset_data_url: str | None = None


class ProviderClaim(StrictModel):
    """One provider-proposed claim and the evidence IDs it relies on."""

    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[Identifier] = Field(min_length=1, max_length=10)
    inference: bool = False


class ProviderAnswer(StrictModel):
    """Structured answer output before server-side citation validation."""

    text: str = Field(min_length=1, max_length=20000)
    claims: list[ProviderClaim] = Field(default_factory=list, max_length=100)
    abstained: bool = False

    @model_validator(mode="after")
    def validate_abstention_shape(self) -> ProviderAnswer:
        if self.abstained and self.claims:
            raise ValueError("an abstention cannot include material claims")
        if not self.abstained and not self.claims:
            raise ValueError("a non-abstaining answer requires at least one claim")
        return self


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces explicit vectors; vector stores must never embed implicitly."""

    @property
    def profile(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VisualUnderstandingProvider(Protocol):
    """Describes one bounded visual asset without tools or wider document access."""

    @property
    def profile(self) -> str: ...

    def describe(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        context: str,
        suggested_modality: Modality,
    ) -> VisualDescription: ...


@runtime_checkable
class AnswerProvider(Protocol):
    """Answers using only the evidence selected and supplied by the server."""

    @property
    def profile(self) -> str: ...

    def answer(
        self,
        question: str,
        evidence: Sequence[ProviderEvidence],
        *,
        allowed_evidence_ids: frozenset[str],
    ) -> ProviderAnswer: ...
