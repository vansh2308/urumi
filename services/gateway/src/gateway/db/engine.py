"""Engine, session factory and the transaction helper every service uses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gateway.config import DatabaseSettings
from gateway.db.models import SCHEMA_REVISION, VERSION_TABLE
from gateway.errors import PersistenceUnavailableError


def create_engine(settings: DatabaseSettings, *, application_name: str) -> AsyncEngine:
    server_settings = {"application_name": application_name}
    if settings.statement_timeout_ms:
        server_settings["statement_timeout"] = str(settings.statement_timeout_ms)
    return create_async_engine(
        settings.url.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_recycle=settings.pool_recycle_seconds,
        pool_pre_ping=True,
        echo=settings.echo,
        hide_parameters=True,
        connect_args={"server_settings": server_settings, "timeout": 5},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class Database:
    """Hands out short transactions. Connection-level failures become a retryable 503."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._session_factory() as session, session.begin():
                yield session
        except (OperationalError, InterfaceError, OSError, TimeoutError) as exc:
            raise PersistenceUnavailableError("The database is unavailable.") from exc
        except DBAPIError as exc:
            if exc.connection_invalidated:
                raise PersistenceUnavailableError("The database is unavailable.") from exc
            raise

    async def check_ready(self) -> None:
        """Verify connectivity and that migrations are at the revision this build expects."""
        async with self.transaction() as session:
            revision = await session.scalar(
                text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608 - constant
            )
        if revision != SCHEMA_REVISION:
            raise PersistenceUnavailableError("Database schema is not at the expected revision.")
