"""Connector registry, team tool policies, member grants and effective-access resolution.

Access to a tool is the intersection of up to three layers, all loaded per request:

1. **Team policy**: the connector must be enabled for the team, with that tool allowed.
   Team admins get exactly this.
2. **Member grant**: non-admin members additionally need a grant for the connector, listing
   the tools (or all tools of the team policy) they may use.
3. **API key narrowing**: a key with ``tool_restrictions`` is limited further.

Nothing a client sends can widen this: the connector is looked up by slug in the registry, so
there is no client-supplied URL or routing anywhere.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import UpstreamSettings
from gateway.db.engine import Database
from gateway.db.models import (
    AuditOutcome,
    Connector,
    MemberToolGrant,
    TeamConnector,
    TeamMembership,
    TeamRole,
)
from gateway.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    UpstreamAuthError,
)
from gateway.services.audit import AuditLog
from gateway.services.principal import Principal
from gateway.services.teams import TeamContext, TeamService
from gateway.upstream.client import McpUpstreamClient, ServerProbe, UpstreamTarget
from gateway.upstream.credentials import (
    CredentialCipher,
    CredentialUnavailableError,
    UpstreamCredential,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


@dataclass(frozen=True, slots=True)
class ToolSet:
    """A set of tool names, or every tool (``all_tools``)."""

    all_tools: bool
    names: frozenset[str]

    @classmethod
    def of(cls, all_tools: bool, names: list[str] | tuple[str, ...] | None) -> ToolSet:
        return cls(all_tools=all_tools, names=frozenset(names or ()))

    def allows(self, tool: str) -> bool:
        return self.all_tools or tool in self.names

    def intersect(self, other: ToolSet) -> ToolSet:
        if self.all_tools:
            return other
        if other.all_tools:
            return self
        return ToolSet(all_tools=False, names=self.names & other.names)

    @property
    def is_empty(self) -> bool:
        return not self.all_tools and not self.names


@dataclass(frozen=True, slots=True)
class ConnectorAccess:
    target: UpstreamTarget
    tools: ToolSet


@dataclass(frozen=True, slots=True)
class NewConnector:
    slug: str
    display_name: str
    url: str
    description: str = ""
    timeout_seconds: float | None = None
    max_concurrency: int | None = None
    credential: UpstreamCredential | None = None


@dataclass(frozen=True, slots=True)
class ConnectorChange:
    display_name: str | None = None
    description: str | None = None
    url: str | None = None
    enabled: bool | None = None
    timeout_seconds: float | None = None
    max_concurrency: int | None = None
    credential: UpstreamCredential | None = None
    clear_credential: bool = False


@dataclass(frozen=True, slots=True)
class TeamConnectorView:
    connector: Connector
    policy: ToolSet
    effective: ToolSet


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    title: str | None
    description: str | None
    allowed_for_team: bool


@dataclass(frozen=True, slots=True)
class GrantView:
    connector_slug: str
    tools: ToolSet
    updated_at: datetime


def key_restriction(principal: Principal, slug: str) -> ToolSet | None:
    """The API key's narrowing for one connector, or None if the key does not narrow."""
    if principal.api_key_tool_restrictions is None:
        return None
    names: set[str] = set()
    for entry in principal.api_key_tool_restrictions:
        connector, _, tool = entry.partition("/")
        if connector != slug:
            continue
        if tool == "*":
            return ToolSet(all_tools=True, names=frozenset())
        names.add(tool)
    return ToolSet(all_tools=False, names=frozenset(names))


def _validate_tools(tools: list[str]) -> list[str]:
    cleaned = sorted({t.strip() for t in tools})
    if any(not TOOL_NAME_RE.match(t) for t in cleaned):
        raise InvalidInputError("Tool names may only contain letters, digits, '_', '.' and '-'.")
    return cleaned


