"""Typed HTTP client used exclusively by the Streamlit server process."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from document_intelligence.config import Settings
from document_intelligence.models import (
    Answer,
    ContentElement,
    Conversation,
    Document,
    IngestionJob,
    QueryRequest,
    SystemStatus,
    UploadReceipt,
)


class ApiClientError(RuntimeError):
    """A sanitized, presentation-safe API failure."""

    def __init__(self, message: str, *, status_code: int = 0, code: str = "request_failed"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class DocumentIntelligenceClient:
    """Small fail-closed wrapper around the versioned API."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: httpx.Timeout | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout or httpx.Timeout(30.0, connect=3.0),
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> DocumentIntelligenceClient:
        token = settings.api_token.get_secret_value() if settings.api_token else None
        return cls(settings.resolved_api_base_url, token=token)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise ApiClientError(
                "The workspace took too long to respond. Your accepted work is safe; try again.",
                code="timeout",
            ) from exc
        except httpx.ConnectError as exc:
            raise ApiClientError(
                "The document service is not reachable yet. Start the API and worker, then retry.",
                code="unavailable",
            ) from exc
        except httpx.HTTPStatusError as exc:
            message = "The request could not be completed."
            code = "request_failed"
            try:
                payload = exc.response.json()
                detail = payload.get("detail", payload)
                if isinstance(detail, dict):
                    message = str(detail.get("message", message))
                    code = str(detail.get("code", code))
                elif isinstance(detail, str):
                    message = detail
            except (ValueError, TypeError):
                pass
            raise ApiClientError(
                message,
                status_code=exc.response.status_code,
                code=code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiClientError(
                "A network error interrupted the request.", code="network"
            ) from exc

    def status(self) -> SystemStatus:
        return SystemStatus.model_validate(self._request("GET", "/api/v1/status").json())

    def list_documents(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        sort: str = "recent",
    ) -> list[Document]:
        params = {"sort": sort}
        if query:
            params["query"] = query
        if status and status != "All":
            params["status"] = status
        payload = self._request("GET", "/api/v1/documents", params=params).json()
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        return [Document.model_validate(item) for item in items]

    def upload_documents(
        self, files: Sequence[tuple[str, bytes, str]], *, idempotency_key: str
    ) -> list[UploadReceipt]:
        multipart = [
            ("files", (name, content, content_type)) for name, content, content_type in files
        ]
        response = self._request(
            "POST",
            "/api/v1/documents",
            files=multipart,
            headers={"Idempotency-Key": idempotency_key},
            timeout=60.0,
        )
        return [UploadReceipt.model_validate(item) for item in response.json()]

    def load_sample(self) -> UploadReceipt:
        return UploadReceipt.model_validate(
            self._request("POST", "/api/v1/demo/sample", timeout=60.0).json()
        )

    def get_document(self, document_id: str) -> Document:
        return Document.model_validate(
            self._request("GET", f"/api/v1/documents/{document_id}").json()
        )

    def list_elements(self, document_id: str) -> list[ContentElement]:
        payload = self._request("GET", f"/api/v1/documents/{document_id}/elements").json()
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        return [ContentElement.model_validate(item) for item in items]

    def reprocess_document(self, document_id: str) -> IngestionJob:
        return IngestionJob.model_validate(
            self._request("POST", f"/api/v1/documents/{document_id}/reprocess").json()
        )

    def delete_document(self, document_id: str) -> IngestionJob:
        return IngestionJob.model_validate(
            self._request("DELETE", f"/api/v1/documents/{document_id}").json()
        )

    def list_jobs(self) -> list[IngestionJob]:
        payload = self._request("GET", "/api/v1/jobs").json()
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        return [IngestionJob.model_validate(item) for item in items]

    def retry_job(self, job_id: str) -> IngestionJob:
        return IngestionJob.model_validate(
            self._request("POST", f"/api/v1/jobs/{job_id}/retry").json()
        )

    def list_conversations(self) -> list[Conversation]:
        payload = self._request("GET", "/api/v1/conversations").json()
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        return [Conversation.model_validate(item) for item in items]

    def ask(self, request: QueryRequest) -> Answer:
        return Answer.model_validate(
            self._request(
                "POST",
                "/api/v1/query",
                json=request.model_dump(mode="json"),
                timeout=120.0,
            ).json()
        )

    def fetch_asset(self, asset_url: str) -> bytes:
        if not asset_url.startswith("/api/v1/assets/"):
            raise ApiClientError("The evidence asset reference is invalid.", code="invalid_asset")
        return self._request("GET", asset_url, timeout=30.0).content
