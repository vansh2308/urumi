"""Teams, memberships and roles, plus the per-request team authorization context.

Authorization is always derived from the membership row loaded from the database for the
current request, never from anything in the request body, so a member cannot become an admin
by editing a payload. Resources of teams the caller does not belong to answer 404, which
prevents IDOR probing for other teams' ids.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import ApiKeySettings, RateLimitSettings
from gateway.db.engine import Database
from gateway.db.models import AuditOutcome, Team, TeamMembership, TeamRole, User
from gateway.errors import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from gateway.services.audit import AuditLog
from gateway.services.principal import Principal


@dataclass(frozen=True, slots=True)
class TeamContext:
    """The caller's standing in one team, valid for the current request only."""

    principal: Principal
    team: Team
    membership_id: uuid.UUID
    role: TeamRole

    @property
    def team_id(self) -> uuid.UUID:
        return self.team.id

    @property
    def is_admin(self) -> bool:
        return self.role is TeamRole.ADMIN


@dataclass(frozen=True, slots=True)
class MemberView:
    user_id: uuid.UUID
    email: str
    display_name: str
    role: TeamRole
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class TeamWithRole:
    team: Team
    role: TeamRole


@dataclass(frozen=True, slots=True)
class TeamSettingsChange:
    name: str | None = None
    members_can_create_keys: bool | None = None
    max_key_ttl_days: int | None = None
    rate_limit_per_minute: int | None = None
    clear_rate_limit: bool = False


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")[:48] or "team"
    return f"{slug}-{secrets.token_hex(3)}"


