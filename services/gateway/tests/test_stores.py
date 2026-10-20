"""Stores API: creation (idempotent, capped, slugs never reused), visibility, delete and retry,
and the rules for the ``kind=store`` connectors the provisioner registers."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from gateway.app import create_app
from gateway.config import Settings, StoreSettings
from gateway.container import Container
from gateway.db.models import Store, StoreStatus
from gateway.stores.naming import mcp_db_name, slug_base, slug_candidates
from gateway.stores.service import register_store_connector
from tests.conftest import BASE_URL, User, World, build_world, make_settings, signup, sql


@asynccontextmanager
async def running(
    dsn: str, settings: Settings | None = None
) -> AsyncIterator[tuple[Container, httpx.AsyncClient]]:
    app = create_app(settings or make_settings(dsn))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        yield app.state.container, client


def _name() -> str:
    return f"Shop {uuid.uuid4().hex[:6]}"


async def _create(
    client: httpx.AsyncClient, user: User, team_id: str, name: str, **headers: str
) -> httpx.Response:
    return await client.post(
        f"/v1/teams/{team_id}/stores", headers=user.auth | headers, json={"name": name}
    )


async def _team(client: httpx.AsyncClient, user: User) -> str:
    response = await client.post("/v1/teams", headers=user.auth, json={"name": "Stores"})
    return str(response.json()["id"])


async def _make_ready(container: Container, store_id: str) -> None:
    """What the provisioner's register step does, without a cluster."""
    async with container.db.transaction() as session:
        store = await session.get(Store, uuid.UUID(store_id))
        assert store is not None
        await register_store_connector(
            session, store, audit=container.audit, timeout_seconds=5, max_concurrency=4
        )
        store.status = StoreStatus.READY.value


# --- naming ---------------------------------------------------------------------------------------


def test_slugs_are_short_lowercase_and_start_with_a_letter() -> None:
    assert slug_base("Acme Outdoor Gear & Co.") == "acme-outdoor-gear"
    assert slug_base("42 Things") == "s-42-things"
    assert slug_base("Ünïcode!!") == "n-code"
    assert slug_base("x") == "x-store"
    candidates = slug_candidates("Acme")
    assert candidates[:3] == ["acme", "acme-2", "acme-3"]
    assert all(len(c) <= 21 and "_" not in c for c in candidates)
    assert mcp_db_name("6f1c-aa") == "store_6f1caa"


# --- create ---------------------------------------------------------------------------------------


async def test_create_returns_202_with_a_one_time_password(database_dsn: str) -> None:
    async with running(database_dsn) as (_, client):
        user = await signup(client, "creator")
        team_id = await _team(client, user)
        name = _name()
        created = await _create(client, user, team_id, name)
        listed = await client.get(f"/v1/teams/{team_id}/stores", headers=user.auth)

    assert created.status_code == 202, created.text
    body = created.json()
    assert body["created"] is True
    assert body["status"] == "requested"
    assert body["phase"] == "Provisioning"
    assert body["admin_password"]
    assert body["url"] == f"http://{body['slug']}.localtest.me"
    assert body["namespace"] == f"store-{body['slug']}"
    assert body["created_by_email"] == user.email
    assert [s["id"] for s in listed.json()] == [body["id"]]
    assert "admin_password" not in listed.json()[0]
    rows = await sql(
        database_dsn,
        "SELECT admin_password_ciphertext FROM stores WHERE id = $1",
        uuid.UUID(body["id"]),
    )
    assert rows[0]["admin_password_ciphertext"] not in (None, body["admin_password"])
    audit = await sql(
        database_dsn, "SELECT action FROM audit_events WHERE target_id = $1", body["id"]
    )
    assert [r["action"] for r in audit] == ["store.create"]


async def test_retries_return_the_same_store(database_dsn: str) -> None:
    async with running(database_dsn) as (_, client):
        user = await signup(client, "retrier")
        team_id = await _team(client, user)
        name = _name()
        first = await _create(client, user, team_id, name, **{"Idempotency-Key": "key-0000001"})
        again = await _create(client, user, team_id, name, **{"Idempotency-Key": "key-0000001"})
        same_name = await _create(client, user, team_id, name.upper())
        reused_key = await _create(
            client, user, team_id, _name(), **{"Idempotency-Key": "key-0000001"}
        )

    assert first.status_code == 202
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    assert again.json()["created"] is False
    assert again.json()["admin_password"] is None
    assert (same_name.status_code, same_name.json()["id"]) == (200, first.json()["id"])
    assert reused_key.status_code == 409


