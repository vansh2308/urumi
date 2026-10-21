"""The store provisioner's state machine, against an in-memory cluster and Helm.

The fakes model just enough behaviour: ``helm upgrade --install`` creates the release's
workloads (and the setup Job when a run id is set), pods become ready after a few polls, and
deleting a namespace removes everything in it. What is checked is the sequence of steps, what
is created, retries, timeouts, restarts, teardown and the database rows left behind.
"""

from __future__ import annotations

import asyncio
import base64
import stat
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from gateway.app import create_app
from gateway.config import ProvisionerSettings, StoreSettings, UpstreamSettings
from gateway.container import Container
from gateway.db.models import Store
from gateway.hosted.kube import ClusterError
from gateway.provisioner.engine import Provisioner, next_step
from gateway.provisioner.helm import HelmRunner, ReleaseStatus, recover
from gateway.provisioner.mcp_db import PostgresMcpDatabase
from gateway.provisioner.values import split_image, store_values
from gateway.stores.naming import mcp_db_name
from gateway.upstream.credentials import CredentialCipher
from tests.conftest import BASE_URL, User, _admin_dsn, make_settings, signup, sql

WORK = "('requested', 'provisioning', 'deleting')"


# --- fakes ----------------------------------------------------------------------------------------


@dataclass
class FakeKube:
    objects: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    namespace: str = "urumi"
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    # Namespace deletion takes this many reads before the namespace is gone.
    terminating_reads: int = 1

    def _key(self, kind: str, name: str, namespace: str | None) -> tuple[str, str, str]:
        return (kind, "" if kind == "Namespace" else (namespace or self.namespace), name)

    async def apply(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any]:
        key = self._key(manifest["kind"], manifest["metadata"]["name"], namespace)
        self.calls.append(("apply", key[0], key[2]))
        self.objects[key] = manifest
        return manifest

    async def create(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any] | None:
        key = self._key(manifest["kind"], manifest["metadata"]["name"], namespace)
        self.calls.append(("create", key[0], key[2]))
        if key in self.objects:
            return None
        self.objects[key] = manifest
        return manifest

    async def get(
        self, kind: str, name: str, *, namespace: str | None = None
    ) -> dict[str, Any] | None:
        key = self._key(kind, name, namespace)
        obj = self.objects.get(key)
        if obj is not None and kind == "Namespace" and obj["metadata"].get("deletionTimestamp"):
            obj["_reads"] = obj.get("_reads", 0) + 1
            if obj["_reads"] > self.terminating_reads:
                del self.objects[key]
                return None
        return obj

    async def list(
        self, kind: str, *, label_selector: str = "", namespace: str | None = None
    ) -> list[dict[str, Any]]:
        ns = namespace or self.namespace
        items = [o for (k, n, _), o in self.objects.items() if k == kind and n == ns]
        if label_selector:
            key, _, value = label_selector.partition("=")
            items = [o for o in items if o["metadata"].get("labels", {}).get(key) == value]
        return items

    async def delete(self, kind: str, name: str, *, namespace: str | None = None) -> bool:
        self.calls.append(("delete", kind, name))
        key = self._key(kind, name, namespace)
        if key not in self.objects:
            return False
        if kind == "Namespace":
            self.objects[key]["metadata"]["deletionTimestamp"] = "now"
            for other in [k for k in self.objects if k[1] == name]:
                del self.objects[other]
            return True
        del self.objects[key]
        return True

    async def pod_log(self, name: str, *, namespace: str, tail_lines: int = 20) -> str:
        return "store-setup: WordPress did not finish installing\n"

    def in_namespace(self, ns: str) -> dict[tuple[str, str], dict[str, Any]]:
        return {(k, n): o for (k, s, n), o in self.objects.items() if s == ns}


