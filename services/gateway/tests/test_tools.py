"""Connector registry, authorized tool discovery and invocation (REST and MCP), and usage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.conftest import BASE_URL, AppClient, bearer, build_world, create_key, signup, sql


def _call_path(team_id: str, slug: str, tool: str) -> str:
    return f"/v1/teams/{team_id}/connectors/{slug}/tools/{tool}/call"


@asynccontextmanager
async def mcp_session(
    client: httpx.AsyncClient, slug: str, token: str
) -> AsyncIterator[ClientSession]:
    client.headers.update(bearer(token))
    try:
        async with (
            streamable_http_client(f"{BASE_URL}/v1/mcp/{slug}", http_client=client) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            yield session
    finally:
        client.headers.pop("Authorization", None)


async def test_only_platform_admins_register_connectors_and_only_allow_listed_urls(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        body: dict[str, Any] = {"slug": "other", "display_name": "Other", "url": upstream_url}
        by_member = await client.post("/v1/connectors", headers=world.member.auth, json=body)
        metadata = await client.post(
            "/v1/connectors",
            headers=world.admin.auth,
            json=body | {"url": "http://169.254.169.254/latest/meta-data"},
        )
        member_view = await client.get("/v1/connectors", headers=world.member.auth)
        admin_view = await client.get("/v1/connectors", headers=world.admin.auth)

    assert by_member.status_code == 403
    assert metadata.status_code == 422
    assert all("url" not in c for c in member_view.json())  # upstream URLs stay private
    assert any(c.get("url") == upstream_url for c in admin_view.json())


async def test_probe_lists_upstream_tools(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        probe = await client.post(
            f"/v1/connectors/{world.connector_id}/probe", headers=world.admin.auth
        )
    assert probe.status_code == 200
    assert probe.json()["server_name"] == "demo"
    assert {t["name"] for t in probe.json()["tools"]} == {"echo", "add", "fail", "seen_token"}


async def test_discovery_and_invocation_follow_member_grants(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        auth = bearer(key["key"])
        tools = await client.get(
            f"/v1/teams/{world.team_id}/connectors/{world.slug}/tools", headers=auth
        )
        allowed = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=auth,
            json={"arguments": {"text": "hello"}},
        )
        denied = await client.post(
            _call_path(world.team_id, world.slug, "add"),
            headers=auth,
            json={"arguments": {"a": 1, "b": 2}},
        )

    assert [t["name"] for t in tools.json()] == ["echo"]
    assert "inputSchema" in tools.json()[0]
    assert allowed.status_code == 200
    assert allowed.json()["content"][0]["text"] == "hello"
    assert allowed.json().get("isError", False) is False
    assert denied.status_code == 403


async def test_key_scopes_and_tool_restrictions_narrow_access(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo", "add"])
        read_only = await create_key(client, world.member, world.team_id, scopes=["tools:read"])
        restricted = await create_key(
            client, world.member, world.team_id, tool_restrictions=[f"{world.slug}/add"]
        )
        no_call = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=bearer(read_only["key"]),
            json={"arguments": {"text": "x"}},
        )
        restricted_tools = await client.get(
            f"/v1/teams/{world.team_id}/connectors/{world.slug}/tools",
            headers=bearer(restricted["key"]),
        )
        restricted_echo = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=bearer(restricted["key"]),
            json={"arguments": {"text": "x"}},
        )
        restricted_add = await client.post(
            _call_path(world.team_id, world.slug, "add"),
            headers=bearer(restricted["key"]),
            json={"arguments": {"a": 2, "b": 3}},
        )

    assert no_call.status_code == 403
    assert [t["name"] for t in restricted_tools.json()] == ["add"]
    assert restricted_echo.status_code == 403
    assert restricted_add.json()["content"][0]["text"] == "5"


async def test_sessions_cannot_invoke_tools(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        response = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=world.member.auth,
            json={"arguments": {"text": "x"}},
        )
    assert response.status_code == 403


async def test_a_key_cannot_reach_another_team(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        other = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        response = await client.post(
            _call_path(other.team_id, other.slug, "echo"),
            headers=bearer(key["key"]),
            json={"arguments": {"text": "x"}},
        )
    assert response.status_code == 404


async def test_tool_errors_and_upstream_outages_are_reported_safely(
    app_client: AppClient, database_dsn: str, upstream_url: str, closed_port_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["fail"])
        key = await create_key(client, world.member, world.team_id)
        tool_error = await client.post(
            _call_path(world.team_id, world.slug, "fail"),
            headers=bearer(key["key"]),
            json={"arguments": {}},
        )
        await client.patch(
            f"/v1/connectors/{world.connector_id}",
            headers=world.admin.auth,
            json={"url": closed_port_url},
        )
        outage = await client.post(
            _call_path(world.team_id, world.slug, "fail"),
            headers=bearer(key["key"]),
            json={"arguments": {}},
        )

    assert tool_error.status_code == 200
    assert tool_error.json()["isError"] is True
    assert outage.status_code == 502
    assert outage.json()["error"]["code"] == "upstream_unavailable"
    assert outage.json()["error"]["retryable"] is True
    assert closed_port_url not in outage.text


async def test_disabled_connector_blocks_calls(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        await client.patch(
            f"/v1/connectors/{world.connector_id}",
            headers=world.admin.auth,
            json={"enabled": False},
        )
        response = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=bearer(key["key"]),
            json={"arguments": {"text": "x"}},
        )
    assert response.status_code in {403, 404}


async def test_mcp_endpoint_exposes_only_permitted_tools(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        async with mcp_session(client, world.slug, key["key"]) as session:
            listed = await session.list_tools()
            echoed = await session.call_tool("echo", {"text": "via mcp"})
            denied = await session.call_tool("add", {"a": 1, "b": 1})
        with_session = await client.post(
            f"/v1/mcp/{world.slug}",
            headers=world.member.auth | {"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert [t.name for t in listed.tools] == ["echo"]
    assert echoed.isError is False
    assert echoed.content[0].text == "via mcp"  # type: ignore[union-attr]
    assert denied.isError is True
    assert denied.content[0].text.startswith("forbidden:")  # type: ignore[union-attr]
    assert with_session.status_code == 403


async def test_usage_and_audit_are_recorded_with_access_limits(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=bearer(key["key"]) | {"X-Request-ID": "usage-req-1"},
            json={"arguments": {"text": "x"}},
        )
        await client.post(
            _call_path(world.team_id, world.slug, "add"),
            headers=bearer(key["key"]),
            json={"arguments": {"a": 1, "b": 1}},
        )
        team = f"/v1/teams/{world.team_id}"
        member_usage = await client.get(f"{team}/usage/invocations", headers=world.member.auth)
        admin_audit = await client.get(f"{team}/audit-events", headers=world.admin.auth)
        outsider = await signup(client, "outsider")
        outsider_usage = await client.get(f"{team}/usage/invocations", headers=outsider.auth)

    rows = member_usage.json()["items"]
    assert member_usage.status_code == 200
    assert {(r["tool_name"], r["outcome"]) for r in rows} >= {("echo", "success")}
    assert any(r["request_id"] == "usage-req-1" for r in rows)
    actions = {e["action"] for e in admin_audit.json()["items"]}
    assert "api_key.create" in actions or any(a.startswith("api_key") for a in actions)
    assert key["key"] not in admin_audit.text
    assert outsider_usage.status_code == 404


async def test_connector_credential_is_sent_upstream_but_never_returned(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    secret = "s3cr3t-upstream-token"
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        slug = f"cred-{world.slug}"
        registered = await client.post(
            "/v1/connectors",
            headers=world.admin.auth,
            json={
                "slug": slug,
                "display_name": "With credential",
                "url": upstream_url,
                "credential": {
                    "type": "header",
                    "header_name": "X-Upstream-Token",
                    "value": secret,
                },
            },
        )
        await client.put(
            f"/v1/teams/{world.team_id}/connectors/{slug}",
            headers=world.admin.auth,
            json={"allow_all_tools": True},
        )
        key = await create_key(client, world.admin, world.team_id)
        call = await client.post(
            _call_path(world.team_id, slug, "seen_token"),
            headers=bearer(key["key"]),
            json={"arguments": {}},
        )
        listed = await client.get("/v1/connectors", headers=world.admin.auth)
        stored = await sql(
            database_dsn,
            "SELECT credential_header, credential_ciphertext FROM connectors WHERE slug = $1",
            slug,
        )
        cleared = await client.patch(
            f"/v1/connectors/{registered.json()['id']}",
            headers=world.admin.auth,
            json={"clear_credential": True},
        )
        after_clear = await client.post(
            _call_path(world.team_id, slug, "seen_token"),
            headers=bearer(key["key"]),
            json={"arguments": {}},
        )

    assert registered.status_code == 201, registered.text
    assert registered.json()["credential"] == {
        "header_name": "X-Upstream-Token",
        "configured": True,
    }
    assert call.status_code == 200, call.text
    assert call.json()["content"][0]["text"] == secret
    assert secret not in listed.text and secret not in registered.text
    assert stored[0]["credential_header"] == "X-Upstream-Token"
    assert secret not in stored[0]["credential_ciphertext"]  # encrypted at rest
    assert cleared.status_code == 200 and cleared.json().get("credential") is None
    assert after_clear.json()["content"][0]["text"] == ""


async def test_reserved_credential_headers_are_refused(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        response = await client.post(
            "/v1/connectors",
            headers=world.admin.auth,
            json={
                "slug": f"bad-{world.slug}",
                "display_name": "Bad",
                "url": upstream_url,
                "credential": {"type": "header", "header_name": "Host", "value": "evil"},
            },
        )
    assert response.status_code == 422


async def test_removing_a_connector_hides_its_tools(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        by_member = await client.delete(
            f"/v1/connectors/{world.connector_id}", headers=world.member.auth
        )
        removed = await client.delete(
            f"/v1/connectors/{world.connector_id}", headers=world.admin.auth
        )
        call = await client.post(
            _call_path(world.team_id, world.slug, "echo"),
            headers=bearer(key["key"]),
            json={"arguments": {"text": "hi"}},
        )

    assert by_member.status_code == 403
    assert removed.status_code == 204
    assert call.status_code == 404
