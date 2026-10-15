"""Engine and session-factory construction."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from woocommerce_mcp.config import DatabaseSettings


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
