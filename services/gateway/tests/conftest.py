"""Shared fixtures.

Tests never read the service's ``.env`` and never reach a real database or MCP server:

* PostgreSQL is a throwaway database created for the test session on the server named by
  ``TEST_DATABASE_URL`` (a local container, see the README), migrated, and dropped afterwards.
  Tests that need it are skipped when the variable is unset and refuse a non-local host.
* The upstream is a small FastMCP server started on a free localhost port for the session.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from gateway.app import create_app
from gateway.config import (
    ApiKeySettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    OAuthSettings,
    RateLimitSettings,
    RetentionSettings,
    ServerSettings,
    Settings,
    UpstreamSettings,
)

SERVICE_DIR = Path(__file__).resolve().parents[1]
BASE_URL = "http://gateway.test"
PASSWORD = "correct horse battery staple"
_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


# --- PostgreSQL -----------------------------------------------------------------------------------


def _admin_dsn() -> str:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL is not set; see 'Tests' in the README")
    parts = urlsplit(raw.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parts.hostname not in _LOCAL_DB_HOSTS:
        pytest.fail("TEST_DATABASE_URL must point at a local, disposable PostgreSQL server")
    return urlunsplit(parts)


async def _admin_exec(dsn: str, sql: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_dsn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A freshly migrated database for this test session (plain ``postgresql://`` DSN)."""
    admin = _admin_dsn()
    name = f"gateway_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin_exec(admin, f'CREATE DATABASE "{name}"'))
    dsn = urlunsplit(urlsplit(admin)._replace(path=f"/{name}"))
    try:
        _migrate(dsn, tmp_path_factory.mktemp("alembic"))
        yield dsn
    finally:
        asyncio.run(_admin_exec(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _migrate(dsn: str, workdir: Path) -> None:
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_DIR / "alembic"))
    previous_cwd, previous_url = Path.cwd(), os.environ.get("DB_URL")
    # alembic/env.py reads DB_URL; running from an empty directory keeps a local .env out of it.
    os.environ["DB_URL"] = dsn
    os.chdir(workdir)
    try:
        command.upgrade(config, "head")
    finally:
        os.chdir(previous_cwd)
        if previous_url is None:
            os.environ.pop("DB_URL", None)
        else:
            os.environ["DB_URL"] = previous_url


async def sql(dsn: str, query: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(await conn.fetch(query, *args))
    finally:
        await conn.close()


# --- fake upstream MCP server ---------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _demo_server(port: int) -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP(
        "demo", host="127.0.0.1", port=port, stateless_http=True, json_response=True
    )

    @mcp.tool()
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @mcp.tool()
    def fail() -> str:
        """Always fails."""
        raise ToolError("boom")

    @mcp.tool()
    def seen_token(ctx: Context[Any, Any, Any]) -> str:
        """The X-Upstream-Token header this server received (to test connector credentials)."""
        request = ctx.request_context.request
        return request.headers.get("x-upstream-token", "") if request is not None else ""

    return mcp


@pytest.fixture(scope="session")
def upstream_url() -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            _demo_server(port).streamable_http_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            pytest.fail("demo MCP server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def closed_port_url() -> str:
    """A localhost URL nothing listens on."""
    return f"http://127.0.0.1:{_free_port()}/mcp"


# --- application ----------------------------------------------------------------------------------


def make_settings(dsn: str) -> Settings:
    return Settings(
        app=AppSettings(_env_file=None, log_level="WARNING", log_format="console"),
        server=ServerSettings(_env_file=None),
        database=DatabaseSettings(_env_file=None, url=dsn, pool_size=5),
        auth=AuthSettings(_env_file=None, token_pepper="test-pepper-" + "x" * 40),
        api_keys=ApiKeySettings(_env_file=None),
        rate_limit=RateLimitSettings(_env_file=None, auth_per_minute=1000),
        upstream=UpstreamSettings(
            _env_file=None,
            allowed_hosts=["127.0.0.1:*"],
            allow_insecure_http=True,
            default_call_timeout_seconds=5,
            tool_cache_ttl_seconds=0,
            credential_keys=[Fernet.generate_key().decode()],
        ),
        retention=RetentionSettings(_env_file=None, purge_interval_seconds=0),
        oauth=OAuthSettings(
            _env_file=None,
            enabled=True,
            issuer_url=BASE_URL,
            consent_url="http://app.test/oauth/consent",
            allow_http_issuer=True,
            requests_per_minute=1000,
        ),
    )


AppClient = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def app_client(database_dsn: str) -> AppClient:
    """Opens the app (lifespan included) inside the test's own task, as anyio requires."""

    @asynccontextmanager
    async def open_client() -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(make_settings(database_dsn))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
        ):
            yield client

    return open_client


