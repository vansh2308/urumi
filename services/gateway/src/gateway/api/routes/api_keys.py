"""Team API key management. Requires a user session: an API key cannot manage keys."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from gateway.api.deps import API_PREFIX, ContainerDep, TeamSessionDep
from gateway.api.schemas import (
    ApiKeyOut,
    CreateApiKeyRequest,
    IssuedApiKeyOut,
    RotateApiKeyRequest,
)
from gateway.db.models import ApiKey, ApiKeyScope
from gateway.services.api_keys import IssuedApiKey, NewApiKey, key_status

router = APIRouter(prefix=f"{API_PREFIX}/teams/{{team_id}}/api-keys", tags=["api keys"])


def _out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        team_id=key.team_id,
        owner_user_id=key.owner_user_id,
        name=key.name,
        prefix=key.prefix,
        scopes=[ApiKeyScope(s) for s in key.scopes],
        tool_restrictions=key.tool_restrictions,
        status=key_status(key).value,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        rotated_from_id=key.rotated_from_id,
    )


def _issued(issued: IssuedApiKey) -> IssuedApiKeyOut:
    return IssuedApiKeyOut(**_out(issued.key).model_dump(), key=issued.raw)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IssuedApiKeyOut)
async def create_api_key(
    body: CreateApiKeyRequest, ctx: TeamSessionDep, container: ContainerDep
) -> IssuedApiKeyOut:
    """Issue a key owned by the caller in this team. The secret is returned only here."""
    issued = await container.api_keys.create(
        ctx,
        NewApiKey(
            name=body.name,
            scopes=frozenset(body.scopes),
            expires_in_days=body.expires_in_days,
            tool_restrictions=tuple(body.tool_restrictions)
            if body.tool_restrictions is not None
            else None,
        ),
    )
    return _issued(issued)


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    ctx: TeamSessionDep,
    container: ContainerDep,
    owner_user_id: uuid.UUID | None = Query(None, description="Admins: filter by owner."),
    include_inactive: bool = Query(False, description="Include revoked and expired keys."),
) -> list[ApiKeyOut]:
    """Admins see every key in the team; members see their own."""
    keys = await container.api_keys.list(
        ctx, owner_user_id=owner_user_id, include_inactive=include_inactive
    )
    return [_out(k) for k in keys]


@router.get("/{key_id}", response_model=ApiKeyOut)
async def get_api_key(key_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep) -> ApiKeyOut:
    """Key metadata (never the secret)."""
    return _out(await container.api_keys.get(ctx, key_id))


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> ApiKeyOut:
    """Revoke immediately. Admins can revoke any team key; members their own."""
    return _out(await container.api_keys.revoke(ctx, key_id))


@router.post(
    "/{key_id}/rotate", status_code=status.HTTP_201_CREATED, response_model=IssuedApiKeyOut
)
async def rotate_api_key(
    key_id: uuid.UUID,
    ctx: TeamSessionDep,
    container: ContainerDep,
    body: RotateApiKeyRequest | None = None,
) -> IssuedApiKeyOut:
    """Replace a key with a new secret (owner only), optionally keeping the old one briefly."""
    grace = body.grace_hours if body else 0
    return _issued(await container.api_keys.rotate(ctx, key_id, grace_hours=grace))
