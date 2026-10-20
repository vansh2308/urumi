"""Connector registry (platform admins), team tool policies and member grants (team admins),
tool discovery (members) and REST tool invocation (API keys)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status
from mcp import types as mcp_types

from gateway.api.deps import (
    API_PREFIX,
    ContainerDep,
    PrincipalDep,
    SessionPrincipalDep,
    TeamDep,
    TeamSessionDep,
)
from gateway.api.schemas import (
    CallToolRequest,
    CatalogEntryOut,
    ConnectorOut,
    ConnectorStatusOut,
    CredentialOut,
    ExchangeOut,
    GrantOut,
    ProbeOut,
    RegisterConnectorRequest,
    TeamConnectorOut,
    ToolOut,
    ToolPolicyRequest,
    ToolSetOut,
    UpdateConnectorRequest,
)
from gateway.db.models import Connector, ConnectorKind
from gateway.errors import PermissionDeniedError
from gateway.services.connectors import (
    ConnectorChange,
    ConnectorStatus,
    GrantView,
    NewConnector,
    ToolSet,
)
from gateway.services.principal import Principal
from gateway.services.tools import CallContext, Transport, qualified_name

registry = APIRouter(prefix=f"{API_PREFIX}/connectors", tags=["connector registry"])
team = APIRouter(prefix=f"{API_PREFIX}/teams/{{team_id}}", tags=["team connectors & tools"])


def connector_out(connector: Connector, principal: Principal) -> ConnectorOut:
    out = ConnectorOut(
        id=connector.id,
        slug=connector.slug,
        display_name=connector.display_name,
        description=connector.description,
        enabled=connector.enabled,
        kind=connector.kind,
        managed=connector.kind == ConnectorKind.STORE.value,
    )
    if principal.is_platform_admin:
        out.url = connector.url
        if connector.credential_header:
            out.credential = CredentialOut(header_name=connector.credential_header)
        out.timeout_seconds = connector.timeout_seconds
        out.max_concurrency = connector.max_concurrency
        out.created_at = connector.created_at
        out.updated_at = connector.updated_at
    return out


def _status_out(status: ConnectorStatus) -> ConnectorStatusOut:
    failure = status.last_failure
    return ConnectorStatusOut(
        slug=status.connector.slug,
        display_name=status.connector.display_name,
        state=status.state,
        error_code=status.error_code,
        error_message=status.error_message,
        latency_ms=status.latency_ms,
        checked_at=status.checked_at,
        last_success_at=status.last_success_at,
        last_failure=ExchangeOut(
            at=failure.at,
            error_code=failure.error_code,
            error_message=failure.error_message,
            duration_ms=failure.duration_ms,
        )
        if failure
        else None,
    )


def _toolset(tools: ToolSet) -> ToolSetOut:
    return ToolSetOut(allow_all_tools=tools.all_tools, tools=sorted(tools.names))


def _tool_out(tool: mcp_types.Tool) -> ToolOut:
    return ToolOut(name=tool.name, title=tool.title, description=tool.description)


def _grant_out(grant: GrantView) -> GrantOut:
    return GrantOut(
        connector=grant.connector_slug,
        allow_all_tools=grant.tools.all_tools,
        tools=sorted(grant.tools.names),
        updated_at=grant.updated_at,
    )


# --- platform registry --------------------------------------------------------------------------


@registry.post("", status_code=status.HTTP_201_CREATED, response_model=ConnectorOut)
async def register_connector(
    body: RegisterConnectorRequest, principal: SessionPrincipalDep, container: ContainerDep
) -> ConnectorOut:
    """Register an upstream MCP server (platform admin). The URL must be allow-listed; the
    optional credential is stored encrypted and sent only to that URL."""
    connector = await container.connectors.register(
        principal,
        NewConnector(
            slug=body.slug,
            display_name=body.display_name,
            description=body.description,
            url=body.url,
            timeout_seconds=body.timeout_seconds,
            max_concurrency=body.max_concurrency,
            credential=body.credential.to_domain() if body.credential else None,
        ),
    )
    return connector_out(connector, principal)


@registry.get("", response_model=list[ConnectorOut], response_model_exclude_none=True)
async def list_connectors(
    principal: SessionPrincipalDep, container: ContainerDep
) -> list[ConnectorOut]:
    """Registered connectors. Team admins use this to pick what to enable for their team."""
    return [
        connector_out(c, principal) for c in await container.connectors.list_registry(principal)
    ]


@registry.get("/status", response_model=list[ConnectorStatusOut])
async def registry_status(
    principal: SessionPrincipalDep, container: ContainerDep
) -> list[ConnectorStatusOut]:
    """Ping every registered connector now (platform admin): connected, unreachable (with the
    reason) or disabled. Servers are pinged in parallel; each gets at most 10 s."""
    if not principal.is_platform_admin:
        raise PermissionDeniedError("Registry-wide connector status is for platform admins.")
    return [_status_out(s) for s in await container.connectors.registry_status(principal)]


@registry.get("/{connector_id}", response_model=ConnectorOut)
async def get_connector(
    connector_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> ConnectorOut:
    """Full registry entry (platform admin)."""
    return connector_out(await container.connectors.get(principal, connector_id), principal)


@registry.patch("/{connector_id}", response_model=ConnectorOut)
async def update_connector(
    connector_id: uuid.UUID,
    body: UpdateConnectorRequest,
    principal: SessionPrincipalDep,
    container: ContainerDep,
) -> ConnectorOut:
    """Change or disable a connector (platform admin). Disabling blocks every team at once."""
    connector = await container.connectors.update(
        principal,
        connector_id,
        ConnectorChange(
            display_name=body.display_name,
            description=body.description,
            url=body.url,
            enabled=body.enabled,
            timeout_seconds=body.timeout_seconds,
            max_concurrency=body.max_concurrency,
            credential=body.credential.to_domain() if body.credential else None,
            clear_credential=body.clear_credential,
        ),
    )
    return connector_out(connector, principal)


@registry.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_connector(
    connector_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> None:
    """Remove a connector with every team's policy and grants for it (platform admin)."""
    await container.connectors.remove(principal, connector_id)


