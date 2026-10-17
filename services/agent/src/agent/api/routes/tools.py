"""The caller's identity and the tools the agent would offer the model for them."""

from __future__ import annotations

from fastapi import APIRouter

from agent.api.deps import API_PREFIX, CallerDep, ContainerDep
from agent.api.schemas import MeOut, ToolOut, ToolsOut

router = APIRouter(prefix=API_PREFIX, tags=["identity & tools"])


@router.get("/me", response_model=MeOut)
async def me(caller: CallerDep) -> MeOut:
    """Who the API key belongs to, as verified by the gateway."""
    i = caller.identity
    return MeOut(
        user_id=i.user_id,
        email=i.email,
        display_name=i.display_name,
        team_id=i.team_id,
        team_name=i.team_name,
        role=i.role,
        api_key_id=i.api_key_id,
    )


@router.get("/tools", response_model=ToolsOut)
async def list_tools(caller: CallerDep, container: ContainerDep) -> ToolsOut:
    """Tools discovered through the gateway for this caller, exactly as offered to the model."""
    toolbox = await container.tools.discover(caller)
    return ToolsOut(
        tools=[
            ToolOut(
                name=b.function_name,
                connector=b.connector,
                tool=b.tool,
                description=b.description,
                input_schema=b.input_schema,
            )
            for b in toolbox.bindings.values()
        ],
        unavailable_connectors=list(toolbox.unavailable_connectors),
    )
