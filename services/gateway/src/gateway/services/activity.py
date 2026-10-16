"""Audit-log and usage queries with tenant isolation, plus retention purging.

Visibility rules:

* Team admins see every audit event and usage record of their team.
* Members see only their own: filters naming anyone else are refused, not silently widened.
* API keys (scope ``usage:read``) see only their owner's usage, never the audit log.
* Platform admins see registry events, which belong to no team.

Lists are keyset-paginated newest first with an opaque cursor.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, String, and_, case, cast, delete, func, or_, select, tuple_

from gateway.config import RetentionSettings
from gateway.db.engine import Database
from gateway.db.models import (
    ApiKeyScope,
    AuditEvent,
    AuthSession,
    Invitation,
    ToolInvocation,
)
from gateway.errors import InvalidInputError, PermissionDeniedError
from gateway.services.principal import CredentialKind, Principal
from gateway.services.rate_limit import RateLimiter
from gateway.services.teams import TeamContext

MAX_PAGE = 200
MAX_SUMMARY_RANGE = timedelta(days=366)


class SummaryGroup(StrEnum):
    TOOL = "tool"
    CONNECTOR = "connector"
    USER = "user"
    OUTCOME = "outcome"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class TimeRange:
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditFilter:
    actor_user_id: uuid.UUID | None = None
    action: str | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class UsageFilter:
    user_id: uuid.UUID | None = None
    connector: str | None = None
    tool: str | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SummaryRow:
    key: str
    calls: int
    errors: int
    denied: int
    avg_duration_ms: float
    p95_duration_ms: float


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, row_id = raw.partition("|")
        return datetime.fromisoformat(ts), uuid.UUID(row_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidInputError("Invalid cursor.") from exc


def _apply_range[S: Select[Any]](stmt: S, column: Any, window: TimeRange) -> S:
    if window.since is not None:
        stmt = stmt.where(column >= window.since)
    if window.until is not None:
        stmt = stmt.where(column < window.until)
    return stmt


class ActivityService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- audit ---------------------------------------------------------------------------------

    async def team_audit(
        self,
        ctx: TeamContext,
        filters: AuditFilter,
        window: TimeRange,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[AuditEvent]:
        if ctx.principal.credential is CredentialKind.API_KEY:
            raise PermissionDeniedError("The audit log requires a user session.")
        actor = filters.actor_user_id
        if not ctx.is_admin:
            if actor not in (None, ctx.principal.user_id):
                raise PermissionDeniedError("Members can only view their own activity.")
            actor = ctx.principal.user_id
        stmt = select(AuditEvent).where(AuditEvent.team_id == ctx.team_id)
        return await self._audit_page(stmt, filters, actor, window, limit=limit, cursor=cursor)

    async def platform_audit(
        self,
        principal: Principal,
        filters: AuditFilter,
        window: TimeRange,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[AuditEvent]:
        """Events that belong to no team (registry changes, logins). Platform admins only."""
        if not principal.is_platform_admin or principal.credential is CredentialKind.API_KEY:
            raise PermissionDeniedError("Platform audit events require a platform admin.")
        stmt = select(AuditEvent).where(AuditEvent.team_id.is_(None))
        return await self._audit_page(
            stmt, filters, filters.actor_user_id, window, limit=limit, cursor=cursor
        )

    async def _audit_page(
        self,
        stmt: Select[Any],
        filters: AuditFilter,
        actor: uuid.UUID | None,
        window: TimeRange,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[AuditEvent]:
        if actor is not None:
            stmt = stmt.where(AuditEvent.actor_user_id == actor)
        if filters.action:
            stmt = stmt.where(AuditEvent.action == filters.action)
        if filters.outcome:
            stmt = stmt.where(AuditEvent.outcome == filters.outcome)
        stmt = _apply_range(stmt, AuditEvent.created_at, window)
        return await self._page(stmt, AuditEvent, limit=limit, cursor=cursor)

    # --- usage ---------------------------------------------------------------------------------

    def _usage_owner(self, ctx: TeamContext, requested: uuid.UUID | None) -> uuid.UUID | None:
        ctx.principal.require_scope(ApiKeyScope.USAGE_READ)
        if ctx.is_admin and ctx.principal.credential is CredentialKind.SESSION:
            return requested
        if requested not in (None, ctx.principal.user_id):
            raise PermissionDeniedError("You can only view your own usage.")
        return ctx.principal.user_id

    def _usage_query(
        self, ctx: TeamContext, filters: UsageFilter, window: TimeRange, owner: uuid.UUID | None
    ) -> Select[Any]:
        stmt = select(ToolInvocation).where(ToolInvocation.team_id == ctx.team_id)
        if owner is not None:
            stmt = stmt.where(ToolInvocation.user_id == owner)
        if filters.connector:
            stmt = stmt.where(ToolInvocation.connector_slug == filters.connector)
        if filters.tool:
            stmt = stmt.where(ToolInvocation.tool_name == filters.tool)
        if filters.outcome:
            stmt = stmt.where(ToolInvocation.outcome == filters.outcome)
        return _apply_range(stmt, ToolInvocation.created_at, window)

    async def invocations(
        self,
        ctx: TeamContext,
        filters: UsageFilter,
        window: TimeRange,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[ToolInvocation]:
        owner = self._usage_owner(ctx, filters.user_id)
        stmt = self._usage_query(ctx, filters, window, owner)
        return await self._page(stmt, ToolInvocation, limit=limit, cursor=cursor)

    async def summary(
        self,
        ctx: TeamContext,
        filters: UsageFilter,
        window: TimeRange,
        *,
        group_by: SummaryGroup,
    ) -> list[SummaryRow]:
        owner = self._usage_owner(ctx, filters.user_id)
        now = datetime.now(UTC)
        window = TimeRange(
            since=window.since or now - timedelta(days=30), until=window.until or now
        )
        assert window.since is not None and window.until is not None  # noqa: S101 - set above
        if window.until - window.since > MAX_SUMMARY_RANGE:
            raise InvalidInputError("The summary range may span at most 366 days.")

        base = self._usage_query(ctx, filters, window, owner).subquery()
        inv = base.c
        key = {
            SummaryGroup.TOOL: inv.connector_slug + "/" + inv.tool_name,
            SummaryGroup.CONNECTOR: inv.connector_slug,
            SummaryGroup.USER: cast(inv.user_id, String),
            SummaryGroup.OUTCOME: inv.outcome,
            SummaryGroup.DAY: func.to_char(func.date_trunc("day", inv.created_at), "YYYY-MM-DD"),
        }[group_by].label("key")
        stmt = (
            select(
                key,
                func.count().label("calls"),
                func.sum(case((inv.outcome != "success", 1), else_=0)).label("errors"),
                func.sum(case((inv.outcome == "denied", 1), else_=0)).label("denied"),
                func.coalesce(func.avg(inv.duration_ms), 0).label("avg_ms"),
                func.coalesce(func.percentile_cont(0.95).within_group(inv.duration_ms), 0).label(
                    "p95_ms"
                ),
            )
            .group_by(key)
            .order_by(func.count().desc())
            .limit(500)
        )
        async with self._db.transaction() as session:
            rows = (await session.execute(stmt)).all()
        return [
            SummaryRow(
                key=str(r.key),
                calls=int(r.calls),
                errors=int(r.errors or 0),
                denied=int(r.denied or 0),
                avg_duration_ms=round(float(r.avg_ms), 1),
                p95_duration_ms=round(float(r.p95_ms), 1),
            )
            for r in rows
        ]

    # --- shared --------------------------------------------------------------------------------

    async def _page[M: AuditEvent | ToolInvocation](
        self, stmt: Select[Any], model: type[M], *, limit: int, cursor: str | None
    ) -> Page[M]:
        limit = max(1, min(limit, MAX_PAGE))
        if cursor:
            ts, row_id = _decode_cursor(cursor)
            stmt = stmt.where(tuple_(model.created_at, model.id) < tuple_(ts, row_id))
        stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
        async with self._db.transaction() as session:
            rows = list((await session.scalars(stmt)).all())
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None
        return Page(items=rows, next_cursor=next_cursor)


class RetentionService:
    """Deletes rows past their retention period; run periodically by the app lifespan."""

    def __init__(
        self, db: Database, *, rate_limiter: RateLimiter, settings: RetentionSettings
    ) -> None:
        self._db = db
        self._rate_limiter = rate_limiter
        self._settings = settings

    async def purge(self) -> dict[str, int]:
        now = datetime.now(UTC)
        sessions_cutoff = now - timedelta(days=self._settings.sessions_days)
        deleted: dict[str, int] = {}
        async with self._db.transaction() as session:
            for name, stmt in (
                (
                    "audit_events",
                    delete(AuditEvent).where(
                        AuditEvent.created_at < now - timedelta(days=self._settings.audit_days)
                    ),
                ),
                (
                    "tool_invocations",
                    delete(ToolInvocation).where(
                        ToolInvocation.created_at < now - timedelta(days=self._settings.usage_days)
                    ),
                ),
                (
                    "auth_sessions",
                    delete(AuthSession).where(
                        or_(
                            AuthSession.expires_at < sessions_cutoff,
                            AuthSession.revoked_at < sessions_cutoff,
                        )
                    ),
                ),
                (
                    "invitations",
                    delete(Invitation).where(
                        and_(
                            Invitation.accepted_at.is_(None),
                            Invitation.expires_at < sessions_cutoff,
                        )
                    ),
                ),
            ):
                result = await session.execute(stmt)
                deleted[name] = int(getattr(result, "rowcount", 0) or 0)
        deleted["rate_limit_counters"] = await self._rate_limiter.purge()
        return {k: v for k, v in deleted.items() if v}
