"""ASGI application factory: REST API, health probes and lifespan.

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

from agent import __version__
from agent.api.errors import install_error_handlers
from agent.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from agent.api.routes import chat, conversations, health, tools, usage
from agent.config import Settings
from agent.container import Container, build_container
from agent.log import configure_logging

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
        try:
            yield
        finally:
            await _cancel_runs(container)
            await container.aclose()
            logger.info("stopped")

    docs = settings.server.docs_enabled
    app = FastAPI(
        title="Urumi Agent",
        version=__version__,
        description="LLM agent whose tools are discovered from and invoked through the gateway.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.container = container
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(tools.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(usage.router)

    # The last middleware added is the outermost: request context wraps everything, so even
    # 413 and CORS responses carry a request id and are counted.
    if settings.server.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_allowed_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
            allow_credentials=False,  # bearer tokens only; no cookies
            max_age=600,
        )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.server.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware, metrics=container.metrics)
    return app


async def _cancel_runs(container: Container) -> None:
    """Streams still open after uvicorn's drain period: cancel their runs and give them a moment
    to record ``cancelled`` and release their conversations before the pools close."""
    tasks = list(container.run_tasks)
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.wait(tasks, timeout=5)
    logger.info("cancelled in-flight runs", extra={"count": len(tasks)})
