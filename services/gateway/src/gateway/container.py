"""Composition root: builds the object graph once per process and owns resource lifetimes.

Nothing here is a global; ``create_app`` builds a container and stores it on the application so
request dependencies can reach it. Constructing the engine and HTTP client is synchronous and
opens no connections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from gateway.config import Settings
from gateway.db.engine import Database, create_engine, create_session_factory
from gateway.hosted.kube import KubeApi, build_kube_http_client
from gateway.hosted.service import HostedDeployer, HostedServerService
from gateway.metrics import Metrics
from gateway.security import PasswordService, TokenHasher
from gateway.services.activity import ActivityService, RetentionService
from gateway.services.api_keys import ApiKeyService
from gateway.services.audit import AuditLog
from gateway.services.auth import AuthService
from gateway.services.connectors import ConnectorService
from gateway.services.invitations import InvitationService
from gateway.services.rate_limit import RateLimiter
from gateway.services.teams import TeamService
from gateway.services.tools import ToolService
from gateway.upstream.client import McpUpstreamClient, build_upstream_http_client
from gateway.upstream.credentials import CredentialCipher

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    http_client: httpx.AsyncClient
    kube_http_client: httpx.AsyncClient | None
    db: Database
    metrics: Metrics
    tokens: TokenHasher
    audit: AuditLog
    rate_limiter: RateLimiter
    auth: AuthService
    teams: TeamService
    invitations: InvitationService
    api_keys: ApiKeyService
    upstream: McpUpstreamClient
    connectors: ConnectorService
    hosted: HostedServerService
    tools: ToolService
    activity: ActivityService
    retention: RetentionService

    async def aclose(self) -> None:
        """Release pooled connections. Called once, after in-flight requests have drained."""
        try:
            await self.http_client.aclose()
            if self.kube_http_client is not None:
                await self.kube_http_client.aclose()
        finally:
            await self.engine.dispose()
        logger.info("resources released")


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.database, application_name=settings.app.service_name)
    db = Database(create_session_factory(engine))
    metrics = Metrics()
    tokens = TokenHasher(settings.auth.token_pepper)
    audit = AuditLog(db)
    auth = AuthService(
        db, passwords=PasswordService(), tokens=tokens, audit=audit, settings=settings.auth
    )
    teams = TeamService(
        db,
        audit=audit,
        api_key_settings=settings.api_keys,
        rate_limit_settings=settings.rate_limit,
    )
    http_client = build_upstream_http_client(settings.upstream)
    upstream = McpUpstreamClient(http_client, settings=settings.upstream, metrics=metrics)
    cipher = CredentialCipher(settings.upstream.credential_keys)
    kube_http_client: httpx.AsyncClient | None = None
    deployer: HostedDeployer | None = None
    if settings.hosted.enabled:
        kube_http_client = build_kube_http_client(settings.hosted)
        deployer = HostedDeployer(KubeApi(kube_http_client, settings.hosted), settings.hosted)
    connectors = ConnectorService(
        db,
        upstream=upstream,
        teams=teams,
        audit=audit,
        settings=settings.upstream,
        cipher=cipher,
        deployer=deployer,
    )
    rate_limiter = RateLimiter(db, metrics, enabled=settings.rate_limit.enabled)
    return Container(
        settings=settings,
        engine=engine,
        http_client=http_client,
        kube_http_client=kube_http_client,
        db=db,
        metrics=metrics,
        tokens=tokens,
        audit=audit,
        rate_limiter=rate_limiter,
        auth=auth,
        teams=teams,
        invitations=InvitationService(
            db, tokens=tokens, teams=teams, auth=auth, audit=audit, settings=settings.auth
        ),
        api_keys=ApiKeyService(db, tokens=tokens, audit=audit, settings=settings.api_keys),
        upstream=upstream,
        connectors=connectors,
        hosted=HostedServerService(
            db,
            deployer=deployer,
            upstream=upstream,
            audit=audit,
            cipher=cipher,
            settings=settings.hosted,
            upstream_settings=settings.upstream,
        ),
        tools=ToolService(
            db,
            teams=teams,
            connectors=connectors,
            upstream=upstream,
            audit=audit,
            metrics=metrics,
        ),
        activity=ActivityService(db),
        retention=RetentionService(db, rate_limiter=rate_limiter, settings=settings.retention),
    )
