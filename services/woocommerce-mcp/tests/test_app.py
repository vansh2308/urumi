"""The ASGI application end to end: probes and MCP tools over Streamable HTTP, driven by the
official MCP client, with a fake store and a throwaway database."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from tests.conftest import FakeStore, make_settings
from woocommerce_mcp import container as container_module
from woocommerce_mcp.app import create_app

pytestmark = pytest.mark.usefixtures("clean_operations")

BASE_URL = "http://127.0.0.1:8000"


@asynccontextmanager
async def running(
    database_url: str,
    store_http: Callable[[], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
    **woocommerce: Any,
) -> AsyncIterator[tuple[Starlette, httpx.AsyncClient]]:
    monkeypatch.setattr(container_module, "build_http_client", lambda _settings: store_http())
    app = create_app(make_settings(database_url, **woocommerce))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        yield app, client


@asynccontextmanager
async def mcp_session(client: httpx.AsyncClient) -> AsyncIterator[ClientSession]:
    async with (
        streamable_http_client(f"{BASE_URL}/mcp", http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


AppClient = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def app_client(
    database_url: str,
    store_http: Callable[[], httpx.AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> AppClient:
    """Opens the app (lifespan included) inside the test's own task, as anyio requires."""

    @asynccontextmanager
    async def open_client() -> AsyncIterator[httpx.AsyncClient]:
        async with running(database_url, store_http, monkeypatch) as (_, http):
            yield http

    return open_client


def _text(result: Any) -> str:
    """Tool error text without FastMCP's 'Error executing tool <name>: ' prefix."""
    text = "\n".join(c.text for c in result.content if c.type == "text")
    return text.split(": ", 1)[1] if text.startswith("Error executing tool ") else text


async def test_liveness_and_readiness(app_client: AppClient) -> None:
    async with app_client() as client:
        live = await client.get("/healthz")
        ready = await client.get("/readyz")
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"database": "ok"}}


async def test_readiness_fails_without_the_database(
    store_http: Callable[[], httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    unreachable = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none"
    async with running(unreachable, store_http, monkeypatch) as (_, http):
        response = await http.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "fail"}


async def test_lists_the_five_tools_with_safety_annotations(app_client: AppClient) -> None:
    async with app_client() as client, mcp_session(client) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools) == {
        "list_products",
        "create_product",
        "list_orders",
        "get_order",
        "update_order_status",
    }
    assert tools["list_products"].annotations.readOnlyHint is True
    assert tools["update_order_status"].annotations.destructiveHint is True
    assert "idempotency_key" in tools["create_product"].inputSchema["properties"]


async def test_list_products_returns_structured_content(
    app_client: AppClient, store: FakeStore
) -> None:
    async with app_client() as client, mcp_session(client) as session:
        result = await session.call_tool("list_products", {"per_page": 5})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["items"][0]["id"] == 1
    assert result.structuredContent["total"] == 1
    assert store.calls("GET", "/products")[0].url.params["per_page"] == "5"


async def test_invalid_arguments_never_reach_the_store(
    app_client: AppClient, store: FakeStore
) -> None:
    async with app_client() as client, mcp_session(client) as session:
        result = await session.call_tool(
            "create_product", {"name": "Mug", "regular_price": "5.00", "sale_price": "9.00"}
        )

    assert result.isError is True
    assert _text(result).startswith("invalid_input:")
    assert store.calls("POST") == []


async def test_create_product_with_idempotency_key_writes_once(
    app_client: AppClient, store: FakeStore
) -> None:
    arguments = {"name": "Mug", "regular_price": "12.50", "idempotency_key": "mug-create-0001"}
    async with app_client() as client, mcp_session(client) as session:
        first = await session.call_tool("create_product", arguments)
        retry = await session.call_tool("create_product", arguments)

    assert first.isError is False
    assert first.structuredContent == retry.structuredContent
    assert len(store.calls("POST", "/products")) == 1


async def test_upstream_errors_become_safe_tool_errors(
    app_client: AppClient, store: FakeStore
) -> None:
    async with app_client() as client, mcp_session(client) as session:
        missing = await session.call_tool("get_order", {"order_id": 999})
        store.fail_next = [503, 503, 503]
        unavailable = await session.call_tool("list_orders", {})

    assert missing.isError is True
    assert _text(missing) == "not_found: Order 999 was not found."
    assert unavailable.isError is True
    assert _text(unavailable).startswith("upstream_unavailable:")
    assert _text(unavailable).endswith("(retryable)")
    assert "cs_test" not in _text(unavailable)


async def test_update_order_status_rules(app_client: AppClient, store: FakeStore) -> None:
    async with app_client() as client, mcp_session(client) as session:
        refund = await session.call_tool(
            "update_order_status", {"order_id": 7, "status": "refunded"}
        )
        changed = await session.call_tool(
            "update_order_status", {"order_id": 7, "status": "completed"}
        )
        unchanged = await session.call_tool(
            "update_order_status", {"order_id": 7, "status": "completed"}
        )

    assert refund.isError is True
    assert _text(refund).startswith("invalid_input:")
    assert changed.structuredContent is not None
    assert changed.structuredContent["changed"] is True
    assert changed.structuredContent["previous_status"] == "processing"
    assert unchanged.structuredContent is not None
    assert unchanged.structuredContent["changed"] is False
    assert len(store.calls("PUT")) == 1


async def test_rejects_unknown_host_headers(app_client: AppClient) -> None:
    async with app_client() as client:
        response = await client.post(
            "/mcp",
            headers={
                "Host": "evil.example",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert response.status_code == 421