class TeamService:
    def __init__(
        self,
        db: Database,
        *,
        audit: AuditLog,
        api_key_settings: ApiKeySettings,
        rate_limit_settings: RateLimitSettings,
    ) -> None:
        self._db = db
        self._audit = audit
        self._key_settings = api_key_settings
        self._rate_settings = rate_limit_settings

    # --- authorization context -----------------------------------------------------------------

    async def context(self, principal: Principal, team_id: uuid.UUID) -> TeamContext:
        if principal.api_key_team_id is not None and principal.api_key_team_id != team_id:
            raise NotFoundError("Team not found.")
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(Team, TeamMembership.id, TeamMembership.role)
                    .join(TeamMembership, TeamMembership.team_id == Team.id)
                    .where(Team.id == team_id, TeamMembership.user_id == principal.user_id)
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("Team not found.")
        team, membership_id, role = row
        return TeamContext(
            principal=principal, team=team, membership_id=membership_id, role=TeamRole(role)
        )

    async def require_admin(self, ctx: TeamContext) -> None:
        if not ctx.is_admin:
            await self._audit.record(
                action="authz.denied",
                actor=ctx.principal,
                team_id=ctx.team_id,
                outcome=AuditOutcome.DENIED,
                details={"required_role": TeamRole.ADMIN.value},
            )
            raise PermissionDeniedError("This action requires the team admin role.")

    # --- teams ---------------------------------------------------------------------------------

    async def create_team(self, principal: Principal, *, name: str) -> Team:
        team = Team(
            id=uuid.uuid4(),
            name=name.strip(),
            slug=slugify(name),
            members_can_create_keys=True,
            max_key_ttl_days=self._key_settings.default_ttl_days,
        )
        async with self._db.transaction() as session:
            session.add(team)
            await session.flush()
            session.add(
                TeamMembership(
                    id=uuid.uuid4(),
                    team_id=team.id,
                    user_id=principal.user_id,
                    role=TeamRole.ADMIN.value,
                )
            )
            self._audit.add(
                session,
                action="team.create",
                actor=principal,
                team_id=team.id,
                target_type="team",
                target_id=team.id,
                details={"name": team.name},
            )
        return team

    async def list_for_user(self, user_id: uuid.UUID) -> list[TeamWithRole]:
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(Team, TeamMembership.role)
                    .join(TeamMembership, TeamMembership.team_id == Team.id)
                    .where(TeamMembership.user_id == user_id)
                    .order_by(Team.name)
                )
            ).all()
        return [TeamWithRole(team=team, role=TeamRole(role)) for team, role in rows]

    async def update_settings(self, ctx: TeamContext, change: TeamSettingsChange) -> Team:
        await self.require_admin(ctx)
        values: dict[str, Any] = {}
        if change.name is not None:
            values["name"] = change.name.strip()
        if change.members_can_create_keys is not None:
            values["members_can_create_keys"] = change.members_can_create_keys
        if change.max_key_ttl_days is not None:
            if change.max_key_ttl_days > self._key_settings.max_ttl_days:
                raise InvalidInputError(
                    f"max_key_ttl_days cannot exceed {self._key_settings.max_ttl_days}."
                )
            values["max_key_ttl_days"] = change.max_key_ttl_days
        if change.clear_rate_limit:
            values["rate_limit_per_minute"] = None
        elif change.rate_limit_per_minute is not None:
            if change.rate_limit_per_minute > self._rate_settings.max_team_per_minute:
                raise InvalidInputError(
                    "rate_limit_per_minute cannot exceed "
                    f"{self._rate_settings.max_team_per_minute}."
                )
            values["rate_limit_per_minute"] = change.rate_limit_per_minute
        if not values:
            return ctx.team
        async with self._db.transaction() as session:
            team = (
                await session.execute(
                    update(Team)
                    .where(Team.id == ctx.team_id)
                    .values(**values, updated_at=func.now())
                    .returning(Team)
                )
            ).scalar_one()
            self._audit.add(
                session,
                action="team.update_settings",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="team",
                target_id=ctx.team_id,
                details={k: v for k, v in values.items()},
            )
        return team

    # --- members -------------------------------------------------------------------------------

    async def list_members(self, ctx: TeamContext) -> list[MemberView]:
        await self.require_admin(ctx)
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(User, TeamMembership.role, TeamMembership.created_at)
                    .join(TeamMembership, TeamMembership.user_id == User.id)
                    .where(TeamMembership.team_id == ctx.team_id)
                    .order_by(TeamMembership.created_at)
                )
            ).all()
        return [
            MemberView(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                role=TeamRole(role),
                joined_at=joined,
            )
            for user, role, joined in rows
        ]

    async def change_role(self, ctx: TeamContext, user_id: uuid.UUID, role: TeamRole) -> None:
        await self.require_admin(ctx)
        async with self._db.transaction() as session:
            membership = await self._lock_membership(session, ctx.team_id, user_id)
            if membership.role == role.value:
                return
            if membership.role == TeamRole.ADMIN.value:
                await self._ensure_another_admin(session, ctx.team_id, user_id)
            membership.role = role.value
            membership.updated_at = datetime.now(UTC)
            self._audit.add(
                session,
                action="member.change_role",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="user",
                target_id=user_id,
                details={"role": role.value},
            )

    async def remove_member(self, ctx: TeamContext, user_id: uuid.UUID) -> None:
        await self.require_admin(ctx)
        async with self._db.transaction() as session:
            membership = await self._lock_membership(session, ctx.team_id, user_id)
            if membership.role == TeamRole.ADMIN.value:
                await self._ensure_another_admin(session, ctx.team_id, user_id)
            await session.execute(delete(TeamMembership).where(TeamMembership.id == membership.id))
            self._audit.add(
                session,
                action="member.remove",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="user",
                target_id=user_id,
            )

    async def add_member(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        role: TeamRole,
    ) -> None:
        session.add(
            TeamMembership(id=uuid.uuid4(), team_id=team_id, user_id=user_id, role=role.value)
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("The user is already a member of this team.") from exc

    async def _lock_membership(
        self, session: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> TeamMembership:
        # Lock all admin rows of the team too, so two concurrent demotions cannot both see
        # "another admin exists" and leave the team without one.
        await session.execute(
            select(TeamMembership.id)
            .where(TeamMembership.team_id == team_id, TeamMembership.role == TeamRole.ADMIN.value)
            .with_for_update()
        )
        membership = await session.scalar(
            select(TeamMembership)
            .where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
            .with_for_update()
        )
        if membership is None:
            raise NotFoundError("Member not found.")
        return membership

    async def _ensure_another_admin(
        self, session: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        others = await session.scalar(
            select(func.count())
            .select_from(TeamMembership)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.role == TeamRole.ADMIN.value,
                TeamMembership.user_id != user_id,
            )
        )
        if not others:
            raise ConflictError("A team must keep at least one admin.")
