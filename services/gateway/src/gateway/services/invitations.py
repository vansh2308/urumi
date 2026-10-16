"""Team invitations.

The gateway sends no email: creating an invitation returns the raw ``uri_`` token once, and the
admin delivers it. Accepting requires being signed in as the invited email address, so a leaked
token cannot be redeemed by somebody else's account.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import AuthSettings
from gateway.db.engine import Database
from gateway.db.models import Invitation, TeamMembership, TeamRole, User
from gateway.errors import ConflictError, InvalidInputError, NotFoundError
from gateway.security import TokenHasher, TokenKind
from gateway.services.audit import AuditLog
from gateway.services.auth import AuthService, normalise_email
from gateway.services.principal import Principal
from gateway.services.teams import TeamContext, TeamService


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


def invitation_status(inv: Invitation, now: datetime | None = None) -> InvitationStatus:
    if inv.accepted_at is not None:
        return InvitationStatus.ACCEPTED
    if inv.revoked_at is not None:
        return InvitationStatus.REVOKED
    if inv.expires_at <= (now or datetime.now(UTC)):
        return InvitationStatus.EXPIRED
    return InvitationStatus.PENDING


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    token: str


_INVALID = "This invitation is invalid, expired or was issued to a different email address."


class InvitationService:
    def __init__(
        self,
        db: Database,
        *,
        tokens: TokenHasher,
        teams: TeamService,
        auth: AuthService,
        audit: AuditLog,
        settings: AuthSettings,
    ) -> None:
        self._db = db
        self._tokens = tokens
        self._teams = teams
        self._auth = auth
        self._audit = audit
        self._ttl = timedelta(hours=settings.invitation_ttl_hours)

    async def create(self, ctx: TeamContext, *, email: str, role: TeamRole) -> IssuedInvitation:
        await self._teams.require_admin(ctx)
        email = normalise_email(email)
        issued = self._tokens.issue(TokenKind.INVITATION)
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            already_member = await session.scalar(
                select(TeamMembership.id)
                .join(User, User.id == TeamMembership.user_id)
                .where(TeamMembership.team_id == ctx.team_id, User.email == email)
            )
            if already_member is not None:
                raise ConflictError("That user is already a member of this team.")
            # An expired open invitation would block the unique index; retire it first.
            await session.execute(
                update(Invitation)
                .where(
                    Invitation.team_id == ctx.team_id,
                    Invitation.email == email,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at <= now,
                )
                .values(revoked_at=now)
            )
            invitation = Invitation(
                id=uuid.uuid4(),
                team_id=ctx.team_id,
                email=email,
                role=role.value,
                token_hash=issued.digest,
                invited_by_user_id=ctx.principal.user_id,
                created_at=now,
                expires_at=now + self._ttl,
            )
            session.add(invitation)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "An open invitation for this email already exists; revoke it first."
                ) from exc
            self._audit.add(
                session,
                action="invitation.create",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="invitation",
                target_id=invitation.id,
                details={"email": email, "role": role.value},
            )
        return IssuedInvitation(invitation=invitation, token=issued.raw)

    async def list(self, ctx: TeamContext, *, include_closed: bool) -> list[Invitation]:
        await self._teams.require_admin(ctx)
        stmt = select(Invitation).where(Invitation.team_id == ctx.team_id)
        if not include_closed:
            stmt = stmt.where(
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > datetime.now(UTC),
            )
        async with self._db.transaction() as session:
            return list((await session.scalars(stmt.order_by(Invitation.created_at.desc()))).all())

    async def revoke(self, ctx: TeamContext, invitation_id: uuid.UUID) -> None:
        await self._teams.require_admin(ctx)
        async with self._db.transaction() as session:
            revoked = await session.scalar(
                update(Invitation)
                .where(
                    Invitation.id == invitation_id,
                    Invitation.team_id == ctx.team_id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
                .returning(Invitation.id)
            )
            if revoked is None:
                raise NotFoundError("No open invitation with that id.")
            self._audit.add(
                session,
                action="invitation.revoke",
                actor=ctx.principal,
                team_id=ctx.team_id,
                target_type="invitation",
                target_id=invitation_id,
            )

    async def accept(self, principal: Principal, raw_token: str) -> Invitation:
        async with self._db.transaction() as session:
            return await self._accept(
                session, raw_token, user_id=principal.user_id, email=principal.email
            )

    async def register_and_accept(
        self, *, raw_token: str, email: str, password: str, display_name: str
    ) -> User:
        """Registration path that works even when self-signup is disabled."""
        password_hash = await self._auth.hash_new_password(password)
        async with self._db.transaction() as session:
            user = await self._auth.create_user(
                session, email=email, password_hash=password_hash, display_name=display_name
            )
            await self._accept(session, raw_token, user_id=user.id, email=user.email)
        return user

    async def _accept(
        self, session: AsyncSession, raw_token: str, *, user_id: uuid.UUID, email: str
    ) -> Invitation:
        if TokenHasher.kind_of(raw_token) is not TokenKind.INVITATION:
            raise InvalidInputError(_INVALID)
        now = datetime.now(UTC)
        invitation = await session.scalar(
            select(Invitation)
            .where(Invitation.token_hash == self._tokens.digest(raw_token))
            .with_for_update()
        )
        if (
            invitation is None
            or invitation_status(invitation, now) is not InvitationStatus.PENDING
            or invitation.email != normalise_email(email)
        ):
            raise InvalidInputError(_INVALID)
        await self._teams.add_member(
            session, team_id=invitation.team_id, user_id=user_id, role=TeamRole(invitation.role)
        )
        invitation.accepted_at = now
        invitation.accepted_by_user_id = user_id
        self._audit.add(
            session,
            action="invitation.accept",
            actor_user_id=user_id,
            team_id=invitation.team_id,
            target_type="invitation",
            target_id=invitation.id,
            details={"role": invitation.role},
        )
        return invitation
