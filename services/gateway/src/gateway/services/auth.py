"""Users, password login and session tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import AuthSettings
from gateway.db.engine import Database
from gateway.db.models import AuditOutcome, AuthSession, User
from gateway.errors import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    SignupDisabledError,
)
from gateway.security import PasswordService, TokenHasher, TokenKind
from gateway.services.audit import AuditLog
from gateway.services.principal import CredentialKind, Principal


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    expires_at: datetime
    user: User


def normalise_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        db: Database,
        *,
        passwords: PasswordService,
        tokens: TokenHasher,
        audit: AuditLog,
        settings: AuthSettings,
    ) -> None:
        self._db = db
        self._passwords = passwords
        self._tokens = tokens
        self._audit = audit
        self._settings = settings

    def validate_password(self, password: str) -> None:
        if len(password) < self._settings.password_min_length:
            raise InvalidInputError(
                f"Password must be at least {self._settings.password_min_length} characters."
            )
        if len(password) > 256 or not password.strip():
            raise InvalidInputError("Password is not acceptable.")

    async def hash_new_password(self, password: str) -> str:
        """Validate and hash; done before opening a transaction since hashing is slow."""
        self.validate_password(password)
        return await self._passwords.hash(password)

    async def create_user(
        self, session: AsyncSession, *, email: str, password_hash: str, display_name: str
    ) -> User:
        """Insert a user in the caller's transaction. Registration policy is checked by callers."""
        user = User(
            id=uuid.uuid4(),
            email=normalise_email(email),
            display_name=display_name.strip(),
            password_hash=password_hash,
            is_active=True,
            is_platform_admin=False,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("An account with this email already exists.") from exc
        self._audit.add(
            session,
            action="user.register",
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
        )
        return user

    async def register(self, *, email: str, password: str, display_name: str) -> User:
        if not self._settings.allow_signup:
            raise SignupDisabledError()
        password_hash = await self.hash_new_password(password)
        async with self._db.transaction() as session:
            return await self.create_user(
                session, email=email, password_hash=password_hash, display_name=display_name
            )

    async def login(
        self, *, email: str, password: str, ip: str | None, user_agent: str | None
    ) -> IssuedSession:
        email = normalise_email(email)
        async with self._db.transaction() as session:
            user = await session.scalar(select(User).where(User.email == email))
        ok = await self._passwords.verify(user.password_hash if user else None, password)
        if user is None or not ok or not user.is_active:
            await self._audit.record(
                action="auth.login",
                actor_user_id=user.id if user else None,
                outcome=AuditOutcome.DENIED,
                details={"reason": "invalid_credentials" if not ok else "inactive"},
            )
            raise AuthenticationError("Invalid email or password.")

        issued = self._tokens.issue(TokenKind.SESSION)
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self._settings.session_ttl_hours)
        new_hash = (
            await self._passwords.hash(password)
            if self._passwords.needs_rehash(user.password_hash)
            else None
        )
        async with self._db.transaction() as session:
            session.add(
                AuthSession(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    token_hash=issued.digest,
                    expires_at=expires_at,
                    ip=ip,
                    user_agent=(user_agent or "")[:256] or None,
                )
            )
            values: dict[str, object] = {"last_login_at": now}
            if new_hash:
                values["password_hash"] = new_hash
            await session.execute(update(User).where(User.id == user.id).values(**values))
            self._audit.add(
                session,
                action="auth.login",
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
            )
        return IssuedSession(token=issued.raw, expires_at=expires_at, user=user)

    async def logout(self, principal: Principal) -> None:
        if principal.session_id is None:
            return
        async with self._db.transaction() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.id == principal.session_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            self._audit.add(session, action="auth.logout", actor=principal)

    async def get_user(self, user_id: uuid.UUID) -> User:
        async with self._db.transaction() as session:
            user = await session.get(User, user_id)
        if user is None:
            raise AuthenticationError()
        return user

    async def authenticate_session(self, raw_token: str) -> Principal:
        digest = self._tokens.digest(raw_token)
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(AuthSession.id, User)
                    .join(User, User.id == AuthSession.user_id)
                    .where(
                        AuthSession.token_hash == digest,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > now,
                        User.is_active.is_(True),
                    )
                )
            ).one_or_none()
        if row is None:
            raise AuthenticationError()
        session_id, user = row
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_platform_admin=user.is_platform_admin,
            credential=CredentialKind.SESSION,
            session_id=session_id,
        )
