"""Hosted MCP servers: a platform admin names a container image, the gateway runs it.

:class:`HostedDeployer` talks to Kubernetes only; :class:`HostedServerService` owns the
registry rows, validation, audit and the order of operations. A hosted server is an ordinary
connector (kind ``hosted``) whose URL is its in-cluster Service, so discovery, authorization,
invocation and auditing are exactly those of any other connector.

Only images matching ``HOSTED_ALLOWED_IMAGES`` can be deployed, and the gateway's Kubernetes
permissions are limited to its own namespace for hosted servers (see the Helm chart).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from gateway.config import HostedSettings, UpstreamSettings
from gateway.db.engine import Database
from gateway.db.models import Connector, ConnectorKind, HostedServer
from gateway.errors import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from gateway.hosted import manifests
from gateway.hosted.kube import ClusterError, KubeApi
from gateway.hosted.manifests import ClusterPolicy, Egress, HostedSpec
from gateway.services.audit import AuditLog
from gateway.services.principal import Principal
from gateway.upstream.client import McpUpstreamClient
from gateway.upstream.credentials import CredentialCipher, UpstreamCredential

logger = logging.getLogger(__name__)

_IMAGE_RE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$"
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,199}$")
_RESERVED_ENV = frozenset({"HOME", "TMPDIR"})
_MAX_ENV = 50
_MAX_ARGS = 30
_MAX_NAME = 59  # "mcp-" + slug must fit a 63-character DNS label

# Pod waiting reasons that will not fix themselves.
_FAILED_REASONS = frozenset(
    {
        "ErrImagePull",
        "ImagePullBackOff",
        "InvalidImageName",
        "CrashLoopBackOff",
        "CreateContainerConfigError",
        "CreateContainerError",
        "RunContainerError",
    }
)

Phase = Literal["deploying", "ready", "failed", "stopped", "missing", "unknown"]


@dataclass(frozen=True, slots=True)
class HostedStatus:
    phase: Phase
    reason: str | None = None
    ready_replicas: int = 0
    restarts: int = 0


@dataclass(frozen=True, slots=True)
class NewHostedServer:
    slug: str
    display_name: str
    image: str
    port: int
    path: str = "/mcp"
    description: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict, repr=False)
    credential: UpstreamCredential | None = None
    cpu_millicores: int | None = None
    memory_mib: int | None = None
    egress: Egress = "internet"
    timeout_seconds: float | None = None
    max_concurrency: int | None = None


@dataclass(frozen=True, slots=True)
class HostedChange:
    image: str | None = None
    args: tuple[str, ...] | None = None
    env: dict[str, str] | None = None
    # Values to set; a key mapped to None is removed. Other keys are left as they are.
    secret_env: dict[str, str | None] | None = field(default=None, repr=False)
    cpu_millicores: int | None = None
    memory_mib: int | None = None
    egress: Egress | None = None


@dataclass(frozen=True, slots=True)
class HostedView:
    connector: Connector
    server: HostedServer
    status: HostedStatus


class HostedDeployer:
    """Applies, scales, inspects and removes a hosted server's Kubernetes objects."""

    def __init__(self, kube: KubeApi, settings: HostedSettings) -> None:
        self._kube = kube
        self.policy = ClusterPolicy(
            namespace=settings.namespace,
            gateway_namespace=settings.gateway_namespace,
            gateway_pod_label=settings.gateway_pod_selector,
            run_as_user=settings.run_as_user,
        )

    async def deploy(self, spec: HostedSpec, secret_values: dict[str, str] | None) -> None:
        """Create or update every object. ``secret_values`` None leaves the Secret alone."""
        if secret_values is not None:
            await self._kube.apply(manifests.secret(spec, self.policy, secret_values))
        await self._kube.apply(manifests.service(spec, self.policy))
        await self._kube.apply(manifests.network_policy(spec, self.policy))
        await self._kube.apply(manifests.deployment(spec, self.policy))

    async def patch_secret(self, name: str, changes: dict[str, str | None]) -> None:
        current = await self._kube.get("Secret", manifests.secret_name(name))
        if current is None:
            raise ClusterError("The hosted server's Secret is missing; redeploy it.")
        patch: dict[str, Any] = {
            "stringData": {k: v for k, v in changes.items() if v is not None},
            "data": {k: None for k, v in changes.items() if v is None},
        }
        await self._kube.merge_patch("Secret", manifests.secret_name(name), patch)

    async def scale(self, name: str, replicas: int) -> None:
        await self._kube.merge_patch("Deployment", name, {"spec": {"replicas": replicas}})

    async def teardown(self, name: str) -> None:
        """Remove every object of the server. Missing objects are fine (retry-safe)."""
        await self._kube.delete("Deployment", name)
        await self._kube.delete("Service", name)
        await self._kube.delete("NetworkPolicy", name)
        await self._kube.delete("Secret", manifests.secret_name(name))

    async def status(self, name: str) -> HostedStatus:  # noqa: PLR0911 - one return per phase
        try:
            deployment = await self._kube.get("Deployment", name)
            if deployment is None:
                return HostedStatus("missing", "The Deployment does not exist.")
            pods = await self._kube.list("Pod", label_selector=manifests.label_selector(name))
        except ClusterError as exc:
            return HostedStatus("unknown", exc.public_message)
        spec = deployment.get("spec", {})
        state = deployment.get("status", {})
        ready = int(state.get("readyReplicas") or 0)
        restarts = sum(
            int(cs.get("restartCount") or 0)
            for pod in pods
            for cs in pod.get("status", {}).get("containerStatuses") or []
        )
        if spec.get("replicas", 1) == 0:
            return HostedStatus("stopped", None, ready, restarts)
        if ready >= 1:
            return HostedStatus("ready", None, ready, restarts)
        for pod in pods:
            for cs in pod.get("status", {}).get("containerStatuses") or []:
                waiting = (cs.get("state") or {}).get("waiting") or {}
                if waiting.get("reason") in _FAILED_REASONS:
                    message = str(waiting.get("message") or "")[:300]
                    reason = f"{waiting['reason']}: {message}" if message else waiting["reason"]
                    return HostedStatus("failed", reason, ready, restarts)
        for condition in state.get("conditions") or []:
            failed_rollout = (
                condition.get("type") == "Progressing"
                and condition.get("reason") == "ProgressDeadlineExceeded"
            )
            if failed_rollout or (
                condition.get("type") == "ReplicaFailure" and condition.get("status") == "True"
            ):
                return HostedStatus(
                    "failed", str(condition.get("message") or "")[:300], ready, restarts
                )
        return HostedStatus("deploying", None, ready, restarts)


