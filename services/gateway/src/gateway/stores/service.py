"""Store requests from the dashboard: create, list, delete and retry, with the per-user and
platform caps. The gateway only writes rows; the provisioner does the work (gateway.provisioner).

Creation is retry-safe: the same ``Idempotency-Key`` (per user) or the same name in the team
returns the existing store instead of a second one. The cap check and the insert happen in one
transaction that holds a row lock on the user (two parallel requests of one user serialize)
and a transaction-level advisory lock for the platform-wide total.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import StoreSettings
from gateway.db.engine import Database
from gateway.db.models import (
    AuditEvent,
    Connector,
    ConnectorKind,
    MemberToolGrant,
    Store,
    StoreStatus,
    TeamConnector,
    TeamMembership,
    TeamRole,
    User,
)
from gateway.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
)
from gateway.services.audit import AuditLog
from gateway.services.teams import TeamContext
from gateway.stores.naming import RELEASE_NAME, mcp_url, slug_candidates
from gateway.upstream.credentials import CredentialCipher

# pg_advisory_xact_lock key serializing store creation platform-wide (the total cap).
_CREATE_LOCK_KEY = 0x5354_4F52  # "STOR"
LIVE = (StoreStatus.DELETED.value,)


@dataclass(frozen=True, slots=True)
class StoreView:
    store: Store
    created_by_name: str | None
    created_by_email: str | None


@dataclass(frozen=True, slots=True)
class CreatedStore:
    view: StoreView
    created: bool
    # The WordPress admin password, returned once when the store is first requested.
    admin_password: str | None


@dataclass(frozen=True, slots=True)
class StoreEvent:
    at: datetime
    action: str
    outcome: str
    details: dict[str, Any]


class StoreService:
    def __init__(
        self,
        db: Database,
        *,
        audit: AuditLog,
        cipher: CredentialCipher,
        settings: StoreSettings,
    ) -> None:
        self._db = db
        self._audit = audit
        self._cipher = cipher
        self._settings = settings

    @property
    def settings(self) -> StoreSettings:
        return self._settings

    def _require_enabled(self) -> None:
        if not self._settings.enabled:
            raise NotFoundError("Stores are not enabled on this platform.")

    # --- create --------------------------------------------------------------------------------

    async def create(
        self, ctx: TeamContext, name: str, *, idempotency_key: str | None = None
    ) -> CreatedStore:
        self._require_enabled()
        name = " ".join(name.split())
        if not 1 <= len(name) <= 60:
            raise InvalidInputError("A store name must be 1 to 60 characters.")
        user_id = ctx.principal.user_id
        async with self._db.transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CREATE_LOCK_KEY}
            )
            # Row lock on the creator: the count below cannot change under us.
            await session.execute(select(User.id).where(User.id == user_id).with_for_update())

            existing = await self._existing(session, ctx, name, idempotency_key)
            if existing is not None:
                return CreatedStore(
                    view=await self._view(session, existing), created=False, admin_password=None
                )

            await self._check_caps(session, user_id)
            slug = await self._free_slug(session, name)
            password = secrets.token_urlsafe(18)
            now = datetime.now(UTC)
            store = Store(
                id=uuid.uuid4(),
                team_id=ctx.team_id,
                created_by_user_id=user_id,
                name=name,
                slug=slug,
                namespace=self._settings.namespace_of(slug),
                release=RELEASE_NAME,
                host=self._settings.host_of(slug),
                status=StoreStatus.REQUESTED.value,
                attempts=0,
                deadline_at=now + timedelta(seconds=self._settings.provision_timeout_seconds),
                idempotency_key=idempotency_key,
                # Without a credential key the provisioner picks the password itself and the
                # user cannot be shown it.
                admin_password_ciphertext=(
                    self._cipher.encrypt(password) if self._cipher.configured else None
                ),
                created_at=now,
                updated_at=now,
            )
            session.add(store)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError("A store with this name already exists.") from exc
            self._audit.add(
                session,
                action="store.create",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="store",
                target_id=store.id,
                details={"name": name, "slug": slug, "host": store.host},
            )
            view = StoreView(store, ctx.principal.display_name, ctx.principal.email)
        return CreatedStore(
            view=view,
            created=True,
            admin_password=password if store.admin_password_ciphertext else None,
        )

    async def _existing(
        self, session: AsyncSession, ctx: TeamContext, name: str, key: str | None
    ) -> Store | None:
        if key is not None:
            by_key = await session.scalar(
                select(Store).where(
                    Store.created_by_user_id == ctx.principal.user_id,
                    Store.idempotency_key == key,
                )
            )
            if by_key is not None:
                if by_key.team_id != ctx.team_id or by_key.name.lower() != name.lower():
                    raise ConflictError(
                        "This Idempotency-Key was already used for a different store request."
                    )
                return by_key
        by_name = await session.scalar(
            select(Store).where(
                Store.team_id == ctx.team_id,
                func.lower(Store.name) == name.lower(),
                Store.status.not_in(LIVE),
            )
        )
        if by_name is None:
            return None
        if by_name.status == StoreStatus.FAILED.value:
            raise ConflictError(
                f"The store '{by_name.name}' exists but failed to provision; retry or delete it."
            )
        if by_name.status == StoreStatus.DELETING.value:
            raise ConflictError(f"The store '{by_name.name}' is being deleted.")
        return by_name

    async def _check_caps(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        mine = await session.scalar(
            select(func.count())
            .select_from(Store)
            .where(Store.created_by_user_id == user_id, Store.status.not_in(LIVE))
        )
        if (mine or 0) >= self._settings.max_per_user:
            raise QuotaExceededError(
                f"You already have {self._settings.max_per_user} stores; delete one first."
            )
        total = await session.scalar(
            select(func.count()).select_from(Store).where(Store.status.not_in(LIVE))
        )
        if (total or 0) >= self._settings.max_total:
            raise QuotaExceededError(
                f"The platform already runs its maximum of {self._settings.max_total} stores."
            )

    @staticmethod
    async def _free_slug(session: AsyncSession, name: str) -> str:
        """First candidate used by no store (deleted ones included) and no connector."""
        candidates = slug_candidates(name)
        taken = set(
            (await session.scalars(select(Store.slug).where(Store.slug.in_(candidates)))).all()
        ) | set(
            (
                await session.scalars(select(Connector.slug).where(Connector.slug.in_(candidates)))
            ).all()
        )
        for candidate in candidates:
            if candidate not in taken:
                return candidate
        raise ConflictError("Could not find a free address for this store name; try another.")

    # --- read ----------------------------------------------------------------------------------

    async def list_team(self, ctx: TeamContext) -> list[StoreView]:
        self._require_enabled()
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(Store, User.display_name, User.email)
                    .outerjoin(User, User.id == Store.created_by_user_id)
                    .where(Store.team_id == ctx.team_id, Store.status.not_in(LIVE))
                    .order_by(Store.created_at.desc())
                )
            ).all()
        return [StoreView(store, display, email) for store, display, email in rows]

    async def get(
        self, ctx: TeamContext, store_id: uuid.UUID
    ) -> tuple[StoreView, list[StoreEvent]]:
        self._require_enabled()
        async with self._db.transaction() as session:
            store = await self._team_store(session, ctx, store_id)
            view = await self._view(session, store)
            events = (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.target_type == "store", AuditEvent.target_id == str(store.id))
                    .order_by(AuditEvent.created_at.desc())
                    .limit(30)
                )
            ).all()
        return view, [
            StoreEvent(e.created_at, e.action, e.outcome, dict(e.details)) for e in events
        ]

    # --- delete / retry ------------------------------------------------------------------------

    async def delete(self, ctx: TeamContext, store_id: uuid.UUID) -> StoreView:
        """Mark the store for teardown. Its connector is disabled at once, so its tools vanish
        from the agent and /v1/mcp before the provisioner removes anything. Idempotent."""
        self._require_enabled()
        async with self._db.transaction() as session:
            store = await self._team_store(session, ctx, store_id, for_update=True)
            await self._require_owner_or_admin(ctx, store)
            if store.status not in (StoreStatus.DELETING.value, StoreStatus.DELETED.value):
                previous = store.status
                store.status = StoreStatus.DELETING.value
                store.status_reason = None
                store.admin_password_ciphertext = None
                store.updated_at = datetime.now(UTC)
                # A worker holding the lease finishes its current step, then sees 'deleting'.
                if store.connector_id is not None:
                    connector = await session.get(Connector, store.connector_id)
                    if connector is not None:
                        connector.enabled = False
                self._audit.add(
                    session,
                    action="store.delete",
                    actor=ctx.principal,
                    team_id=ctx.team_id,
                    target_type="store",
                    target_id=store.id,
                    details={"slug": store.slug, "previous_status": previous},
                )
            view = await self._view(session, store)
        return view

    async def retry(self, ctx: TeamContext, store_id: uuid.UUID) -> StoreView:
        """Provision a failed store again from the start (its resources were torn down when it
        failed; every step is idempotent anyway)."""
        self._require_enabled()
        async with self._db.transaction() as session:
            store = await self._team_store(session, ctx, store_id, for_update=True)
            await self._require_owner_or_admin(ctx, store)
            if store.status != StoreStatus.FAILED.value:
                raise ConflictError("Only a failed store can be retried.")
            now = datetime.now(UTC)
            store.status = StoreStatus.REQUESTED.value
            store.status_reason = None
            store.step = None
            store.attempts = 0
            store.deadline_at = now + timedelta(seconds=self._settings.provision_timeout_seconds)
            store.lease_owner = None
            store.lease_expires_at = None
            store.updated_at = now
            self._audit.add(
                session,
                action="store.retry",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="store",
                target_id=store.id,
                details={"slug": store.slug},
            )
            view = await self._view(session, store)
        return view

    # --- helpers -------------------------------------------------------------------------------

    @staticmethod
    async def _team_store(
        session: AsyncSession, ctx: TeamContext, store_id: uuid.UUID, *, for_update: bool = False
    ) -> Store:
        stmt = select(Store).where(
            Store.id == store_id,
            Store.team_id == ctx.team_id,
            Store.status != StoreStatus.DELETED.value,
        )
        if for_update:
            stmt = stmt.with_for_update()
        store = await session.scalar(stmt)
        if store is None:
            raise NotFoundError("Store not found.")
        return store

    async def _require_owner_or_admin(self, ctx: TeamContext, store: Store) -> None:
        if ctx.is_admin or store.created_by_user_id == ctx.principal.user_id:
            return
        raise PermissionDeniedError("Only the store's creator or a team admin can do this.")

    @staticmethod
    async def _view(session: AsyncSession, store: Store) -> StoreView:
        creator = (
            await session.get(User, store.created_by_user_id) if store.created_by_user_id else None
        )
        return StoreView(
            store,
            creator.display_name if creator else None,
            creator.email if creator else None,
        )


# --- connector registration (used by the provisioner) ------------------------------------------


async def register_store_connector(
    session: AsyncSession,
    store: Store,
    *,
    audit: AuditLog,
    timeout_seconds: float,
    max_concurrency: int,
) -> Connector:
    """Insert (or refresh) the store's ``kind=store`` connector, link it to the owning team
    with every tool, and grant the creator access when they are a plain member. Runs in the
    caller's transaction, together with the status change to ``ready``."""
    now = datetime.now(UTC)
    connector = await session.scalar(select(Connector).where(Connector.slug == store.slug))
    if connector is None:
        connector = Connector(
            id=uuid.uuid4(),
            slug=store.slug,
            created_by_user_id=store.created_by_user_id,
            created_at=now,
        )
        session.add(connector)
    elif connector.kind != ConnectorKind.STORE.value or connector.owner_team_id != store.team_id:
        raise ConflictError(f"The connector slug '{store.slug}' is taken by another server.")
    connector.display_name = store.name
    connector.description = f"WooCommerce store {store.name} ({store.host})"
    connector.url = mcp_url(store.namespace)
    connector.kind = ConnectorKind.STORE.value
    connector.owner_team_id = store.team_id
    connector.enabled = True
    connector.timeout_seconds = timeout_seconds
    connector.max_concurrency = max_concurrency
    connector.updated_at = now
    await session.flush()

    link = await session.scalar(
        select(TeamConnector).where(
            TeamConnector.team_id == store.team_id, TeamConnector.connector_id == connector.id
        )
    )
    if link is None:
        session.add(
            TeamConnector(
                id=uuid.uuid4(),
                team_id=store.team_id,
                connector_id=connector.id,
                allow_all_tools=True,
                allowed_tools=[],
                created_at=now,
                updated_at=now,
            )
        )
    if store.created_by_user_id is not None:
        membership = await session.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == store.team_id,
                TeamMembership.user_id == store.created_by_user_id,
            )
        )
        if membership is not None and membership.role == TeamRole.MEMBER.value:
            grant = await session.scalar(
                select(MemberToolGrant).where(
                    MemberToolGrant.membership_id == membership.id,
                    MemberToolGrant.connector_id == connector.id,
                )
            )
            if grant is None:
                session.add(
                    MemberToolGrant(
                        id=uuid.uuid4(),
                        membership_id=membership.id,
                        connector_id=connector.id,
                        allow_all_tools=True,
                        tools=[],
                        created_at=now,
                        updated_at=now,
                    )
                )
    store.connector_id = connector.id
    audit.add(
        session,
        action="store.connector_registered",
        actor_user_id=store.created_by_user_id,
        team_id=store.team_id,
        target_type="store",
        target_id=store.id,
        details={"connector": connector.slug},
    )
    return connector
