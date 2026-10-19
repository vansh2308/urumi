"""ORM schema. Alembic migrations in ``alembic/versions`` are the source of truth for DDL."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Bumped by every migration; /health/ready fails until the database is at this revision.
SCHEMA_REVISION = "0008"
# Own version table so the gateway can share a PostgreSQL database with other services.
VERSION_TABLE = "gateway_alembic_version"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Operator flag for the connector registry only; it grants no access inside any team.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """A dashboard/management login. Only the HMAC of the bearer token is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(256))


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class AuditEvent(Base):
    """Platform-wide audit trail. Rows reference ids without foreign keys so history survives
    deletion of the user, team or key it mentions. ``details`` never holds secrets."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="outcome_valid"),
        Index("ix_audit_events_team_id_created_at", "team_id", "created_at"),
        Index("ix_audit_events_actor_user_id_created_at", "actor_user_id", "created_at"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created_at()
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class RateLimitCounter(Base):
    """Fixed-window request counters shared by every gateway replica."""

    __tablename__ = "rate_limit_counters"
    __table_args__ = (Index("ix_rate_limit_counters_window_start", "window_start"),)

    bucket: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class TeamRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    # Team-wide settings managed by team admins.
    members_can_create_keys: Mapped[bool] = mapped_column(Boolean, default=True)
    max_key_ttl_days: Mapped[int] = mapped_column(Integer, default=90)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_id_user_id"),
        CheckConstraint("role IN ('admin', 'member')", name="role_valid"),
        Index("ix_team_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class Invitation(Base):
    """An invitation to join a team. Only the HMAC of the invitation token is stored."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role_valid"),
        # At most one open invitation per email and team.
        Index(
            "uq_invitations_team_id_email_open",
            "team_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyScope(StrEnum):
    TOOLS_READ = "tools:read"  # discover servers and tools
    TOOLS_CALL = "tools:call"  # invoke tools (REST or the MCP endpoint)
    USAGE_READ = "usage:read"  # read the owner's own usage records


class ApiKey(Base):
    """A platform API key (``urk_...``) owned by one user within one team.

    These are gateway credentials for platform clients. They are unrelated to downstream
    integration credentials such as WooCommerce consumer keys, which stay in the upstream MCP
    server and are never stored, returned or forwarded by the gateway.
    """

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_team_id_owner_user_id", "team_id", "owner_user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    # "connector/tool" or "connector/*" entries narrowing the owner's access; NULL = no narrowing.
    tool_restrictions: Mapped[list[str] | None] = mapped_column(ARRAY(String(200)))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    # Set for a dashboard session key: it expires with that login session and is revoked on
    # logout or when the dashboard asks for a new one.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL")
    )
    # Set for an OAuth access token: the grant (client + user + team) it was issued under. It
    # is replaced on every refresh and revoked with the grant.
    oauth_grant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="SET NULL")
    )


class OAuthClient(Base):
    """A dynamically registered OAuth client (RFC 7591), e.g. Claude Code or Cursor."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_name: Mapped[str | None] = mapped_column(String(200))
    # The registered metadata (redirect URIs, auth method, ...), without the secret.
    info: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Confidential clients only; Fernet-encrypted because the token endpoint compares it.
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class OAuthAuthorizationRequest(Base):
    """An /authorize request waiting for the user's decision on the consent page."""

    __tablename__ = "oauth_authorization_requests"

    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE")
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthGrant(Base):
    """A user's consent for one client in one team; holds the (hashed) refresh token."""

    __tablename__ = "oauth_grants"
    __table_args__ = (Index("ix_oauth_grants_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    resource: Mapped[str | None] = mapped_column(String(500))
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthCode(Base):
    """A single-use authorization code (stored hashed), bound to the PKCE challenge."""

    __tablename__ = "oauth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(2000))
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(Boolean)
    resource: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorKind(StrEnum):
    REMOTE = "remote"
    HOSTED = "hosted"


class Connector(Base):
    """A registered upstream MCP server. Managed by platform admins only.

    Clients address connectors by ``slug``; the URL is set at registration (and checked against
    ``UPSTREAM_ALLOWED_HOSTS``) and is never taken from a client request.
    """

    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(String(500))
    # 'remote': registered by URL. 'hosted': deployed by the gateway from a container image;
    # its URL is the in-cluster Service and is managed by the platform (see HostedServer).
    kind: Mapped[str] = mapped_column(String(16), default=ConnectorKind.REMOTE.value)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional static credential sent as one header on every upstream call. The value is
    # encrypted (see gateway.upstream.credentials) and never leaves the gateway.
    credential_header: Mapped[str | None] = mapped_column(String(64))
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    max_concurrency: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class HostedServer(Base):
    """Deployment spec of a hosted connector. Secret values are not here: they live only in the
    server's Kubernetes Secret; this row records their names."""

    __tablename__ = "hosted_servers"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True
    )
    k8s_name: Mapped[str] = mapped_column(String(63), unique=True)
    namespace: Mapped[str] = mapped_column(String(63))
    image: Mapped[str] = mapped_column(String(300))
    port: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(200))
    args: Mapped[list[str]] = mapped_column(ARRAY(String(500)), default=list)
    env: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    secret_keys: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)
    cpu_millicores: Mapped[int] = mapped_column(Integer)
    memory_mib: Mapped[int] = mapped_column(Integer)
    egress: Mapped[str] = mapped_column(String(16))
    config_revision: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class TeamConnector(Base):
    """A connector enabled for a team, with the tools the team may use (admins get these)."""

    __tablename__ = "team_connectors"
    __table_args__ = (
        UniqueConstraint("team_id", "connector_id", name="uq_team_connectors_team_id_connector_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    allow_all_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_tools: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class MemberToolGrant(Base):
    """Tools of one team connector that one (non-admin) member may use."""

    __tablename__ = "member_tool_grants"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "connector_id", name="uq_member_tool_grants_membership_id_connector_id"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team_memberships.id", ondelete="CASCADE")
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    allow_all_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    tools: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class ToolInvocation(Base):
    """One authorized (or denied) tool call: the usage record. Arguments are never stored."""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        Index("ix_tool_invocations_team_id_created_at", "team_id", "created_at"),
        Index("ix_tool_invocations_team_id_user_id_created_at", "team_id", "user_id", "created_at"),
        Index("ix_tool_invocations_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created_at()
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    connector_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    connector_slug: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(128))
    transport: Mapped[str] = mapped_column(String(8))  # rest | mcp
    outcome: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(128))
