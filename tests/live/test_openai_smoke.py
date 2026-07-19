from __future__ import annotations

import io
import math

import pytest
from PIL import Image, ImageDraw

from document_intelligence.config import Settings
from document_intelligence.models import Modality
from document_intelligence.providers import (
    OpenAIAnswerProvider,
    OpenAIEmbeddingProvider,
    OpenAIVisualProvider,
    ProviderEvidence,
)

pytestmark = pytest.mark.live


def test_openai_embedding_smoke(live_openai_settings: Settings) -> None:
    provider = OpenAIEmbeddingProvider(
        api_key=live_openai_settings.openai_api_key,
        model=live_openai_settings.openai_embedding_model,
        dimensions=32,
        timeout_seconds=live_openai_settings.provider_timeout_seconds,
        max_attempts=live_openai_settings.provider_max_attempts,
    )

    vector = provider.embed_query("Document intelligence live embedding smoke test.")

    assert len(vector) == 32
    assert all(math.isfinite(value) for value in vector)


def test_openai_grounded_answer_smoke(live_openai_settings: Settings) -> None:
    evidence_id = "node_live_smoke"
    evidence = ProviderEvidence(
        id=evidence_id,
        workspace_id="workspace_live",
        document_id="document_live",
        version_id="version_live",
        document_name="live-smoke.pdf",
        element_id="element_live",
        page_number=1,
        modality=Modality.TABLE_ROW,
        content="The audited Q2 net revenue was $4.8 million.",
        retrieval_score=1.0,
    )
    provider = OpenAIAnswerProvider(
        api_key=live_openai_settings.openai_api_key,
        model=live_openai_settings.openai_chat_model,
        timeout_seconds=live_openai_settings.provider_timeout_seconds,
        max_attempts=live_openai_settings.provider_max_attempts,
    )

    answer = provider.answer(
        "What was the audited Q2 net revenue?",
        [evidence],
        allowed_evidence_ids=frozenset({evidence_id}),
    )

    assert not answer.abstained
    assert answer.claims
    assert all(set(claim.citation_ids) <= {evidence_id} for claim in answer.claims)


def test_openai_visual_understanding_smoke(live_openai_settings: Settings) -> None:
    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(20, 95), (100, 70), (210, 20)], fill="navy", width=5)
    draw.text((20, 100), "Q1", fill="black")
    draw.text((190, 25), "Q2", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    provider = OpenAIVisualProvider(
        api_key=live_openai_settings.openai_api_key,
        model=live_openai_settings.openai_vision_model,
        timeout_seconds=live_openai_settings.provider_timeout_seconds,
        max_attempts=live_openai_settings.provider_max_attempts,
    )

    description = provider.describe(
        buffer.getvalue(),
        mime_type="image/png",
        context="A small synthetic trend chart labeled Q1 and Q2.",
        suggested_modality=Modality.CHART,
    )

    assert description.summary
    assert description.modality in {Modality.IMAGE, Modality.CHART, Modality.DIAGRAM}
    assert 0.0 <= description.confidence <= 1.0
