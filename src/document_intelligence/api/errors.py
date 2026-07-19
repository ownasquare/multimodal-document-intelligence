"""Stable error envelope and exception translation for the HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from document_intelligence.repository import (
    InvalidStateTransitionError,
    LeaseLostError,
    PersistenceConflictError,
    RecordNotFoundError,
)


class ApiError(RuntimeError):
    """Expected application error safe to serialize to an API caller."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        headers: Mapping[str, str] | None = None,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code
        self.retryable = retryable
        self.headers = dict(headers or {})
        self.details = details


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unavailable"))


def error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    headers: Mapping[str, str] | None = None,
    details: object | None = None,
) -> JSONResponse:
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id(request),
        "retryable": retryable,
    }
    if details is not None:
        detail["details"] = details
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"detail": detail}),
        headers=dict(headers or {}),
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(
            request,
            code=error.code,
            message=error.public_message,
            status_code=error.status_code,
            retryable=error.retryable,
            headers=error.headers,
            details=error.details,
        )

    @app.exception_handler(RecordNotFoundError)
    async def handle_not_found(request: Request, error: RecordNotFoundError) -> JSONResponse:
        del error
        return error_response(
            request,
            code="resource_not_found",
            message="The requested resource was not found.",
            status_code=404,
        )

    @app.exception_handler(PersistenceConflictError)
    async def handle_conflict(request: Request, error: PersistenceConflictError) -> JSONResponse:
        del error
        return error_response(
            request,
            code="persistence_conflict",
            message="The request conflicts with existing workspace state.",
            status_code=409,
        )

    @app.exception_handler(InvalidStateTransitionError)
    async def handle_invalid_state(
        request: Request, error: InvalidStateTransitionError
    ) -> JSONResponse:
        del error
        return error_response(
            request,
            code="invalid_state",
            message="This action is not available in the resource's current state.",
            status_code=409,
        )

    @app.exception_handler(LeaseLostError)
    async def handle_lease_lost(request: Request, error: LeaseLostError) -> JSONResponse:
        del error
        return error_response(
            request,
            code="lease_lost",
            message="This work item is already owned or has expired.",
            status_code=409,
            retryable=True,
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, error: ValueError) -> JSONResponse:
        del error
        return error_response(
            request,
            code="invalid_request",
            message="The request could not be applied to the selected resource.",
            status_code=422,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        safe_details = [
            {"location": list(item["loc"]), "type": item["type"]} for item in error.errors()
        ]
        return error_response(
            request,
            code="validation_error",
            message="The request did not match the expected format.",
            status_code=422,
            details=safe_details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "http_error"
        message = "The requested resource was not found."
        if error.status_code != 404:
            message = "The request could not be completed."
        return error_response(
            request,
            code=code,
            message=message,
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, error: Exception) -> JSONResponse:
        del error
        return error_response(
            request,
            code="internal_error",
            message="The service could not complete this request.",
            status_code=500,
            retryable=True,
        )