class ConnectorService:
    def __init__(
        self,
        db: Database,
        *,
        upstream: McpUpstreamClient,
        teams: TeamService,
        audit: AuditLog,
        settings: UpstreamSettings,
        cipher: CredentialCipher,
    ) -> None:
        self._db = db
        self._upstream = upstream
        self._teams = teams
        self._audit = audit
        self._settings = settings
        self._cipher = cipher

    def target_of(self, connector: Connector) -> UpstreamTarget:
        """The upstream call target, with the connector's credential decrypted."""
        headers: dict[str, str] = {}
        if connector.credential_header and connector.credential_ciphertext:
            try:
                value = self._cipher.decrypt(connector.credential_ciphertext)
            except CredentialUnavailableError as exc:
                raise UpstreamAuthError(
                    "The connector's credential cannot be used; a platform admin must set it again."
                ) from exc
            headers[connector.credential_header] = value
        return UpstreamTarget(
            connector_id=connector.id,
            slug=connector.slug,
            url=connector.url,
            timeout_seconds=connector.timeout_seconds,
            max_concurrency=connector.max_concurrency,
            headers=headers,
        )

    def _apply_credential(self, connector: Connector, credential: UpstreamCredential) -> None:
        connector.credential_header = credential.header_name
        connector.credential_ciphertext = self._cipher.encrypt(credential.value)

    # --- platform registry (platform admins) ---------------------------------------------------

    def _require_platform_admin(self, principal: Principal) -> None:
        if not principal.is_platform_admin:
            raise PermissionDeniedError(
                "Managing the connector registry requires a platform admin."
            )

    def _check_url(self, url: str) -> str:
        url = url.strip()
        if not self._settings.is_allowed_url(url):
            raise InvalidInputError(
                "The connector URL is not on the gateway's upstream allow-list "
                "(UPSTREAM_ALLOWED_HOSTS) or uses a disallowed scheme."
            )
        return url

    def _check_timeout(self, value: float | None) -> float:
        if value is None:
            return self._settings.default_call_timeout_seconds
        if not 0 < value <= self._settings.max_call_timeout_seconds:
            raise InvalidInputError(
                f"timeout_seconds must be between 0 and {self._settings.max_call_timeout_seconds}."
            )
        return value

    async def register(self, principal: Principal, spec: NewConnector) -> Connector:
        self._require_platform_admin(principal)
        if not SLUG_RE.match(spec.slug):
            raise InvalidInputError("slug must be lowercase letters, digits and '-'.")
        connector = Connector(
            id=uuid.uuid4(),
            slug=spec.slug,
            display_name=spec.display_name.strip(),
            description=spec.description.strip(),
            url=self._check_url(spec.url),
            enabled=True,
            timeout_seconds=self._check_timeout(spec.timeout_seconds),
            max_concurrency=spec.max_concurrency or self._settings.default_max_concurrency,
            created_by_user_id=principal.user_id,
        )
        if spec.credential is not None:
            self._apply_credential(connector, spec.credential)
        async with self._db.transaction() as session:
            session.add(connector)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError("A connector with this slug already exists.") from exc
            self._audit.add(
                session,
                action="connector.register",
                actor=principal,
                target_type="connector",
                target_id=connector.id,
                details={"slug": connector.slug, "credential": spec.credential is not None},
            )
        return connector

    async def list_registry(self, principal: Principal) -> list[Connector]:
        """Platform admins see every connector; everyone else only enabled ones."""
        stmt = select(Connector).order_by(Connector.slug)
        if not principal.is_platform_admin:
            stmt = stmt.where(Connector.enabled.is_(True))
        async with self._db.transaction() as session:
            return list((await session.scalars(stmt)).all())

    async def get(self, principal: Principal, connector_id: uuid.UUID) -> Connector:
        self._require_platform_admin(principal)
        async with self._db.transaction() as session:
            connector = await session.get(Connector, connector_id)
        if connector is None:
            raise NotFoundError("Connector not found.")
        return connector

    async def update(
        self, principal: Principal, connector_id: uuid.UUID, change: ConnectorChange
    ) -> Connector:
        self._require_platform_admin(principal)
        values: dict[str, Any] = {}
        if change.display_name is not None:
            values["display_name"] = change.display_name.strip()
        if change.description is not None:
            values["description"] = change.description.strip()
        if change.url is not None:
            values["url"] = self._check_url(change.url)
        if change.enabled is not None:
            values["enabled"] = change.enabled
        if change.timeout_seconds is not None:
            values["timeout_seconds"] = self._check_timeout(change.timeout_seconds)
        if change.max_concurrency is not None:
            values["max_concurrency"] = change.max_concurrency
        if change.credential is not None and change.clear_credential:
            raise InvalidInputError("Set a credential or clear it, not both.")
        async with self._db.transaction() as session:
            connector = await session.get(Connector, connector_id, with_for_update=True)
            if connector is None:
                raise NotFoundError("Connector not found.")
            for key, value in values.items():
                setattr(connector, key, value)
            details: dict[str, Any] = {k: v for k, v in values.items() if k != "url"}
            if "url" in values:
                details["url_changed"] = True
            if change.credential is not None:
                self._apply_credential(connector, change.credential)
                details["credential"] = "set"
            elif change.clear_credential:
                connector.credential_header = None
                connector.credential_ciphertext = None
                details["credential"] = "cleared"
            connector.updated_at = datetime.now(UTC)
            self._audit.add(
                session,
                action="connector.update",
                actor=principal,
                target_type="connector",
                target_id=connector.id,
                details=details,
            )
        self._upstream.invalidate(connector_id)
        return connector

    async def remove(self, principal: Principal, connector_id: uuid.UUID) -> Connector:
        """Delete a connector with its team policies and member grants (platform admin)."""
        self._require_platform_admin(principal)
        async with self._db.transaction() as session:
            connector = await session.get(Connector, connector_id, with_for_update=True)
            if connector is None:
                raise NotFoundError("Connector not found.")
            await session.delete(connector)
            self._audit.add(
                session,
                action="connector.remove",
                actor=principal,
                target_type="connector",
                target_id=connector.id,
                details={"slug": connector.slug},
            )
        self._upstream.invalidate(connector_id)
        return connector

    async def probe(self, principal: Principal, connector_id: uuid.UUID) -> ServerProbe:
        connector = await self.get(principal, connector_id)
        return await self._upstream.probe(self.target_of(connector))

    # --- team policy (team admins) -------------------------------------------------------------

    async def _enabled_connector(self, slug: str) -> Connector:
        async with self._db.transaction() as session:
            connector = await session.scalar(
                select(Connector).where(Connector.slug == slug, Connector.enabled.is_(True))
            )
        if connector is None:
            raise NotFoundError("Connector not found.")
        return connector

    async def set_team_policy(
        self, ctx: TeamContext, slug: str, *, allow_all_tools: bool, tools: list[str]
    ) -> TeamConnectorView:
        await self._teams.require_admin(ctx)
        connector = await self._enabled_connector(slug)
        names = [] if allow_all_tools else _validate_tools(tools)
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            await session.execute(
                insert(TeamConnector)
                .values(
                    id=uuid.uuid4(),
                    team_id=ctx.team_id,
                    connector_id=connector.id,
                    allow_all_tools=allow_all_tools,
                    allowed_tools=names,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_team_connectors_team_id_connector_id",
                    set_={
                        "allow_all_tools": allow_all_tools,
                        "allowed_tools": names,
                        "updated_at": now,
                    },
                )
            )
            self._audit.add(
                session,
                action="team_connector.set_policy",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="connector",
                target_id=connector.id,
                details={"slug": slug, "allow_all_tools": allow_all_tools, "tools": names},
            )
        policy = ToolSet.of(allow_all_tools, names)
        return TeamConnectorView(connector=connector, policy=policy, effective=policy)

    async def remove_team_policy(self, ctx: TeamContext, slug: str) -> None:
        await self._teams.require_admin(ctx)
        connector = await self._enabled_or_any(slug)
        async with self._db.transaction() as session:
            deleted = await session.scalar(
                delete(TeamConnector)
                .where(
                    TeamConnector.team_id == ctx.team_id,
                    TeamConnector.connector_id == connector.id,
                )
                .returning(TeamConnector.id)
            )
            if deleted is None:
                raise NotFoundError("Connector is not enabled for this team.")
            # Grants only make sense under a team policy; drop them with it.
            await session.execute(
                delete(MemberToolGrant).where(
                    MemberToolGrant.connector_id == connector.id,
                    MemberToolGrant.membership_id.in_(
                        select(TeamMembership.id).where(TeamMembership.team_id == ctx.team_id)
                    ),
                )
            )
            self._audit.add(
                session,
                action="team_connector.remove",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="connector",
                target_id=connector.id,
                details={"slug": slug},
            )

    async def _enabled_or_any(self, slug: str) -> Connector:
        async with self._db.transaction() as session:
            connector = await session.scalar(select(Connector).where(Connector.slug == slug))
        if connector is None:
            raise NotFoundError("Connector not found.")
        return connector

    async def list_team_connectors(self, ctx: TeamContext) -> list[TeamConnectorView]:
        """Admins: every connector enabled for the team. Members: those they can use."""
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(Connector, TeamConnector)
                    .join(TeamConnector, TeamConnector.connector_id == Connector.id)
                    .where(TeamConnector.team_id == ctx.team_id, Connector.enabled.is_(True))
                    .order_by(Connector.slug)
                )
            ).all()
            grants: dict[uuid.UUID, MemberToolGrant] = {}
            if not ctx.is_admin:
                grants = {
                    g.connector_id: g
                    for g in (
                        await session.scalars(
                            select(MemberToolGrant).where(
                                MemberToolGrant.membership_id == ctx.membership_id
                            )
                        )
                    ).all()
                }
        views: list[TeamConnectorView] = []
        for connector, team_connector in rows:
            policy = ToolSet.of(team_connector.allow_all_tools, team_connector.allowed_tools)
            effective = self._effective_from_policy(
                ctx, connector.slug, policy, grants.get(connector.id)
            )
            if ctx.is_admin or not effective.is_empty:
                views.append(TeamConnectorView(connector, policy, effective))
        return views

    async def catalog(self, ctx: TeamContext, slug: str) -> list[CatalogEntry]:
        """The upstream's full tool list, marked with what the team policy allows (admin)."""
        await self._teams.require_admin(ctx)
        access = await self._team_policy(ctx, slug)
        tools = await self._upstream.list_tools(access.target)
        return [
            CatalogEntry(
                name=t.name,
                title=t.title,
                description=t.description,
                allowed_for_team=access.tools.allows(t.name),
            )
            for t in tools
        ]

    # --- member grants (team admins) -----------------------------------------------------------

    async def list_grants(self, ctx: TeamContext, user_id: uuid.UUID) -> list[GrantView]:
        await self._teams.require_admin(ctx)
        async with self._db.transaction() as session:
            membership_id = await self._membership_id(session, ctx, user_id)
            rows = (
                await session.execute(
                    select(MemberToolGrant, Connector.slug)
                    .join(Connector, Connector.id == MemberToolGrant.connector_id)
                    .where(MemberToolGrant.membership_id == membership_id)
                    .order_by(Connector.slug)
                )
            ).all()
        return [
            GrantView(
                connector_slug=slug,
                tools=ToolSet.of(grant.allow_all_tools, grant.tools),
                updated_at=grant.updated_at,
            )
            for grant, slug in rows
        ]

    async def set_grant(
        self,
        ctx: TeamContext,
        user_id: uuid.UUID,
        slug: str,
        *,
        allow_all_tools: bool,
        tools: list[str],
    ) -> GrantView:
        await self._teams.require_admin(ctx)
        access = await self._team_policy(ctx, slug)
        names = [] if allow_all_tools else _validate_tools(tools)
        outside = [n for n in names if not access.tools.allows(n)]
        if outside:
            raise InvalidInputError(
                "These tools are not enabled for the team on this connector: " + ", ".join(outside)
            )
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            membership_id = await self._membership_id(session, ctx, user_id, members_only=True)
            await session.execute(
                insert(MemberToolGrant)
                .values(
                    id=uuid.uuid4(),
                    membership_id=membership_id,
                    connector_id=access.target.connector_id,
                    allow_all_tools=allow_all_tools,
                    tools=names,
                    granted_by_user_id=ctx.principal.user_id,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_member_tool_grants_membership_id_connector_id",
                    set_={
                        "allow_all_tools": allow_all_tools,
                        "tools": names,
                        "granted_by_user_id": ctx.principal.user_id,
                        "updated_at": now,
                    },
                )
            )
            self._audit.add(
                session,
                action="member_grant.set",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="user",
                target_id=user_id,
                details={"connector": slug, "allow_all_tools": allow_all_tools, "tools": names},
            )
        return GrantView(
            connector_slug=slug, tools=ToolSet.of(allow_all_tools, names), updated_at=now
        )

    async def revoke_grant(self, ctx: TeamContext, user_id: uuid.UUID, slug: str) -> None:
        await self._teams.require_admin(ctx)
        connector = await self._enabled_or_any(slug)
        async with self._db.transaction() as session:
            membership_id = await self._membership_id(session, ctx, user_id)
            deleted = await session.scalar(
                delete(MemberToolGrant)
                .where(
                    MemberToolGrant.membership_id == membership_id,
                    MemberToolGrant.connector_id == connector.id,
                )
                .returning(MemberToolGrant.id)
            )
            if deleted is None:
                raise NotFoundError("The member has no grant for this connector.")
            self._audit.add(
                session,
                action="member_grant.revoke",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="user",
                target_id=user_id,
                details={"connector": slug},
            )

    # --- effective access (every discovery / invocation) ----------------------------------------

    async def resolve(self, ctx: TeamContext, slug: str) -> ConnectorAccess:
        access = await self._team_policy(ctx, slug)
        grant: MemberToolGrant | None = None
        if not ctx.is_admin:
            async with self._db.transaction() as session:
                grant = await session.scalar(
                    select(MemberToolGrant).where(
                        MemberToolGrant.membership_id == ctx.membership_id,
                        MemberToolGrant.connector_id == access.target.connector_id,
                    )
                )
        tools = self._effective_from_policy(ctx, slug, access.tools, grant)
        if tools.is_empty:
            await self._audit.record(
                action="authz.denied",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="connector",
                target_id=access.target.connector_id,
                outcome=AuditOutcome.DENIED,
                details={"connector": slug, "reason": "no_tool_access"},
            )
            raise PermissionDeniedError("You have no access to tools on this connector.")
        return ConnectorAccess(target=access.target, tools=tools)

    async def _team_policy(self, ctx: TeamContext, slug: str) -> ConnectorAccess:
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(Connector, TeamConnector)
                    .join(TeamConnector, TeamConnector.connector_id == Connector.id)
                    .where(
                        Connector.slug == slug,
                        Connector.enabled.is_(True),
                        TeamConnector.team_id == ctx.team_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Connector not found or not enabled for this team.")
        connector, team_connector = row
        return ConnectorAccess(
            target=self.target_of(connector),
            tools=ToolSet.of(team_connector.allow_all_tools, team_connector.allowed_tools),
        )

    @staticmethod
    def _effective_from_policy(
        ctx: TeamContext, slug: str, policy: ToolSet, grant: MemberToolGrant | None
    ) -> ToolSet:
        tools = policy
        if not ctx.is_admin:
            if grant is None:
                return ToolSet(all_tools=False, names=frozenset())
            tools = tools.intersect(ToolSet.of(grant.allow_all_tools, grant.tools))
        narrowing = key_restriction(ctx.principal, slug)
        if narrowing is not None:
            tools = tools.intersect(narrowing)
        return tools

    @staticmethod
    async def _membership_id(
        session: AsyncSession,
        ctx: TeamContext,
        user_id: uuid.UUID,
        *,
        members_only: bool = False,
    ) -> uuid.UUID:
        row = (
            await session.execute(
                select(TeamMembership.id, TeamMembership.role).where(
                    TeamMembership.team_id == ctx.team_id, TeamMembership.user_id == user_id
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Member not found.")
        membership_id, role = row
        if members_only and role == TeamRole.ADMIN.value:
            raise InvalidInputError(
                "Admins already have every tool enabled for the team; grants apply to members."
            )
        result: uuid.UUID = membership_id
        return result
