"""Composition root: builds the object graph once per process and owns resource lifetimes.

Nothing here is a global; ``create_app`` builds a container and stores it on the application so
request dependencies can reach it. Constructing the engine and HTTP client is synchronous and
opens no connections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from gateway.config import Settings
from gateway.db.engine import Database, create_engine, create_session_factory
from gateway.metrics import Metrics
from gateway.security import PasswordService, TokenHasher
from gateway.services.audit import AuditLog
from gateway.services.auth import AuthService
from gateway.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    db: Database
    metrics: Metrics
    tokens: TokenHasher
    audit: AuditLog
    rate_limiter: RateLimiter
    auth: AuthService

    async def aclose(self) -> None:
        """Release pooled connections. Called once, after in-flight requests have drained."""
        await self.engine.dispose()
        logger.info("resources released")


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.database, application_name=settings.app.service_name)
    db = Database(create_session_factory(engine))
    metrics = Metrics()
    tokens = TokenHasher(settings.auth.token_pepper)
    audit = AuditLog(db)
    return Container(
        settings=settings,
        engine=engine,
        db=db,
        metrics=metrics,
        tokens=tokens,
        audit=audit,
        rate_limiter=RateLimiter(db, metrics, enabled=settings.rate_limit.enabled),
        auth=AuthService(
            db,
            passwords=PasswordService(),
            tokens=tokens,
            audit=audit,
            settings=settings.auth,
        ),
    )
