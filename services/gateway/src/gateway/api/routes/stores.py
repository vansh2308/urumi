"""Provisioned WooCommerce stores (team members): request, list, delete and retry.

The gateway only records the request; the provisioner creates the namespace, Helm release and
the store's MCP connector in the background. Poll the list (or one store) for its status.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from gateway.api.deps import API_PREFIX, ContainerDep, SessionPrincipalDep, TeamSessionDep
from gateway.api.schemas import (
    CreateStoreRequest,
    StoreCreatedOut,
    StoreDetailOut,
    StoreEventOut,
    StoreOut,
    StoreSettingsOut,
)
from gateway.db.models import StoreStatus
from gateway.errors import InvalidInputError
from gateway.stores.service import StoreView

router = APIRouter(prefix=f"{API_PREFIX}/teams/{{team_id}}/stores", tags=["stores"])
settings_router = APIRouter(prefix=f"{API_PREFIX}/stores", tags=["stores"])

_PHASES = {
    StoreStatus.REQUESTED.value: "Provisioning",
    StoreStatus.PROVISIONING.value: "Provisioning",
    StoreStatus.READY.value: "Ready",
    StoreStatus.FAILED.value: "Failed",
    StoreStatus.DELETING.value: "Deleting",
    StoreStatus.DELETED.value: "Deleted",
}


def store_fields(view: StoreView, scheme: str) -> dict[str, object]:
    store = view.store
    url = f"{scheme}://{store.host}"
    return {
        "id": store.id,
        "team_id": store.team_id,
        "name": store.name,
        "slug": store.slug,
        "status": store.status,
        "phase": _PHASES.get(store.status, store.status),
        "step": store.step,
        "status_reason": store.status_reason,
        "url": url,
        "admin_url": f"{url}/wp-admin/",
        "namespace": store.namespace,
        "connector_id": store.connector_id,
        "connector_slug": store.slug if store.connector_id else None,
        "attempts": store.attempts,
        "created_by_user_id": store.created_by_user_id,
        "created_by_name": view.created_by_name,
        "created_by_email": view.created_by_email,
        "created_at": store.created_at,
        "ready_at": store.ready_at,
        "deadline_at": store.deadline_at,
    }


def _scheme(container: ContainerDep) -> str:
    return "https" if container.settings.stores.tls else "http"


@settings_router.get("/settings", response_model=StoreSettingsOut)
async def store_settings(_: SessionPrincipalDep, container: ContainerDep) -> StoreSettingsOut:
    """Whether stores are offered, and the limits."""
    settings = container.settings.stores
    return StoreSettingsOut(
        enabled=settings.enabled,
        max_per_user=settings.max_per_user,
        max_total=settings.max_total,
        provision_timeout_seconds=settings.provision_timeout_seconds,
        base_domain=settings.base_domain,
    )


@router.get("", response_model=list[StoreOut])
async def list_stores(ctx: TeamSessionDep, container: ContainerDep) -> list[StoreOut]:
    """The team's stores (deleted ones excluded), newest first."""
    scheme = _scheme(container)
    return [
        StoreOut.model_validate(store_fields(v, scheme))
        for v in await container.stores.list_team(ctx)
    ]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=StoreCreatedOut)
async def create_store(
    body: CreateStoreRequest,
    ctx: TeamSessionDep,
    container: ContainerDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StoreCreatedOut:
    """Request a store (any team member, within the per-user and platform caps). Answers 202
    at once; provisioning takes a few minutes. Repeating the request with the same
    ``Idempotency-Key``, or for a name the team already has, returns that store (200)."""
    if idempotency_key is not None and not 8 <= len(idempotency_key) <= 100:
        raise InvalidInputError("Idempotency-Key must be 8 to 100 characters.")
    result = await container.stores.create(ctx, body.name, idempotency_key=idempotency_key)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return StoreCreatedOut.model_validate(
        store_fields(result.view, _scheme(container))
        | {"admin_password": result.admin_password, "created": result.created}
    )


@router.get("/{store_id}", response_model=StoreDetailOut)
async def get_store(
    store_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> StoreDetailOut:
    """One store with its recent events (create, steps, failures, delete)."""
    view, events = await container.stores.get(ctx, store_id)
    return StoreDetailOut.model_validate(
        store_fields(view, _scheme(container))
        | {
            "events": [
                StoreEventOut(at=e.at, action=e.action, outcome=e.outcome, details=e.details)
                for e in events
            ]
        }
    )


@router.delete("/{store_id}", status_code=status.HTTP_202_ACCEPTED, response_model=StoreOut)
async def delete_store(
    store_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> StoreOut:
    """Tear the store down (its creator or a team admin): its tools disappear at once, then
    the namespace, volumes and its MCP database schema are removed. Deleting twice is fine."""
    view = await container.stores.delete(ctx, store_id)
    return StoreOut.model_validate(store_fields(view, _scheme(container)))


@router.post("/{store_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=StoreOut)
async def retry_store(
    store_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> StoreOut:
    """Provision a failed store again (its creator or a team admin)."""
    view = await container.stores.retry(ctx, store_id)
    return StoreOut.model_validate(store_fields(view, _scheme(container)))
