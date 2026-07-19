from document_intelligence.retrieval.models import ModalityGroup
from document_intelligence.retrieval.planner import RetrievalPlanner


def test_numeric_comparison_boosts_table_evidence() -> None:
    plan = RetrievalPlanner().plan("Compare Q1 and Q2 revenue percentages")

    assert plan.numeric_intent
    assert plan.groups[0] is ModalityGroup.TABLE
    assert plan.modality_weights[ModalityGroup.TABLE] > plan.modality_weights[ModalityGroup.TEXT]


def test_chart_language_boosts_visual_evidence() -> None:
    plan = RetrievalPlanner().plan("What trend does the chart legend show?")

    assert plan.visual_intent
    assert plan.groups[0] is ModalityGroup.VISUAL


def test_summary_language_includes_page_summaries() -> None:
    plan = RetrievalPlanner().plan("Summarize the document themes")

    assert plan.include_page_summaries
    assert plan.modality_weights[ModalityGroup.TEXT] >= 1.25
