"""Composition root: builds the object graph once per process and owns resource lifetimes.

Nothing here is a global; ``create_app`` builds a container and stores it on the application so
request dependencies can reach it. Constructing the engine and HTTP clients is synchronous and
opens no connections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from agent.config import Settings
from agent.db.engine import Database, create_engine, create_session_factory
from agent.metrics import Metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    db: Database
    metrics: Metrics

    async def aclose(self) -> None:
        """Release pooled connections. Called once, after in-flight requests have drained."""
        await self.engine.dispose()
        logger.info("resources released")


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.database, application_name=settings.app.service_name)
    return Container(
        settings=settings,
        engine=engine,
        db=Database(create_session_factory(engine)),
        metrics=Metrics(),
    )