async def test_per_user_cap_holds_under_parallel_requests(database_dsn: str) -> None:
    async with running(database_dsn) as (_, client):
        user = await signup(client, "racer")
        team_id = await _team(client, user)
        responses = await asyncio.gather(
            *(_create(client, user, team_id, _name()) for _ in range(5))
        )

    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 202, 409, 409, 409]
    refused = next(r for r in responses if r.status_code == 409)
    assert refused.json()["error"]["code"] == "quota_exceeded"


async def test_platform_total_cap(database_dsn: str) -> None:
    live = await sql(database_dsn, "SELECT count(*) AS n FROM stores WHERE status <> 'deleted'")
    settings = make_settings(database_dsn)
    settings.stores = StoreSettings(
        _env_file=None, enabled=True, max_per_user=5, max_total=live[0]["n"] + 1
    )
    async with running(database_dsn, settings) as (_, client):
        user = await signup(client, "total")
        team_id = await _team(client, user)
        first = await _create(client, user, team_id, _name())
        second = await _create(client, user, team_id, _name())

    assert first.status_code == 202
    assert second.status_code == 409
    assert "maximum" in second.json()["error"]["message"]


async def test_stores_disabled_answers_404(database_dsn: str) -> None:
    settings = make_settings(database_dsn)
    settings.stores = StoreSettings(_env_file=None, enabled=False)
    async with running(database_dsn, settings) as (_, client):
        user = await signup(client, "nostores")
        team_id = await _team(client, user)
        response = await _create(client, user, team_id, _name())
    assert response.status_code == 404


async def test_slugs_are_never_reused(database_dsn: str) -> None:
    base = f"reuse{uuid.uuid4().hex[:6]}"
    async with running(database_dsn) as (_, client):
        user = await signup(client, "reuse")
        team_id = await _team(client, user)
        first = (await _create(client, user, team_id, base)).json()
        await sql(
            database_dsn,
            "UPDATE stores SET status = 'deleted', deleted_at = now() WHERE id = $1",
            uuid.UUID(first["id"]),
        )
        second = (await _create(client, user, team_id, base)).json()

    assert first["slug"] == base
    assert second["slug"] == f"{base}-2"
    assert second["id"] != first["id"]


# --- visibility, delete, retry --------------------------------------------------------------------


async def test_team_members_see_stores_other_teams_do_not(
    database_dsn: str, upstream_url: str
) -> None:
    async with running(database_dsn) as (_, client):
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        outsider = await signup(client, "outsider")
        store = (await _create(client, world.admin, world.team_id, _name())).json()
        as_member = await client.get(f"/v1/teams/{world.team_id}/stores", headers=world.member.auth)
        as_outsider = await client.get(
            f"/v1/teams/{world.team_id}/stores/{store['id']}", headers=outsider.auth
        )

    assert [s["id"] for s in as_member.json()] == [store["id"]]
    assert as_outsider.status_code == 404


async def test_delete_needs_creator_or_admin_and_is_idempotent(
    database_dsn: str, upstream_url: str
) -> None:
    async with running(database_dsn) as (_, client):
        world = await build_world(client, database_dsn, upstream_url, member_tools=["echo"])
        store = (await _create(client, world.admin, world.team_id, _name())).json()
        url = f"/v1/teams/{world.team_id}/stores/{store['id']}"
        by_member = await client.delete(url, headers=world.member.auth)
        first = await client.delete(url, headers=world.admin.auth)
        second = await client.delete(url, headers=world.admin.auth)
        detail = await client.get(url, headers=world.admin.auth)

    assert by_member.status_code == 403
    assert (first.status_code, first.json()["status"]) == (202, "deleting")
    assert (second.status_code, second.json()["status"]) == (202, "deleting")
    assert [e["action"] for e in detail.json()["events"]] == ["store.delete", "store.create"]


async def test_retry_only_for_failed_stores(database_dsn: str) -> None:
    async with running(database_dsn) as (_, client):
        user = await signup(client, "retry")
        team_id = await _team(client, user)
        store = (await _create(client, user, team_id, _name())).json()
        url = f"/v1/teams/{team_id}/stores/{store['id']}/retry"
        early = await client.post(url, headers=user.auth)
        await sql(
            database_dsn,
            "UPDATE stores SET status = 'failed', status_reason = 'x', step = 'release', "
            "attempts = 3 WHERE id = $1",
            uuid.UUID(store["id"]),
        )
        same_name = await _create(client, user, team_id, store["name"])
        retried = await client.post(url, headers=user.auth)

    assert early.status_code == 409
    assert same_name.status_code == 409
    assert "retry" in same_name.json()["error"]["message"]
    body = retried.json()
    assert retried.status_code == 202
    assert (body["status"], body["step"], body["attempts"], body["status_reason"]) == (
        "requested",
        None,
        0,
        None,
    )


