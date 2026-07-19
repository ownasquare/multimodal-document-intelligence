"""Lazy service resolution and optional bearer authentication."""

from __future__ import annotations

import inspect
import secrets
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Annotated, cast

from fastapi import Header, Request

from document_intelligence.api.contracts import ApplicationServices
from document_intelligence.api.errors import ApiError
from document_intelligence.config import Settings

ServiceSource = ApplicationServices | Callable[[], ApplicationServices]


class ServiceProvider:
    """Resolve a service factory once, only when a service-backed route is called."""

    def __init__(self, source: ServiceSource) -> None:
        self._source = source
        self._instance: ApplicationServices | None = (
            source if isinstance(source, ApplicationServices) else None
        )
        self._lock = Lock()

    def get(self) -> ApplicationServices:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                factory = cast(Callable[[], ApplicationServices], self._source)
                instance = factory()
                if not isinstance(instance, ApplicationServices):
                    raise TypeError("service factory did not return ApplicationServices")
                self._instance = instance
        return self._instance

    async def close(self) -> None:
        if self._instance is None:
            return
        close = getattr(self._instance, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await cast(Awaitable[object], result)


def get_services(request: Request) -> ApplicationServices:
    provider = cast(ServiceProvider, request.app.state.service_provider)
    return provider.get()


def get_api_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def require_bearer_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_api_settings(request)
    expected = settings.api_token
    if expected is None:
        return
    scheme, separator, value = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not value
        or not secrets.compare_digest(value, expected.get_secret_value())
    ):
        raise ApiError(
            "authentication_required",
            "A valid bearer token is required.",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