@dataclass
class FakeHelm:
    kube: FakeKube
    releases: dict[str, ReleaseStatus] = field(default_factory=dict)
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    ready_after: int = 2  # readiness polls before pods are Ready
    job_outcomes: list[str] = field(default_factory=list)  # per setup Job: Complete | Failed
    pod_waiting: str | None = None  # e.g. ImagePullBackOff
    polls: dict[str, int] = field(default_factory=dict)

    async def status(self, release: str, namespace: str) -> ReleaseStatus | None:
        return self.releases.get(namespace)

    async def upgrade_install(
        self, release: str, namespace: str, values: dict[str, Any]
    ) -> ReleaseStatus:
        self.calls.append(("upgrade", namespace))
        if ("Namespace", "", namespace) not in self.kube.objects:
            raise AssertionError("helm install before the namespace exists")
        previous = self.releases.get(namespace)
        status = ReleaseStatus("deployed", (previous.revision if previous else 0) + 1)
        self.releases[namespace] = status
        self.values[namespace] = values
        for kind, name in (
            ("Deployment", "store-wordpress"),
            ("StatefulSet", "store-mariadb"),
            ("Deployment", "store-mcp"),
        ):
            key = (kind, namespace, name)
            self.kube.objects.setdefault(
                key, {"kind": kind, "metadata": {"name": name}, "spec": {"replicas": 1}}
            )
        if self.pod_waiting:
            self.kube.objects[("Pod", namespace, "store-wordpress-x")] = {
                "kind": "Pod",
                "metadata": {"name": "store-wordpress-x"},
                "status": {
                    "containerStatuses": [
                        {
                            "name": "wordpress",
                            "restartCount": 0,
                            "state": {"waiting": {"reason": self.pod_waiting, "message": "nope"}},
                        }
                    ]
                },
            }
        run = values["setup"]["runId"]
        if run:
            name = f"store-setup-{run}"
            outcome = self.job_outcomes.pop(0) if self.job_outcomes else "Complete"
            for key in [k for k in self.kube.objects if k[0] == "Job" and k[1] == namespace]:
                del self.kube.objects[key]
            self.kube.objects[("Pod", namespace, f"{name}-pod")] = {
                "kind": "Pod",
                "metadata": {"name": f"{name}-pod", "labels": {"job-name": name}},
                "status": {},
            }
            self.kube.objects[("Job", namespace, name)] = {
                "kind": "Job",
                "metadata": {"name": name},
                "status": {"conditions": [{"type": outcome, "status": "True"}]},
            }
        return status

    def tick(self, namespace: str) -> None:
        """One readiness poll: workloads become ready after ``ready_after`` polls."""
        self.polls[namespace] = self.polls.get(namespace, 0) + 1
        if self.polls[namespace] >= self.ready_after and not self.pod_waiting:
            for (kind, ns, _), obj in self.kube.objects.items():
                if ns == namespace and kind in {"Deployment", "StatefulSet"}:
                    obj["status"] = {"readyReplicas": 1}

    async def rollback(self, release: str, namespace: str) -> None:
        self.calls.append(("rollback", namespace))
        current = self.releases[namespace]
        self.releases[namespace] = ReleaseStatus("deployed", current.revision + 1)

    async def uninstall(self, release: str, namespace: str) -> bool:
        self.calls.append(("uninstall", namespace))
        if self.releases.pop(namespace, None) is None:
            return False
        for key in [k for k in self.kube.objects if k[1] == namespace and k[0] != "Secret"]:
            if key[0] not in {"ResourceQuota", "LimitRange", "NetworkPolicy", "RoleBinding"}:
                del self.kube.objects[key]
        return True


@dataclass
class FakeMcpDb:
    roles: dict[str, str] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)

    async def ensure(self, name: str, password: str) -> None:
        self.roles[name] = password

    async def drop(self, name: str) -> None:
        self.roles.pop(name, None)
        self.dropped.append(name)

    def dsn_for(self, name: str, password: str) -> str:
        return f"postgresql+asyncpg://{name}:{password}@db.example:5432/urumi"


# --- harness --------------------------------------------------------------------------------------


