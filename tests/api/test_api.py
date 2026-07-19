"""FastAPI workflows, validation, authentication, and safety tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from document_intelligence.api.app import create_app
from document_intelligence.api.contracts import PageEnvelope
from document_intelligence.config import Settings
from document_intelligence.models import (
    Answer,
    ContentElement,
    Conversation,
    Document,
    IngestionJob,
    SystemStatus,
    UploadReceipt,
)
from document_intelligence.repository import RecordNotFoundError
from tests.api.conftest import FakeServices

pytestmark = pytest.mark.asyncio


async def test_default_factory_is_lazy_and_uses_the_supplied_settings(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "factory-data",
        enable_ocr=False,
    )
    app = create_app(settings=settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        live = await client.get("/health/live")
        assert live.status_code == 200
        assert not settings.data_dir.exists()

        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"]["database"] is True
        assert settings.database_path.is_file()


async def test_liveness_does_not_construct_lazy_services_and_preserves_request_id(
    settings: Settings,
) -> None:
    constructed: list[FakeServices] = []

    def factory() -> FakeServices:
        instance = FakeServices()
        constructed.append(instance)
        return instance

    app = create_app(factory, settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        live = await client.get("/health/live", headers={"X-Request-ID": "request-123"})
        assert live.status_code == 200
        assert live.json() == {"status": "alive"}
        assert live.headers["X-Request-ID"] == "request-123"
        assert constructed == []

        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["checks"] == {"database": True}
        assert len(constructed) == 1
    assert constructed[0].closed


async def test_ui_client_response_shapes_are_exact(client: httpx.AsyncClient) -> None:
    status_response = await client.get("/api/v1/status")
    assert status_response.status_code == 200
    SystemStatus.model_validate(status_response.json())

    documents = await client.get("/api/v1/documents")
    document_page = PageEnvelope[Document].model_validate(documents.json())
    assert document_page.total == 1
    document_id = document_page.items[0].id

    Document.model_validate((await client.get(f"/api/v1/documents/{document_id}")).json())
    element_payload = (await client.get(f"/api/v1/documents/{document_id}/elements")).json()
    assert PageEnvelope[ContentElement].model_validate(element_payload).total == 1
    assert (
        PageEnvelope[IngestionJob].model_validate((await client.get("/api/v1/jobs")).json()).total
        == 1
    )
    assert (
        PageEnvelope[Conversation]
        .model_validate((await client.get("/api/v1/conversations")).json())
        .total
        == 1
    )


async def test_upload_is_bounded_sanitized_and_idempotent(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    upload = [("files", ("../../Quarterly.pdf", b"%PDF-1.7\nbody", "application/pdf"))]
    first = await client.post(
        "/api/v1/documents",
        files=upload,
        headers={"Idempotency-Key": "upload-key-1"},
    )
    second = await client.post(
        "/api/v1/documents",
        files=upload,
        headers={"Idempotency-Key": "upload-key-1"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    first_receipts = [UploadReceipt.model_validate(item) for item in first.json()]
    assert [UploadReceipt.model_validate(item) for item in second.json()] == first_receipts
    assert services.uploaded_names == ["Quarterly.pdf"]
    assert services.uploaded_bytes == [b"%PDF-1.7\nbody"]
    assert services.upload_calls == 2


async def test_upload_rejects_non_pdf_magic_mime_size_and_batch(
    client: httpx.AsyncClient,
) -> None:
    invalid_magic = await client.post(
        "/api/v1/documents",
        files=[("files", ("fake.pdf", b"not a pdf", "application/pdf"))],
        headers={"Idempotency-Key": "upload-key-2"},
    )
    assert invalid_magic.status_code == 422
    assert invalid_magic.json()["detail"]["code"] == "invalid_pdf"

    invalid_mime = await client.post(
        "/api/v1/documents",
        files=[("files", ("notes.txt", b"%PDF-1.7", "text/plain"))],
        headers={"Idempotency-Key": "upload-key-3"},
    )
    assert invalid_mime.status_code == 415
    assert invalid_mime.json()["detail"]["code"] == "invalid_file_type"

    oversized = await client.post(
        "/api/v1/documents",
        files=[("files", ("large.pdf", b"%PDF-" + b"x" * 1024, "application/pdf"))],
        headers={"Idempotency-Key": "upload-key-4"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "file_too_large"

    too_many = await client.post(
        "/api/v1/documents",
        files=[("files", (f"{index}.pdf", b"%PDF-1.7", "application/pdf")) for index in range(3)],
        headers={"Idempotency-Key": "upload-key-5"},
    )
    assert too_many.status_code == 413
    assert too_many.json()["detail"]["code"] == "upload_batch_too_large"


async def test_mutations_query_and_demo_return_ui_models(
    client: httpx.AsyncClient,
) -> None:
    IngestionJob.model_validate(
        (await client.post("/api/v1/documents/document-1/reprocess")).json()
    )
    IngestionJob.model_validate((await client.delete("/api/v1/documents/document-1")).json())
    IngestionJob.model_validate((await client.post("/api/v1/jobs/job-1/retry")).json())
    answer = await client.post(
        "/api/v1/query",
        json={"question": "What changed?", "document_ids": ["document-1"]},
    )
    assert answer.status_code == 200
    Answer.model_validate(answer.json())
    UploadReceipt.model_validate((await client.post("/api/v1/demo/sample")).json())


async def test_query_without_document_ids_uses_the_ready_workspace_scope(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/query", json={"question": "What changed?"})

    assert response.status_code == 200
    Answer.model_validate(response.json())


async def test_optional_bearer_auth_is_constant_shape(
    services: FakeServices, settings: Settings
) -> None:
    protected = settings.model_copy(update={"api_token": SecretStr("test-token")})
    app = create_app(services, protected)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        missing = await client.get("/api/v1/status")
        invalid = await client.get(
            "/api/v1/status", headers={"Authorization": "Bearer wrong-token"}
        )
        valid = await client.get("/api/v1/status", headers={"Authorization": "Bearer test-token"})
        public = await client.get("/health/live")

    assert missing.status_code == invalid.status_code == 401
    assert missing.json()["detail"]["code"] == "authentication_required"
    assert invalid.json()["detail"]["code"] == "authentication_required"
    assert valid.status_code == 200
    assert public.status_code == 200


async def test_asset_route_rejects_traversal_and_original_pdfs(
    client: httpx.AsyncClient, services: FakeServices
) -> None:
    traversal = await client.get("/api/v1/assets/workspace-1%5C..%5Csecret.png")
    original = await client.get("/api/v1/assets/workspace-1/version-1/original.pdf")
    image = await client.get("/api/v1/assets/workspace-1/version-1/page-1.png")

    assert traversal.status_code == 400
    assert traversal.json()["detail"]["code"] == "invalid_asset"
    assert original.status_code == 403
    assert original.json()["detail"]["code"] == "original_document_private"
    assert image.status_code == 200
    assert image.content == b"png-bytes"
    assert image.headers["Content-Type"].startswith("image/png")
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert services.asset_keys == ["workspace-1/version-1/page-1.png"]


async def test_errors_are_typed_sanitized_and_include_request_id(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get(
        "/api/v1/documents/not-found", headers={"X-Request-ID": "known-request"}
    )
    invalid = await client.get("/api/v1/documents?limit=0")

    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "document_not_found",
        "message": "The requested document was not found.",
        "request_id": "known-request",
        "retryable": False,
    }
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "validation_error"
    assert "input" not in str(invalid.json()["detail"].get("details"))


async def test_unexpected_errors_do_not_leak_internal_details(
    client: httpx.AsyncClient,
    services: FakeServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_status() -> SystemStatus:
        raise RuntimeError("/private/path provider-secret-value")

    monkeypatch.setattr(services, "status", fail_status)
    response = await client.get("/api/v1/status")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    assert "private" not in response.text
    assert "secret" not in response.text


async def test_domain_errors_map_to_stable_http_codes(
    client: httpx.AsyncClient,
    services: FakeServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_job(job_id: str) -> IngestionJob:
        del job_id
        raise RecordNotFoundError("internal repository detail")

    monkeypatch.setattr(services, "retry_job", missing_job)
    response = await client.post("/api/v1/jobs/missing/retry")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "resource_not_found"
    assert "repository" not in response.text


async def test_cors_and_trusted_host_policy_uses_settings(
    client: httpx.AsyncClient,
) -> None:
    preflight = await client.options(
        "/api/v1/status",
        headers={
            "Origin": "http://127.0.0.1:8514",
            "Access-Control-Request-Method": "GET",
        },
    )
    untrusted = await client.get("/health/live", headers={"Host": "evil.example"})

    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:8514"
    assert untrusted.status_code == 400
