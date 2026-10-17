"""Aggregated agent metrics from the persisted runs and tool call logs.

Members see their own usage; team admins (role from the gateway) may ask for the whole team.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from agent.db.engine import Database
from agent.db.models import AgentRun, RunStatus, ToolCallLog
from agent.errors import PermissionDeniedError
from agent.gateway.client import Identity


@dataclass(frozen=True, slots=True)
class ToolUsage:
    connector: str | None
    tool: str | None
    calls: int
    failures: int
    avg_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class UsageSummary:
    scope: str
    since: datetime
    runs: int = 0
    runs_by_status: dict[str, int] = field(default_factory=dict)
    avg_latency_ms: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    avg_first_token_ms: float | None = None
    avg_llm_latency_ms: float | None = None
    avg_steps: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_calls_by_outcome: dict[str, int] = field(default_factory=dict)
    tools: list[ToolUsage] = field(default_factory=list)


class UsageService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def summary(self, identity: Identity, *, team_scope: bool, days: int) -> UsageSummary:
        if team_scope and not identity.is_team_admin:
            raise PermissionDeniedError("Only team admins can view team-wide usage.")
        since = datetime.now(UTC) - timedelta(days=days)
        run_filter: list[Any] = [
            AgentRun.team_id == identity.team_id,
            AgentRun.created_at >= since,
            AgentRun.status != RunStatus.RUNNING.value,
        ]
        log_filter: list[Any] = [
            ToolCallLog.team_id == identity.team_id,
            ToolCallLog.created_at >= since,
        ]
        if not team_scope:
            run_filter.append(AgentRun.user_id == identity.user_id)
            log_filter.append(ToolCallLog.user_id == identity.user_id)

        async with self._db.transaction() as session:
            totals = (
                await session.execute(
                    select(
                        func.count(AgentRun.id),
                        func.avg(AgentRun.latency_ms),
                        func.percentile_cont(0.5).within_group(AgentRun.latency_ms),
                        func.percentile_cont(0.95).within_group(AgentRun.latency_ms),
                        func.avg(AgentRun.first_token_ms),
                        func.avg(AgentRun.llm_latency_ms),
                        func.avg(AgentRun.steps),
                        func.coalesce(func.sum(AgentRun.input_tokens), 0),
                        func.coalesce(func.sum(AgentRun.output_tokens), 0),
                    ).where(*run_filter)
                )
            ).one()
            by_status = (
                await session.execute(
                    select(AgentRun.status, func.count(AgentRun.id))
                    .where(*run_filter)
                    .group_by(AgentRun.status)
                )
            ).all()
            by_outcome = (
                await session.execute(
                    select(ToolCallLog.outcome, func.count(ToolCallLog.id))
                    .where(*log_filter)
                    .group_by(ToolCallLog.outcome)
                )
            ).all()
            failures = func.count(ToolCallLog.id).filter(ToolCallLog.outcome != "success")
            per_tool = (
                await session.execute(
                    select(
                        ToolCallLog.connector_slug,
                        ToolCallLog.tool_name,
                        func.count(ToolCallLog.id),
                        failures,
                        func.avg(ToolCallLog.latency_ms),
                    )
                    .where(*log_filter)
                    .group_by(ToolCallLog.connector_slug, ToolCallLog.tool_name)
                    .order_by(func.count(ToolCallLog.id).desc())
                    .limit(50)
                )
            ).all()

        outcomes = {outcome: count for outcome, count in by_outcome}
        return UsageSummary(
            scope="team" if team_scope else "me",
            since=since,
            runs=totals[0],
            runs_by_status={status: count for status, count in by_status},
            avg_latency_ms=_round(totals[1]),
            p50_latency_ms=_round(totals[2]),
            p95_latency_ms=_round(totals[3]),
            avg_first_token_ms=_round(totals[4]),
            avg_llm_latency_ms=_round(totals[5]),
            avg_steps=_round(totals[6], 2),
            input_tokens=int(totals[7]),
            output_tokens=int(totals[8]),
            tool_calls=sum(outcomes.values()),
            tool_calls_by_outcome=outcomes,
            tools=[
                ToolUsage(
                    connector=None if connector is None else str(connector),
                    tool=None if tool is None else str(tool),
                    calls=_int(calls),
                    failures=_int(failed),
                    avg_latency_ms=_round(latency),
                )
                for connector, tool, calls, failed, latency in per_tool
            ],
        )


def _round(value: Any, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _int(value: Any) -> int:
    return int(value or 0)