@dataclass
class Rig:
    container: Container
    client: httpx.AsyncClient
    kube: FakeKube
    helm: FakeHelm
    mcp: FakeMcpDb
    user: User
    team_id: str
    dsn: str
    sleeps: list[float]
    on_sleep: list[Callable[[], Awaitable[None]]]

    def provisioner(self, worker_id: str = "test-worker", **overrides: Any) -> Provisioner:
        settings = ProvisionerSettings(
            _env_file=None,
            worker_id=worker_id,
            step_poll_seconds=0.01,
            poll_interval_seconds=0.01,
            teardown_wait_seconds=10,
            mcp_image="urumi/woocommerce-mcp:abc123",
            store_image="docker.io/urumi/store-wordpress:abc123",
            **overrides,
        )

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            for ns in {k[1] for k in self.kube.objects if k[0] == "Deployment"}:
                self.helm.tick(ns)
            for hook in self.on_sleep:
                await hook()
            await asyncio.sleep(0)

        return Provisioner(
            self.container.db,
            kube=self.kube,
            helm=self.helm,
            mcp_db=self.mcp,
            cipher=CredentialCipher(self.container.settings.upstream.credential_keys),
            audit=self.container.audit,
            settings=settings,
            stores=self.container.settings.stores,
            upstream=UpstreamSettings(_env_file=None),
            sleep=sleep,
        )

    async def create(self, name: str | None = None) -> dict[str, Any]:
        response = await self.client.post(
            f"/v1/teams/{self.team_id}/stores",
            headers=self.user.auth,
            json={"name": name or f"Shop {uuid.uuid4().hex[:6]}"},
        )
        assert response.status_code == 202, response.text
        return dict(response.json())

    async def store(self, store_id: str) -> asyncpg.Record:
        rows = await sql(
            self.dsn,
            "SELECT * FROM stores WHERE id = $1",
            uuid.UUID(store_id),
        )
        return rows[0]

    async def drive(self, provisioner: Provisioner) -> None:
        """Claim and process until nothing is left to do."""
        for _ in range(20):
            store_id = await provisioner.claim()
            if store_id is None:
                return
            await provisioner.process(store_id)
        raise AssertionError("the provisioner never ran out of work")


RigFactory = Callable[[], AbstractAsyncContextManager[Rig]]


@pytest.fixture
def rigs(database_dsn: str) -> RigFactory:
    """Opens the app (lifespan included) inside the test's own task, as anyio requires."""

    @asynccontextmanager
    async def open_rig() -> AsyncIterator[Rig]:
        # Rows other tests left pending must not be picked up here.
        await sql(
            database_dsn,
            f"UPDATE stores SET lease_owner = 'parked', lease_expires_at = now() + interval '1 day' WHERE status IN {WORK}",  # noqa: E501, S608
        )
        app = create_app(make_settings(database_dsn))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
        ):
            user = await signup(client, "provisioned")
            team = await client.post("/v1/teams", headers=user.auth, json={"name": "Provisioned"})
            kube = FakeKube()
            yield Rig(
                container=app.state.container,
                client=client,
                kube=kube,
                helm=FakeHelm(kube),
                mcp=FakeMcpDb(),
                user=user,
                team_id=team.json()["id"],
                dsn=database_dsn,
                sleeps=[],
                on_sleep=[],
            )

    return open_rig


async def _events(dsn: str, store_id: str) -> list[str]:
    rows = await sql(
        dsn,
        "SELECT action, details FROM audit_events WHERE target_id = $1 ORDER BY created_at",
        store_id,
    )
    return [r["action"] for r in rows]


# --- the happy path -------------------------------------------------------------------------------


