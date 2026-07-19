from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import document_intelligence.container as container_module
from document_intelligence.answering.claims import ClaimValidationError, validate_claims
from document_intelligence.answering.service import AnswerService
from document_intelligence.config import Settings
from document_intelligence.container import create_runtime
from document_intelligence.ingestion.nodes import build_nodes
from document_intelligence.jobs import JobCoordinator
from document_intelligence.models import ContentElement, JobKind, JobStatus, Modality, QueryRequest
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.providers import (
    DeterministicAnswerProvider,
    DeterministicEmbeddingProvider,
    ProviderAnswer,
    ProviderClaim,
)
from document_intelligence.retrieval import (
    ChromaVectorIndex,
    HybridRetriever,
    RetrievalScope,
    RetrievedEvidence,
)

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_QUESTIONS = PROJECT_ROOT / "tests" / "fixtures" / "golden_questions.json"


class _GoldenOCRBackend:
    """Stable OCR boundary for the repository-owned raster fixtures."""

    Output = SimpleNamespace(DICT="dict")

    _OBSERVATIONS: ClassVar[dict[tuple[int, int], str]] = {
        (378, 225): ("Monthly net revenue $M APR $2.5M MAY $2.7M JUN $3.2M"),
        (378, 221): (
            "Q2 gross bookings product mix CORE PLATFORM 45% PRO ANALYTICS 35% "
            "SERVICES 20% Gross bookings represented $10.5M"
        ),
        (459, 594): (
            "DATE JUNE 15 2026 SUBJECT JUNE 14 PACKING SYSTEM INTERRUPTION "
            "At 14:08 PT the packing barcode service stopped accepting new sessions after its "
            "service certificate expired. The outage lasted 47 minutes and delayed 320 customer "
            "orders. ROOT CAUSE Expired barcode-service certificate."
        ),
        (378, 259): (
            "Demand alert Regional reroute Partner capacity available YES Release to partner "
            "NO Manual hold Incident lead approval Exception branch requires both amber steps."
        ),
    }

    def image_to_data(self, image: Any, **_kwargs: object) -> dict[str, list[object]]:
        words = self._OBSERVATIONS.get(image.size, "").split()
        return {"text": words, "conf": ["95"] * len(words)}


def _normalized_fact(value: str) -> str:
    replacements = {
        "million": "m",
        "august": "aug",
        "minutes": "minute",
        "orders": "order",
    }
    normalized = value.casefold().replace("-", " ")
    for source, replacement in replacements.items():
        normalized = normalized.replace(source, replacement)
    cleaned = " ".join(
        "".join(character for character in token if character.isalnum() or character in ".%").strip(
            "."
        )
        for token in normalized.replace("$", "").split()
    )
    return re.sub(r"(?<=\d)\s+m\b", "m", cleaned)


def _assert_fact(answer_text: str, expected_fact: str) -> None:
    normalized_answer = _normalized_fact(answer_text)
    normalized_expected = _normalized_fact(expected_fact)
    expected_tokens = set(normalized_expected.split()) - {"a", "an", "of", "the", "to"}
    answer_tokens = set(normalized_answer.split())
    assert normalized_expected in normalized_answer or expected_tokens <= answer_tokens, (
        f"missing fact {expected_fact!r} in answer {answer_text!r}"
    )


def _element(identifier: str, page: int, modality: Modality, content: str) -> ContentElement:
    return ContentElement(
        id=identifier,
        workspace_id="workspace_1",
        document_id="northstar",
        version_id="northstar_v1",
        page_number=page,
        modality=modality,
        content=content,
        extraction_method="golden-fixture",
    )


def _retrieved(identifier: str, content: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        record_id=identifier,
        workspace_id="workspace_1",
        document_id="northstar",
        version_id="northstar_v1",
        document_name="Northstar.pdf",
        element_id=f"element_{identifier}",
        page_number=2,
        modality=Modality.TABLE_ROW,
        content=content,
        final_score=1.0,
    )


