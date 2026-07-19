from __future__ import annotations

from pathlib import Path

import pytest

from document_intelligence.ingestion.nodes import build_nodes
from document_intelligence.models import ContentElement, Modality
from document_intelligence.providers import DeterministicEmbeddingProvider
from document_intelligence.retrieval import ChromaVectorIndex, HybridRetriever, RetrievalScope

pytestmark = pytest.mark.integration


def test_gold_evidence_is_in_top_ten_for_table_chart_and_diagram_questions(tmp_path: Path) -> None:
    elements = [
        ContentElement(
            id="gold_table",
            workspace_id="workspace_1",
            document_id="document_1",
            version_id="version_1",
            page_number=2,
            modality=Modality.TABLE_ROW,
            content="Q2 revenue was $4.8 million and increased by 14 percent.",
            extraction_method="fixture",
        ),
        ContentElement(
            id="gold_chart",
            workspace_id="workspace_1",
            document_id="document_1",
            version_id="version_1",
            page_number=3,
            modality=Modality.CHART,
            content="The revenue chart legend uses USD and the line trends upward in Q2.",
            extraction_method="fixture",
        ),
        ContentElement(
            id="gold_diagram",
            workspace_id="workspace_1",
            document_id="document_1",
            version_id="version_1",
            page_number=4,
            modality=Modality.DIAGRAM,
            content="The onboarding diagram flows from signup to activation to retention.",
            extraction_method="fixture",
        ),
        *[
            ContentElement(
                id=f"distractor_{index}",
                workspace_id="workspace_1",
                document_id="document_1",
                version_id="version_1",
                page_number=5 + index,
                modality=Modality.TEXT,
                content=f"Appendix note {index} about office facilities and schedules.",
                extraction_method="fixture",
            )
            for index in range(12)
        ],
    ]
    embeddings = DeterministicEmbeddingProvider()
    index = ChromaVectorIndex(tmp_path / "chroma", embeddings, parser_profile="fixture-v1")
    index.upsert(
        build_nodes(
            elements,
            parser_profile="fixture-v1",
            embedding_profile=embeddings.profile,
        )
    )
    retriever = HybridRetriever(index)
    scope = RetrievalScope("workspace_1", ("version_1",), ("document_1",))
    questions = {
        "How much did Q2 revenue increase?": "gold_table",
        "What trend and units does the revenue chart legend show?": "gold_chart",
        "How does the onboarding diagram reach retention?": "gold_diagram",
    }

    for question, expected_element in questions.items():
        hits = retriever.retrieve(question, scope, top_k=10)
        assert expected_element in {hit.element_id for hit in hits}