async def test_a_store_is_provisioned_step_by_step(rigs: RigFactory, database_dsn: str) -> None:
    async with rigs() as rig:
        created = await rig.create("Acme Outdoor")
        ns = created["namespace"]

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert (row["status"], row["step"], row["status_reason"]) == ("ready", "register", None)
        assert row["lease_owner"] is None
        assert row["admin_password_ciphertext"] is None
        assert row["ready_at"] is not None

        objects = rig.kube.in_namespace(ns)
        namespace = rig.kube.objects[("Namespace", "", ns)]["metadata"]["labels"]
        assert namespace["urumi.io/managed-by"] == "provisioner"
        assert namespace["urumi.io/store"] == created["id"]
        assert namespace["pod-security.kubernetes.io/enforce"] == "baseline"
        assert {("ResourceQuota", "store-quota"), ("LimitRange", "store-limits")} <= set(objects)
        assert ("NetworkPolicy", "default-deny") in objects
        binding = objects[("RoleBinding", "urumi-provisioner")]
        assert binding["roleRef"]["name"] == "urumi-store-manager"
        # The password shown once at creation is the one WordPress is installed with.
        creds = objects[("Secret", "store-credentials")]["data"]
        assert base64.b64decode(creds["wordpress-password"]).decode() == created["admin_password"]
        mcp = objects[("Secret", "store-mcp")]["data"]
        assert base64.b64decode(mcp["WC_CONSUMER_KEY"]).decode().startswith("ck_")
        db_name = mcp_db_name(created["id"])
        assert db_name in base64.b64decode(mcp["DB_URL"]).decode()
        assert db_name in rig.mcp.roles

        values = rig.helm.values[ns]
        assert values["wordpress"]["wordpressHost"] == f"{created['slug']}.localtest.me"
        env = values["store-mcp"]["env"]
        assert env["STORE_NAME"] == "Acme Outdoor"
        assert env["WC_BASE_URL"] == f"http://{created['slug']}.localtest.me"
        assert env["WC_CONNECT_URL"] == f"http://store-wordpress.{ns}.svc.cluster.local"
        assert values["setup"]["runId"] == "0"
        assert values["store-mcp"]["image"] == {
            "repository": "urumi/woocommerce-mcp",
            "tag": "abc123",
            "pullPolicy": "IfNotPresent",
        }

        connector = await sql(
            database_dsn,
            "SELECT c.kind, c.url, c.owner_team_id, tc.allow_all_tools FROM connectors c "
            "JOIN team_connectors tc ON tc.connector_id = c.id WHERE c.slug = $1",
            created["slug"],
        )
        assert connector[0]["kind"] == "store"
        assert connector[0]["url"] == f"http://store-mcp.{ns}.svc.cluster.local:8000/mcp"
        assert str(connector[0]["owner_team_id"]) == rig.team_id
        assert connector[0]["allow_all_tools"] is True
        steps = [
            r["details"]
            for r in await sql(
                database_dsn,
                "SELECT details FROM audit_events WHERE target_id = $1 AND action = 'store.step' "
                "ORDER BY created_at",
                created["id"],
            )
        ]
        assert len(steps) == 6
        assert (await _events(database_dsn, created["id"]))[-2:] == [
            "store.connector_registered",
            "store.ready",
        ]


async def test_stores_run_in_parallel_with_a_worker_pool(rigs: RigFactory) -> None:
    async with rigs() as rig:
        created = await asyncio.gather(*(rig.create() for _ in range(2)))
        other = await signup(rig.client, "second")
        team = await rig.client.post("/v1/teams", headers=other.auth, json={"name": "Second"})
        third = await rig.client.post(
            f"/v1/teams/{team.json()['id']}/stores", headers=other.auth, json={"name": "Third"}
        )
        ids = [c["id"] for c in created] + [third.json()["id"]]
        provisioner = rig.provisioner(workers=3)
        stop = asyncio.Event()
        runner = asyncio.create_task(provisioner.run(stop))
        try:
            async with asyncio.timeout(20):
                while True:
                    statuses = {(await rig.store(i))["status"] for i in ids}
                    if statuses == {"ready"}:
                        break
                    await asyncio.sleep(0.05)
        finally:
            stop.set()
            await runner


async def test_two_workers_never_claim_the_same_store(rigs: RigFactory) -> None:
    async with rigs() as rig:
        await rig.create()
        first, second = rig.provisioner("worker-a"), rig.provisioner("worker-b")

        claims = await asyncio.gather(first.claim(), second.claim())

        assert sorted(c is None for c in claims) == [False, True]


# --- restarts and retries -------------------------------------------------------------------------


