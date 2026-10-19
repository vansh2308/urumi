"""Hosted MCP servers (platform admins): deploy an MCP server from a container image.

A hosted server is also an ordinary connector: enable it for teams, disable or remove it with
the connector registry endpoints. Removing it deletes its Deployment, Service, NetworkPolicy and
Secret from the cluster.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from gateway.api.deps import API_PREFIX, ContainerDep, SessionPrincipalDep
from gateway.api.routes.connectors import connector_out
from gateway.api.schemas import (
    DeployHostedServerRequest,
    HostedServerOut,
    HostedSettingsOut,
    HostedStatusOut,
    UpdateHostedServerRequest,
)
from gateway.errors import PermissionDeniedError
from gateway.hosted.service import HostedChange, HostedView, NewHostedServer
from gateway.services.principal import Principal

router = APIRouter(prefix=f"{API_PREFIX}/hosted-servers", tags=["hosted MCP servers"])


def _out(view: HostedView, principal: Principal) -> HostedServerOut:
    server = view.server
    return HostedServerOut(
        connector=connector_out(view.connector, principal),
        image=server.image,
        port=server.port,
        path=server.path,
        args=list(server.args),
        env=dict(server.env),
        secret_env_keys=list(server.secret_keys),
        cpu_millicores=server.cpu_millicores,
        memory_mib=server.memory_mib,
        egress=server.egress,
        namespace=server.namespace,
        k8s_name=server.k8s_name,
        status=HostedStatusOut(
            phase=view.status.phase,
            reason=view.status.reason,
            ready_replicas=view.status.ready_replicas,
            restarts=view.status.restarts,
        ),
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.get("/settings", response_model=HostedSettingsOut)
async def hosted_settings(
    principal: SessionPrincipalDep, container: ContainerDep
) -> HostedSettingsOut:
    """Whether hosting is enabled and which images and limits are allowed (platform admin)."""
    if not principal.is_platform_admin:
        raise PermissionDeniedError("Managing hosted MCP servers requires a platform admin.")
    settings = container.hosted.settings
    return HostedSettingsOut(
        enabled=container.hosted.enabled,
        namespace=settings.namespace,
        allowed_images=settings.allowed_images,
        default_cpu_millicores=settings.default_cpu_millicores,
        max_cpu_millicores=settings.max_cpu_millicores,
        default_memory_mib=settings.default_memory_mib,
        max_memory_mib=settings.max_memory_mib,
        max_servers=settings.max_servers,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=HostedServerOut)
async def deploy_hosted_server(
    body: DeployHostedServerRequest, principal: SessionPrincipalDep, container: ContainerDep
) -> HostedServerOut:
    """Deploy an MCP server image into the cluster and register it as a connector."""
    view = await container.hosted.create(
        principal,
        NewHostedServer(
            slug=body.slug,
            display_name=body.display_name,
            description=body.description,
            image=body.image,
            port=body.port,
            path=body.path,
            args=tuple(body.args),
            env=body.env,
            secret_env={k: v.get_secret_value() for k, v in body.secret_env.items()},
            credential=body.credential.to_domain() if body.credential else None,
            cpu_millicores=body.cpu_millicores,
            memory_mib=body.memory_mib,
            egress=body.egress,
            timeout_seconds=body.timeout_seconds,
            max_concurrency=body.max_concurrency,
        ),
    )
    return _out(view, principal)


@router.get("", response_model=list[HostedServerOut])
async def list_hosted_servers(
    principal: SessionPrincipalDep, container: ContainerDep
) -> list[HostedServerOut]:
    """Every hosted server with its live cluster status."""
    return [_out(v, principal) for v in await container.hosted.list(principal)]


@router.get("/{connector_id}", response_model=HostedServerOut)
async def get_hosted_server(
    connector_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> HostedServerOut:
    return _out(await container.hosted.get(principal, connector_id), principal)


@router.patch("/{connector_id}", response_model=HostedServerOut)
async def update_hosted_server(
    connector_id: uuid.UUID,
    body: UpdateHostedServerRequest,
    principal: SessionPrincipalDep,
    container: ContainerDep,
) -> HostedServerOut:
    """Change the image, arguments, variables, limits or egress; rolls the pod."""
    view = await container.hosted.update(
        principal,
        connector_id,
        HostedChange(
            image=body.image,
            args=tuple(body.args) if body.args is not None else None,
            env=body.env,
            secret_env=(
                {k: v.get_secret_value() if v else None for k, v in body.secret_env.items()}
                if body.secret_env is not None
                else None
            ),
            cpu_millicores=body.cpu_millicores,
            memory_mib=body.memory_mib,
            egress=body.egress,
        ),
    )
    return _out(view, principal)


@router.post("/{connector_id}/restart", response_model=HostedServerOut)
async def restart_hosted_server(
    connector_id: uuid.UUID, principal: SessionPrincipalDep, container: ContainerDep
) -> HostedServerOut:
    """Restart the server's pod without changing its spec."""
    return _out(await container.hosted.restart(principal, connector_id), principal)
