from __future__ import annotations

from pathlib import Path

import pytest

from document_intelligence.ingestion.nodes import build_nodes
from document_intelligence.models import ContentElement, Modality
from document_intelligence.providers import DeterministicEmbeddingProvider
from document_intelligence.retrieval import ChromaVectorIndex, HybridRetriever, RetrievalScope

pytestmark = pytest.mark.integration


def _element(
    identifier: str,
    *,
    document_id: str,
    version_id: str,
    page: int,
    modality: Modality,
    content: str,
) -> ContentElement:
    return ContentElement(
        id=identifier,
        workspace_id="workspace_1",
        document_id=document_id,
        version_id=version_id,
        page_number=page,
        modality=modality,
        content=content,
        extraction_method="fixture",
    )


def _index(path: Path) -> tuple[ChromaVectorIndex, DeterministicEmbeddingProvider]:
    provider = DeterministicEmbeddingProvider()
    return (
        ChromaVectorIndex(path, provider, parser_profile="fixture-v1"),
        provider,
    )


def test_chroma_upsert_scope_restart_and_delete_readback(tmp_path: Path) -> None:
    index, provider = _index(tmp_path / "chroma")
    elements = [
        _element(
            "element_table_v1",
            document_id="document_1",
            version_id="version_1",
            page=2,
            modality=Modality.TABLE_ROW,
            content="Q2 revenue was $4.8 million, an increase of 14 percent.",
        ),
        _element(
            "element_chart_v1",
            document_id="document_1",
            version_id="version_1",
            page=3,
            modality=Modality.CHART,
            content="The revenue chart trends upward from Q1 to Q2.",
        ),
        _element(
            "element_old_version",
            document_id="document_1",
            version_id="version_old",
            page=2,
            modality=Modality.TABLE_ROW,
            content="Old version says Q2 revenue was $2 million.",
        ),
        _element(
            "element_other_document",
            document_id="document_2",
            version_id="version_2",
            page=1,
            modality=Modality.TEXT,
            content="A different document discusses revenue without quarterly values.",
        ),
    ]
    nodes = build_nodes(
        elements,
        parser_profile="fixture-v1",
        embedding_profile=provider.profile,
        document_names={"document_1": "Northstar.pdf", "document_2": "Other.pdf"},
    )

    assert index.upsert(nodes) == 4
    assert index.upsert(nodes) == 4
    assert index.count_version("workspace_1", "version_1") == 2
    assert len(set(index.collection_names.values())) == 3

    restarted, _ = _index(tmp_path / "chroma")
    hits = HybridRetriever(restarted).retrieve(
        "How much did Q2 revenue increase?",
        RetrievalScope(
            workspace_id="workspace_1",
            ready_version_ids=("version_1",),
            document_ids=("document_1",),
        ),
        top_k=5,
    )

    assert hits
    assert all(hit.version_id == "version_1" for hit in hits)
    assert all(hit.document_id == "document_1" for hit in hits)
    assert hits[0].element_id == "element_table_v1"

    assert restarted.delete_version("workspace_1", "version_1") == 2
    assert restarted.count_version("workspace_1", "version_1") == 0
    assert (
        HybridRetriever(restarted).retrieve(
            "Q2 revenue",
            RetrievalScope(
                workspace_id="workspace_1",
                ready_version_ids=("version_1",),
                document_ids=("document_1",),
            ),
        )
        == []
    )
