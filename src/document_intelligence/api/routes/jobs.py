"""Durable job inventory and explicit retry routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from document_intelligence.api.contracts import ApplicationServices, PageEnvelope
from document_intelligence.api.dependencies import get_services
from document_intelligence.models import IngestionJob, JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=PageEnvelope[IngestionJob])
def list_jobs(
    services: Annotated[ApplicationServices, Depends(get_services)],
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageEnvelope[IngestionJob]:
    page = services.list_jobs(status=job_status, limit=limit, offset=offset)
    return PageEnvelope[IngestionJob](
        items=list(page.items), total=page.total, limit=limit, offset=offset
    )


@router.post(
    "/{job_id}/retry",
    response_model=IngestionJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: str,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> IngestionJob:
    return services.retry_job(job_id)
