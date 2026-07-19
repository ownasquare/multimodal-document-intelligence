from __future__ import annotations

import io
import json
import math
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from document_intelligence.models import Modality
from document_intelligence.parsers.ocr import OCRProcessor
from document_intelligence.providers import (
    DeterministicAnswerProvider,
    DeterministicEmbeddingProvider,
    DeterministicVisualProvider,
    OpenAIAnswerProvider,
    OpenAIEmbeddingProvider,
    OpenAIVisualProvider,
    ProviderError,
    ProviderEvidence,
    image_data_url,
)


def _evidence(identifier: str = "node_1") -> ProviderEvidence:
    return ProviderEvidence(
        id=identifier,
        workspace_id="workspace_1",
        document_id="document_1",
        version_id="version_1",
        document_name="Northstar.pdf",
        element_id="element_1",
        page_number=2,
        modality=Modality.TABLE_ROW,
        content="Q2 revenue increased 14 percent to $4.8 million.",
        retrieval_score=1.0,
    )


def test_deterministic_embeddings_are_stable_normalized_and_384_dimensional() -> None:
    provider = DeterministicEmbeddingProvider()
    first = provider.embed_query("Revenue increased by fourteen percent")
    second = provider.embed_texts(["Revenue increased by fourteen percent"])[0]

    assert first == second
    assert len(first) == 384
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)
    assert first != provider.embed_query("A completely different sentence")


def test_deterministic_visual_provider_reports_mechanics_and_bounded_context() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")

    description = DeterministicVisualProvider().describe(
        buffer.getvalue(),
        mime_type="image/png",
        context="Figure 2 shows quarterly revenue.",
        suggested_modality=Modality.CHART,
    )

    assert description.modality is Modality.CHART
    assert "40 by 20" in description.summary
    assert "quarterly revenue" in description.summary


class _VisualOCRBackend:
    Output = SimpleNamespace(DICT="dict")

    def __init__(self) -> None:
        self.configs: list[str] = []

    def image_to_data(self, image: Image.Image, **kwargs: object) -> dict[str, list[object]]:
        assert image.size == (40, 20)
        self.configs.append(str(kwargs["config"]))
        return {
            "text": ["April", "$2.5M", "May", "$2.7M", "June", "$3.2M"],
            "conf": ["96", "94", "95", "93", "95", "92"],
        }


def test_deterministic_visual_provider_can_add_local_ocr_observations() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    backend = _VisualOCRBackend()
    provider = DeterministicVisualProvider(
        ocr_processor=OCRProcessor(enabled=True, backend=backend)
    )

    description = provider.describe(
        buffer.getvalue(),
        mime_type="image/png",
        context="Monthly net revenue chart.",
        suggested_modality=Modality.CHART,
    )

    assert provider.profile == "deterministic-visual-tesseract-v1"
    assert description.observed_text == ["April $2.5M May $2.7M June $3.2M"]
    assert "$3.2M" in description.summary
    assert description.confidence == 0.65
    assert backend.configs == ["--psm 11"]


def test_deterministic_answer_is_extractive_and_abstains_without_lexical_support() -> None:
    provider = DeterministicAnswerProvider()
    supported = provider.answer(
        "How much did Q2 revenue increase?",
        [_evidence()],
        allowed_evidence_ids=frozenset({"node_1"}),
    )
    unsupported = provider.answer(
        "What food does the CEO prefer?",
        [_evidence()],
        allowed_evidence_ids=frozenset({"node_1"}),
    )

    assert not supported.abstained
    assert supported.claims[0].text in _evidence().content
    assert supported.claims[0].citation_ids == ["node_1"]
    assert unsupported.abstained


