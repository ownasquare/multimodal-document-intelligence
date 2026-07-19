"""Saved conversation inventory routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from document_intelligence.api.contracts import ApplicationServices, PageEnvelope
from document_intelligence.api.dependencies import get_services
from document_intelligence.models import Conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=PageEnvelope[Conversation])
def list_conversations(
    services: Annotated[ApplicationServices, Depends(get_services)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageEnvelope[Conversation]:
    page = services.list_conversations(limit=limit, offset=offset)
    return PageEnvelope[Conversation](
        items=list(page.items), total=page.total, limit=limit, offset=offset
    )
