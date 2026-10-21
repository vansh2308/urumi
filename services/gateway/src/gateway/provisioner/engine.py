"""The store state machine: claim a row, run its next step, save, repeat.

The database is the only source of truth. A worker claims a store with
``SELECT ... FOR UPDATE SKIP LOCKED`` and holds a lease on it (renewed in the background); a
provisioner that dies simply stops renewing, and the next one resumes from the saved ``step``.
Every step is "make it so" (server-side apply, create-if-missing, ``helm upgrade --install``,
polling), so running a step twice is harmless.

Provisioning steps, in order (``step`` holds the last one that finished):

=============  ===================================================================
namespace      namespace + RoleBinding, ResourceQuota, LimitRange, default-deny
secrets        WordPress/MariaDB passwords and the MCP server's REST key and DB_URL,
               created only if missing (a retry never rotates a live password)
mcp-db         the store's own Postgres role + schema for its MCP server
release        ``helm upgrade --install`` of the urumi-store chart (no waiting in Helm)
wait-ready     WordPress, MariaDB and the MCP server Ready; image/crash errors fail fast
setup          the WP-CLI setup Job (WooCommerce, permalinks, COD, REST key)
register       connector + team link + creator grant + status ready, in one transaction
=============  ===================================================================

Past ``deadline_at`` a store fails with the step and the last thing it was waiting for, and its
resources are removed. Deleting runs: connector rows, ``helm uninstall``, namespace (and with it
PVCs and Secrets), wait until it is gone, drop the MCP schema and role.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import asyncpg
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from gateway.config import ProvisionerSettings, StoreSettings, UpstreamSettings
from gateway.db.engine import Database
from gateway.db.models import AuditOutcome, Connector, ConnectorKind, Store, StoreStatus
from gateway.errors import AppError
from gateway.hosted.kube import ClusterError
from gateway.provisioner import manifests
from gateway.provisioner.helm import Helm, HelmError, recover
from gateway.provisioner.kube import Kube
from gateway.provisioner.mcp_db import McpDatabase
from gateway.provisioner.values import store_values
from gateway.services.audit import AuditLog
from gateway.stores.naming import mcp_db_name
from gateway.stores.service import register_store_connector
from gateway.upstream.credentials import CredentialCipher, CredentialUnavailableError

logger = logging.getLogger(__name__)

STEPS = ("namespace", "secrets", "mcp-db", "release", "wait-ready", "setup", "register")
STEP_LABELS = {
    "namespace": "creating the namespace",
    "secrets": "creating the store's secrets",
    "mcp-db": "creating the MCP server's database schema",
    "release": "installing the Helm release",
    "wait-ready": "waiting for WordPress, MariaDB and the MCP server to become ready",
    "setup": "setting up WooCommerce",
    "register": "registering the store's MCP server",
}
WORK_STATUSES = (
    StoreStatus.REQUESTED.value,
    StoreStatus.PROVISIONING.value,
    StoreStatus.DELETING.value,
)
# Waiting reasons that will not fix themselves: fail at once instead of at the deadline.
FATAL_WAITING = frozenset(
    {"ImagePullBackOff", "InvalidImageName", "CreateContainerConfigError", "ErrImageNeverPull"}
)
CRASH_RESTARTS = 5
WORKLOADS = (
    ("Deployment", "store-wordpress", "WordPress"),
    ("StatefulSet", "store-mariadb", "MariaDB"),
    ("Deployment", "store-mcp", "MCP server"),
)


class StepFailed(Exception):  # noqa: N818 - a step outcome, not a programming error
    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class LeaseLost(Exception):  # noqa: N818 - control flow
    """Another worker owns the store now (our lease expired); stop touching it."""


def next_step(done: str | None) -> str:
    return STEPS[0] if done is None else STEPS[min(STEPS.index(done) + 1, len(STEPS) - 1)]


@dataclass
class Lease:
    store_id: uuid.UUID
    owner: str
    lost: bool = False
    renewals: int = field(default=0)


class Provisioner:
    def __init__(
        self,
        db: Database,
        *,
        kube: Kube,
        helm: Helm,
        mcp_db: McpDatabase | None,
        cipher: CredentialCipher,
        audit: AuditLog,
        settings: ProvisionerSettings,
        stores: StoreSettings,
        upstream: UpstreamSettings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._db = db
        self._kube = kube
        self._helm = helm
        self._mcp_db = mcp_db
        self._cipher = cipher
        self._audit = audit
        self._settings = settings
        self._stores = stores
        self._upstream = upstream
        self._sleep = sleep
        self.worker_id = settings.worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"

    # --- run loop ------------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Worker pool plus the periodic drift check, until ``stop`` is set."""
        logger.info(
            "provisioner started",
            extra={"worker_id": self.worker_id, "workers": self._settings.workers},
        )
        tasks = [
            asyncio.create_task(self._worker_loop(n, stop), name=f"provisioner-worker-{n}")
            for n in range(self._settings.workers)
        ]
        if self._settings.drift_interval_seconds:
            tasks.append(asyncio.create_task(self._drift_loop(stop), name="provisioner-drift"))
        try:
            await stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("provisioner stopped")

    async def _worker_loop(self, n: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                store_id = await self.claim()
            except (SQLAlchemyError, AppError, OSError):
                logger.exception("claiming a store failed")
                store_id = None
            if store_id is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self._settings.poll_interval_seconds)
                continue
            await self.process(store_id)

    async def _drift_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._settings.drift_interval_seconds)
            if stop.is_set():
                return
            try:
                await self.check_drift()
            except Exception:
                logger.exception("drift check failed")

    # --- claiming and leases -------------------------------------------------------------------

    async def claim(self) -> uuid.UUID | None:
        """Take one store that needs work and whose lease is free or expired."""
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            store = await session.scalar(
                select(Store)
                .where(
                    Store.status.in_(WORK_STATUSES),
                    (Store.lease_expires_at.is_(None)) | (Store.lease_expires_at < func.now()),
                )
                .order_by(Store.updated_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if store is None:
                return None
            if store.lease_owner and store.lease_owner != self.worker_id:
                logger.warning(
                    "taking over an expired lease",
                    extra={"store": store.slug, "previous_owner": store.lease_owner},
                )
            store.lease_owner = self.worker_id
            store.lease_expires_at = now + timedelta(seconds=self._settings.lease_seconds)
            if store.status == StoreStatus.REQUESTED.value:
                store.status = StoreStatus.PROVISIONING.value
            store.updated_at = now
            return store.id

    async def process(self, store_id: uuid.UUID) -> None:
        """Drive one claimed store as far as it goes, under a renewed lease."""
        lease = Lease(store_id=store_id, owner=self.worker_id)
        renewer = asyncio.create_task(self._renew(lease))
        try:
            store = await self._load(lease)
            if store.status == StoreStatus.DELETING.value:
                await self._delete(lease, store)
            elif store.status == StoreStatus.PROVISIONING.value:
                await self._provision(lease)
        except LeaseLost:
            logger.warning("lease lost; another worker continues", extra={"store_id": store_id})
        except Exception:
            # Unexpected: hand the store back with a pause, so it is retried, not hot-looped.
            logger.exception("store processing failed", extra={"store_id": store_id})
            with contextlib.suppress(Exception):
                await self._release(lease, retry_in=30)
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer

    async def _renew(self, lease: Lease) -> None:
        interval = self._settings.lease_seconds / 4
        while True:
            await asyncio.sleep(interval)
            try:
                async with self._db.transaction() as session:
                    renewed = await session.scalar(
                        update(Store)
                        .where(Store.id == lease.store_id, Store.lease_owner == lease.owner)
                        .values(
                            lease_expires_at=func.now()
                            + timedelta(seconds=self._settings.lease_seconds)
                        )
                        .returning(Store.id)
                    )
            except (SQLAlchemyError, AppError, OSError):
                logger.warning("lease renewal failed", extra={"store_id": lease.store_id})
                continue
            if renewed is None:
                lease.lost = True
                return
            lease.renewals += 1

    async def _load(self, lease: Lease) -> Store:
        async with self._db.transaction() as session:
            store = await session.get(Store, lease.store_id)
        if store is None or store.lease_owner != lease.owner:
            raise LeaseLost
        return store

    async def _save(self, lease: Lease, **values: Any) -> None:
        if lease.lost:
            raise LeaseLost
        async with self._db.transaction() as session:
            saved = await session.scalar(
                update(Store)
                .where(Store.id == lease.store_id, Store.lease_owner == lease.owner)
                .values(**values, updated_at=func.now())
                .returning(Store.id)
            )
        if saved is None:
            lease.lost = True
            raise LeaseLost

    async def _release(self, lease: Lease, *, retry_in: float = 0) -> None:
        await self._save(
            lease,
            lease_owner=None,
            lease_expires_at=(
                datetime.now(UTC) + timedelta(seconds=retry_in) if retry_in else None
            ),
        )

    async def _event(
        self,
        store: Store,
        action: str,
        details: dict[str, Any] | None = None,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
    ) -> None:
        await self._audit.record(
            action=action,
            team_id=store.team_id,
            target_type="store",
            target_id=store.id,
            outcome=outcome,
            details={"slug": store.slug, "worker": self.worker_id} | (details or {}),
        )

    # --- provisioning --------------------------------------------------------------------------

    async def _provision(self, lease: Lease) -> None:
        while True:
            store = await self._load(lease)
            if store.status == StoreStatus.DELETING.value:
                await self._delete(lease, store)
                return
            if store.status != StoreStatus.PROVISIONING.value:
                await self._release(lease)
                return
            step = next_step(store.step)
            if datetime.now(UTC) >= store.deadline_at:
                detail = f": {store.status_reason}" if store.status_reason else ""
                await self._fail(lease, store, f"Timed out {STEP_LABELS[step]}{detail}")
                return
            try:
                waiting = await self._run_step(step, store, lease)
            except StepFailed as exc:
                attempts = store.attempts + 1
                await self._event(
                    store,
                    "store.step_failed",
                    {"step": step, "reason": exc.reason, "attempt": attempts},
                    AuditOutcome.FAILURE,
                )
                if not exc.retryable or attempts >= self._settings.max_attempts:
                    await self._fail(lease, store, f"Failed {STEP_LABELS[step]}: {exc.reason}")
                    return
                await self._save(
                    lease,
                    attempts=attempts,
                    status_reason=f"Retrying {STEP_LABELS[step]} after: {exc.reason}",
                )
                await self._sleep(min(30.0, 5.0 * attempts))
                continue
            if step == "register":
                return  # the register step set the store ready and released the lease
            if waiting is not None:
                if waiting != store.status_reason:
                    await self._save(lease, status_reason=waiting)
                await self._sleep(self._settings.step_poll_seconds)
                continue
            await self._save(lease, step=step, status_reason=None)
            await self._event(store, "store.step", {"step": step})
            logger.info("store step done", extra={"store": store.slug, "step": step})

    async def _run_step(self, step: str, store: Store, lease: Lease) -> str | None:
        """Run one step: None when done, a description of what it waits for otherwise."""
        handlers: dict[str, Callable[[Store, Lease], Awaitable[str | None]]] = {
            "namespace": self._step_namespace,
            "secrets": self._step_secrets,
            "mcp-db": self._step_mcp_db,
            "release": self._step_release,
            "wait-ready": self._step_wait_ready,
            "setup": self._step_setup,
            "register": self._step_register,
        }
        try:
            return await handlers[step](store, lease)
        except (LeaseLost, StepFailed):
            raise
        except ClusterError as exc:
            # 403/422 from the API server will not change by retrying (RBAC, admission policy,
            # quota); 5xx and connection errors might.
            retryable = exc.status is None or exc.status >= 500 or exc.status == 409
            raise StepFailed(exc.public_message, retryable=retryable) from exc
        except HelmError as exc:
            raise StepFailed(f"helm: {exc.message}") from exc
        except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            raise StepFailed(f"MCP database: {str(exc)[:200]}") from exc
        except (OSError, SQLAlchemyError) as exc:
            raise StepFailed(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    async def _step_namespace(self, store: Store, lease: Lease) -> str | None:
        existing = await self._kube.get("Namespace", store.namespace)
        if existing is not None:
            metadata = existing.get("metadata", {})
            if metadata.get("deletionTimestamp"):
                return "Waiting for the previous namespace to be removed"
            if (metadata.get("labels") or {}).get(manifests.STORE_LABEL) != str(store.id):
                raise StepFailed(
                    f"Namespace {store.namespace} exists and does not belong to this store",
                    retryable=False,
                )
        ns = store.namespace
        await self._kube.apply(manifests.namespace(store))
        await self._kube.apply(manifests.role_binding(store, self._settings), namespace=ns)
        await self._kube.apply(manifests.resource_quota(store), namespace=ns)
        await self._kube.apply(manifests.limit_range(store), namespace=ns)
        await self._kube.apply(manifests.default_deny(store), namespace=ns)
        return None

    async def _step_secrets(self, store: Store, lease: Lease) -> str | None:
        ns = store.namespace
        password: str | None = None
        if store.admin_password_ciphertext:
            try:
                password = self._cipher.decrypt(store.admin_password_ciphertext)
            except CredentialUnavailableError:
                logger.warning("admin password cannot be decrypted; generating one")
        await self._kube.create(manifests.credentials_secret(store, password), namespace=ns)
        if await self._kube.get("Secret", manifests.MCP_SECRET, namespace=ns) is None:
            mcp_db = self._require_mcp_db()
            dsn = mcp_db.dsn_for(mcp_db_name(str(store.id)), manifests.new_db_password())
            await self._kube.create(manifests.mcp_secret(store, dsn), namespace=ns)
        if store.admin_password_ciphertext:
            # It is in the Secret now; the database never keeps it.
            await self._save(lease, admin_password_ciphertext=None)
        return None

    async def _step_mcp_db(self, store: Store, lease: Lease) -> str | None:
        secret = await self._kube.get("Secret", manifests.MCP_SECRET, namespace=store.namespace)
        dsn = manifests.secret_value(secret or {}, "DB_URL")
        password = unquote(urlsplit(dsn).password or "") if dsn else ""
        if not password:
            raise StepFailed("The store-mcp Secret has no database URL", retryable=False)
        await self._require_mcp_db().ensure(mcp_db_name(str(store.id)), password)
        return None

    async def _helm_apply(self, store: Store, setup_run: str | None) -> None:
        release, ns = store.release, store.namespace
        status = await recover(self._helm, release, ns)
        if status is not None and status.status == "failed" and status.revision <= 1:
            # A failed first install cannot be upgraded; start it over.
            await self._helm.uninstall(release, ns)
        values = store_values(
            store, settings=self._settings, stores=self._stores, setup_run=setup_run
        )
        result = await self._helm.upgrade_install(release, ns, values)
        if result.status != "deployed":
            raise StepFailed(f"the Helm release is {result.status}")

    async def _step_release(self, store: Store, lease: Lease) -> str | None:
        await self._helm_apply(store, setup_run=None)
        return None

    async def _step_wait_ready(self, store: Store, lease: Lease) -> str | None:
        ns = store.namespace
        for pod in await self._kube.list("Pod", namespace=ns):
            await self._check_pod(pod, ns)
        progress: list[str] = []
        pending = False
        for kind, name, label in WORKLOADS:
            obj = await self._kube.get(kind, name, namespace=ns)
            ready = int(((obj or {}).get("status") or {}).get("readyReplicas") or 0)
            wanted = int(((obj or {}).get("spec") or {}).get("replicas") or 1)
            pending = pending or ready < 1
            progress.append(f"{label} {min(ready, wanted)}/{wanted}")
        if not pending:
            return None
        detail = "Waiting for pods: " + ", ".join(progress)
        warning = await self._last_warning(ns)
        if warning:
            if "exceeded quota" in warning:
                raise StepFailed(f"Resource quota exceeded: {warning}", retryable=False)
            detail += f"; last event: {warning}"
        return detail

    async def _check_pod(self, pod: dict[str, Any], ns: str) -> None:
        name = pod.get("metadata", {}).get("name", "?")
        status = pod.get("status") or {}
        for cs in (status.get("initContainerStatuses") or []) + (
            status.get("containerStatuses") or []
        ):
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason", "")
            message = (waiting.get("message") or "")[:200]
            if reason in FATAL_WAITING:
                raise StepFailed(
                    f"{name}/{cs.get('name')}: {reason} {message}".strip(), retryable=False
                )
            if reason == "CrashLoopBackOff" and int(cs.get("restartCount") or 0) >= CRASH_RESTARTS:
                log = await self._kube.pod_log(name, namespace=ns, tail_lines=5)
                last = log.strip().splitlines()[-1][:200] if log.strip() else ""
                raise StepFailed(
                    f"{name}/{cs.get('name')} keeps crashing" + (f": {last}" if last else ""),
                    retryable=False,
                )

    async def _last_warning(self, ns: str) -> str | None:
        events = [
            e for e in await self._kube.list("Event", namespace=ns) if e.get("type") == "Warning"
        ]
        if not events:
            return None
        latest = max(
            events,
            key=lambda e: str(e.get("lastTimestamp") or e.get("eventTime") or ""),
        )
        return f"{latest.get('reason', '')}: {(latest.get('message') or '')[:200]}"

    async def _step_setup(self, store: Store, lease: Lease) -> str | None:
        run = str(store.attempts)
        name = f"store-setup-{run}"
        ns = store.namespace
        job = await self._kube.get("Job", name, namespace=ns)
        if job is None:
            await self._helm_apply(store, setup_run=run)
            return "Starting the WooCommerce setup job"
        for condition in (job.get("status") or {}).get("conditions") or []:
            if condition.get("status") != "True":
                continue
            if condition.get("type") == "Complete":
                return None
            if condition.get("type") == "Failed":
                pods = await self._kube.list("Pod", namespace=ns, label_selector=f"job-name={name}")
                last = ""
                if pods:
                    log = await self._kube.pod_log(pods[-1]["metadata"]["name"], namespace=ns)
                    lines = [line for line in log.strip().splitlines() if line.strip()]
                    last = lines[-1][:200] if lines else ""
                raise StepFailed("the setup job failed" + (f": {last}" if last else ""))
        return "Setting up WooCommerce (plugin, permalinks, payments, REST key)"

    async def _step_register(self, store: Store, lease: Lease) -> str | None:
        async with self._db.transaction() as session:
            row = await session.scalar(select(Store).where(Store.id == store.id).with_for_update())
            if row is None or row.lease_owner != lease.owner:
                raise LeaseLost
            if row.status != StoreStatus.PROVISIONING.value:
                # Deleted meanwhile: hand it straight to the delete flow (next claim).
                row.lease_owner = None
                row.lease_expires_at = None
                return None
            await register_store_connector(
                session,
                row,
                audit=self._audit,
                timeout_seconds=self._upstream.default_call_timeout_seconds,
                max_concurrency=self._upstream.default_max_concurrency,
            )
            now = datetime.now(UTC)
            row.status = StoreStatus.READY.value
            row.step = "register"
            row.status_reason = None
            row.ready_at = now
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            self._audit.add(
                session,
                action="store.ready",
                actor_user_id=row.created_by_user_id,
                team_id=row.team_id,
                target_type="store",
                target_id=row.id,
                details={
                    "slug": row.slug,
                    "seconds": round((now - row.created_at).total_seconds()),
                },
            )
        logger.info("store ready", extra={"store": store.slug})
        return None

    def _require_mcp_db(self) -> McpDatabase:
        if self._mcp_db is None:
            raise StepFailed(
                "The provisioner has no database for store MCP servers "
                "(PROVISIONER_MCP_ADMIN_DSN is not set)",
                retryable=False,
            )
        return self._mcp_db

    # --- failure, teardown and deletion --------------------------------------------------------

    async def _fail(self, lease: Lease, store: Store, reason: str) -> None:
        """Remove what was created, then record the failure. The row stays for Retry/Delete."""
        logger.warning("store failed", extra={"store": store.slug, "reason": reason})
        await self._save(lease, status_reason=f"{reason} (cleaning up)")
        try:
            await self._teardown(store)
        except Exception as exc:
            logger.exception("cleanup after failure incomplete", extra={"store": store.slug})
            reason += f" (cleanup incomplete: {str(exc)[:120]})"
        await self._save(
            lease,
            status=StoreStatus.FAILED.value,
            status_reason=reason[:2000],
            admin_password_ciphertext=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._event(store, "store.failed", {"reason": reason}, AuditOutcome.FAILURE)

    async def _teardown(self, store: Store) -> None:
        """Everything a store owns in the cluster and the MCP database. Tolerates absence."""
        ns = store.namespace
        if await self._kube.get("Namespace", ns) is not None:
            await recover(self._helm, store.release, ns)
            await self._helm.uninstall(store.release, ns)
            await self._kube.delete("Namespace", ns)
        await self._wait_namespace_gone(ns)
        if self._mcp_db is not None:
            await self._mcp_db.drop(mcp_db_name(str(store.id)))

    async def _wait_namespace_gone(self, ns: str) -> None:
        deadline = asyncio.get_running_loop().time() + self._settings.teardown_wait_seconds
        while await self._kube.get("Namespace", ns) is not None:
            if asyncio.get_running_loop().time() > deadline:
                raise StepFailed(f"namespace {ns} is still terminating")
            await self._sleep(self._settings.step_poll_seconds)

    async def _delete(self, lease: Lease, store: Store) -> None:
        # Tools disappear first: the connector rows go (team links and grants cascade).
        async with self._db.transaction() as session:
            removed = await session.scalars(
                delete(Connector)
                .where(
                    Connector.kind == ConnectorKind.STORE.value,
                    (Connector.id == store.connector_id) | (Connector.slug == store.slug),
                )
                .returning(Connector.slug)
            )
            slugs = list(removed.all())
        if slugs:
            await self._event(store, "store.connector_removed", {"connectors": slugs})
        try:
            await self._teardown(store)
        except (StepFailed, ClusterError, HelmError, OSError) as exc:
            reason = exc.reason if isinstance(exc, StepFailed) else str(exc)
            logger.warning(
                "store teardown incomplete", extra={"store": store.slug, "reason": reason}
            )
            await self._save(lease, status_reason=f"Deleting: {reason[:300]}")
            await self._release(lease, retry_in=15)
            return
        now = datetime.now(UTC)
        await self._save(
            lease,
            status=StoreStatus.DELETED.value,
            status_reason=None,
            deleted_at=now,
            connector_id=None,
            admin_password_ciphertext=None,
            lease_owner=None,
            lease_expires_at=None,
        )
        await self._event(store, "store.deleted")
        logger.info("store deleted", extra={"store": store.slug})

    # --- drift ---------------------------------------------------------------------------------

    async def check_drift(self) -> list[str]:
        """Mark ready stores whose namespace or release vanished as failed (and hide their
        tools). Returns the slugs it marked."""
        async with self._db.transaction() as session:
            ready = list(
                (
                    await session.scalars(
                        select(Store).where(Store.status == StoreStatus.READY.value)
                    )
                ).all()
            )
        marked: list[str] = []
        for store in ready:
            missing: list[str] = []
            ns = await self._kube.get("Namespace", store.namespace)
            if ns is None or ns.get("metadata", {}).get("deletionTimestamp"):
                missing.append("namespace")
            elif await self._helm.status(store.release, store.namespace) is None:
                missing.append("Helm release")
            if not missing:
                continue
            reason = "Resources missing: " + ", ".join(missing)
            async with self._db.transaction() as session:
                row = await session.scalar(
                    select(Store)
                    .where(Store.id == store.id, Store.status == StoreStatus.READY.value)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    continue
                row.status = StoreStatus.FAILED.value
                row.status_reason = reason
                row.updated_at = datetime.now(UTC)
                if row.connector_id is not None:
                    await session.execute(
                        update(Connector)
                        .where(Connector.id == row.connector_id)
                        .values(enabled=False)
                    )
            await self._event(store, "store.drift", {"reason": reason}, AuditOutcome.FAILURE)
            marked.append(store.slug)
        return marked
