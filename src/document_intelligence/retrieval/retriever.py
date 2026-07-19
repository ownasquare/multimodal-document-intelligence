"""High-level hybrid retriever with fail-closed scope checks."""

from __future__ import annotations

from document_intelligence.retrieval.index import ChromaVectorIndex
from document_intelligence.retrieval.models import RetrievalScope, RetrievedEvidence
from document_intelligence.retrieval.planner import RetrievalPlanner
from document_intelligence.retrieval.reranker import fuse_and_diversify


class HybridRetriever:
    def __init__(
        self,
        index: ChromaVectorIndex,
        *,
        planner: RetrievalPlanner | None = None,
    ) -> None:
        self.index = index
        self.planner = planner or RetrievalPlanner()

    def retrieve(
        self,
        question: str,
        scope: RetrievalScope,
        *,
        top_k: int = 10,
    ) -> list[RetrievedEvidence]:
        if not question.strip():
            raise ValueError("question cannot be empty")
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between one and fifty")
        if not scope.ready_version_ids:
            return []

        plan = self.planner.plan(question)
        candidate_count = max(top_k * 3, 20)
        vector_hits = self.index.vector_search(
            question,
            scope,
            groups=plan.groups,
            limit_per_group=candidate_count,
        )
        lexical_hits = self.index.lexical_search(
            question,
            scope,
            groups=plan.groups,
            limit=candidate_count * len(plan.groups),
        )
        fused = fuse_and_diversify(vector_hits, lexical_hits, plan, top_k=top_k)

        allowed_versions = set(scope.ready_version_ids)
        allowed_documents = set(scope.document_ids)
        for hit in fused:
            if hit.workspace_id != scope.workspace_id or hit.version_id not in allowed_versions:
                raise RuntimeError(
                    "retrieval backend returned evidence outside the ready-version scope"
                )
            if allowed_documents and hit.document_id not in allowed_documents:
                raise RuntimeError("retrieval backend returned evidence outside the document scope")
        return fused
