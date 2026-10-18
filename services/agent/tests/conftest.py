"""Shared fixtures.

Tests never read the service's ``.env`` and never reach a real database, gateway or model:

* PostgreSQL is a throwaway database created for the test session on the server named by
  ``TEST_DATABASE_URL`` (a local container, see the README), migrated, and dropped afterwards.
  Tests that need it are skipped when the variable is unset and refuse a non-local host.
* The gateway is an in-process fake behind ``httpx.MockTransport`` that enforces per-key tool
  access the way the real one does.
* The model is a scripted provider implementing the provider-neutral interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config

from agent import container as container_module
from agent.app import create_app
from agent.config import (
    AppSettings,
    DatabaseSettings,
    GatewaySettings,
    GeminiSettings,
    LlmSettings,
    RunSettings,
    ServerSettings,
    Settings,
)
from agent.llm.base import FinishReason, LlmRequest, LlmResponse, TextSink, ToolCall

SERVICE_DIR = Path(__file__).resolve().parents[1]
BASE_URL = "http://agent.test"
GATEWAY_URL = "http://gateway.test"
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
def database_dsn(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A freshly migrated database for this test session (plain ``postgresql://`` DSN)."""
    admin = _admin_dsn()
    name = f"agent_test_{uuid.uuid4().hex[:12]}"
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


# --- fake gateway ---------------------------------------------------------------------------------

LIST_PRODUCTS_SCHEMA = {
    "type": "object",
    "properties": {"per_page": {"type": "integer", "minimum": 1, "maximum": 100}},
}
UPDATE_STATUS_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "integer"}, "status": {"type": "string"}},
    "required": ["order_id", "status"],
}
TOOLS = {"list_products": LIST_PRODUCTS_SCHEMA, "update_order_status": UPDATE_STATUS_SCHEMA}


@dataclass
class GatewayUser:
    key: str
    user_id: uuid.UUID
    team_id: uuid.UUID
    tools: set[str]
    email: str = "user@example.com"


@dataclass
class FakeGateway:
    """Answers the four gateway routes the agent uses and records tool calls."""

    users: dict[str, GatewayUser] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    call_responses: dict[str, httpx.Response] = field(default_factory=dict)
    seen_request_ids: list[str] = field(default_factory=list)
    # Tools revoked between discovery and the call (an admin changed the grant mid-run).
    revoke_on_call: set[str] = field(default_factory=set)

    def add_user(
        self, tools: Sequence[str] = tuple(TOOLS), team_id: uuid.UUID | None = None
    ) -> GatewayUser:
        user = GatewayUser(
            key=f"urk_{uuid.uuid4().hex}{uuid.uuid4().hex}",
            user_id=uuid.uuid4(),
            team_id=team_id or uuid.uuid4(),
            tools=set(tools),
        )
        self.users[user.key] = user
        return user

    def handler(self, request: httpx.Request) -> httpx.Response:
        if rid := request.headers.get("x-request-id"):
            self.seen_request_ids.append(rid)
        user = self.users.get(request.headers.get("authorization", "").removeprefix("Bearer "))
        if user is None:
            return _gateway_error(401, "unauthenticated", "Authentication required.")
        path = unquote(request.url.path)
        if path == "/v1/me":
            return httpx.Response(
                200,
                json={
                    "user": {"id": str(user.user_id), "email": user.email, "display_name": "U"},
                    "credential": "api_key",
                    "api_key_id": str(uuid.uuid4()),
                    "teams": [
                        {"team_id": str(user.team_id), "team_name": "Acme", "role": "member"}
                    ],
                },
            )
        prefix = f"/v1/teams/{user.team_id}/connectors"
        if not path.startswith(prefix):
            return _gateway_error(404, "not_found", "Team not found.")
        if path == prefix:
            return httpx.Response(
                200,
                json=[
                    {
                        "slug": "shop",
                        "display_name": "Shop",
                        "description": "",
                        "your_access": {"allow_all_tools": False, "tools": sorted(user.tools)},
                    }
                ],
            )
        if path == f"{prefix}/shop/tools":
            return httpx.Response(
                200,
                json=[
                    {"name": name, "description": f"{name} tool", "inputSchema": TOOLS[name]}
                    for name in sorted(user.tools)
                ],
            )
        if match := re.fullmatch(rf"{prefix}/shop/tools/([^/]+)/call", path):
            tool = match.group(1)
            arguments = json.loads(request.content)["arguments"]
            self.calls.append((user.key, tool, arguments))
            user.tools -= self.revoke_on_call
            if tool not in user.tools:
                return _gateway_error(403, "forbidden", "You are not permitted to call this tool.")
            if tool in self.call_responses:
                return self.call_responses[tool]
            payload = {"tool": tool, "arguments": arguments}
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "structuredContent": payload,
                    "isError": False,
                },
            )
        return _gateway_error(404, "not_found", "Not found.")


def _gateway_error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


# --- scripted model -------------------------------------------------------------------------------


def answer(text: str) -> LlmResponse:
    return LlmResponse(
        text=text, tool_calls=(), finish_reason=FinishReason.STOP, input_tokens=10, output_tokens=5
    )


def call_tools(*calls: tuple[str, Any]) -> LlmResponse:
    return LlmResponse(
        text="",
        tool_calls=tuple(
            ToolCall(id=f"call_{i}_{uuid.uuid4().hex[:6]}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ),
        finish_reason=FinishReason.TOOL_CALLS,
        input_tokens=10,
        output_tokens=5,
    )


@dataclass
class ScriptedProvider:
    """Returns the scripted responses in order; records every request it received."""

    script: list[LlmResponse] = field(default_factory=list)
    requests: list[LlmRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return "scripted-1"

    async def generate(self, request: LlmRequest, on_text: TextSink) -> LlmResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("the model was called more often than scripted")
        response = self.script.pop(0)
        for word in response.text.split(" ") if response.text else []:
            await on_text(word + " ")
        return response


# --- application ----------------------------------------------------------------------------------


def make_settings(dsn: str, **run: Any) -> Settings:
    return Settings(
        app=AppSettings(_env_file=None, log_level="WARNING", log_format="console"),
        server=ServerSettings(_env_file=None),
        database=DatabaseSettings(_env_file=None, url=dsn, pool_size=5),
        gateway=GatewaySettings(_env_file=None, url=GATEWAY_URL),
        llm=LlmSettings(_env_file=None, model="scripted-1"),
        gemini=GeminiSettings(_env_file=None, api_key="test-key-not-used"),
        run=RunSettings(_env_file=None, **run),
    )


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


AppClient = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def app_client(
    database_dsn: str,
    gateway: FakeGateway,
    provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> AppClient:
    """Opens the app (lifespan included) inside the test's own task, as anyio requires."""
    monkeypatch.setattr(container_module, "build_provider", lambda *_: provider)
    monkeypatch.setattr(
        container_module,
        "build_gateway_http_client",
        lambda settings: httpx.AsyncClient(
            base_url=settings.url, transport=httpx.MockTransport(gateway.handler)
        ),
    )

    @asynccontextmanager
    async def open_client(**run: Any) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(make_settings(database_dsn, **run))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE_URL, timeout=30
            ) as client,
        ):
            yield client

    return open_client


def bearer(user: GatewayUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.key}"}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events