@registry.post("/{connector_id}/probe", response_model=ProbeOut)
async def probe_connector(
    connector_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> ProbeOut:
    """Connect to the upstream and list its tools (platform admin)."""
    probe = await container.connectors.probe(principal, connector_id)
    return ProbeOut(
        server_name=probe.name,
        server_version=probe.version,
        protocol_version=probe.protocol_version,
        tools=[_tool_out(t) for t in probe.tools],
    )


# --- team policy and grants ---------------------------------------------------------------------


@team.get("/connectors", response_model=list[TeamConnectorOut])
async def list_team_connectors(ctx: TeamDep, container: ContainerDep) -> list[TeamConnectorOut]:
    """Connectors available to the caller in this team, with the tools they may use."""
    views = await container.connectors.list_team_connectors(ctx)
    return [
        TeamConnectorOut(
            slug=v.connector.slug,
            display_name=v.connector.display_name,
            description=v.connector.description,
            kind=v.connector.kind,
            team_policy=_toolset(v.policy) if ctx.is_admin else None,
            your_access=_toolset(v.effective),
        )
        for v in views
    ]


@team.get("/connectors/status", response_model=list[ConnectorStatusOut])
async def team_connector_status(ctx: TeamDep, container: ContainerDep) -> list[ConnectorStatusOut]:
    """Ping the team's connectors you can see: connected or unreachable, with the reason."""
    return [_status_out(s) for s in await container.connectors.team_status(ctx)]


@team.put("/connectors/{slug}", response_model=TeamConnectorOut)
async def set_team_connector(
    slug: str, body: ToolPolicyRequest, ctx: TeamSessionDep, container: ContainerDep
) -> TeamConnectorOut:
    """Enable a connector for the team and choose its permitted tools (admin)."""
    view = await container.connectors.set_team_policy(
        ctx, slug, allow_all_tools=body.allow_all_tools, tools=body.tools
    )
    return TeamConnectorOut(
        slug=view.connector.slug,
        display_name=view.connector.display_name,
        description=view.connector.description,
        team_policy=_toolset(view.policy),
        your_access=_toolset(view.effective),
    )


@team.delete("/connectors/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_connector(slug: str, ctx: TeamSessionDep, container: ContainerDep) -> None:
    """Disable a connector for the team and drop its member grants (admin)."""
    await container.connectors.remove_team_policy(ctx, slug)


@team.get("/connectors/{slug}/catalog", response_model=list[CatalogEntryOut])
async def connector_catalog(
    slug: str, ctx: TeamSessionDep, container: ContainerDep
) -> list[CatalogEntryOut]:
    """Every tool the upstream offers, marked with whether the team allows it (admin)."""
    return [
        CatalogEntryOut(
            name=e.name,
            title=e.title,
            description=e.description,
            allowed_for_team=e.allowed_for_team,
        )
        for e in await container.connectors.catalog(ctx, slug)
    ]


@team.get("/members/{user_id}/grants", response_model=list[GrantOut])
async def list_member_grants(
    user_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> list[GrantOut]:
    """A member's tool grants (admin)."""
    return [_grant_out(g) for g in await container.connectors.list_grants(ctx, user_id)]


@team.put("/members/{user_id}/grants/{slug}", response_model=GrantOut)
async def set_member_grant(
    user_id: uuid.UUID,
    slug: str,
    body: ToolPolicyRequest,
    ctx: TeamSessionDep,
    container: ContainerDep,
) -> GrantOut:
    """Give a member access to some (or all team-enabled) tools of a connector (admin)."""
    grant = await container.connectors.set_grant(
        ctx, user_id, slug, allow_all_tools=body.allow_all_tools, tools=body.tools
    )
    return _grant_out(grant)


@team.delete("/members/{user_id}/grants/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_member_grant(
    user_id: uuid.UUID, slug: str, ctx: TeamSessionDep, container: ContainerDep
) -> None:
    """Revoke a member's access to a connector (admin)."""
    await container.connectors.revoke_grant(ctx, user_id, slug)


# --- discovery and invocation -------------------------------------------------------------------


@team.get("/connectors/{slug}/tools", response_model=list[dict[str, Any]])
async def list_tools(slug: str, ctx: TeamDep, container: ContainerDep) -> list[dict[str, Any]]:
    """Tools the caller may invoke, as MCP tool definitions (session, or key with tools:read)."""
    tools = await container.tools.list_tools(ctx, slug)
    return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]


@team.get("/tools", response_model=dict[str, Any])
async def list_all_tools(ctx: TeamDep, container: ContainerDep) -> dict[str, Any]:
    """Every tool the caller may use on every enabled server, the same listing as the ``/v1/mcp``
    endpoint. Servers that did not answer are reported in ``unavailable``."""
    listing = await container.tools.list_all_tools(ctx)
    return {
        "tools": [
            {
                "connector": server.slug,
                "qualified_name": qualified_name(server.slug, tool.name),
            }
            | tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for server in listing.servers
            for tool in server.tools
        ],
        "unavailable": [{"connector": slug, "code": code} for slug, code in listing.unavailable],
    }


@team.post("/connectors/{slug}/tools/{tool_name}/call", response_model=dict[str, Any])
async def call_tool(
    team_id: uuid.UUID,
    slug: str,
    tool_name: str,
    body: CallToolRequest,
    principal: PrincipalDep,
    container: ContainerDep,
) -> dict[str, Any]:
    """Invoke a tool (API key with tools:call). Returns the MCP ``CallToolResult`` unchanged;
    ``isError: true`` means the tool itself reported an error."""
    result = await container.tools.call_tool(
        CallContext(
            principal=principal,
            team_id=team_id,
            connector_slug=slug,
            tool=tool_name,
            transport=Transport.REST,
        ),
        body.arguments,
    )
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)
