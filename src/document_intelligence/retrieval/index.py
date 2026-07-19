"""Versioned Chroma adapter that always receives explicit embeddings."""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from llama_index.core.schema import TextNode
from pydantic.warnings import PydanticDeprecatedSince211

from document_intelligence.ingestion.nodes import NODE_SERIALIZER_VERSION
from document_intelligence.models import BoundingBox, Modality
from document_intelligence.providers.base import EmbeddingProvider
from document_intelligence.providers.deterministic import lexical_tokens
from document_intelligence.retrieval.models import (
    ModalityGroup,
    RetrievalScope,
    RetrievedEvidence,
    modality_group,
)

_COLLECTION_SCHEMA_VERSION = 1
_MAX_SEARCHABLE_CHARACTERS = 12000


@dataclass(frozen=True, slots=True)
class IndexFingerprint:
    embedding_profile: str
    dimensions: int
    parser_profile: str
    node_serializer: str
    modality_group: ModalityGroup
    distance_metric: str = "cosine"

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {
                "collection_schema": _COLLECTION_SCHEMA_VERSION,
                "embedding_profile": self.embedding_profile,
                "dimensions": self.dimensions,
                "parser_profile": self.parser_profile,
                "node_serializer": self.node_serializer,
                "modality": self.modality_group.value,
                "distance_metric": self.distance_metric,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def collection_name(self) -> str:
        return f"mdi_v{_COLLECTION_SCHEMA_VERSION}_{self.modality_group.value}_{self.digest[:20]}"


class IndexCompatibilityError(RuntimeError):
    """Raised before incompatible vectors can be mixed in one namespace."""


class ChromaVectorIndex:
    """Persistent local index with separate text, table, and visual namespaces."""

    def __init__(
        self,
        path: Path,
        embedding_provider: EmbeddingProvider,
        *,
        parser_profile: str,
        embedding_profile: str | None = None,
        client: Any | None = None,
    ) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        warnings.filterwarnings(
            "ignore",
            category=PydanticDeprecatedSince211,
            module=r"chromadb(?:\..*)?",
        )
        self.path = path
        self.embedding_provider = embedding_provider
        self.parser_profile = parser_profile
        self.embedding_profile = embedding_profile or embedding_provider.profile
        self._client = client or chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl=(
                    "document_intelligence.retrieval.telemetry.NoOpProductTelemetry"
                ),
            ),
        )
        self._collections: dict[ModalityGroup, Any] = {}

    @property
    def collection_names(self) -> dict[ModalityGroup, str]:
        return {group: self._fingerprint(group).collection_name for group in ModalityGroup}

    def upsert(self, nodes: Sequence[TextNode]) -> int:
        """Idempotently insert nodes, validating the complete index profile first."""

        grouped: dict[ModalityGroup, list[TextNode]] = {group: [] for group in ModalityGroup}
        for node in nodes:
            self._validate_node(node)
            grouped[modality_group(Modality(str(node.metadata["modality"])))].append(node)

        written = 0
        for group, group_nodes in grouped.items():
            if not group_nodes:
                continue
            collection = self._collection(group)
            documents = [node.text[:_MAX_SEARCHABLE_CHARACTERS] for node in group_nodes]
            embeddings = self.embedding_provider.embed_texts(documents)
            if len(embeddings) != len(group_nodes) or any(
                len(vector) != self.embedding_provider.dimensions for vector in embeddings
            ):
                raise IndexCompatibilityError("embedding provider returned incompatible vectors")
            collection.upsert(
                ids=[node.node_id for node in group_nodes],
                embeddings=embeddings,
                documents=documents,
                metadatas=[dict(node.metadata) for node in group_nodes],
            )
            written += len(group_nodes)
        return written

    def vector_search(
        self,
        question: str,
        scope: RetrievalScope,
        *,
        groups: Sequence[ModalityGroup],
        limit_per_group: int,
    ) -> list[RetrievedEvidence]:
        if not scope.ready_version_ids or limit_per_group < 1:
            return []
        query_embedding = self.embedding_provider.embed_query(question)
        if len(query_embedding) != self.embedding_provider.dimensions:
            raise IndexCompatibilityError("query embedding has incompatible dimensions")
        where = _scope_where(scope)
        hits: list[RetrievedEvidence] = []
        for group in groups:
            collection = self._collection(group)
            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit_per_group,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            ids = _first_result_list(result.get("ids"))
            documents = _first_result_list(result.get("documents"))
            metadatas = _first_result_list(result.get("metadatas"))
            distances = _first_result_list(result.get("distances"))
            for record_id, document, metadata, distance in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                similarity = max(0.0, 1.0 - float(distance))
                hits.append(
                    _evidence_from_record(
                        str(record_id),
                        str(document),
                        _require_metadata(metadata),
                        vector_score=similarity,
                    )
                )
        return hits

    def lexical_search(
        self,
        question: str,
        scope: RetrievalScope,
        *,
        groups: Sequence[ModalityGroup],
        limit: int,
        candidate_limit_per_group: int = 5000,
    ) -> list[RetrievedEvidence]:
        if not scope.ready_version_ids or limit < 1:
            return []
        where = _scope_where(scope)
        records: list[RetrievedEvidence] = []
        for group in groups:
            result = self._collection(group).get(
                where=where,
                limit=candidate_limit_per_group,
                include=["documents", "metadatas"],
            )
            ids = list(result.get("ids") or [])
            documents = list(result.get("documents") or [])
            metadatas = list(result.get("metadatas") or [])
            for record_id, document, metadata in zip(ids, documents, metadatas, strict=True):
                records.append(
                    _evidence_from_record(
                        str(record_id), str(document), _require_metadata(metadata)
                    )
                )
        return _bm25_like(question, records, limit=limit)

    def delete_version(self, workspace_id: str, version_id: str) -> int:
        where = {
            "$and": [
                {"workspace_id": {"$eq": workspace_id}},
                {"version_id": {"$eq": version_id}},
            ]
        }
        before = self.count_version(workspace_id, version_id)
        for group in ModalityGroup:
            self._collection(group).delete(where=where)
        if self.count_version(workspace_id, version_id) != 0:
            raise RuntimeError("vector deletion readback failed")
        return before

    def count_version(self, workspace_id: str, version_id: str) -> int:
        where = {
            "$and": [
                {"workspace_id": {"$eq": workspace_id}},
                {"version_id": {"$eq": version_id}},
            ]
        }
        return sum(
            len(self._collection(group).get(where=where, include=[]).get("ids") or [])
            for group in ModalityGroup
        )

    def _fingerprint(self, group: ModalityGroup) -> IndexFingerprint:
        return IndexFingerprint(
            embedding_profile=self.embedding_profile,
            dimensions=self.embedding_provider.dimensions,
            parser_profile=self.parser_profile,
            node_serializer=NODE_SERIALIZER_VERSION,
            modality_group=group,
        )

    def _collection(self, group: ModalityGroup) -> Any:
        cached = self._collections.get(group)
        if cached is not None:
            return cached
        fingerprint = self._fingerprint(group)
        expected_metadata: dict[str, str | int | float | bool] = {
            "schema_version": _COLLECTION_SCHEMA_VERSION,
            "fingerprint": fingerprint.digest,
            "embedding_profile": fingerprint.embedding_profile,
            "dimensions": fingerprint.dimensions,
            "parser_profile": fingerprint.parser_profile,
            "node_serializer": fingerprint.node_serializer,
            "modality_group": group.value,
            "hnsw:space": fingerprint.distance_metric,
        }
        # Chroma 0.6.x supports Pydantic 2 but accesses model_fields through the
        # deprecated instance property. Keep that upstream compatibility warning
        # scoped to the call while retaining warnings-as-errors everywhere else.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=PydanticDeprecatedSince211,
                module=r"chromadb(?:\..*)?",
            )
            collection = self._client.get_or_create_collection(
                name=fingerprint.collection_name,
                metadata=expected_metadata,
                embedding_function=None,
            )
        actual = collection.metadata or {}
        for key, expected in expected_metadata.items():
            if actual.get(key) != expected:
                raise IndexCompatibilityError(
                    f"collection metadata is incompatible for field {key}"
                )
        self._collections[group] = collection
        return collection

    def _validate_node(self, node: TextNode) -> None:
        required = {
            "workspace_id",
            "document_id",
            "version_id",
            "document_name",
            "page_number",
            "modality",
            "element_id",
            "parser_profile",
            "embedding_profile",
            "node_serializer",
        }
        missing = required - node.metadata.keys()
        if missing:
            raise IndexCompatibilityError(f"node metadata is missing: {', '.join(sorted(missing))}")
        if node.metadata["parser_profile"] != self.parser_profile:
            raise IndexCompatibilityError("node parser profile does not match the index namespace")
        if node.metadata["embedding_profile"] != self.embedding_profile:
            raise IndexCompatibilityError(
                "node embedding profile does not match the index namespace"
            )
        if node.metadata["node_serializer"] != NODE_SERIALIZER_VERSION:
            raise IndexCompatibilityError("node serializer version is incompatible")
        if not node.text.strip():
            raise ValueError("empty nodes cannot be indexed")