def test_deterministic_answer_summarizes_a_grounded_incident_concisely() -> None:
    incident = _evidence("incident").model_copy(
        update={
            "modality": Modality.OCR,
            "content": (
                "The packing barcode service stopped accepting sessions after its service "
                "certificate expired. The outage lasted 47 minutes and delayed 320 customer "
                "orders. ROOT CAUSE: Expired barcode-service certificate."
            ),
        }
    )

    answer = DeterministicAnswerProvider().answer(
        "What caused the packing interruption, how long did it last, and how many orders were "
        "delayed?",
        [incident],
        allowed_evidence_ids=frozenset({"incident"}),
    )

    assert answer.text == (
        "An expired barcode-service certificate caused a 47-minute outage that delayed 320 orders."
    )
    assert answer.claims[0].citation_ids == ["incident"]
    assert answer.claims[0].inference is True


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _EmbeddingEndpoint:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.failures:
            raise self.failures.pop(0)
        dimensions = int(kwargs["dimensions"])
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index + 1)] * dimensions)
                for index, _ in enumerate(kwargs["input"])
            ]
        )


class _ResponsesEndpoint:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.output))


def test_openai_embedding_retries_only_bounded_server_failures_and_sets_timeout() -> None:
    endpoint = _EmbeddingEndpoint([_StatusError(503), _StatusError(503)])
    sleeps: list[float] = []
    provider = OpenAIEmbeddingProvider(
        client=SimpleNamespace(embeddings=endpoint),
        dimensions=32,
        timeout_seconds=7.5,
        max_attempts=3,
        sleep=sleeps.append,
    )

    vectors = provider.embed_texts(["one", "two"])

    assert len(endpoint.calls) == 3
    assert sleeps == [0.25, 0.5]
    assert endpoint.calls[-1]["timeout"] == 7.5
    assert [len(vector) for vector in vectors] == [32, 32]


def test_openai_embedding_does_not_retry_authentication_failure() -> None:
    endpoint = _EmbeddingEndpoint([_StatusError(401)])
    provider = OpenAIEmbeddingProvider(
        client=SimpleNamespace(embeddings=endpoint),
        dimensions=32,
        max_attempts=5,
        sleep=lambda _: None,
    )

    with pytest.raises(ProviderError, match="server-side provider configuration") as caught:
        provider.embed_query("question")

    assert caught.value.code == "provider_authentication_error"
    assert len(endpoint.calls) == 1


def test_openai_answer_enforces_evidence_allowlist_and_disables_tools() -> None:
    endpoint = _ResponsesEndpoint(
        {
            "text": "Revenue increased.",
            "claims": [
                {"text": "Revenue increased.", "citation_ids": ["outside"], "inference": False}
            ],
            "abstained": False,
        }
    )
    provider = OpenAIAnswerProvider(client=SimpleNamespace(responses=endpoint))

    with pytest.raises(ProviderError) as caught:
        provider.answer(
            "What changed?",
            [_evidence()],
            allowed_evidence_ids=frozenset({"node_1"}),
        )

    assert caught.value.code == "provider_invalid_citation"
    assert endpoint.calls[0]["tools"] == []
    assert endpoint.calls[0]["timeout"] == 60.0


def test_openai_visual_uses_validated_data_url_and_structured_output() -> None:
    endpoint = _ResponsesEndpoint(
        {
            "summary": "A line chart rises from Q1 to Q2.",
            "modality": "chart",
            "observed_text": ["Q1", "Q2"],
            "observed_facts": ["The plotted line rises."],
            "confidence": 0.9,
        }
    )
    provider = OpenAIVisualProvider(client=SimpleNamespace(responses=endpoint))

    result = provider.describe(
        b"png-bytes",
        mime_type="image/png",
        context="Quarterly result",
        suggested_modality=Modality.CHART,
    )

    assert result.modality is Modality.CHART
    serialized_call = json.dumps(endpoint.calls[0])
    assert "data:image/png;base64," in serialized_call
    assert endpoint.calls[0]["tools"] == []
    with pytest.raises(ValueError, match="unsupported image"):
        image_data_url(b"svg", "image/svg+xml")
