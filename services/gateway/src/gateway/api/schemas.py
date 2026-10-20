"""Request and response bodies of the REST API.

Request models forbid unknown fields, so a payload cannot smuggle in attributes such as
``role`` or ``is_platform_admin`` that the endpoint does not accept.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, SecretStr

from gateway.db.models import ApiKeyScope, TeamRole
from gateway.errors import InvalidInputError
from gateway.upstream.credentials import UpstreamCredential


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


DisplayName = Annotated[str, Field(min_length=1, max_length=100)]
Password = Annotated[str, Field(min_length=1, max_length=256)]


# --- auth ---------------------------------------------------------------------------------------


class RegisterRequest(RequestModel):
    email: EmailStr
    password: Password
    display_name: DisplayName
    invitation_token: str | None = Field(
        default=None,
        max_length=128,
        description="Optional uri_ token. Joins the team on registration and is required when "
        "self-signup is disabled.",
    )


class LoginRequest(RequestModel):
    email: EmailStr
    password: Password


class UserOut(ResponseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_platform_admin: bool
    created_at: datetime


class SessionOut(BaseModel):
    access_token: str = Field(description="Bearer token. Shown once; store it securely.")
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_at: datetime
    user: UserOut


class MembershipOut(BaseModel):
    team_id: uuid.UUID
    team_name: str
    team_slug: str
    role: TeamRole


class MeOut(BaseModel):
    user: UserOut
    credential: str = Field(description="session or api_key")
    api_key_id: uuid.UUID | None = None
    teams: list[MembershipOut]


# --- teams --------------------------------------------------------------------------------------

TeamName = Annotated[str, Field(min_length=1, max_length=100)]


class CreateTeamRequest(RequestModel):
    name: TeamName


class UpdateTeamRequest(RequestModel):
    name: TeamName | None = None
    members_can_create_keys: bool | None = Field(
        default=None, description="Whether members may create API keys for themselves."
    )
    max_key_ttl_days: int | None = Field(default=None, ge=1)
    rate_limit_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Requests per minute for each API key or session in this team. Set "
        "reset_rate_limit=true instead to fall back to the platform default.",
    )
    reset_rate_limit: bool = False


class TeamOut(ResponseModel):
    id: uuid.UUID
    name: str
    slug: str
    members_can_create_keys: bool
    max_key_ttl_days: int
    rate_limit_per_minute: int | None
    created_at: datetime


class TeamWithRoleOut(TeamOut):
    role: TeamRole = Field(description="The caller's role in this team.")


class MemberOut(ResponseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: TeamRole
    joined_at: datetime


class ChangeRoleRequest(RequestModel):
    role: TeamRole


# --- invitations --------------------------------------------------------------------------------


class CreateInvitationRequest(RequestModel):
    email: EmailStr
    role: TeamRole = TeamRole.MEMBER


class InvitationOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    email: str
    role: TeamRole
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class IssuedInvitationOut(InvitationOut):
    token: str = Field(description="uri_ token. Shown once; deliver it to the invitee.")


class AcceptInvitationRequest(RequestModel):
    token: str = Field(min_length=20, max_length=128)


# --- API keys -----------------------------------------------------------------------------------


class CreateApiKeyRequest(RequestModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    scopes: Annotated[list[ApiKeyScope], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: [ApiKeyScope.TOOLS_READ, ApiKeyScope.TOOLS_CALL]
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, description="Defaults to the platform default, capped by team policy."
    )
    tool_restrictions: Annotated[list[str], Field(min_length=1, max_length=100)] | None = Field(
        default=None,
        description="Narrow the key to these 'connector-slug/tool' or 'connector-slug/*' "
        "entries. It never grants more than the owner's own access.",
    )


class RotateApiKeyRequest(RequestModel):
    grace_hours: int = Field(
        default=0, ge=0, description="Keep the old key working this long (0 = revoke now)."
    )


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    prefix: str = Field(description="First characters of the key, for identification.")
    scopes: list[ApiKeyScope]
    tool_restrictions: list[str] | None
    status: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    rotated_from_id: uuid.UUID | None
    session_bound: bool = Field(
        default=False, description="A dashboard key that ends with the login session."
    )
    oauth_issued: bool = Field(
        default=False, description="An OAuth access token for a connected app (short-lived)."
    )


class IssuedApiKeyOut(ApiKeyOut):
    key: str = Field(description="The urk_ secret. Shown once; it cannot be retrieved later.")


# --- oauth --------------------------------------------------------------------------------------


class OAuthConfigOut(BaseModel):
    enabled: bool
    issuer: str | None = None
    mcp_url: str | None = Field(
        default=None, description="The MCP endpoint OAuth clients connect to."
    )


class ConsentRequestOut(BaseModel):
    client_id: str
    client_name: str | None = Field(description="Self-declared by the client; not verified.")
    client_uri: str | None
    redirect_host: str = Field(description="Where the browser goes back to after approval.")
    scopes: list[str]
    expires_at: datetime


class ConsentDecisionRequest(RequestModel):
    approve: bool
    team_id: uuid.UUID | None = Field(
        default=None, description="Required to approve: the team the app acts in."
    )


class ConsentDecisionOut(BaseModel):
    redirect_to: str


class OAuthGrantOut(BaseModel):
    id: uuid.UUID
    client_name: str | None
    team_id: uuid.UUID
    team_name: str
    scopes: list[str]
    created_at: datetime
    last_refreshed_at: datetime | None
    access_key_prefix: str | None = Field(
        default=None, description="Prefix of the current access token (an API key)."
    )


# --- connectors ---------------------------------------------------------------------------------

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]


class CredentialIn(RequestModel):
    """A static credential the gateway sends to the upstream on every call. Write-only."""

    type: Literal["bearer", "header"] = Field(
        description="'bearer' sends 'Authorization: Bearer <value>'; 'header' sends "
        "'<header_name>: <value>'."
    )
    header_name: Annotated[str, Field(max_length=64)] | None = Field(
        default=None, description="Required for type 'header', e.g. X-Api-Key."
    )
    value: SecretStr = Field(description="The token or header value. Never returned.")

    def to_domain(self) -> UpstreamCredential:
        raw = self.value.get_secret_value()
        if self.type == "bearer":
            if self.header_name is not None:
                raise InvalidInputError("header_name is only used with type 'header'.")
            return UpstreamCredential.bearer(raw)
        if not self.header_name:
            raise InvalidInputError("header_name is required for type 'header'.")
        return UpstreamCredential.header(self.header_name, raw)


class CredentialOut(BaseModel):
    header_name: str
    configured: bool = True


class RegisterConnectorRequest(RequestModel):
    slug: Slug = Field(description="Stable identifier clients use, e.g. 'woocommerce'.")
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=500)] = ""
    url: Annotated[str, Field(max_length=500)] = Field(
        description="Streamable HTTP endpoint, e.g. http://127.0.0.1:8000/mcp. Must match "
        "UPSTREAM_ALLOWED_HOSTS."
    )
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)
    credential: CredentialIn | None = None


class UpdateConnectorRequest(RequestModel):
    display_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    url: Annotated[str, Field(max_length=500)] | None = None
    enabled: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)
    credential: CredentialIn | None = Field(
        default=None, description="Replace the connector's credential."
    )
    clear_credential: bool = Field(default=False, description="Remove the credential.")


class ConnectorOut(BaseModel):
    """Registry entry. ``url`` and limits are only shown to platform admins."""

    id: uuid.UUID
    slug: str
    display_name: str
    description: str
    enabled: bool
    kind: str | None = Field(
        default=None,
        description="'remote' (by URL), 'hosted' (deployed from an image) or 'store' (the MCP "
        "server of a provisioned store: managed, only enable/disable applies).",
    )
    managed: bool = Field(
        default=False, description="Managed by a provisioned store: no edit or delete here."
    )
    url: str | None = None
    credential: CredentialOut | None = Field(
        default=None, description="The credential's header name only; the value is never shown."
    )
    timeout_seconds: float | None = None
    max_concurrency: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeployHostedServerRequest(RequestModel):
    slug: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,58}$")] = Field(
        description="Connector slug; the cluster objects are named mcp-<slug>."
    )
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=500)] = ""
    image: Annotated[str, Field(min_length=1, max_length=300)] = Field(
        description="Container image speaking MCP Streamable HTTP. Must match "
        "HOSTED_ALLOWED_IMAGES, e.g. mcp/duckduckgo:latest."
    )
    port: int = Field(ge=1, le=65535, description="Port the server listens on.")
    path: Annotated[str, Field(max_length=200)] = Field(
        default="/mcp", description="MCP endpoint path."
    )
    args: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=30)] = Field(
        default_factory=list, description="Container arguments (replace the image's CMD)."
    )
    env: dict[str, Annotated[str, Field(max_length=4096)]] = Field(
        default_factory=dict, description="Plain environment variables (not secret)."
    )
    secret_env: dict[str, SecretStr] = Field(
        default_factory=dict,
        description="Secret environment variables. Stored only in the server's Kubernetes "
        "Secret; never returned.",
    )
    credential: CredentialIn | None = Field(
        default=None, description="Header the gateway sends to the server on every call."
    )
    cpu_millicores: int | None = Field(default=None, ge=10)
    memory_mib: int | None = Field(default=None, ge=32)
    egress: Literal["internet", "none"] = Field(
        default="internet",
        description="'internet': public addresses on 80/443 only (never cluster or private "
        "ranges). 'none': DNS only.",
    )
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)


class UpdateHostedServerRequest(RequestModel):
    image: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    args: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=30)] | None = None
    env: dict[str, Annotated[str, Field(max_length=4096)]] | None = Field(
        default=None, description="Replaces every plain variable."
    )
    secret_env: dict[str, SecretStr | None] | None = Field(
        default=None,
        description="Secret variables to set; null removes one. Others are kept.",
    )
    cpu_millicores: int | None = Field(default=None, ge=10)
    memory_mib: int | None = Field(default=None, ge=32)
    egress: Literal["internet", "none"] | None = None


class HostedStatusOut(BaseModel):
    phase: str = Field(description="deploying | ready | failed | stopped | missing | unknown")
    reason: str | None = None
    ready_replicas: int
    restarts: int


class HostedServerOut(BaseModel):
    connector: ConnectorOut
    image: str
    port: int
    path: str
    args: list[str]
    env: dict[str, str]
    secret_env_keys: list[str] = Field(description="Names only; values are never returned.")
    cpu_millicores: int
    memory_mib: int
    egress: str
    namespace: str
    k8s_name: str
    status: HostedStatusOut
    created_at: datetime
    updated_at: datetime


class HostedSettingsOut(BaseModel):
    enabled: bool
    namespace: str
    allowed_images: list[str]
    default_cpu_millicores: int
    max_cpu_millicores: int
    default_memory_mib: int
    max_memory_mib: int
    max_servers: int


class ExchangeOut(BaseModel):
    at: datetime
    error_code: str | None
    error_message: str | None
    duration_ms: float


class ConnectorStatusOut(BaseModel):
    slug: str
    display_name: str
    state: str = Field(description="connected | unreachable | disabled (a ping, just now)")
    error_code: str | None = Field(
        default=None, description="Why it is unreachable, e.g. upstream_unavailable."
    )
    error_message: str | None = None
    latency_ms: float | None = Field(default=None, description="MCP initialize + ping.")
    checked_at: datetime
    last_success_at: datetime | None = Field(
        default=None, description="Last successful exchange seen by this gateway replica."
    )
    last_failure: ExchangeOut | None = Field(
        default=None, description="Last failed exchange seen by this gateway replica."
    )


class ToolOut(BaseModel):
    name: str
    title: str | None = None
    description: str | None = None


class ProbeOut(BaseModel):
    server_name: str
    server_version: str
    protocol_version: str
    tools: list[ToolOut]


class ToolPolicyRequest(RequestModel):
    allow_all_tools: bool = False
    tools: Annotated[list[ToolName], Field(max_length=500)] = Field(default_factory=list)


class ToolSetOut(BaseModel):
    allow_all_tools: bool
    tools: list[str]


class TeamConnectorOut(BaseModel):
    slug: str
    display_name: str
    description: str
    kind: str = Field(default="remote", description="'remote', 'hosted' or 'store'.")
    team_policy: ToolSetOut | None = Field(
        default=None, description="Tools enabled for the team (admins only)."
    )
    your_access: ToolSetOut = Field(description="Tools the caller may use.")


class CatalogEntryOut(ToolOut):
    allowed_for_team: bool


class GrantOut(BaseModel):
    connector: str
    allow_all_tools: bool
    tools: list[str]
    updated_at: datetime


# --- tool invocation ----------------------------------------------------------------------------


class CallToolRequest(RequestModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


# --- audit and usage ----------------------------------------------------------------------------


class AuditEventOut(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    team_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_api_key_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    ip: Annotated[str | None, BeforeValidator(lambda v: None if v is None else str(v))]
    details: dict[str, Any]


class InvocationOut(ResponseModel):
    id: uuid.UUID
    created_at: datetime
    user_id: uuid.UUID
    api_key_id: uuid.UUID | None
    connector_slug: str
    tool_name: str
    transport: str
    outcome: str
    error_code: str | None
    duration_ms: int
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next (older) page.")


class InvocationPage(BaseModel):
    items: list[InvocationOut]
    next_cursor: str | None


class SummaryRowOut(BaseModel):
    key: str
    calls: int
    errors: int = Field(description="Calls whose outcome was not success.")
    denied: int
    avg_duration_ms: float
    p95_duration_ms: float


class UsageSummaryOut(BaseModel):
    group_by: str
    since: datetime | None
    until: datetime | None
    rows: list[SummaryRowOut]


# --- stores ---------------------------------------------------------------------------------------


class CreateStoreRequest(RequestModel):
    name: Annotated[str, Field(min_length=1, max_length=60)] = Field(
        description="Display name, e.g. 'Acme'. The address (slug) is derived from it."
    )


class StoreOut(BaseModel):
    """A provisioned store. ``status``: requested | provisioning | ready | failed | deleting;
    ``step`` is the last provisioning step that finished."""

    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    slug: str
    status: str
    phase: str = Field(description="Provisioning | Ready | Failed | Deleting, for display.")
    step: str | None
    status_reason: str | None
    url: str
    admin_url: str
    namespace: str
    connector_id: uuid.UUID | None
    connector_slug: str | None = Field(
        default=None, description="Set once ready: the tools are named <slug>__<tool>."
    )
    attempts: int
    created_by_user_id: uuid.UUID | None
    created_by_name: str | None
    created_by_email: str | None
    created_at: datetime
    ready_at: datetime | None
    deadline_at: datetime


class StoreCreatedOut(StoreOut):
    admin_username: str = "admin"
    admin_password: str | None = Field(
        default=None,
        description="The wp-admin password, returned only by the request that created the "
        "store. It is not stored by the gateway and cannot be shown again.",
    )
    created: bool = Field(description="False when an existing store was returned (retry).")


class StoreEventOut(BaseModel):
    at: datetime
    action: str
    outcome: str
    details: dict[str, Any]


class StoreDetailOut(StoreOut):
    events: list[StoreEventOut]


class StoreSettingsOut(BaseModel):
    enabled: bool
    max_per_user: int
    max_total: int
    provision_timeout_seconds: int
    base_domain: str
