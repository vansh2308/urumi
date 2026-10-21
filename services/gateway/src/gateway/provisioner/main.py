"""Process entry point: ``gateway-provisioner``.

Runs the worker pool with a tiny HTTP server for the probes (``/health/live``,
``/health/ready`` on PROVISIONER_HEALTH_PORT). It needs DB_URL (the gateway database),
UPSTREAM_CREDENTIAL_KEYS (to read the one-time admin password), PROVISIONER_MCP_ADMIN_DSN and
the STORES_* / PROVISIONER_* settings; no AUTH_* secret, it serves no API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gateway.__main__ import report_invalid_settings
from gateway.config import (
    AppSettings,
    DatabaseSettings,
    ProvisionerSettings,
    StoreSettings,
    UpstreamSettings,
)
from gateway.db.engine import Database, create_engine, create_session_factory
from gateway.errors import AppError
from gateway.hosted.kube import KubeApi, build_kube_http_client
from gateway.log import configure_logging
from gateway.provisioner.engine import Provisioner
from gateway.provisioner.helm import HelmRunner
from gateway.provisioner.mcp_db import PostgresMcpDatabase
from gateway.services.audit import AuditLog
from gateway.upstream.credentials import CredentialCipher

logger = logging.getLogger(__name__)


def create_app(
    app: AppSettings,
    database: DatabaseSettings,
    settings: ProvisionerSettings,
    stores: StoreSettings,
    upstream: UpstreamSettings,
) -> Starlette:
    configure_logging(level=app.log_level, fmt=app.log_format, service_name="gateway-provisioner")
    engine = create_engine(database, application_name="gateway-provisioner")
    db = Database(create_session_factory(engine))
    kube_http = build_kube_http_client(settings)
    provisioner = Provisioner(
        db,
        kube=KubeApi(kube_http, settings),
        helm=HelmRunner(
            binary=settings.helm_binary,
            chart=settings.chart_path,
            timeout_seconds=settings.helm_timeout_seconds,
        ),
        mcp_db=(
            PostgresMcpDatabase(settings.mcp_admin_dsn.get_secret_value())
            if settings.mcp_admin_dsn
            else None
        ),
        cipher=CredentialCipher(upstream.credential_keys),
        audit=AuditLog(db),
        settings=settings,
        stores=stores,
        upstream=upstream,
    )
    stop = asyncio.Event()
    state: dict[str, asyncio.Task[None]] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if not stores.enabled:
            logger.warning("STORES_ENABLED is false; the provisioner will still process rows")
        state["run"] = asyncio.create_task(provisioner.run(stop), name="provisioner")
        try:
            yield
        finally:
            # Workers stop between steps; a lease left behind expires and another replica (or
            # the next start) resumes from the saved step.
            stop.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state["run"], 20)
            await kube_http.aclose()
            await engine.dispose()

    async def live(_: Request) -> JSONResponse:
        task = state.get("run")
        alive = task is not None and not task.done()
        return JSONResponse({"status": "ok" if alive else "stopped"}, 200 if alive else 503)

    async def ready(_: Request) -> JSONResponse:
        try:
            await db.check_ready()
        except AppError:
            return JSONResponse({"status": "unavailable", "checks": {"database": "fail"}}, 503)
        return JSONResponse({"status": "ready", "worker_id": provisioner.worker_id})

    return Starlette(
        routes=[
            Route("/health/live", live, methods=["GET"]),
            Route("/health/ready", ready, methods=["GET"]),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    try:
        app = AppSettings(service_name="gateway-provisioner")
        database = DatabaseSettings()
        settings = ProvisionerSettings()
        stores = StoreSettings()
        upstream = UpstreamSettings()
    except Exception as exc:
        report_invalid_settings(exc)
    config = uvicorn.Config(
        create_app(app, database, settings, stores, upstream),
        host=settings.health_host,
        port=settings.health_port,
        lifespan="on",
        log_config=None,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=20,
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
