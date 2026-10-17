"""Liveness, readiness and Prometheus metrics. Unauthenticated and not rate limited."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from agent.api.deps import ContainerDep
from agent.errors import AppError
from agent.metrics import CONTENT_TYPE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """The process is up and the event loop responds. No dependencies are checked."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(container: ContainerDep) -> JSONResponse:
    """PostgreSQL is reachable and migrated. The gateway and the model provider are deliberately
    not checked: their outages surface per request instead of taking every replica out."""
    ok = True
    try:
        async with asyncio.timeout(container.settings.server.readiness_timeout_seconds):
            await container.db.check_ready()
    except (AppError, TimeoutError):
        logger.warning("readiness check failed", extra={"check": "database"})
        ok = False
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": {"database": "ok" if ok else "fail"}},
        status_code=200 if ok else 503,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(container: ContainerDep) -> Response:
    if not container.settings.server.metrics_enabled:
        return Response(status_code=404)
    return Response(container.metrics.render(), media_type=CONTENT_TYPE)
