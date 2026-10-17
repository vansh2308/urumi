"""Agent metrics aggregated from persisted runs: latency, tokens, tool outcomes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from agent.api.deps import API_PREFIX, CallerDep, ContainerDep
from agent.api.schemas import UsageSummaryOut

router = APIRouter(prefix=f"{API_PREFIX}/usage", tags=["usage"])


@router.get("/summary", response_model=UsageSummaryOut)
async def usage_summary(
    caller: CallerDep,
    container: ContainerDep,
    scope: Annotated[
        Literal["me", "team"], Query(description="team requires the team admin role.")
    ] = "me",
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> UsageSummaryOut:
    summary = await container.usage.summary(caller.identity, team_scope=scope == "team", days=days)
    return UsageSummaryOut.model_validate(summary)
