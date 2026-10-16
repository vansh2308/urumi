"""Authorized tool discovery and invocation, shared by the REST API and the MCP endpoint.

Order of checks for a call: credential scope, team membership, connector enabled for the team,
effective tool access, then the upstream call under a per-connector concurrency limit and a
deadline. Every call (including denials) produces one ``tool_invocations`` row, written after
the upstream call so no database connection is held while waiting on it.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import anyio
from mcp import types as mcp_types

from gateway.context import current_request_id
from gateway.db.engine import Database
from gateway.db.models import ApiKeyScope, AuditOutcome, ToolInvocation
from gateway.errors import AppError, PermissionDeniedError, PersistenceUnavailableError
from gateway.metrics import Metrics
from gateway.services.audit import AuditLog
from gateway.services.connectors import ConnectorService
from gateway.services.principal import CredentialKind, Principal
from gateway.services.teams import TeamContext, TeamService
from gateway.upstream.client import McpUpstreamClient

logger = logging.getLogger(__name__)

# Upstream tool errors follow "<code>: <message>" (FastMCP prefixes "Error executing tool X: ");
# keep only a well-formed code.
_ERROR_CODE_RE = re.compile(r"^(?:Error executing tool [^:]{1,128}: )?([a-z][a-z0-9_]{1,63}):")


class Transport(StrEnum):
    REST = "rest"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class CallContext:
    principal: Principal
    team_id: uuid.UUID
    connector_slug: str
    tool: str
    transport: Transport


class ToolService:
    def __init__(
        self,
        db: Database,
        *,
        teams: TeamService,
        connectors: ConnectorService,
        upstream: McpUpstreamClient,
        audit: AuditLog,
        metrics: Metrics,
    ) -> None:
        self._db = db
        self._teams = teams
        self._connectors = connectors
        self._upstream = upstream
        self._audit = audit
        self._metrics = metrics

    async def list_tools(self, ctx: TeamContext, slug: str) -> list[mcp_types.Tool]:
        """Tools on ``slug`` the caller may invoke (needs ``tools:read`` for API keys)."""
        ctx.principal.require_scope(ApiKeyScope.TOOLS_READ)
        access = await self._connectors.resolve(ctx, slug)
        tools = await self._upstream.list_tools(access.target)
        return [t for t in tools if access.tools.allows(t.name)]

    async def call_tool(
        self, call: CallContext, arguments: dict[str, Any]
    ) -> mcp_types.CallToolResult:
        started = time.perf_counter()
        team_ctx: TeamContext | None = None
        connector_id: uuid.UUID | None = None
        outcome = "internal_error"
        error_code: str | None = None
        try:
            team_ctx = await self._authorize(call)
            access = await self._connectors.resolve(team_ctx, call.connector_slug)
            connector_id = access.target.connector_id
            if not access.tools.allows(call.tool):
                await self._audit.record(
                    action="authz.denied",
                    actor=call.principal,
                    team_id=call.team_id,
                    target_type="tool",
                    target_id=f"{call.connector_slug}/{call.tool}"[:64],
                    outcome=AuditOutcome.DENIED,
                    details={"reason": "tool_not_permitted"},
                )
                raise PermissionDeniedError("You are not permitted to call this tool.")
            result = await self._upstream.call_tool(access.target, call.tool, arguments)
            if result.isError:
                outcome = "tool_error"
                error_code = _tool_error_code(result)
            else:
                outcome = "success"
            return result
        except PermissionDeniedError as exc:
            outcome, error_code = "denied", exc.code
            raise
        except AppError as exc:
            outcome, error_code = exc.code, exc.code
            raise
        except anyio.get_cancelled_exc_class():
            outcome = "cancelled"
            raise
        finally:
            duration = time.perf_counter() - started
            # Record even when the caller went away; shield keeps the write from being cancelled.
            with anyio.CancelScope(shield=True):
                await self._record(
                    call,
                    connector_id,
                    outcome,
                    error_code,
                    duration,
                    persist=team_ctx is not None,  # never write into a team the caller is not in
                )

    async def _authorize(self, call: CallContext) -> TeamContext:
        principal = call.principal
        if principal.credential is not CredentialKind.API_KEY:
            raise PermissionDeniedError("Tool invocation requires a platform API key.")
        principal.require_scope(ApiKeyScope.TOOLS_CALL)
        return await self._teams.context(principal, call.team_id)

    async def _record(
        self,
        call: CallContext,
        connector_id: uuid.UUID | None,
        outcome: str,
        error_code: str | None,
        duration: float,
        *,
        persist: bool,
    ) -> None:
        # Unknown connector slugs are client input; keep them out of metric labels.
        label = call.connector_slug if connector_id is not None else "unknown"
        tool_label = call.tool if connector_id is not None else "unknown"
        self._metrics.tool_invocations.labels(
            connector=label, tool=tool_label, outcome=outcome
        ).inc()
        self._metrics.tool_latency.labels(connector=label).observe(duration)
        logger.info(
            "tool call finished",
            extra={
                "connector": call.connector_slug,
                "tool": call.tool,
                "outcome": outcome,
                "transport": call.transport.value,
                "duration_ms": round(duration * 1000, 1),
            },
        )
        if not persist:
            return
        try:
            async with self._db.transaction() as session:
                session.add(
                    ToolInvocation(
                        id=uuid.uuid4(),
                        team_id=call.team_id,
                        user_id=call.principal.user_id,
                        api_key_id=call.principal.api_key_id,
                        connector_id=connector_id,
                        connector_slug=call.connector_slug[:64],
                        tool_name=call.tool[:128],
                        transport=call.transport.value,
                        outcome=outcome[:32],
                        error_code=error_code,
                        duration_ms=round(duration * 1000),
                        request_id=current_request_id(),
                    )
                )
        except PersistenceUnavailableError:
            logger.warning("usage record not persisted")


def _tool_error_code(result: mcp_types.CallToolResult) -> str:
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            match = _ERROR_CODE_RE.match(block.text)
            if match:
                return match.group(1)
    return "tool_error"
