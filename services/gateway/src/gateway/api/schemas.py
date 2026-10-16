"""Request and response bodies of the REST API.

Request models forbid unknown fields, so a payload cannot smuggle in attributes such as
``role`` or ``is_platform_admin`` that the endpoint does not accept.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from gateway.db.models import ApiKeyScope, TeamRole


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


class IssuedApiKeyOut(ApiKeyOut):
    key: str = Field(description="The urk_ secret. Shown once; it cannot be retrieved later.")


# --- connectors ---------------------------------------------------------------------------------

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]


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


class UpdateConnectorRequest(RequestModel):
    display_name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    url: Annotated[str, Field(max_length=500)] | None = None
    enabled: bool | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)


class ConnectorOut(BaseModel):
    """Registry entry. ``url`` and limits are only shown to platform admins."""

    id: uuid.UUID
    slug: str
    display_name: str
    description: str
    enabled: bool
    url: str | None = None
    timeout_seconds: float | None = None
    max_concurrency: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
