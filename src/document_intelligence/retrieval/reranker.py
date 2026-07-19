"""Reciprocal-rank fusion, duplicate suppression, and evidence diversification."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace

from document_intelligence.retrieval.models import ModalityGroup, RetrievedEvidence
from document_intelligence.retrieval.planner import RetrievalPlan


def fuse_and_diversify(
    vector_hits: Sequence[RetrievedEvidence],
    lexical_hits: Sequence[RetrievedEvidence],
    plan: RetrievalPlan,
    *,
    top_k: int,
    rrf_constant: int = 60,
    max_per_page: int = 3,
) -> list[RetrievedEvidence]:
    """Fuse independent rankings while preserving modality and page breadth."""

    if top_k < 1:
        return []
    by_id: dict[str, RetrievedEvidence] = {}
    fused_scores: dict[str, float] = {}
    for weight, hits in (
        (plan.vector_weight, vector_hits),
        (plan.lexical_weight, lexical_hits),
    ):
        for rank, hit in enumerate(hits, start=1):
            existing = by_id.get(hit.record_id)
            by_id[hit.record_id] = (
                hit
                if existing is None
                else replace(
                    existing,
                    vector_score=max(existing.vector_score, hit.vector_score),
                    lexical_score=max(existing.lexical_score, hit.lexical_score),
                )
            )
            modality_weight = plan.modality_weights.get(hit.group, 1.0)
            fused_scores[hit.record_id] = fused_scores.get(hit.record_id, 0.0) + (
                weight * modality_weight / (rrf_constant + rank)
            )

    ordered = sorted(
        by_id.values(),
        key=lambda hit: (-fused_scores[hit.record_id], hit.page_number, hit.record_id),
    )
    maximum = max(fused_scores.values(), default=1.0)
    maximum_lexical = max((hit.lexical_score for hit in ordered), default=0.0)
    scored = [
        replace(
            hit,
            final_score=(
                0.75 * fused_scores[hit.record_id] / maximum
                + 0.25 * (hit.lexical_score / maximum_lexical if maximum_lexical > 0 else 0.0)
            ),
        )
        for hit in ordered
    ]
    scored.sort(key=lambda hit: (-hit.final_score, hit.page_number, hit.record_id))
    unique = _suppress_duplicates(scored)

    selected: list[RetrievedEvidence] = []
    selected_ids: set[str] = set()
    page_counts: dict[tuple[str, int], int] = {}
    effective_page_limit = max(max_per_page, 4) if plan.numeric_intent else max_per_page

    # Ensure the high-priority intent groups get a chance before filling by score.
    for group in plan.groups:
        candidate = next((hit for hit in unique if hit.group == group), None)
        if candidate is not None and len(selected) < top_k:
            selected.append(candidate)
            selected_ids.add(candidate.record_id)
            key = (candidate.document_id, candidate.page_number)
            page_counts[key] = page_counts.get(key, 0) + 1

    for hit in unique:
        if hit.record_id in selected_ids:
            continue
        page_key = (hit.document_id, hit.page_number)
        if page_counts.get(page_key, 0) >= effective_page_limit:
            continue
        selected.append(hit)
        selected_ids.add(hit.record_id)
        page_counts[page_key] = page_counts.get(page_key, 0) + 1
        if len(selected) >= top_k:
            break

    selected.sort(key=lambda hit: (-hit.final_score, hit.page_number, hit.record_id))
    return selected[:top_k]


def _suppress_duplicates(hits: Sequence[RetrievedEvidence]) -> list[RetrievedEvidence]:
    preferred_visuals: dict[tuple[str, str], RetrievedEvidence] = {}
    for hit in hits:
        if hit.group is not ModalityGroup.VISUAL or not hit.asset_key:
            continue
        key = (hit.version_id, hit.asset_key)
        existing = preferred_visuals.get(key)
        quality = (sum(character.isdigit() for character in hit.content), len(hit.content))
        existing_quality = (
            (sum(character.isdigit() for character in existing.content), len(existing.content))
            if existing is not None
            else (-1, -1)
        )
        if quality > existing_quality:
            preferred_visuals[key] = hit

    selected: list[RetrievedEvidence] = []
    seen_elements: set[str] = set()
    seen_content: set[str] = set()
    seen_visual_assets: set[tuple[str, str]] = set()
    for hit in hits:
        normalized = " ".join(hit.content.casefold().split())
        content_key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if hit.element_id in seen_elements or content_key in seen_content:
            continue
        visual_asset = (
            (hit.version_id, hit.asset_key)
            if hit.group is ModalityGroup.VISUAL and hit.asset_key
            else None
        )
        if visual_asset is not None and preferred_visuals[visual_asset].record_id != hit.record_id:
            continue
        if visual_asset is not None and visual_asset in seen_visual_assets:
            continue
        selected.append(hit)
        seen_elements.add(hit.element_id)
        seen_content.add(content_key)
        if visual_asset is not None:
            seen_visual_assets.add(visual_asset)
    return selected
