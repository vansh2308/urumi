"""OAuth 2.1 authorization server for the gateway's MCP endpoint (MCP authorization spec).

The protocol (metadata, /authorize, /token, /register, /revoke, PKCE and redirect URI checks,
client authentication) is the official MCP SDK's (``mcp.server.auth``). This module is the
storage and policy behind it, plugged in as the SDK's ``OAuthAuthorizationServerProvider``:

* **Clients** register dynamically (RFC 7591). Confidential clients' secrets are encrypted at
  rest (the SDK compares them in plain text, so they cannot be hashed).
* **/authorize** stores the request and sends the browser to the dashboard's consent page, where
  the signed-in user picks a team and approves or denies (:meth:`OAuthService.decide`).
* **Codes** are single use, short-lived and stored hashed.
* **Access tokens are platform API keys** (``urk_``) that expire after an hour and belong to an
  :class:`OAuthGrant` (client + user + team + scopes). Every existing check (membership, team
  policy, member grants, scopes, rate limits, audit attribution, revocation) therefore applies
  to OAuth clients unchanged, and they show up on the API keys page.
* **Refresh tokens** are rotated on every use; reusing an old one fails. Revoking a grant (from
  the dashboard or /revoke) revokes its current access token at once.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import OAuthSettings
from gateway.db.engine import Database
from gateway.db.models import (
    ApiKey,
    ApiKeyScope,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthCode,
    OAuthGrant,
    Team,
    TeamMembership,
    User,
)
from gateway.errors import NotFoundError
from gateway.security import TokenHasher, TokenKind
from gateway.services.api_keys import ApiKeyService
from gateway.services.audit import AuditLog
from gateway.services.principal import Principal
from gateway.services.teams import TeamService
from gateway.upstream.credentials import CredentialCipher

logger = logging.getLogger(__name__)

SCOPES = [ApiKeyScope.TOOLS_READ.value, ApiKeyScope.TOOLS_CALL.value, ApiKeyScope.USAGE_READ.value]
DEFAULT_SCOPES = [ApiKeyScope.TOOLS_READ.value, ApiKeyScope.TOOLS_CALL.value]
MCP_PATH = "/v1/mcp"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_PRIVATE_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*\.[a-z0-9+.-]+$")  # e.g. cursor.app, com.x.y


class GatewayAuthorizationCode(AuthorizationCode):
    user_id: uuid.UUID
    team_id: uuid.UUID


class GatewayRefreshToken(RefreshToken):
    grant_id: uuid.UUID


class GatewayAccessToken(AccessToken):
    grant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class PendingRequest:
    client_id: str
    client_name: str | None
    client_uri: str | None
    redirect_host: str
    scopes: list[str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GrantView:
    grant: OAuthGrant
    client_name: str | None
    team_name: str
    active_key_prefix: str | None


def _redirect_uri_allowed(uri: str) -> bool:
    """HTTPS, an HTTP loopback address (native apps, RFC 8252 §7.3) or a private-use scheme
    (RFC 8252 §7.1, e.g. cursor://). Never plain HTTP to another host."""
    parts = urlsplit(uri)
    if parts.fragment:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    if parts.scheme == "http":
        return (parts.hostname or "") in _LOOPBACK
    return bool(_PRIVATE_SCHEME.match(parts.scheme)) or parts.scheme in {"cursor", "vscode"}


class OAuthService:
    """Storage and policy for the SDK's OAuth endpoints, plus the consent and grant APIs."""

    def __init__(
        self,
        db: Database,
        *,
        tokens: TokenHasher,
        api_keys: ApiKeyService,
        teams: TeamService,
        audit: AuditLog,
        cipher: CredentialCipher,
        settings: OAuthSettings,
    ) -> None:
        self._db = db
        self._tokens = tokens
        self._api_keys = api_keys
        self._teams = teams
        self._audit = audit
        self._cipher = cipher
        self._settings = settings

    @property
    def issuer_url(self) -> str:
        return self._settings.issuer_url.rstrip("/")

    @property
    def resource_url(self) -> str:
        return self.issuer_url + MCP_PATH

    # --- clients (RFC 7591) ----------------------------------------------------------------------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        if not uris or not all(_redirect_uri_allowed(u) for u in uris):
            raise RegistrationError(
                "invalid_redirect_uri",
                "Redirect URIs must use HTTPS, an http://localhost address, or an app scheme.",
            )
        secret_ciphertext = None
        if client_info.client_secret:
            if not self._cipher.configured:
                raise RegistrationError(
                    "invalid_client_metadata",
                    "Confidential clients are not supported here; register with "
                    "token_endpoint_auth_method 'none' (PKCE).",
                )
            secret_ciphertext = self._cipher.encrypt(client_info.client_secret)
        info = client_info.model_dump(mode="json", exclude={"client_secret"}, exclude_none=True)
        async with self._db.transaction() as session:
            session.add(
                OAuthClient(
                    client_id=client_info.client_id,
                    client_name=(client_info.client_name or "")[:200] or None,
                    info=info,
                    secret_ciphertext=secret_ciphertext,
                )
            )
        logger.info("oauth client registered", extra={"client": client_info.client_name})

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._db.transaction() as session:
            row = await session.get(OAuthClient, client_id[:64])
        if row is None:
            return None
        secret = self._cipher.decrypt(row.secret_ciphertext) if row.secret_ciphertext else None
        return OAuthClientInformationFull.model_validate(row.info | {"client_secret": secret})

    # --- authorization ---------------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the dashboard's consent page."""
        if params.resource and params.resource.rstrip("/") not in {
            self.resource_url,
            self.issuer_url,
        }:
            raise AuthorizeError("invalid_request", "Unknown resource for this server.")
        issued = self._tokens.issue(TokenKind.OAUTH_REQUEST)
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            session.add(
                OAuthAuthorizationRequest(
                    request_hash=issued.digest,
                    client_id=client.client_id,
                    params=params.model_dump(mode="json"),
                    created_at=now,
                    expires_at=now
                    + timedelta(seconds=self._settings.authorization_request_ttl_seconds),
                )
            )
        return construct_redirect_uri(self._settings.consent_url, request=issued.raw)

    async def describe_request(self, raw: str) -> PendingRequest:
        """What the consent page shows. Anyone holding the request id may see this."""
        async with self._db.transaction() as session:
            request, client = await self._pending(session, raw, lock=False)
        params = AuthorizationParams.model_validate(request.params)
        redirect = urlsplit(str(params.redirect_uri))
        return PendingRequest(
            client_id=client.client_id,
            client_name=client.client_name,
            client_uri=client.info.get("client_uri"),
            redirect_host=redirect.netloc or f"{redirect.scheme}:",
            scopes=params.scopes or DEFAULT_SCOPES,
            expires_at=request.expires_at,
        )

    async def decide(
        self, principal: Principal, raw: str, *, approve: bool, team_id: uuid.UUID | None
    ) -> str:
        """The signed-in user's answer. Returns where to send the browser (the client's
        redirect URI with a code, or with error=access_denied)."""
        ctx = None
        if approve:
            if team_id is None:
                raise NotFoundError("Choose a team.")
            ctx = await self._teams.context(principal, team_id)  # must be a member
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            request, client = await self._pending(session, raw, lock=True)
            request.decided_at = now
            params = AuthorizationParams.model_validate(request.params)
            redirect = str(params.redirect_uri)
            if ctx is None:
                self._audit.add(
                    session,
                    action="oauth.consent.deny",
                    actor=principal,
                    details={"client_id": client.client_id, "client": client.client_name},
                )
                return construct_redirect_uri(
                    redirect, error="access_denied", state=params.state, iss=self.issuer_url
                )
            code = self._tokens.issue(TokenKind.OAUTH_CODE)
            session.add(
                OAuthCode(
                    code_hash=code.digest,
                    client_id=client.client_id,
                    user_id=principal.user_id,
                    team_id=ctx.team_id,
                    scopes=params.scopes or DEFAULT_SCOPES,
                    code_challenge=params.code_challenge,
                    redirect_uri=redirect,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=params.resource,
                    expires_at=now + timedelta(seconds=self._settings.code_ttl_seconds),
                )
            )
            self._audit.add(
                session,
                action="oauth.consent.approve",
                actor=principal,
                team_id=ctx.team_id,
                details={
                    "client_id": client.client_id,
                    "client": client.client_name,
                    "scopes": params.scopes or DEFAULT_SCOPES,
                },
            )
        return construct_redirect_uri(
            redirect, code=code.raw, state=params.state, iss=self.issuer_url
        )

    async def _pending(
        self, session: AsyncSession, raw: str, *, lock: bool
    ) -> tuple[OAuthAuthorizationRequest, OAuthClient]:
        stmt = (
            select(OAuthAuthorizationRequest, OAuthClient)
            .join(OAuthClient, OAuthClient.client_id == OAuthAuthorizationRequest.client_id)
            .where(
                OAuthAuthorizationRequest.request_hash == self._tokens.digest(raw),
                OAuthAuthorizationRequest.decided_at.is_(None),
                OAuthAuthorizationRequest.expires_at > datetime.now(UTC),
            )
        )
        if lock:
            stmt = stmt.with_for_update(of=OAuthAuthorizationRequest)
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise NotFoundError(
                "This authorization request has expired or was already answered. Start the "
                "connection from your MCP client again."
            )
        return row[0], row[1]

    # --- tokens -----------------------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> GatewayAuthorizationCode | None:
        async with self._db.transaction() as session:
            code = await session.scalar(
                select(OAuthCode).where(
                    OAuthCode.code_hash == self._tokens.digest(authorization_code),
                    OAuthCode.client_id == client.client_id,
                    OAuthCode.used_at.is_(None),
                )
            )
        if code is None:
            return None
        return GatewayAuthorizationCode(
            code=authorization_code,
            scopes=list(code.scopes),
            expires_at=code.expires_at.timestamp(),
            client_id=code.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=code.redirect_uri,
            redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
            resource=code.resource,
            subject=str(code.user_id),
            user_id=code.user_id,
            team_id=code.team_id,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: GatewayAuthorizationCode
    ) -> OAuthToken:
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            claimed = await session.scalar(
                update(OAuthCode)
                .where(
                    OAuthCode.code_hash == self._tokens.digest(authorization_code.code),
                    OAuthCode.used_at.is_(None),
                )
                .values(used_at=now)
                .returning(OAuthCode.code_hash)
            )
            if claimed is None:  # used concurrently
                raise TokenError("invalid_grant", "The authorization code was already used.")
            await self._require_membership(
                session, authorization_code.user_id, authorization_code.team_id
            )
            refresh = self._tokens.issue(TokenKind.OAUTH_REFRESH)
            grant = OAuthGrant(
                id=uuid.uuid4(),
                client_id=client.client_id,
                user_id=authorization_code.user_id,
                team_id=authorization_code.team_id,
                scopes=list(authorization_code.scopes),
                resource=authorization_code.resource,
                refresh_token_hash=refresh.digest,
                refresh_expires_at=now + timedelta(days=self._settings.refresh_token_ttl_days),
                created_at=now,
            )
            session.add(grant)
            await session.flush()
            token = await self._issue_access_token(session, grant, client, grant.scopes)
            self._audit.add(
                session,
                action="oauth.grant.create",
                actor_user_id=grant.user_id,
                team_id=grant.team_id,
                target_type="oauth_grant",
                target_id=grant.id,
                details={"client_id": client.client_id, "client": client.client_name},
            )
        return token.model_copy(update={"refresh_token": refresh.raw})

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> GatewayRefreshToken | None:
        async with self._db.transaction() as session:
            grant = await session.scalar(
                select(OAuthGrant).where(
                    OAuthGrant.refresh_token_hash == self._tokens.digest(refresh_token),
                    OAuthGrant.client_id == client.client_id,
                    OAuthGrant.revoked_at.is_(None),
                )
            )
        if grant is None:
            return None
        return GatewayRefreshToken(
            token=refresh_token,
            client_id=grant.client_id,
            scopes=list(grant.scopes),
            expires_at=int(grant.refresh_expires_at.timestamp()),
            resource=grant.resource,
            subject=str(grant.user_id),
            grant_id=grant.id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: GatewayRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        now = datetime.now(UTC)
        async with self._db.transaction() as session:
            grant = await session.scalar(
                select(OAuthGrant)
                .where(
                    OAuthGrant.id == refresh_token.grant_id,
                    OAuthGrant.refresh_token_hash == self._tokens.digest(refresh_token.token),
                    OAuthGrant.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if grant is None:  # rotated concurrently, or revoked
                raise TokenError("invalid_grant", "The refresh token is no longer valid.")
            await self._require_membership(session, grant.user_id, grant.team_id)
            refresh = self._tokens.issue(TokenKind.OAUTH_REFRESH)
            grant.refresh_token_hash = refresh.digest
            grant.refresh_expires_at = now + timedelta(days=self._settings.refresh_token_ttl_days)
            grant.last_refreshed_at = now
            token = await self._issue_access_token(session, grant, client, scopes)
        return token.model_copy(update={"refresh_token": refresh.raw})

    async def load_access_token(self, token: str) -> GatewayAccessToken | None:
        """Only for /revoke: OAuth-issued keys are verified like every other API key."""
        async with self._db.transaction() as session:
            row = (
                await session.execute(
                    select(ApiKey, OAuthGrant.client_id)
                    .join(OAuthGrant, OAuthGrant.id == ApiKey.oauth_grant_id)
                    .where(
                        ApiKey.key_hash == self._tokens.digest(token),
                        ApiKey.revoked_at.is_(None),
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        key, client_id = row
        assert key.oauth_grant_id is not None  # noqa: S101 - guaranteed by the join
        return GatewayAccessToken(
            token=token,
            client_id=client_id,
            scopes=list(key.scopes),
            expires_at=int(key.expires_at.timestamp()),
            subject=str(key.owner_user_id),
            grant_id=key.oauth_grant_id,
        )

    async def revoke_token(self, token: GatewayAccessToken | GatewayRefreshToken) -> None:
        """Revoking either token ends the whole grant (RFC 7009 §2.1 allows this)."""
        async with self._db.transaction() as session:
            await self._revoke_grant(session, token.grant_id, by=None)

    async def _issue_access_token(
        self,
        session: AsyncSession,
        grant: OAuthGrant,
        client: OAuthClientInformationFull,
        scopes: list[str],
    ) -> OAuthToken:
        ttl = self._settings.access_token_ttl_seconds
        issued = await self._api_keys.issue_oauth_key(
            session,
            grant_id=grant.id,
            team_id=grant.team_id,
            user_id=grant.user_id,
            scopes=frozenset(ApiKeyScope(s) for s in scopes),
            client_name=client.client_name or "MCP client",
            lifetime=timedelta(seconds=ttl),
        )
        return OAuthToken(
            access_token=issued.raw,
            token_type="Bearer",  # noqa: S106 - the OAuth token type, not a secret
            expires_in=ttl,
            scope=" ".join(scopes),
        )

    @staticmethod
    async def _require_membership(
        session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID
    ) -> None:
        member = await session.scalar(
            select(TeamMembership.id)
            .join(User, User.id == TeamMembership.user_id)
            .where(
                TeamMembership.user_id == user_id,
                TeamMembership.team_id == team_id,
                User.is_active.is_(True),
            )
        )
        if member is None:
            raise TokenError("invalid_grant", "The user is no longer a member of this team.")

    # --- grants ("connected apps") ---------------------------------------------------------------

    async def list_grants(self, principal: Principal) -> list[GrantView]:
        """The caller's own active grants, across their teams."""
        async with self._db.transaction() as session:
            rows = (
                await session.execute(
                    select(OAuthGrant, OAuthClient.client_name, Team.name)
                    .join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
                    .join(Team, Team.id == OAuthGrant.team_id)
                    .where(OAuthGrant.user_id == principal.user_id, OAuthGrant.revoked_at.is_(None))
                    .order_by(OAuthGrant.created_at.desc())
                )
            ).all()
            prefixes: dict[uuid.UUID, str] = {
                grant_id: prefix
                for grant_id, prefix in (
                    await session.execute(
                        select(ApiKey.oauth_grant_id, ApiKey.prefix).where(
                            ApiKey.oauth_grant_id.in_([g.id for g, _, _ in rows]),
                            ApiKey.revoked_at.is_(None),
                            ApiKey.expires_at > datetime.now(UTC),
                        )
                    )
                ).all()
                if grant_id is not None
            }
        return [GrantView(g, name, team, prefixes.get(g.id)) for g, name, team in rows]

    async def revoke_grant(self, principal: Principal, grant_id: uuid.UUID) -> None:
        async with self._db.transaction() as session:
            grant = await session.scalar(
                select(OAuthGrant).where(
                    OAuthGrant.id == grant_id, OAuthGrant.user_id == principal.user_id
                )
            )
            if grant is None:
                raise NotFoundError("Connected app not found.")
            await self._revoke_grant(session, grant_id, by=principal)

    async def _revoke_grant(
        self, session: AsyncSession, grant_id: uuid.UUID, *, by: Principal | None
    ) -> None:
        revoked = await session.scalar(
            update(OAuthGrant)
            .where(OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
            .returning(OAuthGrant.team_id)
        )
        await ApiKeyService.revoke_grant_keys(
            session, grant_id, revoked_by=by.user_id if by else None
        )
        if revoked is not None:
            self._audit.add(
                session,
                action="oauth.grant.revoke",
                actor=by,
                team_id=revoked,
                target_type="oauth_grant",
                target_id=grant_id,
                details={"via": "dashboard" if by else "revocation_endpoint"},
            )
