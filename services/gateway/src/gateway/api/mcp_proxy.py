"""MCP endpoints of the gateway, speaking MCP Streamable HTTP:

* ``/v1/mcp``: every enabled server at once. Tools are named ``<connector>__<tool>`` (slugs
  cannot contain ``_``, so the first ``__`` always splits them), so two servers may expose tools
  with the same name. Listing fetches all servers in parallel; one that is slow or down is left
  out of that listing rather than failing it.
* ``/v1/mcp/{connector}``: one server, with its original tool names.

MCP clients (Claude, the MCP Inspector, agents) connect with a platform API key as
``Authorization: Bearer urk_...``. The key fixes the team. Both endpoints expose exactly the
tools the key may use and forwards calls through the same
:class:`~gateway.services.tools.ToolService` as the REST API, so authorization, auditing and
usage tracking are identical on both paths.

It runs stateless with JSON responses, like the upstream servers: no MCP session state lives in
the gateway, so any replica can serve any request.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from gateway import __version__
from gateway.api.deps import authenticate_bearer
from gateway.api.errors import error_body
from gateway.container import Container
from gateway.errors import AppError, AuthenticationError, PermissionDeniedError, RateLimitedError
from gateway.services.principal import CredentialKind, Principal
from gateway.services.teams import TeamContext
from gateway.services.tools import (
    CallContext,
    Transport,
    qualified_name,
    split_qualified_name,
)

logger = logging.getLogger(__name__)

_STATE_KEY = "gateway_mcp"

INSTRUCTIONS = """\
Tools of the MCP servers your team uses, exposed through the Urumi gateway. Only tools your API
key is allowed to use are listed. On the all-servers endpoint a tool is named
'<server>__<tool>' and its description starts with the server's name. Errors are returned as
'<code>: <message>'; codes marked '(retryable)' may succeed if retried later."""


@dataclass(frozen=True, slots=True)
class _ProxyRequest:
    principal: Principal
    team_id: uuid.UUID
    connector_slug: str | None  # None: the all-servers endpoint


class McpProxy:
    """ASGI app mounted as a route; authenticates, then hands over to the MCP SDK transport."""

    def __init__(self, container: Container) -> None:
        self._container = container
        self._server: Server[object, object] = Server(
            "urumi-gateway", version=__version__, instructions=INSTRUCTIONS
        )
        # Registered directly (not via the decorators) so the SDK keeps no tool cache: the tool
        # list differs per API key and must never be shared across requests.
        self._server.request_handlers[mcp_types.ListToolsRequest] = self._list_tools
        self._server.request_handlers[mcp_types.CallToolRequest] = self._call_tool
        self.session_manager = StreamableHTTPSessionManager(
            app=self._server,
            stateless=True,
            json_response=True,
            max_request_body_size=container.settings.server.max_request_body_bytes,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        slug = scope.get("path_params", {}).get("connector")
        try:
            principal = await self._authenticate(Headers(scope=scope))
        except AppError as exc:
            await _error_response(exc)(scope, receive, send)
            return
        assert principal.api_key_team_id is not None  # guaranteed for API keys  # noqa: S101
        scope.setdefault("state", {})[_STATE_KEY] = _ProxyRequest(
            principal=principal,
            team_id=principal.api_key_team_id,
            connector_slug=str(slug) if slug is not None else None,
        )
        await self.session_manager.handle_request(scope, receive, send)

    async def _authenticate(self, headers: Headers) -> Principal:
        scheme, _, raw = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not raw:
            raise AuthenticationError("A platform API key is required (Authorization: Bearer).")
        principal = await authenticate_bearer(self._container, raw.strip())
        if principal.credential is not CredentialKind.API_KEY:
            raise PermissionDeniedError("The MCP endpoint requires a platform API key.")
        await self._container.rate_limiter.hit(
            principal.rate_limit_bucket,
            limit=principal.rate_limit_per_minute
            or self._container.settings.rate_limit.per_principal_per_minute,
            scope="mcp",
        )
        return principal

    def _current(self) -> _ProxyRequest:
        request = self._server.request_context.request
        state = getattr(request, "scope", {}).get("state", {})
        proxy_request = state.get(_STATE_KEY)
        if not isinstance(proxy_request, _ProxyRequest):  # pragma: no cover - wiring error
            raise RuntimeError("MCP request reached the server without authentication")
        return proxy_request

    async def _list_tools(self, _: mcp_types.ListToolsRequest) -> mcp_types.ServerResult:
        req = self._current()
        try:
            ctx = await self._container.teams.context(req.principal, req.team_id)
            if req.connector_slug is None:
                tools = await self._aggregated_tools(ctx)
            else:
                tools = await self._container.tools.list_tools(ctx, req.connector_slug)
        except AppError as exc:
            raise McpError(
                mcp_types.ErrorData(code=mcp_types.INVALID_REQUEST, message=_message(exc))
            ) from exc
        return mcp_types.ServerResult(mcp_types.ListToolsResult(tools=tools))

    async def _aggregated_tools(self, ctx: TeamContext) -> list[mcp_types.Tool]:
        listing = await self._container.tools.list_all_tools(ctx)
        if listing.unavailable:
            logger.warning(
                "servers left out of the aggregated tool list",
                extra={"unavailable": [f"{slug}:{code}" for slug, code in listing.unavailable]},
            )
        return [
            tool.model_copy(
                update={
                    "name": qualified_name(server.slug, tool.name),
                    "description": f"[{server.display_name}] {tool.description or ''}".strip(),
                }
            )
            for server in listing.servers
            for tool in server.tools
        ]

    async def _call_tool(self, request: mcp_types.CallToolRequest) -> mcp_types.ServerResult:
        req = self._current()
        slug, tool = req.connector_slug, request.params.name
        if slug is None:
            parts = split_qualified_name(tool)
            if parts is None:
                return mcp_types.ServerResult(
                    mcp_types.CallToolResult(
                        content=[
                            mcp_types.TextContent(
                                type="text",
                                text="not_found: Tool names here are '<server>__<tool>'.",
                            )
                        ],
                        isError=True,
                    )
                )
            slug, tool = parts
        try:
            result = await self._container.tools.call_tool(
                CallContext(
                    principal=req.principal,
                    team_id=req.team_id,
                    connector_slug=slug,
                    tool=tool,
                    transport=Transport.MCP,
                ),
                request.params.arguments or {},
            )
        except AppError as exc:
            result = mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=_message(exc))], isError=True
            )
        return mcp_types.ServerResult(result)


def _message(exc: AppError) -> str:
    suffix = " (retryable)" if exc.retryable else ""
    return f"{exc.code}: {exc.public_message}{suffix}"


def _error_response(exc: AppError) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        error_body(exc.code, exc.public_message), status_code=exc.status_code, headers=headers
    )