class HostedServerService:
    def __init__(
        self,
        db: Database,
        *,
        deployer: HostedDeployer | None,
        upstream: McpUpstreamClient,
        audit: AuditLog,
        cipher: CredentialCipher,
        settings: HostedSettings,
        upstream_settings: UpstreamSettings,
    ) -> None:
        self._db = db
        self._deployer = deployer
        self._upstream = upstream
        self._audit = audit
        self._cipher = cipher
        self._settings = settings
        self._upstream_settings = upstream_settings

    @property
    def enabled(self) -> bool:
        return self._deployer is not None

    @property
    def settings(self) -> HostedSettings:
        return self._settings

    def _require(self, principal: Principal) -> HostedDeployer:
        if not principal.is_platform_admin:
            raise PermissionDeniedError("Managing hosted MCP servers requires a platform admin.")
        if self._deployer is None:
            raise InvalidInputError("Hosted MCP servers are not enabled on this gateway.")
        return self._deployer

    # --- validation -----------------------------------------------------------------------------

    def _check_image(self, image: str) -> str:
        image = image.strip()
        if len(image) > 300 or not _IMAGE_RE.match(image):
            raise InvalidInputError("The image reference is not valid.")
        if not self._settings.is_allowed_image(image):
            raise InvalidInputError(
                "This image is not on the gateway's allow-list (HOSTED_ALLOWED_IMAGES)."
            )
        return image

    @staticmethod
    def _check_env(env: dict[str, Any], *, what: str) -> None:
        if len(env) > _MAX_ENV:
            raise InvalidInputError(f"At most {_MAX_ENV} {what} are allowed.")
        for name, value in env.items():
            if not _ENV_NAME_RE.match(name) or name in _RESERVED_ENV:
                raise InvalidInputError(f"'{name[:40]}' is not an allowed variable name.")
            if value is not None and len(value) > 4096:
                raise InvalidInputError(f"The value of {name} is too long.")

    @staticmethod
    def _check_args(args: tuple[str, ...]) -> tuple[str, ...]:
        if len(args) > _MAX_ARGS or any(len(a) > 500 or "\0" in a for a in args):
            raise InvalidInputError(f"At most {_MAX_ARGS} arguments of up to 500 characters.")
        return args

    def _cpu(self, value: int | None) -> int:
        cpu = value or self._settings.default_cpu_millicores
        if not 10 <= cpu <= self._settings.max_cpu_millicores:
            raise InvalidInputError(
                f"cpu_millicores must be between 10 and {self._settings.max_cpu_millicores}."
            )
        return cpu

    def _memory(self, value: int | None) -> int:
        memory = value or self._settings.default_memory_mib
        if not 32 <= memory <= self._settings.max_memory_mib:
            raise InvalidInputError(
                f"memory_mib must be between 32 and {self._settings.max_memory_mib}."
            )
        return memory

    def _timeout(self, value: float | None) -> float:
        if value is None:
            return self._upstream_settings.default_call_timeout_seconds
        if not 0 < value <= self._upstream_settings.max_call_timeout_seconds:
            raise InvalidInputError(
                "timeout_seconds must be between 0 and "
                f"{self._upstream_settings.max_call_timeout_seconds}."
            )
        return value

    @staticmethod
    def _spec(connector: Connector, server: HostedServer) -> HostedSpec:
        return HostedSpec(
            connector_id=connector.id,
            name=server.k8s_name,
            image=server.image,
            port=server.port,
            args=tuple(server.args),
            env=dict(server.env),
            cpu_millicores=server.cpu_millicores,
            memory_mib=server.memory_mib,
            egress="none" if server.egress == "none" else "internet",
            replicas=1 if connector.enabled else 0,
            config_revision=server.config_revision,
        )

    # --- operations -----------------------------------------------------------------------------

    async def create(self, principal: Principal, new: NewHostedServer) -> HostedView:
        deployer = self._require(principal)
        if len(new.slug) > _MAX_NAME or not re.match(r"^[a-z0-9][a-z0-9-]*$", new.slug):
            raise InvalidInputError(
                f"slug must be lowercase letters, digits and '-', at most {_MAX_NAME} characters."
            )
        if not 1 <= new.port <= 65535:
            raise InvalidInputError("port must be between 1 and 65535.")
        if not _PATH_RE.match(new.path):
            raise InvalidInputError("path must start with '/' and be a plain URL path.")
        self._check_env(new.env, what="environment variables")
        self._check_env(new.secret_env, what="secret variables")
        if set(new.env) & set(new.secret_env):
            raise InvalidInputError("A variable cannot be both plain and secret.")
        name = f"mcp-{new.slug}"
        connector = Connector(
            id=uuid.uuid4(),
            slug=new.slug,
            display_name=new.display_name.strip(),
            description=new.description.strip(),
            url=manifests.service_url(name, self._settings.namespace, new.port, new.path),
            kind=ConnectorKind.HOSTED.value,
            enabled=True,
            timeout_seconds=self._timeout(new.timeout_seconds),
            max_concurrency=new.max_concurrency or self._upstream_settings.default_max_concurrency,
            created_by_user_id=principal.user_id,
        )
        if new.credential is not None:
            connector.credential_header = new.credential.header_name
            connector.credential_ciphertext = self._cipher.encrypt(new.credential.value)
        server = HostedServer(
            connector_id=connector.id,
            k8s_name=name,
            namespace=self._settings.namespace,
            image=self._check_image(new.image),
            port=new.port,
            path=new.path,
            args=list(self._check_args(new.args)),
            env=dict(new.env),
            secret_keys=sorted(new.secret_env),
            cpu_millicores=self._cpu(new.cpu_millicores),
            memory_mib=self._memory(new.memory_mib),
            egress=new.egress,
            config_revision=uuid.uuid4().hex,
        )
        async with self._db.transaction() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Connector)
                .where(Connector.kind == ConnectorKind.HOSTED.value)
            )
            if (count or 0) >= self._settings.max_servers:
                raise ConflictError(
                    f"The platform already runs {self._settings.max_servers} hosted servers."
                )
            session.add(connector)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError("A connector with this slug already exists.") from exc
            session.add(server)
            await session.flush()

        # Kubernetes is changed only after the rows exist, so a failure here can always be
        # cleaned up by name; on failure nothing is left behind in either place.
        try:
            await deployer.deploy(self._spec(connector, server), dict(new.secret_env))
        except Exception:
            logger.warning("hosted deploy failed; rolling back", extra={"connector": new.slug})
            await self._rollback(deployer, connector, server)
            raise
        await self._audit.record(
            action="hosted_server.deploy",
            actor=principal,
            target_type="connector",
            target_id=connector.id,
            details={
                "slug": connector.slug,
                "image": server.image,
                "secret_keys": server.secret_keys,
                "credential": new.credential is not None,
            },
        )
        return HostedView(connector, server, HostedStatus("deploying"))

    async def _rollback(
        self, deployer: HostedDeployer, connector: Connector, server: HostedServer
    ) -> None:
        try:
            await deployer.teardown(server.k8s_name)
        except ClusterError:
            logger.exception("hosted rollback left objects behind", extra={"name": server.k8s_name})
        async with self._db.transaction() as session:
            row = await session.get(Connector, connector.id)
            if row is not None:
                await session.delete(row)

    async def list(self, principal: Principal) -> list[HostedView]:
        deployer = self._require(principal)
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(Connector, HostedServer)
                    .join(HostedServer, HostedServer.connector_id == Connector.id)
                    .order_by(Connector.slug)
                )
            ).all()
        return [HostedView(c, s, await deployer.status(s.k8s_name)) for c, s in rows]

    async def get(self, principal: Principal, connector_id: uuid.UUID) -> HostedView:
        deployer = self._require(principal)
        connector, server = await self._load(connector_id)
        return HostedView(connector, server, await deployer.status(server.k8s_name))

    async def _load(self, connector_id: uuid.UUID) -> tuple[Connector, HostedServer]:
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(Connector, HostedServer)
                    .join(HostedServer, HostedServer.connector_id == Connector.id)
                    .where(Connector.id == connector_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Hosted server not found.")
        return row[0], row[1]

    async def update(
        self, principal: Principal, connector_id: uuid.UUID, change: HostedChange
    ) -> HostedView:
        deployer = self._require(principal)
        details: dict[str, Any] = {}
        async with self._db.transaction() as session:
            server = await session.get(HostedServer, connector_id, with_for_update=True)
            connector = await session.get(Connector, connector_id)
            if server is None or connector is None:
                raise NotFoundError("Hosted server not found.")
            if change.image is not None:
                server.image = self._check_image(change.image)
                details["image"] = server.image
            if change.args is not None:
                server.args = list(self._check_args(change.args))
                details["args_changed"] = True
            if change.env is not None:
                self._check_env(change.env, what="environment variables")
                server.env = dict(change.env)
                details["env_keys"] = sorted(change.env)
            if change.secret_env:
                self._check_env(change.secret_env, what="secret variables")
                keys = set(server.secret_keys)
                keys |= {k for k, v in change.secret_env.items() if v is not None}
                keys -= {k for k, v in change.secret_env.items() if v is None}
                server.secret_keys = sorted(keys)
                server.config_revision = uuid.uuid4().hex  # restart pods to pick values up
                details["secret_keys"] = server.secret_keys
            if set(server.env) & set(server.secret_keys):
                raise InvalidInputError("A variable cannot be both plain and secret.")
            if change.cpu_millicores is not None:
                server.cpu_millicores = self._cpu(change.cpu_millicores)
            if change.memory_mib is not None:
                server.memory_mib = self._memory(change.memory_mib)
            if change.egress is not None:
                server.egress = change.egress
                details["egress"] = change.egress
            server.updated_at = datetime.now(UTC)
            # Kubernetes first, inside the transaction: if it refuses, the row is not changed.
            if change.secret_env:
                await deployer.patch_secret(server.k8s_name, change.secret_env)
            await deployer.deploy(self._spec(connector, server), None)
            self._audit.add(
                session,
                action="hosted_server.update",
                actor=principal,
                target_type="connector",
                target_id=connector_id,
                details=details,
            )
        self._upstream.invalidate(connector_id)
        return HostedView(connector, server, await deployer.status(server.k8s_name))

    async def restart(self, principal: Principal, connector_id: uuid.UUID) -> HostedView:
        """Roll the pod (e.g. after the image tag moved), keeping the spec."""
        deployer = self._require(principal)
        async with self._db.transaction() as session:
            server = await session.get(HostedServer, connector_id, with_for_update=True)
            connector = await session.get(Connector, connector_id)
            if server is None or connector is None:
                raise NotFoundError("Hosted server not found.")
            server.config_revision = uuid.uuid4().hex
            server.updated_at = datetime.now(UTC)
            await deployer.deploy(self._spec(connector, server), None)
            self._audit.add(
                session,
                action="hosted_server.restart",
                actor=principal,
                target_type="connector",
                target_id=connector_id,
            )
        self._upstream.invalidate(connector_id)
        return HostedView(connector, server, await deployer.status(server.k8s_name))
