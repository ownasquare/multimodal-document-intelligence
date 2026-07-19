"""Credential-free deterministic sample onboarding route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from document_intelligence.api.contracts import ApplicationServices
from document_intelligence.api.dependencies import get_api_settings, get_services
from document_intelligence.api.errors import ApiError
from document_intelligence.config import Settings
from document_intelligence.models import UploadReceipt

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post(
    "/sample",
    response_model=UploadReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
def load_sample(
    services: Annotated[ApplicationServices, Depends(get_services)],
    settings: Annotated[Settings, Depends(get_api_settings)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=200),
    ] = "demo-sample-v1",
) -> UploadReceipt:
    if not settings.demo_mode:
        raise ApiError(
            "demo_disabled",
            "The sample workspace is disabled.",
            status_code=404,
        )
    return services.load_sample(idempotency_key=idempotency_key)
