"""HTTP boundary tests for the Streamlit client."""

from __future__ import annotations

import httpx
import pytest

from document_intelligence.ui.client import ApiClientError, DocumentIntelligenceClient


def test_status_parses_typed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/status"
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "provider_mode": "deterministic",
                "embedding_provider": "deterministic",
                "document_count": 0,
                "ready_document_count": 0,
                "queued_job_count": 0,
                "running_job_count": 0,
                "ocr_available": False,
                "demo_mode": True,
                "warnings": [],
            },
        )

    client = DocumentIntelligenceClient("http://testserver", transport=httpx.MockTransport(handler))
    assert client.status().status == "ready"


def test_error_response_is_sanitized_and_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            422,
            json={"detail": {"code": "invalid_pdf", "message": "This file is not a PDF."}},
        )

    client = DocumentIntelligenceClient("http://testserver", transport=httpx.MockTransport(handler))
    with pytest.raises(ApiClientError) as captured:
        client.status()

    assert captured.value.status_code == 422
    assert captured.value.code == "invalid_pdf"
    assert str(captured.value) == "This file is not a PDF."


def test_asset_client_rejects_arbitrary_urls() -> None:
    client = DocumentIntelligenceClient(
        "http://testserver", transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    with pytest.raises(ApiClientError, match="asset reference"):
        client.fetch_asset("https://example.com/private.pdf")