async def test_a_restarted_provisioner_resumes_from_the_saved_step(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        created = await rig.create()
        crashed = rig.provisioner("crashed")
        # The first worker gets as far as the release, then dies while Helm is mid-install.
        assert await crashed.claim() is not None
        await sql(
            database_dsn,
            "UPDATE stores SET step = 'mcp-db', lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            uuid.UUID(created["id"]),
        )
        ns = created["namespace"]
        rig.kube.objects[("Namespace", "", ns)] = {
            "kind": "Namespace",
            "metadata": {"name": ns, "labels": {"urumi.io/store": created["id"]}},
        }
        rig.kube.objects[("Secret", ns, "store-mcp")] = {
            "data": {
                "DB_URL": base64.b64encode(b"postgresql://u:pw_0123456789abcdefghijkl@h/d").decode()
            }
        }
        rig.helm.releases[ns] = ReleaseStatus("pending-install", 1)

        await rig.drive(rig.provisioner("successor"))

        row = await rig.store(created["id"])
        assert row["status"] == "ready"
        assert ("uninstall", ns) in rig.helm.calls  # the half-done install was cleared first
        assert rig.helm.calls.index(("uninstall", ns)) < rig.helm.calls.index(("upgrade", ns))
        # Steps before the saved one were not repeated.
        assert ("apply", "Namespace", ns) not in rig.kube.calls


async def test_rerunning_the_secrets_step_never_rotates_passwords(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        created = await rig.create()
        await rig.drive(rig.provisioner())
        ns = created["namespace"]
        before = dict(rig.kube.objects[("Secret", ns, "store-credentials")]["data"])
        await sql(
            database_dsn,
            "UPDATE stores SET status = 'provisioning', step = 'namespace' WHERE id = $1",
            uuid.UUID(created["id"]),
        )

        await rig.drive(rig.provisioner())

        assert rig.kube.objects[("Secret", ns, "store-credentials")]["data"] == before
        assert (await rig.store(created["id"]))["status"] == "ready"


async def test_a_failed_setup_job_is_retried_with_a_new_job(rigs: RigFactory) -> None:
    async with rigs() as rig:
        rig.helm.job_outcomes = ["Failed", "Complete"]
        created = await rig.create()

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert (row["status"], row["attempts"]) == ("ready", 1)
        jobs = [k[2] for k in rig.kube.objects if k[0] == "Job"]
        assert jobs == ["store-setup-1"]


async def test_too_many_failures_fail_the_store_and_clean_up(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        rig.helm.job_outcomes = ["Failed", "Failed", "Failed"]
        created = await rig.create()
        ns = created["namespace"]

        await rig.drive(rig.provisioner(max_attempts=3))

        row = await rig.store(created["id"])
        assert row["status"] == "failed"
        assert "setup job failed" in row["status_reason"]
        assert "WordPress did not finish installing" in row["status_reason"]
        assert ("Namespace", "", ns) not in rig.kube.objects
        assert ns not in rig.helm.releases
        assert mcp_db_name(created["id"]) in rig.mcp.dropped
        assert "store.failed" in await _events(database_dsn, created["id"])


async def test_image_pull_errors_fail_fast(rigs: RigFactory) -> None:
    async with rigs() as rig:
        rig.helm.pod_waiting = "ImagePullBackOff"
        created = await rig.create()

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert row["status"] == "failed"
        assert row["attempts"] == 0
        assert "ImagePullBackOff" in row["status_reason"]
        assert row["status_reason"].startswith("Failed waiting for WordPress")


async def test_the_deadline_fails_a_store_with_what_it_waited_for(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        rig.helm.ready_after = 10_000
        created = await rig.create()

        async def expire() -> None:
            row = await rig.store(created["id"])
            if row["step"] == "release":
                await sql(
                    database_dsn,
                    "UPDATE stores SET deadline_at = now() - interval '1 second' WHERE id = $1",
                    uuid.UUID(created["id"]),
                )

        rig.on_sleep.append(expire)

        await rig.drive(rig.provisioner())

        reason = (await rig.store(created["id"]))["status_reason"]
        assert reason.startswith("Timed out waiting for WordPress, MariaDB and the MCP server")
        assert "WordPress 0/1" in reason


async def test_retry_after_failure_provisions_again(rigs: RigFactory) -> None:
    async with rigs() as rig:
        rig.helm.pod_waiting = "ImagePullBackOff"
        created = await rig.create()
        await rig.drive(rig.provisioner())
        rig.helm.pod_waiting = None

        retried = await rig.client.post(
            f"/v1/teams/{rig.team_id}/stores/{created['id']}/retry", headers=rig.user.auth
        )
        await rig.drive(rig.provisioner())

        assert retried.status_code == 202
        assert (await rig.store(created["id"]))["status"] == "ready"


async def test_a_foreign_namespace_is_never_adopted(rigs: RigFactory) -> None:
    async with rigs() as rig:
        created = await rig.create()
        ns = created["namespace"]
        rig.kube.objects[("Namespace", "", ns)] = {
            "kind": "Namespace",
            "metadata": {"name": ns, "labels": {"team": "someone-else"}},
        }

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert row["status"] == "failed"
        assert "does not belong to this store" in row["status_reason"]


async def test_forbidden_by_the_cluster_is_not_retried(rigs: RigFactory) -> None:
    async with rigs() as rig:
        created = await rig.create()

        async def forbidden(manifest: dict[str, Any], *, namespace: str | None = None) -> Any:
            raise ClusterError("Kubernetes API error (HTTP 403): denied by policy", status=403)

        rig.kube.apply = forbidden  # type: ignore[method-assign]

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert (row["status"], row["attempts"]) == ("failed", 0)
        assert "denied by policy" in row["status_reason"]


# --- delete and drift -----------------------------------------------------------------------------


async def test_delete_removes_connector_namespace_and_database(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        created = await rig.create()
        await rig.drive(rig.provisioner())
        ns = created["namespace"]

        deleted = await rig.client.delete(
            f"/v1/teams/{rig.team_id}/stores/{created['id']}", headers=rig.user.auth
        )
        await rig.drive(rig.provisioner())

        assert deleted.status_code == 202
        row = await rig.store(created["id"])
        assert (row["status"], row["connector_id"]) == ("deleted", None)
        assert row["deleted_at"] is not None
        assert not await sql(
            database_dsn, "SELECT 1 FROM connectors WHERE slug = $1", created["slug"]
        )
        assert not rig.kube.in_namespace(ns)
        assert ("Namespace", "", ns) not in rig.kube.objects
        assert ("uninstall", ns) in rig.helm.calls
        assert mcp_db_name(created["id"]) in rig.mcp.dropped
        listed = await rig.client.get(f"/v1/teams/{rig.team_id}/stores", headers=rig.user.auth)
        assert created["id"] not in {s["id"] for s in listed.json()}
        events = await _events(database_dsn, created["id"])
        assert events[-3:] == ["store.delete", "store.connector_removed", "store.deleted"]


async def test_delete_while_provisioning_stops_and_tears_down(rigs: RigFactory) -> None:
    async with rigs() as rig:
        created = await rig.create()

        async def delete_once() -> None:
            if rig.sleeps and len(rig.sleeps) == 1:
                await rig.client.delete(
                    f"/v1/teams/{rig.team_id}/stores/{created['id']}", headers=rig.user.auth
                )

        rig.on_sleep.append(delete_once)
        rig.helm.ready_after = 3

        await rig.drive(rig.provisioner())

        row = await rig.store(created["id"])
        assert row["status"] == "deleted"
        assert ("Namespace", "", created["namespace"]) not in rig.kube.objects


async def test_drift_marks_a_store_whose_namespace_vanished(
    rigs: RigFactory, database_dsn: str
) -> None:
    async with rigs() as rig:
        created = await rig.create()
        provisioner = rig.provisioner()
        await rig.drive(provisioner)
        del rig.kube.objects[("Namespace", "", created["namespace"])]

        marked = await provisioner.check_drift()

        assert created["slug"] in marked
        row = await rig.store(created["id"])
        assert (row["status"], row["status_reason"]) == ("failed", "Resources missing: namespace")
        enabled = await sql(
            database_dsn, "SELECT enabled FROM connectors WHERE slug = $1", created["slug"]
        )
        assert enabled[0]["enabled"] is False


# --- pieces ---------------------------------------------------------------------------------------


def test_steps_follow_each_other() -> None:
    assert next_step(None) == "namespace"
    assert next_step("release") == "wait-ready"
    assert next_step("register") == "register"


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (
            "docker.io/urumi/store-wordpress:abc",
            {
                "registry": "docker.io",
                "repository": "urumi/store-wordpress",
                "tag": "abc",
                "digest": "",
            },
        ),
        (
            "urumi/woocommerce-mcp:1.2",
            {
                "registry": "docker.io",
                "repository": "urumi/woocommerce-mcp",
                "tag": "1.2",
                "digest": "",
            },
        ),
        (
            "ghcr.io/acme/store@sha256:" + "a" * 64,
            {
                "registry": "ghcr.io",
                "repository": "acme/store",
                "tag": "",
                "digest": "sha256:" + "a" * 64,
            },
        ),
        (
            "localhost:5000/store",
            {"registry": "localhost:5000", "repository": "store", "tag": "latest", "digest": ""},
        ),
    ],
)
def test_split_image(image: str, expected: dict[str, str]) -> None:
    assert split_image(image) == expected


def test_tls_values_use_https_and_the_issuer() -> None:
    store = Store(
        id=uuid.uuid4(),
        name="Acme",
        slug="acme",
        namespace="store-acme",
        release="store",
        host="acme.shop.example.com",
    )
    values = store_values(
        store,
        settings=ProvisionerSettings(_env_file=None, tls_cluster_issuer="letsencrypt"),
        stores=StoreSettings(_env_file=None, base_domain="shop.example.com", tls=True),
    )
    assert values["wordpress"]["wordpressScheme"] == "https"
    assert values["wordpress"]["ingress"]["tls"] is True
    assert values["wordpress"]["ingress"]["annotations"] == {
        "cert-manager.io/cluster-issuer": "letsencrypt"
    }
    # The MCP server still signs for, and talks plain HTTP to, the in-cluster Service.
    assert values["store-mcp"]["env"]["WC_BASE_URL"] == "http://acme.shop.example.com"


async def test_recover_clears_interrupted_releases() -> None:
    kube = FakeKube()
    helm = FakeHelm(kube)
    helm.releases["a"] = ReleaseStatus("pending-install", 1)
    helm.releases["b"] = ReleaseStatus("pending-upgrade", 3)
    helm.releases["c"] = ReleaseStatus("deployed", 2)

    assert await recover(helm, "store", "a") is None
    assert await recover(helm, "store", "b") == ReleaseStatus("deployed", 4)
    assert await recover(helm, "store", "c") == ReleaseStatus("deployed", 2)
    assert helm.calls == [("uninstall", "a"), ("rollback", "b")]


async def test_helm_runner_calls_the_binary(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    script = tmp_path / "helm"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'case "$1" in\n'
        '  status) if [ "$2" = missing ]; then echo "Error: release: not found" >&2; exit 1; fi;'
        ' echo \'{"info":{"status":"deployed"},"version":2}\';;\n'
        '  upgrade) cat "$8" >> ' + str(log) + ";;\n"
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    runner = HelmRunner(binary=str(script), chart="/charts/urumi-store", timeout_seconds=30)

    assert await runner.status("missing", "ns") is None
    status = await runner.upgrade_install("store", "store-acme", {"store": {"slug": "acme"}})

    assert status == ReleaseStatus("deployed", 2)
    calls = log.read_text()
    assert "upgrade --install store /charts/urumi-store -n store-acme -f" in calls
    assert '{"store": {"slug": "acme"}}' in calls
    assert "--history-max 3" in calls


async def test_postgres_role_and_schema_per_store() -> None:
    admin = _admin_dsn()
    db = PostgresMcpDatabase(admin)
    a, b = mcp_db_name(str(uuid.uuid4())), mcp_db_name(str(uuid.uuid4()))
    pw_a, pw_b = "pw_" + uuid.uuid4().hex, "pw_" + uuid.uuid4().hex
    try:
        await db.ensure(a, pw_a)
        await db.ensure(a, pw_a)  # idempotent
        await db.ensure(b, pw_b)
        conn_a = await asyncpg.connect(db.dsn_for(a, pw_a).replace("+asyncpg", ""))
        conn_b = await asyncpg.connect(db.dsn_for(b, pw_b).replace("+asyncpg", ""))
        try:
            await conn_a.execute("CREATE TABLE operations (id int)")  # lands in its schema
            assert await conn_a.fetchval("SELECT current_schema()") == a
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn_b.fetch(f"SELECT * FROM {a}.operations")  # noqa: S608
        finally:
            await conn_a.close()
            await conn_b.close()
    finally:
        await db.drop(a)
        await db.drop(b)
        await db.drop(a)  # tolerates absence
    rows = await sql(admin, "SELECT 1 FROM pg_roles WHERE rolname = ANY($1)", [a, b])
    assert rows == []


def test_postgres_names_and_passwords_are_validated() -> None:
    db = PostgresMcpDatabase("postgresql://u:p@localhost/db")
    with pytest.raises(ValueError, match="name"):
        asyncio.run(db.ensure("store_x; DROP ROLE postgres", "p" * 30))
    with pytest.raises(ValueError, match="password"):
        asyncio.run(db.ensure(mcp_db_name(str(uuid.uuid4())), "short'"))
