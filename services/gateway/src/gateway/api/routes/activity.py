"""Audit log and usage queries. Visibility rules live in ``ActivityService``."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from gateway.api.deps import API_PREFIX, ContainerDep, SessionPrincipalDep, TeamDep
from gateway.api.schemas import (
    AuditEventOut,
    AuditPage,
    InvocationOut,
    InvocationPage,
    SummaryRowOut,
    UsageSummaryOut,
)
from gateway.services.activity import AuditFilter, SummaryGroup, TimeRange, UsageFilter

router = APIRouter(prefix=API_PREFIX, tags=["audit & usage"])

_SINCE = Query(None, description="Inclusive lower bound (ISO 8601).")
_UNTIL = Query(None, description="Exclusive upper bound (ISO 8601).")
_LIMIT = Query(50, ge=1, le=200)
_CURSOR = Query(None, max_length=200)


@router.get("/teams/{team_id}/audit-events", response_model=AuditPage)
async def team_audit_events(
    ctx: TeamDep,
    container: ContainerDep,
    actor_user_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None, max_length=64, examples=["api_key.create"]),
    outcome: str | None = Query(None, pattern="^(success|denied|failure)$"),
    since: datetime | None = _SINCE,
    until: datetime | None = _UNTIL,
    limit: int = _LIMIT,
    cursor: str | None = _CURSOR,
) -> AuditPage:
    """Team audit trail. Admins see the whole team; members only their own actions."""
    page = await container.activity.team_audit(
        ctx,
        AuditFilter(actor_user_id=actor_user_id, action=action, outcome=outcome),
        TimeRange(since=since, until=until),
        limit=limit,
        cursor=cursor,
    )
    return AuditPage(
        items=[AuditEventOut.model_validate(e) for e in page.items], next_cursor=page.next_cursor
    )


@router.get("/audit-events", response_model=AuditPage)
async def platform_audit_events(
    principal: SessionPrincipalDep,
    container: ContainerDep,
    actor_user_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None, max_length=64),
    outcome: str | None = Query(None, pattern="^(success|denied|failure)$"),
    since: datetime | None = _SINCE,
    until: datetime | None = _UNTIL,
    limit: int = _LIMIT,
    cursor: str | None = _CURSOR,
) -> AuditPage:
    """Events outside any team: logins, registrations, connector registry (platform admin)."""
    page = await container.activity.platform_audit(
        principal,
        AuditFilter(actor_user_id=actor_user_id, action=action, outcome=outcome),
        TimeRange(since=since, until=until),
        limit=limit,
        cursor=cursor,
    )
    return AuditPage(
        items=[AuditEventOut.model_validate(e) for e in page.items], next_cursor=page.next_cursor
    )


@router.get("/teams/{team_id}/usage/invocations", response_model=InvocationPage)
async def usage_invocations(
    ctx: TeamDep,
    container: ContainerDep,
    user_id: uuid.UUID | None = Query(None, description="Admins: filter by member."),
    connector: str | None = Query(None, max_length=64),
    tool: str | None = Query(None, max_length=128),
    outcome: str | None = Query(None, max_length=32),
    since: datetime | None = _SINCE,
    until: datetime | None = _UNTIL,
    limit: int = _LIMIT,
    cursor: str | None = _CURSOR,
) -> InvocationPage:
    """Tool calls. Admins see the team; members and API keys (usage:read) their own."""
    page = await container.activity.invocations(
        ctx,
        UsageFilter(user_id=user_id, connector=connector, tool=tool, outcome=outcome),
        TimeRange(since=since, until=until),
        limit=limit,
        cursor=cursor,
    )
    return InvocationPage(
        items=[InvocationOut.model_validate(i) for i in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/teams/{team_id}/usage/summary", response_model=UsageSummaryOut)
async def usage_summary(
    ctx: TeamDep,
    container: ContainerDep,
    group_by: SummaryGroup = Query(SummaryGroup.TOOL),
    user_id: uuid.UUID | None = Query(None, description="Admins: filter by member."),
    connector: str | None = Query(None, max_length=64),
    since: datetime | None = Query(None, description="Defaults to 30 days ago."),
    until: datetime | None = Query(None, description="Defaults to now."),
) -> UsageSummaryOut:
    """Call counts, error/denial counts and latency (avg, p95) grouped by a dimension."""
    rows = await container.activity.summary(
        ctx,
        UsageFilter(user_id=user_id, connector=connector),
        TimeRange(since=since, until=until),
        group_by=group_by,
    )
    return UsageSummaryOut(
        group_by=group_by.value,
        since=since,
        until=until,
        rows=[
            SummaryRowOut(
                key=r.key,
                calls=r.calls,
                errors=r.errors,
                denied=r.denied,
                avg_duration_ms=r.avg_duration_ms,
                p95_duration_ms=r.p95_duration_ms,
            )
            for r in rows
        ],
    )
