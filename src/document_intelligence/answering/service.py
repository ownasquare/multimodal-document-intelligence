"""Grounded answering orchestration with fail-closed abstention."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from typing import Protocol

from pydantic import ValidationError

from document_intelligence.answering.citations import (
    CitationAssembler,
    CitationValidationError,
    EvidenceResolver,
)
from document_intelligence.answering.claims import (
    ClaimValidationError,
    selected_evidence_ids,
    validate_claims,
)
from document_intelligence.models import Answer, AnswerClaim
from document_intelligence.providers.base import (
    AnswerProvider,
    ProviderError,
    ProviderEvidence,
)
from document_intelligence.retrieval.models import RetrievalScope, RetrievedEvidence
from document_intelligence.retrieval.retriever import HybridRetriever

_ABSTENTION_TEXT = "I do not have enough supported evidence in the selected documents to answer."


class AnswerRepository(Protocol):
    """Persistence commits the answer, claims, citations, and messages atomically."""

    def persist_answer(self, answer: Answer) -> Answer: ...


class AnswerService:
    def __init__(
        self,
        retriever: HybridRetriever,
        provider: AnswerProvider,
        *,
        repository: AnswerRepository | None = None,
        evidence_resolver: EvidenceResolver | None = None,
        asset_url_builder: Callable[[RetrievedEvidence], str | None] | None = None,
        asset_data_url_builder: Callable[[RetrievedEvidence], str | None] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.retriever = retriever
        self.provider = provider
        self.repository = repository
        self.asset_data_url_builder = asset_data_url_builder
        self.citations = CitationAssembler(
            resolver=evidence_resolver,
            asset_url_builder=asset_url_builder,
        )
        self.id_factory = id_factory or (lambda: f"ans_{uuid.uuid4().hex}")

    def answer(
        self,
        question: str,
        *,
        conversation_id: str,
        scope: RetrievalScope,
        top_k: int = 10,
    ) -> Answer:
        hits = self.retriever.retrieve(question, scope, top_k=top_k)
        if not hits:
            return self._persist(self._abstention(question, conversation_id))

        evidence_by_id = {hit.record_id: hit for hit in hits}
        provider_evidence = [self._provider_evidence(hit) for hit in hits]
        allowlist = frozenset(evidence_by_id)
        try:
            draft = self.provider.answer(
                question,
                provider_evidence,
                allowed_evidence_ids=allowlist,
            )
            if draft.abstained:
                return self._persist(self._abstention(question, conversation_id))
            validated_claims = validate_claims(draft, evidence_by_id)
            selected_ids = selected_evidence_ids(validated_claims)
            answer_id = self.id_factory()
            assembled = self.citations.build(selected_ids, evidence_by_id)
            citation_by_evidence = {
                evidence_id: citation.model_copy(
                    update={"id": _answer_citation_id(answer_id, citation.id)}
                )
                for evidence_id, citation in assembled.items()
            }
            claims = [
                AnswerClaim(
                    text=claim.text,
                    citation_ids=[
                        citation_by_evidence[evidence_id].id for evidence_id in claim.citation_ids
                    ],
                    inference=claim.inference,
                )
                for claim in validated_claims
            ]
            citations = [citation_by_evidence[evidence_id] for evidence_id in selected_ids]
            modalities = list(dict.fromkeys(citation.modality for citation in citations))
            answer = Answer(
                id=answer_id,
                conversation_id=conversation_id,
                question=question,
                text=draft.text,
                claims=claims,
                citations=citations,
                modalities_used=modalities,
                abstained=False,
            )
        except (
            ClaimValidationError,
            CitationValidationError,
            ProviderError,
            ValidationError,
            ValueError,
        ):
            answer = self._abstention(question, conversation_id)
        return self._persist(answer)

    def _provider_evidence(self, hit: RetrievedEvidence) -> ProviderEvidence:
        data_url = self.asset_data_url_builder(hit) if self.asset_data_url_builder else None
        if data_url and not data_url.startswith(
            ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
        ):
            raise ValueError("asset data URL builder returned an unsupported value")
        return ProviderEvidence(
            id=hit.record_id,
            workspace_id=hit.workspace_id,
            document_id=hit.document_id,
            version_id=hit.version_id,
            document_name=hit.document_name,
            element_id=hit.element_id,
            page_number=hit.page_number,
            modality=hit.modality,
            content=hit.content,
            retrieval_score=hit.final_score,
            asset_data_url=data_url,
        )

    def _abstention(self, question: str, conversation_id: str) -> Answer:
        return Answer(
            id=self.id_factory(),
            conversation_id=conversation_id,
            question=question,
            text=_ABSTENTION_TEXT,
            claims=[],
            citations=[],
            modalities_used=[],
            abstained=True,
        )

    def _persist(self, answer: Answer) -> Answer:
        if self.repository is None:
            return answer
        return self.repository.persist_answer(answer)


def _answer_citation_id(answer_id: str, evidence_citation_id: str) -> str:
    """Make citation snapshots unique per answer while retaining stable provenance input."""

    digest = hashlib.sha256(f"{answer_id}\x1f{evidence_citation_id}".encode()).hexdigest()
    return f"cit_{digest[:40]}"
