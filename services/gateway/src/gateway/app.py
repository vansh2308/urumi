"""ASGI application factory: REST API, MCP proxy endpoint, health probes and lifespan.

Routes
------
* ``/health/live``, ``/health/ready``, ``/metrics``: probes and Prometheus metrics.
* ``/v1/...``: versioned REST API (OpenAPI at ``/docs`` when enabled).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from gateway import __version__
from gateway.api.errors import install_error_handlers
from gateway.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from gateway.api.routes import api_keys, auth, health, teams
from gateway.config import Settings
from gateway.container import Container, build_container
from gateway.errors import AppError
from gateway.log import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(
        level=settings.app.log_level,
        fmt=settings.app.log_format,
        service_name=settings.app.service_name,
    )
    container = build_container(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("starting", extra={"version": __version__, "env": settings.app.app_env.value})
        purge_task = _start_purge_loop(container)
        try:
            yield
        finally:
            if purge_task is not None:
                purge_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await purge_task
            await container.aclose()
            logger.info("stopped")

    docs = settings.server.docs_enabled
    app = FastAPI(
        title="Urumi Gateway",
        version=__version__,
        description="Control plane and secure entry point for the platform's MCP servers.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.container = container
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(teams.router)
    app.include_router(api_keys.router)

    # The last middleware added is the outermost: request context wraps everything, so even
    # 413 and CORS responses carry a request id and are counted.
    if settings.server.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_allowed_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Request-ID",
                "Mcp-Session-Id",
                "Mcp-Protocol-Version",
            ],
            expose_headers=[
                "X-Request-ID",
                "Retry-After",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "Mcp-Session-Id",
            ],
            allow_credentials=False,  # bearer tokens only; no cookies
            max_age=600,
        )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.server.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware, metrics=container.metrics)
    return app


def _start_purge_loop(container: Container) -> asyncio.Task[None] | None:
    interval = container.settings.retention.purge_interval_seconds
    if interval <= 0:
        return None

    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                deleted = await container.rate_limiter.purge()
                if deleted:
                    logger.info("purged expired rows", extra={"deleted": deleted})
            except AppError:
                logger.warning("purge failed; will retry")

    return asyncio.create_task(loop(), name="retention-purge")
