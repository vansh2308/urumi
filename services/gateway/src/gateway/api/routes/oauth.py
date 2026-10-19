"""Dashboard side of OAuth: the consent decision and the user's connected apps.

The protocol endpoints themselves (/authorize, /token, /register, /revoke, metadata) come from
the MCP SDK; see ``gateway.oauth``. These need a user session.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from gateway.api.deps import API_PREFIX, ContainerDep, SessionPrincipalDep
from gateway.api.schemas import (
    ConsentDecisionOut,
    ConsentDecisionRequest,
    ConsentRequestOut,
    OAuthConfigOut,
    OAuthGrantOut,
)
from gateway.container import Container
from gateway.errors import NotFoundError
from gateway.oauth.service import OAuthService

router = APIRouter(prefix=f"{API_PREFIX}/oauth", tags=["oauth"])


def _service(container: Container) -> OAuthService:
    if container.oauth is None:
        raise NotFoundError("OAuth is not enabled on this gateway.")
    return container.oauth


@router.get("/config", response_model=OAuthConfigOut)
async def oauth_config(container: ContainerDep) -> OAuthConfigOut:
    """Public: whether MCP clients can sign in with OAuth, and the MCP URL to give them."""
    if container.oauth is None:
        return OAuthConfigOut(enabled=False)
    return OAuthConfigOut(
        enabled=True, issuer=container.oauth.issuer_url, mcp_url=container.oauth.resource_url
    )


@router.get("/requests/{request_id}", response_model=ConsentRequestOut)
async def get_request(
    request_id: str, _: SessionPrincipalDep, container: ContainerDep
) -> ConsentRequestOut:
    """The pending authorization request the consent page asks about."""
    pending = await _service(container).describe_request(request_id)
    return ConsentRequestOut(
        client_id=pending.client_id,
        client_name=pending.client_name,
        client_uri=pending.client_uri,
        redirect_host=pending.redirect_host,
        scopes=pending.scopes,
        expires_at=pending.expires_at,
    )


@router.post("/requests/{request_id}/decision", response_model=ConsentDecisionOut)
async def decide(
    request_id: str,
    body: ConsentDecisionRequest,
    principal: SessionPrincipalDep,
    container: ContainerDep,
) -> ConsentDecisionOut:
    """Approve (for one of your teams) or deny. Returns where to send the browser next."""
    redirect = await _service(container).decide(
        principal, request_id, approve=body.approve, team_id=body.team_id
    )
    return ConsentDecisionOut(redirect_to=redirect)


@router.get("/grants", response_model=list[OAuthGrantOut])
async def list_grants(
    principal: SessionPrincipalDep, container: ContainerDep
) -> list[OAuthGrantOut]:
    """Apps you connected with OAuth, in every team."""
    return [
        OAuthGrantOut(
            id=v.grant.id,
            client_name=v.client_name,
            team_id=v.grant.team_id,
            team_name=v.team_name,
            scopes=list(v.grant.scopes),
            created_at=v.grant.created_at,
            last_refreshed_at=v.grant.last_refreshed_at,
            access_key_prefix=v.active_key_prefix,
        )
        for v in await _service(container).list_grants(principal)
    ]


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_grant(
    grant_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> None:
    """Disconnect an app: its refresh token and current access token stop working at once."""
    await _service(container).revoke_grant(principal, grant_id)
