"""Authentication, teams, RBAC and API key management through the REST API."""

from __future__ import annotations

import uuid

from tests.conftest import PASSWORD, AppClient, bearer, build_world, create_key, signup, sql


async def test_health_probes(app_client: AppClient) -> None:
    async with app_client() as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        metrics = await client.get("/metrics")
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok"}
    assert "gateway" in metrics.text


async def test_register_login_me_and_logout(app_client: AppClient) -> None:
    async with app_client() as client:
        user = await signup(client, "ada")
        me = await client.get("/v1/me", headers=user.auth)
        wrong = await client.post(
            "/v1/auth/login", json={"email": user.email, "password": "not the password"}
        )
        logout = await client.post("/v1/auth/logout", headers=user.auth)
        after = await client.get("/v1/me", headers=user.auth)

    assert me.status_code == 200
    assert me.json()["user"]["email"] == user.email
    assert me.json()["credential"] == "session"
    assert wrong.status_code == 401
    assert logout.status_code == 204
    assert after.status_code == 401


async def test_privileged_fields_cannot_be_smuggled_into_requests(app_client: AppClient) -> None:
    async with app_client() as client:
        response = await client.post(
            "/v1/auth/register",
            json={
                "email": f"eve-{uuid.uuid4().hex[:6]}@example.com",
                "password": PASSWORD,
                "display_name": "eve",
                "is_platform_admin": True,
            },
        )
    assert response.status_code == 422
    assert PASSWORD not in response.text  # validation errors never echo submitted values


