"""Scoped grounded-answer route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from document_intelligence.api.contracts import ApplicationServices
from document_intelligence.api.dependencies import get_api_settings, get_services
from document_intelligence.api.errors import ApiError
from document_intelligence.config import Settings
from document_intelligence.models import Answer, QueryRequest

router = APIRouter(tags=["queries"])


@router.post("/query", response_model=Answer)
def answer_query(
    request: QueryRequest,
    services: Annotated[ApplicationServices, Depends(get_services)],
    settings: Annotated[Settings, Depends(get_api_settings)],
) -> Answer:
    if len(request.question) > settings.max_question_characters:
        raise ApiError(
            "question_too_long",
            "The question exceeds the configured length limit.",
            status_code=422,
        )
    if request.top_k > settings.retrieval_top_k:
        raise ApiError(
            "retrieval_limit_exceeded",
            "The requested evidence count exceeds the configured limit.",
            status_code=422,
        )
    return services.answer(request)
