"""Hosted MCP servers: generated Kubernetes objects and the deploy / update / remove lifecycle.

The Kubernetes API is replaced by an in-memory fake; what is checked is which objects the
gateway asks for and that registry rows and cluster objects stay in step.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from gateway.app import create_app
from gateway.config import HostedSettings
from gateway.hosted import manifests
from gateway.hosted.kube import ClusterError
from gateway.hosted.manifests import ClusterPolicy, HostedSpec
from gateway.hosted.service import HostedDeployer
from tests.conftest import BASE_URL, make_settings, signup, sql

POLICY = ClusterPolicy(
    namespace="urumi-mcp",
    gateway_namespace="urumi",
    gateway_pod_label=("app.kubernetes.io/name", "gateway"),
    run_as_user=65532,
)


def _spec(**overrides: Any) -> HostedSpec:
    values: dict[str, Any] = {
        "connector_id": uuid.uuid4(),
        "name": "mcp-search",
        "image": "mcp/duckduckgo:latest",
        "port": 8000,
        "args": ("--transport", "streamable-http"),
        "env": {"REGION": "us"},
        "cpu_millicores": 200,
        "memory_mib": 128,
        "egress": "internet",
    }
    return HostedSpec(**(values | overrides))


def test_deployment_is_non_root_read_only_and_limited() -> None:
    deployment = manifests.deployment(_spec(), POLICY)
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert container["resources"]["limits"] == {"cpu": "200m", "memory": "128Mi"}
    assert container["envFrom"] == [{"secretRef": {"name": "mcp-search-env", "optional": True}}]
    assert {"name": "REGION", "value": "us"} in container["env"]
    assert container["args"] == ["--transport", "streamable-http"]


def test_network_policy_admits_only_the_gateway_and_blocks_private_egress() -> None:
    policy = manifests.network_policy(_spec(), POLICY)["spec"]
    source = policy["ingress"][0]["from"][0]

    assert source["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "urumi"}
    assert source["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "gateway"}
    internet = policy["egress"][1]["to"][0]["ipBlock"]
    assert {"10.0.0.0/8", "169.254.0.0/16"} <= set(internet["except"])

    sealed = manifests.network_policy(_spec(egress="none"), POLICY)["spec"]
    assert len(sealed["egress"]) == 1  # DNS only


class FakeKube:
    namespace = "urumi-mcp"

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_on: str | None = None

    async def apply(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if manifest["kind"] == self.fail_on:
            raise ClusterError("Kubernetes API error (HTTP 403): forbidden", status=403)
        if manifest["kind"] == "Deployment":
            manifest = manifest | {"status": {"readyReplicas": 1}}
        self.objects[(manifest["kind"], manifest["metadata"]["name"])] = manifest
        return manifest

    async def merge_patch(self, kind: str, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        obj = self.objects[(kind, name)]
        if kind == "Deployment":
            obj["spec"]["replicas"] = patch["spec"]["replicas"]
        if kind == "Secret":
            data = obj["stringData"] | patch["stringData"]
            obj["stringData"] = {k: v for k, v in data.items() if k not in patch["data"]}
        return obj

    async def get(self, kind: str, name: str) -> dict[str, Any] | None:
        return self.objects.get((kind, name))

    async def list(self, kind: str, *, label_selector: str) -> list[dict[str, Any]]:
        return []

    async def delete(self, kind: str, name: str) -> bool:
        return self.objects.pop((kind, name), None) is not None


@pytest.fixture
def kube() -> FakeKube:
    return FakeKube()


@asynccontextmanager
async def hosted_client(dsn: str, kube: FakeKube) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(dsn)
    settings.hosted = HostedSettings(
        _env_file=None, enabled=True, allowed_images=["mcp/duckduckgo:*"]
    )
    app = create_app(settings)
    container = app.state.container
    deployer = HostedDeployer(kube, settings.hosted)  # type: ignore[arg-type]
    container.hosted._deployer = deployer
    container.connectors._deployer = deployer
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client,
    ):
        yield client


def _deploy_body(slug: str, **overrides: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "display_name": "Web search",
        "image": "mcp/duckduckgo:latest",
        "port": 8000,
        "args": ["python", "-m", "duckduckgo_mcp_server.server"],
        "secret_env": {"API_TOKEN": "t0ps3cret"},
    } | overrides


async def _platform_admin(client: httpx.AsyncClient, dsn: str) -> dict[str, str]:
    admin = await signup(client, "operator")
    await sql(dsn, "UPDATE users SET is_platform_admin = true WHERE id = $1", uuid.UUID(admin.id))
    return admin.auth


async def test_deploy_creates_objects_and_a_connector_without_leaking_secrets(
    database_dsn: str, kube: FakeKube
) -> None:
    slug = f"search-{uuid.uuid4().hex[:6]}"
    async with hosted_client(database_dsn, kube) as client:
        auth = await _platform_admin(client, database_dsn)
        member = await signup(client, "member")
        by_member = await client.post(
            "/v1/hosted-servers", headers=member.auth, json=_deploy_body(slug)
        )
        not_allowed = await client.post(
            "/v1/hosted-servers",
            headers=auth,
            json=_deploy_body(slug, image="evil.example.com/miner:latest"),
        )
        deployed = await client.post("/v1/hosted-servers", headers=auth, json=_deploy_body(slug))
        listed = await client.get("/v1/hosted-servers", headers=auth)
        connectors = await client.get("/v1/connectors", headers=auth)

    assert by_member.status_code == 403
    assert not_allowed.status_code == 422
    assert deployed.status_code == 201, deployed.text
    body = deployed.json()
    name = f"mcp-{slug}"
    assert body["connector"]["kind"] == "hosted"
    assert body["connector"]["url"] == f"http://{name}.urumi-mcp.svc.cluster.local:8000/mcp"
    assert body["secret_env_keys"] == ["API_TOKEN"]
    assert {k for k, n in kube.objects if n.startswith(name)} == {
        "Secret",
        "Service",
        "NetworkPolicy",
        "Deployment",
    }
    assert kube.objects[("Secret", f"{name}-env")]["stringData"] == {"API_TOKEN": "t0ps3cret"}
    assert listed.json()[0]["status"]["phase"] == "ready"
    for response in (deployed, listed, connectors):
        assert "t0ps3cret" not in response.text


async def test_failed_deploy_leaves_nothing_behind(database_dsn: str, kube: FakeKube) -> None:
    slug = f"broken-{uuid.uuid4().hex[:6]}"
    kube.fail_on = "Deployment"
    async with hosted_client(database_dsn, kube) as client:
        auth = await _platform_admin(client, database_dsn)
        response = await client.post("/v1/hosted-servers", headers=auth, json=_deploy_body(slug))
    rows = await sql(database_dsn, "SELECT 1 FROM connectors WHERE slug = $1", slug)

    assert response.status_code == 502
    assert rows == []
    assert kube.objects == {}


async def test_disable_scales_to_zero_and_remove_tears_down(
    database_dsn: str, kube: FakeKube
) -> None:
    slug = f"search-{uuid.uuid4().hex[:6]}"
    name = f"mcp-{slug}"
    async with hosted_client(database_dsn, kube) as client:
        auth = await _platform_admin(client, database_dsn)
        deployed = await client.post("/v1/hosted-servers", headers=auth, json=_deploy_body(slug))
        connector_id = deployed.json()["connector"]["id"]
        url_change = await client.patch(
            f"/v1/connectors/{connector_id}", headers=auth, json={"url": "http://127.0.0.1:1/x"}
        )
        disabled = await client.patch(
            f"/v1/connectors/{connector_id}", headers=auth, json={"enabled": False}
        )
        replicas = kube.objects[("Deployment", name)]["spec"]["replicas"]
        secret_update = await client.patch(
            f"/v1/hosted-servers/{connector_id}",
            headers=auth,
            json={"secret_env": {"API_TOKEN": None, "OTHER": "x"}},
        )
        removed = await client.delete(f"/v1/connectors/{connector_id}", headers=auth)
    rows = await sql(database_dsn, "SELECT 1 FROM hosted_servers WHERE k8s_name = $1", name)

    assert url_change.status_code == 422
    assert disabled.status_code == 200 and replicas == 0
    assert secret_update.status_code == 200, secret_update.text
    assert secret_update.json()["secret_env_keys"] == ["OTHER"]
    assert secret_update.json()["status"]["phase"] == "stopped"  # still disabled
    assert removed.status_code == 204
    assert not [key for key in kube.objects if key[1].startswith(name)]
    assert rows == []
