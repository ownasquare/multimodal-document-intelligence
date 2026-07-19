"""Transparent, deterministic modality planning for hybrid retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from document_intelligence.providers.deterministic import lexical_tokens
from document_intelligence.retrieval.models import ModalityGroup

_TABLE_TERMS = frozenset(
    {
        "amount",
        "average",
        "compare",
        "comparison",
        "cost",
        "decrease",
        "difference",
        "increase",
        "impact",
        "likelihood",
        "maximum",
        "mitigation",
        "minimum",
        "percent",
        "percentage",
        "rate",
        "revenue",
        "risk",
        "row",
        "share",
        "table",
        "total",
        "value",
        "versus",
    }
)
_VISUAL_TERMS = frozenset(
    {
        "axis",
        "chart",
        "color",
        "diagram",
        "figure",
        "flow",
        "graph",
        "image",
        "legend",
        "pictured",
        "plot",
        "process",
        "share",
        "shape",
        "trend",
        "visual",
    }
)
_SUMMARY_TERMS = frozenset({"overview", "page", "summarize", "summary", "themes"})


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    groups: tuple[ModalityGroup, ...]
    modality_weights: dict[ModalityGroup, float]
    vector_weight: float
    lexical_weight: float
    include_page_summaries: bool
    numeric_intent: bool
    visual_intent: bool


class RetrievalPlanner:
    """Classifies query intent with explainable lexical rules."""

    def plan(self, question: str) -> RetrievalPlan:
        tokens = set(lexical_tokens(question))
        numeric_token = any(any(character.isdigit() for character in token) for token in tokens)
        numeric_intent = numeric_token or bool(tokens & _TABLE_TERMS)
        visual_intent = bool(tokens & _VISUAL_TERMS)
        summary_intent = bool(tokens & _SUMMARY_TERMS)

        weights = {
            ModalityGroup.TEXT: 1.0,
            ModalityGroup.TABLE: 1.0,
            ModalityGroup.VISUAL: 0.9,
        }
        if numeric_intent:
            weights[ModalityGroup.TABLE] = 1.65
        if visual_intent:
            weights[ModalityGroup.VISUAL] = 1.75
            weights[ModalityGroup.TEXT] = 0.9
        if summary_intent:
            weights[ModalityGroup.TEXT] = max(weights[ModalityGroup.TEXT], 1.25)

        groups = tuple(sorted(weights, key=lambda group: (-weights[group], group.value)))
        return RetrievalPlan(
            groups=groups,
            modality_weights=weights,
            vector_weight=0.55,
            lexical_weight=0.45 if numeric_intent else 0.35,
            include_page_summaries=summary_intent,
            numeric_intent=numeric_intent,
            visual_intent=visual_intent,
        )