def _scope_where(scope: RetrievalScope) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = [
        {"workspace_id": {"$eq": scope.workspace_id}},
        {"version_id": {"$in": list(scope.ready_version_ids)}},
    ]
    if scope.document_ids:
        conditions.append({"document_id": {"$in": list(scope.document_ids)}})
    return {"$and": conditions}


def _first_result_list(value: Any) -> list[Any]:
    if not value:
        return []
    first = value[0]
    return list(first) if first is not None else []


def _require_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IndexCompatibilityError("vector record metadata is missing")
    return value


def _bbox_from_metadata(value: Any) -> BoundingBox | None:
    if not value or not isinstance(value, str):
        return None
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _evidence_from_record(
    record_id: str,
    document: str,
    metadata: dict[str, Any],
    *,
    vector_score: float = 0.0,
    lexical_score: float = 0.0,
) -> RetrievedEvidence:
    try:
        modality = Modality(str(metadata["modality"]))
        page_number = int(metadata["page_number"])
        workspace_id = str(metadata["workspace_id"])
        document_id = str(metadata["document_id"])
        version_id = str(metadata["version_id"])
        element_id = str(metadata["element_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexCompatibilityError("vector record has invalid provenance metadata") from exc
    return RetrievedEvidence(
        record_id=record_id,
        workspace_id=workspace_id,
        document_id=document_id,
        version_id=version_id,
        document_name=str(metadata.get("document_name") or document_id),
        element_id=element_id,
        page_number=page_number,
        modality=modality,
        content=document,
        bbox=_bbox_from_metadata(metadata.get("bbox")),
        asset_key=str(metadata["asset_key"]) if metadata.get("asset_key") else None,
        units=str(metadata["units"]) if metadata.get("units") else None,
        vector_score=vector_score,
        lexical_score=lexical_score,
        metadata={
            str(key): value
            for key, value in metadata.items()
            if isinstance(value, str | int | float | bool)
        },
    )


def _bm25_like(
    question: str, records: Sequence[RetrievedEvidence], *, limit: int
) -> list[RetrievedEvidence]:
    query_terms = Counter(lexical_tokens(question))
    if not query_terms or not records:
        return []
    tokenized = [Counter(lexical_tokens(record.content)) for record in records]
    average_length = sum(sum(counts.values()) for counts in tokenized) / len(tokenized)
    average_length = max(average_length, 1.0)
    document_frequency = {
        term: sum(1 for counts in tokenized if term in counts) for term in query_terms
    }
    scored: list[RetrievedEvidence] = []
    total = len(records)
    for record, counts in zip(records, tokenized, strict=True):
        length = max(sum(counts.values()), 1)
        score = 0.0
        for term, query_count in query_terms.items():
            frequency = counts[term]
            if not frequency:
                continue
            inverse_frequency = math.log(
                1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = frequency + 1.2 * (0.25 + 0.75 * length / average_length)
            score += query_count * inverse_frequency * (frequency * 2.2 / denominator)
        if question.casefold().strip() in record.content.casefold():
            score += 1.0
        if score > 0:
            scored.append(replace(record, lexical_score=score))
    scored.sort(key=lambda item: (-item.lexical_score, item.page_number, item.record_id))
    return scored[:limit]