async def test_members_cannot_administer_the_team(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        team = f"/v1/teams/{world.team_id}"
        own_view = await client.get(team, headers=world.member.auth)
        responses = [
            await client.get(f"{team}/members", headers=world.member.auth),
            await client.post(
                f"{team}/invitations", headers=world.member.auth, json={"email": "x@example.com"}
            ),
            await client.patch(
                f"{team}/members/{world.member.id}",
                headers=world.member.auth,
                json={"role": "admin"},
            ),
            await client.patch(team, headers=world.member.auth, json={"name": "Mine now"}),
            await client.put(
                f"{team}/members/{world.member.id}/grants/{world.slug}",
                headers=world.member.auth,
                json={"allow_all_tools": True},
            ),
            await client.get(
                f"{team}/audit-events",
                params={"actor_user_id": world.admin.id},
                headers=world.member.auth,
            ),
        ]
        own_audit = await client.get(f"{team}/audit-events", headers=world.member.auth)
        roles = await sql(
            database_dsn,
            "SELECT role FROM team_memberships WHERE user_id = $1",
            uuid.UUID(world.member.id),
        )

    assert own_view.json()["role"] == "member"
    assert [r.status_code for r in responses] == [403] * len(responses)
    assert [r["role"] for r in roles] == ["member"]
    assert {e["actor_user_id"] for e in own_audit.json()["items"]} <= {world.member.id}


async def test_other_teams_are_invisible(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        outsider = await signup(client, "mallory")
        team = f"/v1/teams/{world.team_id}"
        responses = [
            await client.get(team, headers=outsider.auth),
            await client.get(f"{team}/members", headers=outsider.auth),
            await client.get(f"{team}/api-keys", headers=outsider.auth),
            await client.get(f"{team}/connectors/{world.slug}/tools", headers=outsider.auth),
        ]
    assert [r.status_code for r in responses] == [404] * len(responses)


async def test_last_admin_cannot_be_demoted(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        response = await client.patch(
            f"/v1/teams/{world.team_id}/members/{world.admin.id}",
            headers=world.admin.auth,
            json={"role": "member"},
        )
    assert response.status_code in {409, 422}


async def test_api_key_secret_is_shown_once_and_only_its_hash_is_stored(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        issued = await create_key(client, world.member, world.team_id)
        listed = await client.get(f"/v1/teams/{world.team_id}/api-keys", headers=world.member.auth)
        fetched = await client.get(
            f"/v1/teams/{world.team_id}/api-keys/{issued['id']}", headers=world.member.auth
        )
        rows = await sql(
            database_dsn, "SELECT * FROM api_keys WHERE id = $1", uuid.UUID(issued["id"])
        )

    raw = issued["key"]
    assert raw.startswith("urk_")
    assert sorted(issued["scopes"]) == ["tools:call", "tools:read"]
    assert raw not in listed.text
    assert raw not in fetched.text
    assert "key" not in fetched.json()
    assert all(raw not in str(value) for value in rows[0].values())


async def test_members_see_only_their_own_keys_and_admins_see_all(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        admin_key = await create_key(client, world.admin, world.team_id)
        member_key = await create_key(client, world.member, world.team_id)
        keys = f"/v1/teams/{world.team_id}/api-keys"
        member_list = await client.get(keys, headers=world.member.auth)
        admin_list = await client.get(keys, headers=world.admin.auth)
        peek = await client.get(f"{keys}/{admin_key['id']}", headers=world.member.auth)
        revoke_other = await client.post(
            f"{keys}/{admin_key['id']}/revoke", headers=world.member.auth
        )

    assert {k["id"] for k in member_list.json()} == {member_key["id"]}
    assert {k["id"] for k in admin_list.json()} == {admin_key["id"], member_key["id"]}
    assert peek.status_code == 404
    assert revoke_other.status_code == 404


async def test_revoked_and_rotated_keys_stop_working(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        keys = f"/v1/teams/{world.team_id}/api-keys"
        revoked = await create_key(client, world.member, world.team_id)
        await client.post(f"{keys}/{revoked['id']}/revoke", headers=world.member.auth)
        after_revoke = await client.get("/v1/me", headers=bearer(revoked["key"]))

        old = await create_key(client, world.member, world.team_id)
        rotated = await client.post(
            f"{keys}/{old['id']}/rotate", headers=world.member.auth, json={"grace_hours": 0}
        )
        old_after = await client.get("/v1/me", headers=bearer(old["key"]))
        new_after = await client.get("/v1/me", headers=bearer(rotated.json()["key"]))

    assert after_revoke.status_code == 401
    assert rotated.status_code == 201
    assert rotated.json()["rotated_from_id"] == old["id"]
    assert old_after.status_code == 401
    assert new_after.status_code == 200
    assert new_after.json()["credential"] == "api_key"


async def test_api_keys_cannot_manage_anything(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.admin, world.team_id)
        auth = bearer(key["key"])
        responses = [
            await client.post("/v1/teams", headers=auth, json={"name": "Another"}),
            await client.get(f"/v1/teams/{world.team_id}/members", headers=auth),
            await client.post(
                f"/v1/teams/{world.team_id}/api-keys", headers=auth, json={"name": "escalate"}
            ),
        ]
    assert [r.status_code for r in responses] == [403] * len(responses)


async def test_removed_member_loses_access_immediately(
    app_client: AppClient, database_dsn: str, upstream_url: str
) -> None:
    async with app_client() as client:
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        key = await create_key(client, world.member, world.team_id)
        removed = await client.delete(
            f"/v1/teams/{world.team_id}/members/{world.member.id}", headers=world.admin.auth
        )
        team = await client.get(f"/v1/teams/{world.team_id}", headers=world.member.auth)
        call = await client.post(
            f"/v1/teams/{world.team_id}/connectors/{world.slug}/tools/echo/call",
            headers=bearer(key["key"]),
            json={"arguments": {"text": "hi"}},
        )

    assert removed.status_code == 204
    assert team.status_code == 404
    assert call.status_code in {401, 403, 404}


async def test_errors_carry_request_ids(app_client: AppClient) -> None:
    async with app_client() as client:
        response = await client.get("/v1/me", headers={"X-Request-ID": "req-test-123"})
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert response.json()["error"]["request_id"] == "req-test-123"


async def test_missing_route_has_the_standard_error_shape(app_client: AppClient) -> None:
    async with app_client() as client:
        response = await client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
