from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from document_intelligence.api import create_app
from document_intelligence.config import Settings
from document_intelligence.container import SAMPLE_PATH, create_runtime
from document_intelligence.jobs import JobCoordinator
from document_intelligence.models import Answer, JobKind, JobStatus, UploadReceipt

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_http_sample_to_grounded_answer_and_verified_delete(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        enable_ocr=False,
        page_render_scale=0.75,
    )
    runtime = create_runtime(settings)
    app = create_app(runtime.application, settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        empty_status = await client.get("/api/v1/status")
        assert empty_status.json()["status"] == "needs_setup"

        accepted_response = await client.post(
            "/api/v1/documents",
            files={
                "files": (
                    "Northstar Q2 Operations Review.pdf",
                    SAMPLE_PATH.read_bytes(),
                    "application/pdf",
                )
            },
            headers={"Idempotency-Key": "http-e2e-upload-v1"},
        )
        assert accepted_response.status_code == 202
        accepted = UploadReceipt.model_validate(accepted_response.json()[0])
        sample_duplicate = await client.post(
            "/api/v1/demo/sample",
            headers={"Idempotency-Key": "http-e2e-sample-v1"},
        )
        assert sample_duplicate.status_code == 202
        assert UploadReceipt.model_validate(sample_duplicate.json()).duplicate is True
        working_status = await client.get("/api/v1/status")
        assert working_status.json()["status"] == "working"

        coordinator = JobCoordinator(runtime.repository, owner="http-e2e", lease_seconds=30)
        ingest_lease = coordinator.lease(kinds=(JobKind.INGEST,))
        assert ingest_lease is not None
        assert runtime.ingestion.process(ingest_lease).job.status is JobStatus.SUCCEEDED

        status_response = await client.get("/api/v1/status")
        assert status_response.status_code == 200
        assert status_response.json()["ready_document_count"] == 1
        document_page = (await client.get("/api/v1/documents")).json()
        assert document_page["total"] == 1
        element_page = (
            await client.get(f"/api/v1/documents/{accepted.document.id}/elements?limit=10")
        ).json()
        assert element_page["total"] > 20
        assert len(element_page["items"]) == 10
        job_page = (await client.get("/api/v1/jobs")).json()
        assert job_page["total"] == 1

        answer_response = await client.post(
            "/api/v1/query",
            json={
                "question": "What was Southeast net revenue?",
                "document_ids": [accepted.document.id],
            },
        )
        assert answer_response.status_code == 200
        answer = Answer.model_validate(answer_response.json())
        assert answer.abstained is False
        assert "1.8" in answer.text
        citation = next(item for item in answer.citations if item.asset_url)
        all_ready_answer = await client.post(
            "/api/v1/query",
            json={"question": "Which region had $1.8M in net revenue?"},
        )
        assert all_ready_answer.status_code == 200
        conversations = (await client.get("/api/v1/conversations")).json()
        assert conversations["total"] == 2

        asset_response = await client.get(citation.asset_url or "")
        assert asset_response.status_code == 200
        assert asset_response.headers["content-type"].startswith("image/png")
        assert asset_response.content.startswith(b"\x89PNG")

        delete_response = await client.delete(f"/api/v1/documents/{accepted.document.id}")
        assert delete_response.status_code == 202
        delete_lease = coordinator.lease(kinds=(JobKind.DELETE,))
        assert delete_lease is not None
        finished, readback = runtime.deletion.execute(delete_lease)
        assert finished.status is JobStatus.SUCCEEDED
        assert readback.verified is True

        missing_document = await client.get(f"/api/v1/documents/{accepted.document.id}")
        missing_asset = await client.get(citation.asset_url or "")
        assert missing_document.status_code == 404
        assert missing_asset.status_code == 404
