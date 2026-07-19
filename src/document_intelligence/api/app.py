"""Dependency-injected FastAPI application composition."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from document_intelligence.api.contracts import ApplicationServices
from document_intelligence.api.dependencies import (
    ServiceProvider,
    ServiceSource,
    require_bearer_token,
)
from document_intelligence.api.errors import install_exception_handlers
from document_intelligence.api.routes import (
    assets,
    conversations,
    demo,
    documents,
    health,
    jobs,
    queries,
)
from document_intelligence.config import Settings

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _default_service_factory(settings: Settings) -> ServiceSource:
    """Import the composition root only when a service-backed route is first called."""

    def build() -> ApplicationServices:
        from document_intelligence.container import create_services

        return create_services(settings)

    return build


def _trusted_hosts(settings: Settings) -> list[str]:
    try:
        if ip_address(settings.api_host).is_unspecified:
            return ["*"]
    except ValueError:
        pass
    hosts = {"testserver", "localhost", "127.0.0.1", "::1", settings.api_host}
    configured_host = urlsplit(settings.resolved_api_base_url).hostname
    if configured_host:
        hosts.add(configured_host)
    return sorted(host for host in hosts if host)


def create_app(
    container_or_factory: ServiceSource | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Create an app without opening durable production state at import time."""

    configured = settings or Settings()
    provider = ServiceProvider(container_or_factory or _default_service_factory(configured))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await provider.close()

    app = FastAPI(
        title="Multimodal Document Intelligence API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.service_provider = provider
    install_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{configured.ui_port}",
            f"http://localhost:{configured.ui_port}",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts(configured))

    @app.middleware("http")
    async def attach_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        requested = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            requested if _REQUEST_ID_PATTERN.fullmatch(requested) else uuid4().hex
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    auth = [Depends(require_bearer_token)]
    app.include_router(health.public_router)
    app.include_router(health.api_router, prefix="/api/v1", dependencies=auth)
    app.include_router(documents.router, prefix="/api/v1", dependencies=auth)
    app.include_router(jobs.router, prefix="/api/v1", dependencies=auth)
    app.include_router(conversations.router, prefix="/api/v1", dependencies=auth)
    app.include_router(queries.router, prefix="/api/v1", dependencies=auth)
    app.include_router(assets.router, prefix="/api/v1", dependencies=auth)
    app.include_router(demo.router, prefix="/api/v1", dependencies=auth)
    return app
