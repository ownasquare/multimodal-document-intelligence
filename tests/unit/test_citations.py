from __future__ import annotations

from document_intelligence.answering.citations import CitationAssembler
from document_intelligence.answering.service import AnswerService
from document_intelligence.models import Answer, ContentElement, Modality
from document_intelligence.providers import (
    DeterministicAnswerProvider,
    ProviderAnswer,
    ProviderClaim,
)
from document_intelligence.retrieval import RetrievalScope, RetrievedEvidence


def _hit() -> RetrievedEvidence:
    return RetrievedEvidence(
        record_id="node_1",
        workspace_id="workspace_1",
        document_id="document_1",
        version_id="version_1",
        document_name="Northstar.pdf",
        element_id="element_1",
        page_number=2,
        modality=Modality.TABLE_ROW,
        content="Q2 revenue increased 14 percent to $4.8 million.",
        bbox=(0.1, 0.2, 0.8, 0.4),
        final_score=1.0,
    )


def _element() -> ContentElement:
    return ContentElement(
        id="element_1",
        workspace_id="workspace_1",
        document_id="document_1",
        version_id="version_1",
        page_number=2,
        modality=Modality.TABLE_ROW,
        content="Quarter | Revenue | Change\nQ2 | $4.8 million | 14 percent",
        bbox=(0.1, 0.2, 0.8, 0.4),
        asset_key="crop_1",
        extraction_method="fixture",
    )


class _Retriever:
    def __init__(self, hits: list[RetrievedEvidence]) -> None:
        self.hits = hits

    def retrieve(
        self, question: str, scope: RetrievalScope, *, top_k: int
    ) -> list[RetrievedEvidence]:
        return self.hits[:top_k]


class _Resolver:
    def __init__(self, element: ContentElement | None) -> None:
        self.element = element

    def get_element(self, element_id: str) -> ContentElement | None:
        return self.element if self.element and self.element.id == element_id else None


class _Repository:
    def __init__(self) -> None:
        self.answers: list[Answer] = []

    def persist_answer(self, answer: Answer) -> Answer:
        self.answers.append(answer)
        return answer


class _InvalidProvider:
    profile = "invalid-test"

    def answer(self, *args: object, **kwargs: object) -> ProviderAnswer:
        return ProviderAnswer(
            text="The result is $99 million.",
            claims=[
                ProviderClaim(
                    text="The result is $99 million.",
                    citation_ids=["node_1"],
                )
            ],
        )


def _scope() -> RetrievalScope:
    return RetrievalScope(
        workspace_id="workspace_1",
        ready_version_ids=("version_1",),
        document_ids=("document_1",),
    )


def test_citation_uses_authoritative_provenance_and_server_asset_url() -> None:
    citation = CitationAssembler(
        resolver=_Resolver(_element()),
        asset_url_builder=lambda hit: f"/api/evidence/{hit.element_id}/asset",
    ).build(["node_1"], {"node_1": _hit()})["node_1"]

    assert citation.id.startswith("cit_")
    assert citation.page_number == 2
    assert citation.modality is Modality.TABLE_ROW
    assert "Quarter | Revenue" in citation.excerpt
    assert citation.bbox == (0.1, 0.2, 0.8, 0.4)
    assert citation.asset_url == "/api/evidence/element_1/asset"


def test_answer_service_maps_allowlisted_evidence_to_immutable_citation_ids() -> None:
    repository = _Repository()
    service = AnswerService(
        _Retriever([_hit()]),  # type: ignore[arg-type]
        DeterministicAnswerProvider(),
        repository=repository,
        evidence_resolver=_Resolver(_element()),
        id_factory=lambda: "answer_1",
    )

    answer = service.answer(
        "How much did Q2 revenue increase?",
        conversation_id="conversation_1",
        scope=_scope(),
    )

    assert not answer.abstained
    assert answer.citations[0].id.startswith("cit_")
    assert answer.claims[0].citation_ids == [answer.citations[0].id]
    assert answer.modalities_used == [Modality.TABLE_ROW]
    assert repository.answers == [answer]


def test_repeated_evidence_gets_answer_scoped_citation_snapshot_ids() -> None:
    answer_ids = iter(("answer_1", "answer_2"))
    service = AnswerService(
        _Retriever([_hit()]),  # type: ignore[arg-type]
        DeterministicAnswerProvider(),
        evidence_resolver=_Resolver(_element()),
        id_factory=lambda: next(answer_ids),
    )

    first = service.answer(
        "How much did Q2 revenue increase?",
        conversation_id="conversation_1",
        scope=_scope(),
    )
    second = service.answer(
        "How much did Q2 revenue increase?",
        conversation_id="conversation_2",
        scope=_scope(),
    )

    assert first.citations[0].element_id == second.citations[0].element_id
    assert first.citations[0].id != second.citations[0].id
    assert first.claims[0].citation_ids == [first.citations[0].id]
    assert second.claims[0].citation_ids == [second.citations[0].id]


def test_invalid_numeric_claim_fails_closed_to_abstention() -> None:
    service = AnswerService(
        _Retriever([_hit()]),  # type: ignore[arg-type]
        _InvalidProvider(),  # type: ignore[arg-type]
        evidence_resolver=_Resolver(_element()),
        id_factory=lambda: "answer_1",
    )

    answer = service.answer(
        "How much did Q2 revenue increase?",
        conversation_id="conversation_1",
        scope=_scope(),
    )

    assert answer.abstained
    assert answer.claims == []
    assert answer.citations == []


def test_authoritative_scope_mismatch_fails_closed() -> None:
    wrong_element = _element().model_copy(update={"version_id": "version_other"})
    service = AnswerService(
        _Retriever([_hit()]),  # type: ignore[arg-type]
        DeterministicAnswerProvider(),
        evidence_resolver=_Resolver(wrong_element),
        id_factory=lambda: "answer_1",
    )

    answer = service.answer(
        "How much did Q2 revenue increase?",
        conversation_id="conversation_1",
        scope=_scope(),
    )

    assert answer.abstained
