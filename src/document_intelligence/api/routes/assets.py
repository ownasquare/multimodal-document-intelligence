"""Private, allowlisted derived-image streaming route."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from document_intelligence.api.contracts import ApplicationServices
from document_intelligence.api.dependencies import get_services
from document_intelligence.api.errors import ApiError

router = APIRouter(prefix="/assets", tags=["assets"])
_ALLOWED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


def _validated_asset_key(value: str) -> str:
    if not value or len(value) > 500 or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ApiError(
            "invalid_asset",
            "The evidence asset reference is invalid.",
            status_code=400,
        )
    parsed = PurePosixPath(value)
    if parsed.as_posix() != value or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ApiError(
            "invalid_asset",
            "The evidence asset reference is invalid.",
            status_code=400,
        )
    if parsed.suffix.lower() == ".pdf":
        raise ApiError(
            "original_document_private",
            "Original documents are not available through the asset route.",
            status_code=403,
        )
    return value


@router.get("/{asset_key:path}")
def stream_asset(
    asset_key: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> StreamingResponse:
    safe_key = _validated_asset_key(asset_key)
    payload = services.open_asset(safe_key)
    if payload is None:
        raise ApiError(
            "asset_not_found",
            "The requested evidence asset was not found.",
            status_code=404,
        )
    if payload.media_type not in _ALLOWED_MEDIA_TYPES:
        payload.stream.close()
        raise ApiError(
            "invalid_asset_type",
            "The stored evidence asset type is not allowed.",
            status_code=409,
        )
    headers = {
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    }
    if payload.content_length is not None:
        headers["Content-Length"] = str(payload.content_length)
    if payload.etag is not None:
        headers["ETag"] = payload.etag
    return StreamingResponse(
        payload.stream,
        media_type=payload.media_type,
        headers=headers,
        background=BackgroundTask(payload.stream.close),
    )
