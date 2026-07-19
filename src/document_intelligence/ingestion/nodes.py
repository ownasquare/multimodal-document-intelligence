"""Stable LlamaIndex node construction behind a project-owned adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode

from document_intelligence.models import ContentElement, Modality

NODE_SERIALIZER_VERSION = "llama-text-node-v1"
_SPLIT_MODALITIES = frozenset({Modality.TEXT, Modality.OCR, Modality.TABLE, Modality.PAGE_SUMMARY})


class LlamaIndexNodeBuilder:
    """Converts authoritative elements into deterministic, metadata-safe TextNodes."""

    def __init__(self, *, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        if chunk_size < 64:
            raise ValueError("chunk_size must be at least 64 tokens")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self._splitter = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            include_metadata=False,
            include_prev_next_rel=False,
        )

    def build(
        self,
        elements: Sequence[ContentElement],
        *,
        parser_profile: str,
        embedding_profile: str,
        document_names: Mapping[str, str] | None = None,
    ) -> list[TextNode]:
        """Build nodes in stable source order without attaching an implicit embedding model."""

        names = document_names or {}
        nodes: list[TextNode] = []
        ordered = sorted(
            elements,
            key=lambda element: (
                element.workspace_id,
                element.document_id,
                element.version_id,
                element.page_number,
                element.modality.value,
                element.id,
            ),
        )
        for element in ordered:
            chunks = self._chunks(element)
            for chunk_index, chunk in enumerate(chunks):
                metadata = self._metadata(
                    element,
                    chunk_index=chunk_index,
                    parser_profile=parser_profile,
                    embedding_profile=embedding_profile,
                    document_name=names.get(element.document_id, element.document_id),
                )
                node_id = _stable_node_id(element, chunk_index, chunk)
                excluded_keys = list(metadata)
                nodes.append(
                    TextNode(
                        id_=node_id,
                        text=chunk,
                        metadata=metadata,
                        embedding=None,
                        excluded_embed_metadata_keys=excluded_keys,
                        excluded_llm_metadata_keys=excluded_keys,
                    )
                )
        return nodes

    def _chunks(self, element: ContentElement) -> list[str]:
        if element.modality not in _SPLIT_MODALITIES:
            return [element.content]
        chunks = [chunk.strip() for chunk in self._splitter.split_text(element.content)]
        return [chunk for chunk in chunks if chunk] or [element.content]

    @staticmethod
    def _metadata(
        element: ContentElement,
        *,
        chunk_index: int,
        parser_profile: str,
        embedding_profile: str,
        document_name: str,
    ) -> dict[str, str | int | float | bool]:
        metadata: dict[str, str | int | float | bool] = {
            "workspace_id": element.workspace_id,
            "document_id": element.document_id,
            "version_id": element.version_id,
            "document_name": document_name[:240],
            "page_number": element.page_number,
            "modality": element.modality.value,
            "element_id": element.id,
            "confidence": element.confidence,
            "extraction_method": element.extraction_method,
            "parser_profile": parser_profile,
            "embedding_profile": embedding_profile,
            "node_serializer": NODE_SERIALIZER_VERSION,
            "chunk_index": chunk_index,
        }
        if element.asset_key:
            metadata["asset_key"] = element.asset_key
        if element.bbox:
            metadata["bbox"] = json.dumps(element.bbox, separators=(",", ":"))
        for key in ("units", "caption", "table_headers", "visual_kind"):
            value = element.metadata.get(key)
            normalized = _metadata_scalar(value)
            if normalized is not None:
                metadata[key] = normalized
        return metadata


def _metadata_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool | int | float | str):
        return value if not isinstance(value, str) or value else None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return json.dumps(list(value), ensure_ascii=True, separators=(",", ":"))
    return None


def _stable_node_id(element: ContentElement, chunk_index: int, chunk: str) -> str:
    payload = "\x1f".join(
        (
            NODE_SERIALIZER_VERSION,
            element.workspace_id,
            element.document_id,
            element.version_id,
            element.id,
            str(chunk_index),
            chunk,
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"node_{digest[:48]}"


def build_nodes(
    elements: Sequence[ContentElement],
    *,
    parser_profile: str,
    embedding_profile: str,
    document_names: Mapping[str, str] | None = None,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[TextNode]:
    """Functional facade used by ingestion pipelines that do not need builder state."""

    return LlamaIndexNodeBuilder(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    ).build(
        elements,
        parser_profile=parser_profile,
        embedding_profile=embedding_profile,
        document_names=document_names,
    )