# --- store connectors -----------------------------------------------------------------------------


async def _store_world(
    client: httpx.AsyncClient, container: Container, dsn: str, upstream_url: str
) -> tuple[World, dict[str, Any]]:
    world = await build_world(client, dsn, upstream_url, member_tools=["echo"])
    # The member creates the store, so the registration must grant them its tools.
    store = (await _create(client, world.member, world.team_id, _name())).json()
    await _make_ready(container, store["id"])
    return world, store


async def test_store_connector_is_visible_only_to_its_team(
    database_dsn: str, upstream_url: str
) -> None:
    async with running(database_dsn) as (container, client):
        world, store = await _store_world(client, container, database_dsn, upstream_url)
        other = await signup(client, "other-admin")
        other_team = await _team(client, other)
        mine = await client.get("/v1/connectors", headers=world.member.auth)
        theirs = await client.get("/v1/connectors", headers=other.auth)
        enable_elsewhere = await client.put(
            f"/v1/teams/{other_team}/connectors/{store['slug']}",
            headers=other.auth,
            json={"allow_all_tools": True},
        )
        member_view = await client.get(
            f"/v1/teams/{world.team_id}/connectors", headers=world.member.auth
        )
        detail = await client.get(
            f"/v1/teams/{world.team_id}/stores/{store['id']}", headers=world.member.auth
        )

    entry = next(c for c in mine.json() if c["slug"] == store["slug"])
    assert (entry["kind"], entry["managed"]) == ("store", True)
    assert store["slug"] not in {c["slug"] for c in theirs.json()}
    assert enable_elsewhere.status_code == 404
    assert store["slug"] in {c["slug"] for c in member_view.json()}
    assert detail.json()["connector_slug"] == store["slug"]
    assert detail.json()["status"] == "ready"


async def test_store_connector_is_managed_in_the_registry(
    database_dsn: str, upstream_url: str
) -> None:
    async with running(database_dsn) as (container, client):
        world, store = await _store_world(client, container, database_dsn, upstream_url)
        connector_id = (
            await sql(database_dsn, "SELECT id FROM connectors WHERE slug = $1", store["slug"])
        )[0]["id"]
        url = f"/v1/connectors/{connector_id}"
        rename = await client.patch(url, headers=world.admin.auth, json={"display_name": "X"})
        disable = await client.patch(url, headers=world.admin.auth, json={"enabled": False})
        remove = await client.delete(url, headers=world.admin.auth)
        collide = await client.post(
            "/v1/connectors",
            headers=world.admin.auth,
            json={"slug": store["slug"], "display_name": "Clash", "url": upstream_url},
        )
        target = container.connectors.target_of(
            await container.connectors.get(
                (await container.auth.authenticate_session(world.admin.token)), connector_id
            )
        )

    assert rename.status_code == 422
    assert (disable.status_code, disable.json()["enabled"]) == (200, False)
    assert remove.status_code == 409
    assert collide.status_code == 409
    # Platform-generated in-cluster URL: exempt from the SSRF destination check like hosted.
    assert target.verify_destination is False
    assert target.url == f"http://store-mcp.store-{store['slug']}.svc.cluster.local:8000/mcp"


async def test_deleting_a_store_disables_its_connector_at_once(
    database_dsn: str, upstream_url: str
) -> None:
    async with running(database_dsn) as (container, client):
        world, store = await _store_world(client, container, database_dsn, upstream_url)
        await client.delete(
            f"/v1/teams/{world.team_id}/stores/{store['id']}", headers=world.member.auth
        )
        connectors = await client.get(
            f"/v1/teams/{world.team_id}/connectors", headers=world.member.auth
        )

    assert store["slug"] not in {c["slug"] for c in connectors.json()}
    rows = await sql(database_dsn, "SELECT enabled FROM connectors WHERE slug = $1", store["slug"])
    assert rows[0]["enabled"] is False


@pytest.mark.parametrize("name", ["", " " * 3, "x" * 61])
async def test_invalid_names_are_rejected(database_dsn: str, name: str) -> None:
    async with running(database_dsn) as (_, client):
        user = await signup(client, "badname")
        team_id = await _team(client, user)
        response = await _create(client, user, team_id, name)
    assert response.status_code == 422