def test_cross_modal_question_and_unsupported_abstention(tmp_path: Path) -> None:
    elements = [
        _element(
            "text_strategy",
            1,
            Modality.TEXT,
            "Northstar's quarterly review covers revenue and customer retention.",
        ),
        _element(
            "table_q2",
            2,
            Modality.TABLE_ROW,
            "Quarter | Revenue | Change\nQ2 | $4.8 million | 14 percent increase",
        ),
        _element(
            "chart_trend",
            3,
            Modality.CHART,
            "Revenue chart: the plotted trend rises from Q1 to Q2; the legend is revenue in USD.",
        ),
        _element(
            "diagram_process",
            4,
            Modality.DIAGRAM,
            "Customer workflow diagram: onboarding leads to activation and retention.",
        ),
    ]
    embeddings = DeterministicEmbeddingProvider()
    index = ChromaVectorIndex(
        tmp_path / "chroma",
        embeddings,
        parser_profile="golden-v1",
    )
    index.upsert(
        build_nodes(
            elements,
            parser_profile="golden-v1",
            embedding_profile=embeddings.profile,
            document_names={"northstar": "Northstar.pdf"},
        )
    )
    retriever = HybridRetriever(index)
    service = AnswerService(
        retriever,
        DeterministicAnswerProvider(),
        id_factory=lambda: "answer_golden",
    )
    scope = RetrievalScope(
        workspace_id="workspace_1",
        ready_version_ids=("northstar_v1",),
        document_ids=("northstar",),
    )

    answer = service.answer(
        "How did Q2 revenue change according to the table and chart trend?",
        conversation_id="conversation_1",
        scope=scope,
    )
    unsupported = service.answer(
        "What is the CEO's favorite food?",
        conversation_id="conversation_1",
        scope=scope,
    )

    assert not answer.abstained
    assert "$4.8 million" in answer.text
    assert any(citation.modality is Modality.TABLE_ROW for citation in answer.citations)
    assert any(citation.modality is Modality.CHART for citation in answer.citations)
    assert unsupported.abstained


def test_derived_arithmetic_requires_source_operands_with_compatible_units_and_basis() -> None:
    west = _retrieved(
        "west",
        "Region: West | Net revenue ($M): 2.3 | On-time (%): 95.4",
    )
    northeast = _retrieved(
        "northeast",
        "Region: Northeast | Net revenue ($M): 2.4 | On-time (%): 95.1",
    )
    valid = ProviderAnswer(
        text="Combined net revenue was $4.7 million (2.3 + 2.4).",
        claims=[
            ProviderClaim(
                text="Combined net revenue was $4.7 million (2.3 + 2.4).",
                citation_ids=["west", "northeast"],
                inference=True,
            )
        ],
    )
    assert validate_claims(valid, {"west": west, "northeast": northeast}) == valid.claims

    incompatible = ProviderAnswer(
        text="Net revenue was $97.7 million ($2.3 million + 95.4%).",
        claims=[
            ProviderClaim(
                text="Net revenue was $97.7 million ($2.3 million + 95.4%).",
                citation_ids=["west"],
                inference=True,
            )
        ],
    )
    with pytest.raises(ClaimValidationError, match="numeric value"):
        validate_claims(incompatible, {"west": west})

    gross = _retrieved("gross", "Gross bookings ($M): 2.4")
    mixed_basis = ProviderAnswer(
        text="The combined amount was $4.7 million ($2.3 million + $2.4 million).",
        claims=[
            ProviderClaim(
                text="The combined amount was $4.7 million ($2.3 million + $2.4 million).",
                citation_ids=["west", "gross"],
                inference=True,
            )
        ],
    )
    with pytest.raises(ClaimValidationError, match="numeric value"):
        validate_claims(mixed_basis, {"west": west, "gross": gross})


def test_checked_in_golden_prompts_execute_through_real_document_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise upload, parse, OCR/visual enrichment, index, retrieval, and answers."""

    ocr = OCRProcessor(enabled=True, backend=_GoldenOCRBackend())
    monkeypatch.setattr(container_module, "OCRProcessor", lambda **_kwargs: ocr)
    runtime = create_runtime(
        Settings(
            environment="test",
            data_dir=tmp_path / "data",
            enable_ocr=True,
            page_render_scale=0.75,
        )
    )
    receipt = runtime.application.load_sample(idempotency_key="golden-lifecycle-v1")
    coordinator = JobCoordinator(runtime.repository, owner="golden-eval", lease_seconds=30)
    lease = coordinator.lease(kinds=(JobKind.INGEST,))
    assert lease is not None
    prepared = runtime.ingestion.process(lease)
    assert prepared.job.status is JobStatus.SUCCEEDED

    questions = json.loads(GOLDEN_QUESTIONS.read_text(encoding="utf-8"))
    assert len(questions) == 10
    for golden in questions:
        answer = runtime.application.answer(
            QueryRequest(
                question=golden["question"],
                document_ids=[receipt.document.id],
            )
        )
        if not golden["answerable"]:
            assert answer.abstained, f"{golden['id']} should abstain: {answer.text}"
            continue

        assert not answer.abstained, f"{golden['id']} unexpectedly abstained"
        for expected_fact in golden["expected_facts"]:
            _assert_fact(answer.text, expected_fact)
        expected_pages = set(golden["evidence_pages"])
        cited_pages = {citation.page_number for citation in answer.citations}
        # Some facts are intentionally repeated in an authoritative table or appendix. Require
        # the expected source set to contribute, while allowing an equivalent repeated source.
        assert expected_pages & cited_pages
