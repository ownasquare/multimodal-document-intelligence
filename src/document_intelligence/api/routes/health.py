"""Liveness, readiness, and product-status routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from document_intelligence.api.contracts import ApplicationServices, ReadinessReport
from document_intelligence.api.dependencies import get_services
from document_intelligence.models import SystemStatus

public_router = APIRouter(tags=["health"])
api_router = APIRouter(tags=["status"])


@public_router.get("/health/live")
def liveness() -> dict[str, str]:
    """Prove only that the HTTP process can serve requests."""

    return {"status": "alive"}


@public_router.get(
    "/health/ready",
    response_model=ReadinessReport,
    responses={503: {"model": ReadinessReport}},
)
def readiness(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> ReadinessReport | JSONResponse:
    """Check local dependencies without making a provider inference call."""

    report = services.readiness()
    if report.status == "ready":
        return report
    return JSONResponse(status_code=503, content=report.model_dump(mode="json"))


@api_router.get("/status", response_model=SystemStatus)
def product_status(
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> SystemStatus:
    return services.status()
