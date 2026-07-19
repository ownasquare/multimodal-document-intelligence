"""Immutable citations assembled only from server-owned provenance."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from document_intelligence.models import Citation, ContentElement
from document_intelligence.retrieval.models import RetrievedEvidence


class EvidenceResolver(Protocol):
    """The minimal authoritative element lookup offered by persistence."""

    def get_element(self, element_id: str) -> ContentElement | None: ...


class CitationValidationError(ValueError):
    """Retrieved or provider-selected provenance failed server validation."""


class CitationAssembler:
    def __init__(
        self,
        *,
        resolver: EvidenceResolver | None = None,
        asset_url_builder: Callable[[RetrievedEvidence], str | None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.asset_url_builder = asset_url_builder

    def build(
        self,
        selected_ids: Sequence[str],
        evidence_by_id: Mapping[str, RetrievedEvidence],
    ) -> dict[str, Citation]:
        """Map evidence IDs to immutable citation snapshots in stable first-use order."""

        citations: dict[str, Citation] = {}
        for evidence_id in dict.fromkeys(selected_ids):
            hit = evidence_by_id.get(evidence_id)
            if hit is None:
                raise CitationValidationError("selected evidence is not in the server allowlist")
            element = self.resolver.get_element(hit.element_id) if self.resolver else None
            if self.resolver and element is None:
                raise CitationValidationError("authoritative evidence no longer exists")
            if element is not None:
                self._validate_element_scope(element, hit)
                excerpt = _bounded_excerpt(element.content, hit.content)
                bbox = element.bbox
                modality = element.modality
                page_number = element.page_number
            else:
                excerpt = hit.content[:4000]
                bbox = hit.bbox
                modality = hit.modality
                page_number = hit.page_number

            asset_url = self.asset_url_builder(hit) if self.asset_url_builder else None
            citation_id = _citation_id(hit, excerpt)
            citations[evidence_id] = Citation(
                id=citation_id,
                document_id=hit.document_id,
                version_id=hit.version_id,
                document_name=hit.document_name,
                element_id=hit.element_id,
                page_number=page_number,
                modality=modality,
                excerpt=excerpt,
                bbox=bbox,
                asset_url=asset_url,
                available=True,
            )
        return citations

    @staticmethod
    def _validate_element_scope(element: ContentElement, hit: RetrievedEvidence) -> None:
        actual = (
            element.workspace_id,
            element.document_id,
            element.version_id,
            element.id,
            element.page_number,
            element.modality,
        )
        expected = (
            hit.workspace_id,
            hit.document_id,
            hit.version_id,
            hit.element_id,
            hit.page_number,
            hit.modality,
        )
        if actual != expected:
            raise CitationValidationError(
                "authoritative evidence provenance does not match retrieval"
            )


def _bounded_excerpt(content: str, needle: str, limit: int = 4000) -> str:
    if len(content) <= limit:
        return content
    location = content.find(needle)
    if location < 0:
        return content[:limit]
    margin = max(0, (limit - len(needle)) // 2)
    start = max(0, location - margin)
    end = min(len(content), start + limit)
    start = max(0, end - limit)
    return content[start:end]


def _citation_id(hit: RetrievedEvidence, excerpt: str) -> str:
    payload = "\x1f".join((hit.version_id, hit.element_id, hit.record_id, excerpt))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"cit_{digest[:40]}"
