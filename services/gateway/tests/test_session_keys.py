"""Dashboard session keys: bound to one login, replaced on reissue, revoked on logout."""

from __future__ import annotations

from tests.conftest import AppClient, bearer, build_world


async def test_session_key_lifecycle(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        # Members may not create keys themselves, yet the dashboard can still get one.
        locked = await client.patch(
            f"/v1/teams/{world.team_id}",
            headers=world.admin.auth,
            json={"members_can_create_keys": False},
        )
        path = f"/v1/teams/{world.team_id}/api-keys/session"
        first = await client.post(path, headers=world.member.auth)
        second = await client.post(path, headers=world.member.auth)
        call = f"/v1/teams/{world.team_id}/connectors/{world.slug}/tools/echo/call"
        old_key_call = await client.post(
            call, headers=bearer(first.json()["key"]), json={"arguments": {"text": "x"}}
        )
        new_key_call = await client.post(
            call, headers=bearer(second.json()["key"]), json={"arguments": {"text": "x"}}
        )
        by_key = await client.post(path, headers=bearer(second.json()["key"]))
        await client.post("/v1/auth/logout", headers=world.member.auth)
        after_logout = await client.post(
            call, headers=bearer(second.json()["key"]), json={"arguments": {"text": "x"}}
        )

    assert locked.status_code == 200, locked.text
    assert first.status_code == 201, first.text
    body = second.json()
    assert body["session_bound"] is True
    assert sorted(body["scopes"]) == ["tools:call", "tools:read", "usage:read"]
    assert old_key_call.status_code == 401  # replaced
    assert new_key_call.status_code == 200
    assert by_key.status_code in (401, 403)  # an API key cannot mint session keys
    assert after_logout.status_code == 401  # logout revokes it