# --- API helpers ----------------------------------------------------------------------------------


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class User:
    id: str
    email: str
    token: str

    @property
    def auth(self) -> dict[str, str]:
        return bearer(self.token)


async def signup(
    client: httpx.AsyncClient, name: str, *, invitation_token: str | None = None
) -> User:
    email = f"{name}-{uuid.uuid4().hex[:8]}@example.com"
    body: dict[str, Any] = {"email": email, "password": PASSWORD, "display_name": name}
    if invitation_token:
        body["invitation_token"] = invitation_token
    registered = await client.post("/v1/auth/register", json=body)
    assert registered.status_code == 201, registered.text
    login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return User(id=registered.json()["id"], email=email, token=login.json()["access_token"])


async def create_key(
    client: httpx.AsyncClient, user: User, team_id: str, **body: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/v1/teams/{team_id}/api-keys", headers=user.auth, json={"name": "test"} | body
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@dataclass(frozen=True)
class World:
    """A team with an admin, a member, and the demo connector enabled for the team."""

    admin: User
    member: User
    team_id: str
    connector_id: str
    slug: str


async def build_world(
    client: httpx.AsyncClient, dsn: str, upstream_url: str, *, member_tools: list[str]
) -> World:
    admin = await signup(client, "admin")
    await sql(dsn, "UPDATE users SET is_platform_admin = true WHERE id = $1", uuid.UUID(admin.id))
    team = await client.post("/v1/teams", headers=admin.auth, json={"name": "Acme"})
    team_id = team.json()["id"]

    slug = f"demo-{uuid.uuid4().hex[:6]}"
    connector = await client.post(
        "/v1/connectors",
        headers=admin.auth,
        json={"slug": slug, "display_name": "Demo", "url": upstream_url},
    )
    assert connector.status_code == 201, connector.text
    policy = await client.put(
        f"/v1/teams/{team_id}/connectors/{slug}",
        headers=admin.auth,
        json={"tools": ["echo", "add", "fail"]},
    )
    assert policy.status_code == 200, policy.text

    invite = await client.post(
        f"/v1/teams/{team_id}/invitations",
        headers=admin.auth,
        json={"email": f"member-{uuid.uuid4().hex[:8]}@example.com"},
    )
    invitation = invite.json()
    member_email = invitation["email"]
    registered = await client.post(
        "/v1/auth/register",
        json={
            "email": member_email,
            "password": PASSWORD,
            "display_name": "member",
            "invitation_token": invitation["token"],
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post("/v1/auth/login", json={"email": member_email, "password": PASSWORD})
    member = User(
        id=registered.json()["id"], email=member_email, token=login.json()["access_token"]
    )
    grant = await client.put(
        f"/v1/teams/{team_id}/members/{member.id}/grants/{slug}",
        headers=admin.auth,
        json={"tools": member_tools},
    )
    assert grant.status_code == 200, grant.text
    return World(
        admin=admin,
        member=member,
        team_id=team_id,
        connector_id=connector.json()["id"],
        slug=slug,
    )
