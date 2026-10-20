"""ASGI application: MCP Streamable HTTP endpoint plus health and readiness probes.

Routes
------
* ``POST/GET/DELETE {MCP_PATH}`` (default ``/mcp``): MCP Streamable HTTP transport.
* ``GET /healthz``: liveness. The process is up and the event loop responds; no dependencies.
* ``GET /readyz``: readiness. PostgreSQL is reachable and migrated (and optionally WooCommerce
  answers). Returns 503 otherwise so Kubernetes stops routing traffic to this replica.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from woocommerce_mcp import __version__
from woocommerce_mcp.config import Settings
from woocommerce_mcp.container import Container, build_container
from woocommerce_mcp.errors import AppError
from woocommerce_mcp.log import configure_logging
from woocommerce_mcp.mcp_server import build_mcp_server

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings()
    configure_logging(
        level=settings.app.log_level,
        fmt=settings.app.log_format,
        service_name=settings.app.service_name,
    )
    container = build_container(settings)
    mcp = build_mcp_server(
        settings.server,
        products=container.products,
        orders=container.orders,
        store_name=settings.app.store_name,
    )
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        logger.info(
            "starting",
            extra={
                "version": __version__,
                "env": settings.app.app_env.value,
                "woocommerce_auth": settings.woocommerce.auth_method.value,
                "store_name": settings.app.store_name,
                "woocommerce_connect_url": settings.woocommerce.connect_url,
                "stateless_http": settings.server.stateless_http,
            },
        )
        purge_task = _start_purge_loop(container)
        try:
            # The session manager owns the task group running MCP requests; leaving this block
            # (on SIGTERM, after uvicorn stops accepting and drains) cancels any stragglers.
            async with mcp.session_manager.run():
                yield
        finally:
            if purge_task is not None:
                purge_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await purge_task
            await container.aclose()
            logger.info("stopped")

    return Starlette(
        routes=[
            Route("/healthz", _liveness, methods=["GET"]),
            Route("/readyz", _readiness(container), methods=["GET"]),
            # Mounted last: the MCP app serves only its own path and 404s anything else.
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


async def _liveness(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _readiness(container: Container) -> Callable[[Request], Awaitable[JSONResponse]]:
    server = container.settings.server

    async def check(name: str, probe: Callable[[], Awaitable[None]]) -> tuple[str, bool]:
        try:
            async with asyncio.timeout(server.readiness_timeout_seconds):
                await probe()
        except (AppError, TimeoutError):
            logger.warning("readiness check failed", extra={"check": name})
            return name, False
        except Exception:
            logger.exception("readiness check errored", extra={"check": name})
            return name, False
        return name, True

    async def readiness(_: Request) -> JSONResponse:
        probes: list[Awaitable[tuple[str, bool]]] = [
            check("database", container.operation_store.check_ready)
        ]
        if server.readiness_checks_woocommerce:
            probes.append(check("woocommerce", container.woocommerce.ping))
        results = dict(await asyncio.gather(*probes))
        ready = all(results.values())
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "checks": {name: "ok" if ok else "fail" for name, ok in results.items()},
            },
            status_code=200 if ready else 503,
        )

    return readiness


def _start_purge_loop(container: Container) -> asyncio.Task[None] | None:
    interval = container.settings.operations.purge_interval_seconds
    if interval <= 0:
        return None

    async def loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                deleted = await container.operations.purge_expired()
                if deleted:
                    logger.info("purged expired operation records", extra={"deleted": deleted})
            except AppError:
                logger.warning("operation purge failed; will retry")

    return asyncio.create_task(loop(), name="operations-purge")
