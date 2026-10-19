"""Client for registered upstream MCP servers, built on the official MCP SDK.

Each operation opens a short MCP session over Streamable HTTP (initialize, then the request) on a
shared, pooled ``httpx.AsyncClient``. That works against both stateless and session-based
servers, needs no sticky state in the gateway, and lets any replica serve any call.

This module knows nothing about users, teams or permissions: callers authorize first and pass a
:class:`UpstreamTarget` built from the registry, never from client input.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
from mcp import ClientSession, McpError
from mcp import types as mcp_types
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from gateway import __version__
from gateway.config import UpstreamSettings
from gateway.context import REQUEST_ID_HEADER, current_request_id
from gateway.errors import (
    UpstreamAuthError,
    UpstreamBlockedError,
    UpstreamBusyError,
    UpstreamError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from gateway.metrics import Metrics
from gateway.upstream.ssrf import DestinationBlockedError, check_destination

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpstreamTarget:
    connector_id: uuid.UUID
    slug: str
    url: str
    timeout_seconds: float
    max_concurrency: int
    # The connector's static credential header, decrypted. Never logged (repr=False).
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    # Re-check where the host resolves before connecting (admin-supplied URLs; see ssrf.py).
    verify_destination: bool = True


@dataclass(frozen=True, slots=True)
class ServerProbe:
    name: str
    version: str
    protocol_version: str
    tools: list[mcp_types.Tool]


# (origin, headers) of the upstream call running in this task. The pooled client is shared by
# every connector, so per-connector credentials ride on the call's context instead of the client.
_call_headers: ContextVar[tuple[str, Mapping[str, str]] | None] = ContextVar(
    "upstream_call_headers", default=None
)


def _origin(url: httpx.URL) -> str:
    return f"{url.scheme}://{url.host}:{url.port or (443 if url.scheme == 'https' else 80)}"


_LOCAL_ADDRESS = {"auto": None, "ipv4": "0.0.0.0", "ipv6": "::"}  # noqa: S104 - bind side only


def build_upstream_http_client(settings: UpstreamSettings) -> httpx.AsyncClient:
    async def add_request_id(request: httpx.Request) -> None:
        if (rid := current_request_id()) is not None:
            request.headers[REQUEST_ID_HEADER] = rid

    async def add_credential(request: httpx.Request) -> None:
        # Only to the connector's own origin: a credential never follows a request elsewhere.
        if (call := _call_headers.get()) is not None and call[0] == _origin(request.url):
            request.headers.update(call[1])

    transport = httpx.AsyncHTTPTransport(
        limits=httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        ),
        local_address=_LOCAL_ADDRESS[settings.ip_family],
        trust_env=False,
    )
    return httpx.AsyncClient(
        transport=transport,
        # Per-call deadlines are enforced around the whole MCP exchange; these bound each step.
        timeout=httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.max_call_timeout_seconds,
            write=settings.connect_timeout_seconds,
            pool=settings.queue_timeout_seconds or 1.0,
        ),
        follow_redirects=False,  # never let an upstream bounce the gateway somewhere else
        headers={"User-Agent": f"urumi-gateway/{__version__}"},
        event_hooks={"request": [add_request_id, add_credential]},
        trust_env=False,
    )


class McpUpstreamClient:
    def __init__(
        self, http_client: httpx.AsyncClient, *, settings: UpstreamSettings, metrics: Metrics
    ) -> None:
        self._http = http_client
        self._settings = settings
        self._metrics = metrics
        # Process-wide caches keyed by connector; they hold no per-request state.
        self._limiters: dict[uuid.UUID, tuple[int, asyncio.Semaphore]] = {}
        self._tool_cache: dict[uuid.UUID, tuple[float, str, list[mcp_types.Tool]]] = {}

    async def list_tools(self, target: UpstreamTarget) -> list[mcp_types.Tool]:
        cached = self._tool_cache.get(target.connector_id)
        if cached is not None and cached[0] > time.monotonic() and cached[1] == target.url:
            return cached[2]

        async def fetch(
            session: ClientSession, _: mcp_types.InitializeResult
        ) -> list[mcp_types.Tool]:
            return await _list_all_tools(session)

        tools = await self._run(target, fetch)
        ttl = self._settings.tool_cache_ttl_seconds
        if ttl > 0:
            self._tool_cache[target.connector_id] = (time.monotonic() + ttl, target.url, tools)
        return tools

    async def call_tool(
        self, target: UpstreamTarget, name: str, arguments: dict[str, Any]
    ) -> mcp_types.CallToolResult:
        async def call(
            session: ClientSession, _: mcp_types.InitializeResult
        ) -> mcp_types.CallToolResult:
            return await session.call_tool(name, arguments)

        return await self._run(target, call)

    async def probe(self, target: UpstreamTarget) -> ServerProbe:
        async def describe(session: ClientSession, init: mcp_types.InitializeResult) -> ServerProbe:
            return ServerProbe(
                name=init.serverInfo.name,
                version=init.serverInfo.version,
                protocol_version=str(init.protocolVersion),
                tools=await _list_all_tools(session),
            )

        self.invalidate(target.connector_id)
        return await self._run(target, describe)

    def invalidate(self, connector_id: uuid.UUID) -> None:
        self._tool_cache.pop(connector_id, None)
        self._limiters.pop(connector_id, None)

    async def _run[T](
        self,
        target: UpstreamTarget,
        operation: Callable[[ClientSession, mcp_types.InitializeResult], Awaitable[T]],
    ) -> T:
        if target.verify_destination:
            try:
                await check_destination(target.url, self._settings)
            except DestinationBlockedError as exc:
                self._metrics.upstream_failures.labels(
                    connector=target.slug, reason="upstream_blocked"
                ).inc()
                logger.warning(
                    "upstream destination blocked",
                    extra={"connector": target.slug, "reason": exc.reason},
                )
                raise UpstreamBlockedError(
                    f"The connector's address is not allowed: {exc.reason}."
                ) from exc
        async with self._slot(target):
            token = _call_headers.set((_origin(httpx.URL(target.url)), target.headers))
            try:
                with anyio.fail_after(target.timeout_seconds):
                    async with (
                        streamable_http_client(target.url, http_client=self._http) as (r, w, _),
                        ClientSession(r, w) as session,
                    ):
                        init = await session.initialize()
                        return await operation(session, init)
            except Exception as exc:
                error = _classify(exc)
                self._metrics.upstream_failures.labels(
                    connector=target.slug, reason=error.code
                ).inc()
                logger.warning(
                    "upstream call failed",
                    extra={"connector": target.slug, "reason": error.code, "error": _leaf(exc)},
                )
                raise error from exc
            finally:
                _call_headers.reset(token)

    @contextlib.asynccontextmanager
    async def _slot(self, target: UpstreamTarget) -> AsyncIterator[None]:
        """Bound in-flight calls per connector so one slow upstream cannot exhaust the pool."""
        entry = self._limiters.get(target.connector_id)
        if entry is None or entry[0] != target.max_concurrency:
            entry = (target.max_concurrency, asyncio.Semaphore(target.max_concurrency))
            self._limiters[target.connector_id] = entry
        semaphore = entry[1]
        try:
            async with asyncio.timeout(self._settings.queue_timeout_seconds or None):
                await semaphore.acquire()
        except TimeoutError:
            self._metrics.upstream_failures.labels(connector=target.slug, reason="busy").inc()
            raise UpstreamBusyError(
                "The connector is at its concurrency limit; retry shortly."
            ) from None
        try:
            yield
        finally:
            semaphore.release()


MAX_DESCRIPTION_CHARS = 2000
MAX_TITLE_CHARS = 200
MAX_TOOLS_PER_SERVER = 500
# C0/C1 control characters except tab and newline, and invisible format characters often used to
# hide text from people reading a tool description (zero-width, bidi overrides).
_HIDDEN_CHARS = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)


def _clean_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = _HIDDEN_CHARS.sub("", value).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def sanitize_tool(tool: mcp_types.Tool) -> mcp_types.Tool:
    """Upstream tool metadata is untrusted text that ends up in model prompts and in the
    dashboard: drop hidden characters and bound its size. Names are checked separately by
    routing (only allow-listed tool names can be called)."""
    return tool.model_copy(
        update={
            "description": _clean_text(tool.description, MAX_DESCRIPTION_CHARS),
            "title": _clean_text(tool.title, MAX_TITLE_CHARS),
        }
    )


async def _list_all_tools(session: ClientSession) -> list[mcp_types.Tool]:
    tools: list[mcp_types.Tool] = []
    cursor: str | None = None
    for _ in range(50):  # bounded pagination
        page = await session.list_tools(
            params=mcp_types.PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor or len(tools) >= MAX_TOOLS_PER_SERVER:
            break
    return [sanitize_tool(t) for t in tools[:MAX_TOOLS_PER_SERVER]]


def _leaf(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__


def _classify(exc: BaseException) -> UpstreamError:  # noqa: PLR0911 - flat dispatch table
    """Map transport/protocol failures to safe errors; upstream URLs never reach the client."""
    if isinstance(exc, BaseExceptionGroup):
        # The SDK runs its transport in task groups; the first leaf is the root cause.
        return (
            _classify(exc.exceptions[0])
            if exc.exceptions
            else UpstreamUnavailableError("The MCP server is unavailable.")
        )
    if isinstance(exc, UpstreamError):
        return exc
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return UpstreamTimeoutError("The MCP server did not respond in time.")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return UpstreamAuthError(
                f"The MCP server refused the request as unauthorized (HTTP {status}). "
                "Check the connector's credential."
            )
        if status >= 500 or status == 429:
            return UpstreamUnavailableError(f"The MCP server is unavailable (HTTP {status}).")
        return UpstreamProtocolError(f"The MCP server refused the connection (HTTP {status}).")
    if isinstance(exc, (httpx.TransportError, anyio.ClosedResourceError, anyio.EndOfStream)):
        return UpstreamUnavailableError("The MCP server is unavailable.")
    if isinstance(exc, McpError):
        if exc.error.message == "Session terminated":  # endpoint answered 404
            return UpstreamProtocolError("The MCP server endpoint was not found.")
        message = exc.error.message[:200]
        return UpstreamRejectedError(f"The MCP server rejected the request: {message}")
    if isinstance(exc, (ValidationError, ValueError)):
        return UpstreamProtocolError("The MCP server returned an invalid response.")
    return UpstreamUnavailableError("The MCP server is unavailable.")
