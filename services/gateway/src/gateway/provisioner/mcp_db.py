"""One Postgres role + schema per store for its MCP server's operations records.

The MCP server keeps idempotency records (with tool results, i.e. order data) in Postgres.
Sharing one database user would let store A's MCP pod read store B's records, so each store
gets ``role store_<id>`` owning ``schema store_<id>``, with ``search_path`` pinned on the role:
its migrations and queries land in its own schema and it has no rights on any other.
"""

from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

_NAME_RE = re.compile(r"^store_[0-9a-f]{32}$")
_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


class McpDatabase(Protocol):
    async def ensure(self, name: str, password: str) -> None: ...

    async def drop(self, name: str) -> None: ...

    def dsn_for(self, name: str, password: str) -> str: ...


def _check(name: str, password: str | None = None) -> None:
    # Identifiers and the password are interpolated into DDL (no bind parameters there), so
    # both are restricted to characters that need no quoting at all.
    if not _NAME_RE.match(name):
        raise ValueError("invalid store database name")
    if password is not None and not _PASSWORD_RE.match(password):
        raise ValueError("invalid store database password")


class PostgresMcpDatabase:
    def __init__(self, admin_dsn: str) -> None:
        self._admin = admin_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    def dsn_for(self, name: str, password: str) -> str:
        parts = urlsplit(self._admin)
        host = parts.hostname or "localhost"
        netloc = f"{name}:{quote(password, safe='')}@{host}" + (
            f":{parts.port}" if parts.port else ""
        )
        return urlunsplit(parts._replace(scheme="postgresql+asyncpg", netloc=netloc))

    async def ensure(self, name: str, password: str) -> None:
        """Create (or bring back in line) the role and its schema. Idempotent."""
        _check(name, password)
        conn = await asyncpg.connect(self._admin, timeout=10)
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", name)
            verb = "ALTER" if exists else "CREATE"
            await conn.execute(
                f"{verb} ROLE {name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                f"CONNECTION LIMIT 20 PASSWORD '{password}'"
            )
            # CREATE SCHEMA ... AUTHORIZATION needs membership of the role (Postgres 16+ does
            # not grant it implicitly to the creating role).
            await conn.execute(f"GRANT {name} TO CURRENT_USER")
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {name} AUTHORIZATION {name}")
            await conn.execute(f"ALTER ROLE {name} SET search_path = {name}")
        finally:
            await conn.close()

    async def drop(self, name: str) -> None:
        _check(name)
        conn = await asyncpg.connect(self._admin, timeout=10)
        try:
            await conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
            await conn.execute(f"DROP ROLE IF EXISTS {name}")
        finally:
            await conn.close()
