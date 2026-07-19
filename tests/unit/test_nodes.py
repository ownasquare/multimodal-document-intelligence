from __future__ import annotations

from document_intelligence.ingestion.nodes import LlamaIndexNodeBuilder
from document_intelligence.models import ContentElement, Modality


def _element(content: str, modality: Modality = Modality.TEXT) -> ContentElement:
    return ContentElement(
        id="element_1",
        workspace_id="workspace_1",
        document_id="document_1",
        version_id="version_1",
        page_number=3,
        modality=modality,
        content=content,
        bbox=(0.1, 0.2, 0.7, 0.8),
        asset_key="asset_1" if modality is Modality.CHART else None,
        extraction_method="pdfplumber",
        metadata={"units": "USD millions", "table_headers": ["Quarter", "Revenue"]},
    )


def test_node_builder_is_stable_and_preserves_provenance_without_embeddings() -> None:
    builder = LlamaIndexNodeBuilder(chunk_size=64, chunk_overlap=8)
    element = _element("Revenue increased in Q2. " * 80)

    first = builder.build(
        [element],
        parser_profile="pdf-v1",
        embedding_profile="deterministic-hash-v1-d384",
        document_names={"document_1": "Northstar.pdf"},
    )
    second = builder.build(
        [element],
        parser_profile="pdf-v1",
        embedding_profile="deterministic-hash-v1-d384",
        document_names={"document_1": "Northstar.pdf"},
    )

    assert len(first) > 1
    assert [node.node_id for node in first] == [node.node_id for node in second]
    assert all(node.embedding is None for node in first)
    assert first[0].metadata["workspace_id"] == "workspace_1"
    assert first[0].metadata["version_id"] == "version_1"
    assert first[0].metadata["page_number"] == 3
    assert first[0].metadata["bbox"] == "[0.1,0.2,0.7,0.8]"
    assert set(first[0].excluded_llm_metadata_keys) == set(first[0].metadata)


def test_visual_and_table_row_elements_remain_atomic() -> None:
    builder = LlamaIndexNodeBuilder(chunk_size=64, chunk_overlap=8)
    long_content = "Quarter | Revenue | Change\n" + ("Q2 | $4.8M | 14%\n" * 100)

    table_nodes = builder.build(
        [_element(long_content, Modality.TABLE_ROW)],
        parser_profile="pdf-v1",
        embedding_profile="deterministic-hash-v1-d384",
    )
    chart_nodes = builder.build(
        [_element(long_content, Modality.CHART)],
        parser_profile="pdf-v1",
        embedding_profile="deterministic-hash-v1-d384",
    )

    assert len(table_nodes) == 1
    assert len(chart_nodes) == 1
    assert chart_nodes[0].metadata["asset_key"] == "asset_1"
