"""OAuth 2.1 for MCP clients: discovery, registration, consent, PKCE, tokens, refresh, revoke."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.conftest import BASE_URL, AppClient, World, bearer, build_world

REDIRECT = "http://127.0.0.1:33418/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _register(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/register",
        json={
            "client_name": "Test MCP client",
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["client_id"])


async def _authorize(
    client: httpx.AsyncClient, world: World, client_id: str, *, approve: bool = True
) -> tuple[dict[str, list[str]], str]:
    verifier, challenge = _pkce()
    started = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": f"{BASE_URL}/v1/mcp",
        },
    )
    assert started.status_code == 302, started.text
    consent = urlsplit(started.headers["location"])
    assert consent.netloc == "app.test"
    request_id = parse_qs(consent.query)["request"][0]

    described = await client.get(f"/v1/oauth/requests/{request_id}", headers=world.member.auth)
    assert described.status_code == 200, described.text
    assert described.json()["client_name"] == "Test MCP client"
    assert described.json()["redirect_host"] == "127.0.0.1:33418"

    decided = await client.post(
        f"/v1/oauth/requests/{request_id}/decision",
        headers=world.member.auth,
        json={"approve": approve, "team_id": world.team_id},
    )
    assert decided.status_code == 200, decided.text
    back = urlsplit(decided.json()["redirect_to"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    return parse_qs(back.query), verifier


async def _token(client: httpx.AsyncClient, **form: str) -> httpx.Response:
    return await client.post("/token", data=form)


async def _mcp_list(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post(
        "/v1/mcp",
        headers=bearer(token) | MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )


async def test_discovery_points_clients_at_the_authorization_server(
    app_client: AppClient,
) -> None:
    async with app_client() as client:
        challenge = await client.post(
            "/v1/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        resource = await client.get("/.well-known/oauth-protected-resource/v1/mcp")
        server = await client.get("/.well-known/oauth-authorization-server")
        config = await client.get("/v1/oauth/config")

    assert challenge.status_code == 401
    assert (
        'resource_metadata="http://gateway.test/.well-known/oauth-protected-resource/v1/mcp"'
        in (challenge.headers["www-authenticate"])
    )
    assert resource.json()["resource"] == "http://gateway.test/v1/mcp"
    assert server.json()["token_endpoint"] == "http://gateway.test/token"
    assert server.json()["code_challenge_methods_supported"] == ["S256"]
    assert config.json()["mcp_url"] == "http://gateway.test/v1/mcp"


async def test_full_flow_tokens_work_and_rotate_and_revoke(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        client_id = await _register(client)
        query, verifier = await _authorize(client, world, client_id)
        assert query["state"] == ["xyz"]
        code = query["code"][0]

        wrong_verifier = await _token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            client_id=client_id,
            code_verifier="x" * 50,
        )
        issued = await _token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            client_id=client_id,
            code_verifier=verifier,
        )
        replayed = await _token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT,
            client_id=client_id,
            code_verifier=verifier,
        )
        tokens: dict[str, Any] = issued.json()
        listed = await _mcp_list(client, tokens["access_token"])

        refreshed = await _token(
            client,
            grant_type="refresh_token",
            refresh_token=tokens["refresh_token"],
            client_id=client_id,
        )
        reused_refresh = await _token(
            client,
            grant_type="refresh_token",
            refresh_token=tokens["refresh_token"],
            client_id=client_id,
        )
        new_tokens: dict[str, Any] = refreshed.json()
        old_access = await _mcp_list(client, tokens["access_token"])
        new_access = await _mcp_list(client, new_tokens["access_token"])

        grants = await client.get("/v1/oauth/grants", headers=world.member.auth)
        keys = await client.get(f"/v1/teams/{world.team_id}/api-keys", headers=world.member.auth)
        revoked = await client.post(
            "/revoke",
            # The SDK's revocation form requires the field even for public clients.
            data={
                "token": new_tokens["refresh_token"],
                "client_id": client_id,
                "client_secret": "",
            },
        )
        after_revoke = await _mcp_list(client, new_tokens["access_token"])
        grants_after = await client.get("/v1/oauth/grants", headers=world.member.auth)

    assert wrong_verifier.status_code == 400
    assert issued.status_code == 200, issued.text
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert replayed.status_code == 400  # single-use code
    assert listed.status_code == 200
    names = [t["name"] for t in listed.json()["result"]["tools"]]
    assert names == [f"{world.slug}__echo"]  # the member's own grants apply
    assert refreshed.status_code == 200 and new_tokens["refresh_token"] != tokens["refresh_token"]
    assert reused_refresh.status_code == 400  # rotation
    assert old_access.status_code == 401 and new_access.status_code == 200
    assert [g["client_name"] for g in grants.json()] == ["Test MCP client"]
    assert any(k["oauth_issued"] for k in keys.json())
    assert revoked.status_code == 200, revoked.text
    assert after_revoke.status_code == 401
    assert grants_after.json() == []


async def test_denied_consent_and_bad_redirects(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        client_id = await _register(client)
        query, _ = await _authorize(client, world, client_id, approve=False)
        insecure = await client.post(
            "/register",
            json={
                "redirect_uris": ["http://evil.example.com/cb"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )

    assert query["error"] == ["access_denied"] and "code" not in query
    assert insecure.status_code == 400
    assert insecure.json()["error"] == "invalid_redirect_uri"
