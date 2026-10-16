"""Platform API keys: issuance, listing, revocation, rotation and verification.

Security policy:

* The raw key is returned exactly once (on create and rotate); only its HMAC is stored.
* Every key expires. Lifetimes are capped by the team's ``max_key_ttl_days`` and the platform
  ``API_KEY_MAX_TTL_DAYS``.
* Members may create keys for themselves only if the team allows it; admins always may.
  Keys are always issued to the caller: nobody can mint a key that acts as another user.
* Admins may list and revoke any key in their team; members only their own.
* Only the owner can rotate a key, since rotation hands out a new working secret.
* On every request the key must be unexpired and unrevoked, and its owner must still be an
  active member of the key's team. Effective permissions are computed live, so role changes
  and grant changes apply to existing keys immediately.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import ApiKeySettings
from gateway.db.engine import Database
from gateway.db.models import ApiKey, ApiKeyScope, Team, TeamMembership, User
from gateway.errors import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from gateway.security import TokenHasher, TokenKind
from gateway.services.audit import AuditLog
from gateway.services.principal import CredentialKind, Principal
from gateway.services.teams import TeamContext

TOOL_RESTRICTION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}/(\*|[A-Za-z0-9_.\-]{1,128})$")


class ApiKeyStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


def key_status(key: ApiKey, now: datetime | None = None) -> ApiKeyStatus:
    if key.revoked_at is not None:
        return ApiKeyStatus.REVOKED
    if key.expires_at <= (now or datetime.now(UTC)):
        return ApiKeyStatus.EXPIRED
    return ApiKeyStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class NewApiKey:
    name: str
    scopes: frozenset[ApiKeyScope]
    expires_in_days: int | None = None
    tool_restrictions: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    key: ApiKey
    raw: str


class ApiKeyService:
    def __init__(
        self, db: Database, *, tokens: TokenHasher, audit: AuditLog, settings: ApiKeySettings
    ) -> None:
        self._db = db
        self._tokens = tokens
        self._audit = audit
        self._settings = settings

    # --- management ----------------------------------------------------------------------------

    async def create(self, ctx: TeamContext, spec: NewApiKey) -> IssuedApiKey:
        if not ctx.is_admin and not ctx.team.members_can_create_keys:
            raise PermissionDeniedError("This team does not allow members to create API keys.")
        restrictions = _validate_restrictions(spec.tool_restrictions)
        lifetime = self._lifetime(ctx.team, spec.expires_in_days)
        async with self._db.transaction() as session:
            await self._enforce_quota(session, ctx)
            issued = await self._insert(
                session,
                ctx,
                name=spec.name,
                scopes=spec.scopes,
                restrictions=restrictions,
                lifetime=lifetime,
                rotated_from=None,
            )
            self._audit.add(
                session,
                action="api_key.create",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="api_key",
                target_id=issued.key.id,
                details={
                    "name": spec.name,
                    "prefix": issued.key.prefix,
                    "scopes": sorted(spec.scopes),
                    "expires_at": issued.key.expires_at.isoformat(),
                },
            )
        return issued

    async def list(
        self, ctx: TeamContext, *, owner_user_id: uuid.UUID | None, include_inactive: bool
    ) -> list[ApiKey]:
        stmt = select(ApiKey).where(ApiKey.team_id == ctx.team_id)
        if not ctx.is_admin:
            if owner_user_id not in (None, ctx.principal.user_id):
                raise PermissionDeniedError("Members can only list their own API keys.")
            owner_user_id = ctx.principal.user_id
        if owner_user_id is not None:
            stmt = stmt.where(ApiKey.owner_user_id == owner_user_id)
        if not include_inactive:
            stmt = stmt.where(ApiKey.revoked_at.is_(None), ApiKey.expires_at > func.now())
        async with self._db.transaction() as session:
            return list((await session.scalars(stmt.order_by(ApiKey.created_at.desc()))).all())

    async def get(self, ctx: TeamContext, key_id: uuid.UUID) -> ApiKey:
        async with self._db.transaction() as session:
            return await self._visible_key(session, ctx, key_id)

    async def revoke(self, ctx: TeamContext, key_id: uuid.UUID) -> ApiKey:
        async with self._db.transaction() as session:
            key = await self._visible_key(session, ctx, key_id, lock=True)
            if key.revoked_at is None:
                key.revoked_at = datetime.now(UTC)
                key.revoked_by_user_id = ctx.principal.user_id
                self._audit.add(
                    session,
                    action="api_key.revoke",
                    actor=ctx.principal,
                    team_id=ctx.team_id,
                    target_type="api_key",
                    target_id=key.id,
                    details={"prefix": key.prefix, "owner_user_id": str(key.owner_user_id)},
                )
        return key

    async def rotate(
        self, ctx: TeamContext, key_id: uuid.UUID, *, grace_hours: int
    ) -> IssuedApiKey:
        if grace_hours > self._settings.max_rotation_grace_hours:
            raise InvalidInputError(
                f"grace_hours cannot exceed {self._settings.max_rotation_grace_hours}."
            )
        if not ctx.is_admin and not ctx.team.members_can_create_keys:
            raise PermissionDeniedError("This team does not allow members to create API keys.")
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            old = await self._visible_key(session, ctx, key_id, lock=True)
            if old.owner_user_id != ctx.principal.user_id:
                raise PermissionDeniedError("Only the key owner can rotate it; revoke it instead.")
            if key_status(old, now) is not ApiKeyStatus.ACTIVE:
                raise ConflictError("Only active keys can be rotated.")
            original_lifetime = old.expires_at - old.created_at
            lifetime = min(original_lifetime, self._lifetime(ctx.team, None, cap_only=True))
            issued = await self._insert(
                session,
                ctx,
                name=old.name,
                scopes=frozenset(ApiKeyScope(s) for s in old.scopes),
                restrictions=tuple(old.tool_restrictions) if old.tool_restrictions else None,
                lifetime=lifetime,
                rotated_from=old.id,
            )
            if grace_hours == 0:
                old.revoked_at = now
                old.revoked_by_user_id = ctx.principal.user_id
            else:
                old.expires_at = min(old.expires_at, now + timedelta(hours=grace_hours))
            self._audit.add(
                session,
                action="api_key.rotate",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="api_key",
                target_id=old.id,
                details={
                    "old_prefix": old.prefix,
                    "new_key_id": str(issued.key.id),
                    "new_prefix": issued.key.prefix,
                    "grace_hours": grace_hours,
                },
            )
        return issued

    # --- verification --------------------------------------------------------------------------

    async def authenticate(self, raw: str) -> Principal:
        digest = self._tokens.digest(raw)
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(ApiKey, User, Team.rate_limit_per_minute)
                    .join(User, User.id == ApiKey.owner_user_id)
                    .join(Team, Team.id == ApiKey.team_id)
                    .join(
                        TeamMembership,
                        (TeamMembership.team_id == ApiKey.team_id)
                        & (TeamMembership.user_id == ApiKey.owner_user_id),
                    )
                    .where(
                        ApiKey.key_hash == digest,
                        ApiKey.revoked_at.is_(None),
                        ApiKey.expires_at > now,
                        User.is_active.is_(True),
                    )
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationError()
            key, user, team_limit = row
            resolution = timedelta(seconds=self._settings.last_used_resolution_seconds)
            if key.last_used_at is None or key.last_used_at < now - resolution:
                await session.execute(
                    update(ApiKey)
                    .where(
                        ApiKey.id == key.id,
                        or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < now - resolution),
                    )
                    .values(last_used_at=now)
                )
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_platform_admin=False,  # keys never carry operator privileges
            credential=CredentialKind.API_KEY,
            api_key_id=key.id,
            api_key_team_id=key.team_id,
            api_key_scopes=frozenset(ApiKeyScope(s) for s in key.scopes),
            api_key_tool_restrictions=(
                tuple(key.tool_restrictions) if key.tool_restrictions is not None else None
            ),
            rate_limit_per_minute=team_limit,
        )

    # --- internals -----------------------------------------------------------------------------

    def _lifetime(self, team: Team, days: int | None, *, cap_only: bool = False) -> timedelta:
        cap = min(team.max_key_ttl_days, self._settings.max_ttl_days)
        if cap_only:
            return timedelta(days=cap)
        if days is None:
            days = min(self._settings.default_ttl_days, cap)
        if days > cap:
            raise InvalidInputError(f"This team allows API keys to live at most {cap} days.")
        return timedelta(days=days)

    async def _enforce_quota(self, session: AsyncSession, ctx: TeamContext) -> None:
        active = await session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.team_id == ctx.team_id,
                ApiKey.owner_user_id == ctx.principal.user_id,
                ApiKey.revoked_at.is_(None),
                ApiKey.expires_at > func.now(),
            )
        )
        if (active or 0) >= self._settings.max_keys_per_user_per_team:
            raise ConflictError("Active API key limit reached; revoke an unused key first.")

    async def _insert(
        self,
        session: AsyncSession,
        ctx: TeamContext,
        *,
        name: str,
        scopes: frozenset[ApiKeyScope],
        restrictions: tuple[str, ...] | None,
        lifetime: timedelta,
        rotated_from: uuid.UUID | None,
    ) -> IssuedApiKey:
        if not scopes:
            raise InvalidInputError("At least one scope is required.")
        issued = self._tokens.issue(TokenKind.API_KEY)
        now = datetime.now(UTC)
        key = ApiKey(
            id=uuid.uuid4(),
            team_id=ctx.team_id,
            owner_user_id=ctx.principal.user_id,
            name=name.strip(),
            prefix=issued.display_prefix,
            key_hash=issued.digest,
            scopes=sorted(s.value for s in scopes),
            tool_restrictions=list(restrictions) if restrictions is not None else None,
            created_by_user_id=ctx.principal.user_id,
            created_at=now,
            expires_at=now + lifetime,
            rotated_from_id=rotated_from,
        )
        session.add(key)
        await session.flush()
        return IssuedApiKey(key=key, raw=issued.raw)

    async def _visible_key(
        self, session: AsyncSession, ctx: TeamContext, key_id: uuid.UUID, *, lock: bool = False
    ) -> ApiKey:
        stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.team_id == ctx.team_id)
        if not ctx.is_admin:
            stmt = stmt.where(ApiKey.owner_user_id == ctx.principal.user_id)
        if lock:
            stmt = stmt.with_for_update()
        key = await session.scalar(stmt)
        if key is None:
            raise NotFoundError("API key not found.")
        return key


def _validate_restrictions(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    cleaned = tuple(sorted({v.strip() for v in values}))
    bad = [v for v in cleaned if not TOOL_RESTRICTION_RE.match(v)]
    if bad:
        raise InvalidInputError(
            "tool_restrictions entries must look like 'connector-slug/tool_name' or "
            "'connector-slug/*'."
        )
    if not cleaned:
        raise InvalidInputError("tool_restrictions must not be empty; omit it instead.")
    return cleaned
