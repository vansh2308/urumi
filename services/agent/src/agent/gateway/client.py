"""HTTP client for the gateway: identity, tool discovery and tool invocation.

The agent has no gateway credential of its own. Every request carries the end user's platform
API key, so the gateway applies that user's team membership, tool policy, member grants, key
scopes and key tool restrictions, rate-limits and audits the call under the user's key. The
agent's request id is forwarded as ``X-Request-ID``, so gateway audit and usage rows share it.

Only documented gateway routes are used:

* ``GET  /v1/me``
* ``GET  /v1/teams/{team_id}/connectors``
* ``GET  /v1/teams/{team_id}/connectors/{slug}/tools``
* ``POST /v1/teams/{team_id}/connectors/{slug}/tools/{tool}/call``
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from agent.config import GatewaySettings
from agent.context import REQUEST_ID_HEADER, current_request_id
from agent.errors import AuthenticationError, GatewayUnavailableError, PermissionDeniedError
from agent.metrics import Metrics

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_CHARS = 300


@dataclass(frozen=True, slots=True)
class Identity:
    """The caller as the gateway knows them. An API key is bound to exactly one team."""

    user_id: uuid.UUID
    email: str
    display_name: str
    team_id: uuid.UUID
    team_name: str
    role: str  # admin | member
    api_key_id: uuid.UUID

    @property
    def is_team_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True, slots=True)
class Caller:
    identity: Identity
    api_key: SecretStr  # forwarded to the gateway per request, never stored or logged


@dataclass(frozen=True, slots=True)
class GatewayConnector:
    slug: str
    display_name: str
    description: str


@dataclass(frozen=True, slots=True)
class GatewayTool:
    connector: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GatewayToolResponse:
    """Outcome of one invocation. ``result`` is the MCP ``CallToolResult`` on HTTP 200;
    otherwise ``error_code``/``error_message`` come from the gateway's error body."""

    http_status: int | None  # None when no response arrived (network error or timeout)
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


def build_gateway_http_client(settings: GatewaySettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.url,
        timeout=httpx.Timeout(
            settings.request_timeout_seconds, connect=settings.connect_timeout_seconds
        ),
        limits=httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        ),
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "urumi-agent"},
    )


class GatewayClient:
    def __init__(
        self, http: httpx.AsyncClient, *, settings: GatewaySettings, metrics: Metrics
    ) -> None:
        self._http = http
        self._settings = settings
        self._metrics = metrics

    async def whoami(self, api_key: SecretStr) -> Identity:
        """Verify the key with the gateway and return who it belongs to."""
        response = await self._get("identity", "/v1/me", api_key)
        body = _json_object(response)
        if body is None:
            raise self._bad_response("identity")
        teams = body.get("teams")
        user = body.get("user")
        if (
            body.get("credential") != "api_key"
            or not isinstance(teams, list)
            or len(teams) != 1
            or not isinstance(user, dict)
        ):
            # A key always belongs to exactly one team; anything else is not an API key.
            raise AuthenticationError("Use a platform API key (urk_...).")
        team = teams[0]
        try:
            return Identity(
                user_id=uuid.UUID(str(user["id"])),
                email=str(user["email"]),
                display_name=str(user["display_name"]),
                team_id=uuid.UUID(str(team["team_id"])),
                team_name=str(team["team_name"]),
                role=str(team["role"]),
                api_key_id=uuid.UUID(str(body["api_key_id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._bad_response("identity") from exc

    async def list_connectors(self, caller: Caller) -> list[GatewayConnector]:
        """Connectors of the caller's team on which the caller may use at least one tool."""
        team_id = caller.identity.team_id
        response = await self._get(
            "list_connectors", f"/v1/teams/{team_id}/connectors", caller.api_key
        )
        body = response.json() if _is_json(response) else None
        if not isinstance(body, list):
            raise self._bad_response("list_connectors")
        connectors: list[GatewayConnector] = []
        for item in body:
            if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                continue
            access = item.get("your_access") or {}
            if not (access.get("allow_all_tools") or access.get("tools")):
                continue
            connectors.append(
                GatewayConnector(
                    slug=item["slug"],
                    display_name=str(item.get("display_name") or item["slug"]),
                    description=str(item.get("description") or ""),
                )
            )
        return connectors

    async def list_tools(self, caller: Caller, slug: str) -> list[GatewayTool]:
        """MCP tool definitions on ``slug`` that the caller may invoke (filtered by the gateway)."""
        team_id = caller.identity.team_id
        path = f"/v1/teams/{team_id}/connectors/{quote(slug, safe='')}/tools"
        response = await self._get("list_tools", path, caller.api_key)
        body = response.json() if _is_json(response) else None
        if not isinstance(body, list):
            raise self._bad_response("list_tools")
        tools: list[GatewayTool] = []
        for item in body:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            schema = item.get("inputSchema")
            tools.append(
                GatewayTool(
                    connector=slug,
                    name=item["name"],
                    title=item.get("title") if isinstance(item.get("title"), str) else None,
                    description=str(item.get("description") or ""),
                    input_schema=schema if isinstance(schema, dict) else {"type": "object"},
                )
            )
        return tools

    async def call_tool(
        self, caller: Caller, slug: str, tool: str, arguments: dict[str, Any]
    ) -> GatewayToolResponse:
        """Invoke a tool. Never raises for gateway or upstream failures: they are returned so
        the agent can report them to the model and the user truthfully."""
        team_id = caller.identity.team_id
        path = (
            f"/v1/teams/{team_id}/connectors/{quote(slug, safe='')}"
            f"/tools/{quote(tool, safe='')}/call"
        )
        try:
            response = await self._http.post(
                path,
                json={"arguments": arguments},
                headers=self._headers(caller.api_key),
                timeout=httpx.Timeout(
                    self._settings.tool_call_timeout_seconds,
                    connect=self._settings.connect_timeout_seconds,
                ),
            )
        except httpx.TimeoutException:
            self._metrics.gateway_failures.labels(operation="call_tool", reason="timeout").inc()
            return GatewayToolResponse(
                http_status=None,
                error_code="gateway_timeout",
                error_message="The gateway did not answer in time; the call may or may not "
                "have been applied.",
            )
        except httpx.HTTPError:
            self._metrics.gateway_failures.labels(operation="call_tool", reason="network").inc()
            return GatewayToolResponse(
                http_status=None,
                error_code="gateway_unavailable",
                error_message="The gateway could not be reached; the call was not made.",
                retryable=True,
            )

        if response.status_code == 200:
            body = _json_object(response)
            if body is None:
                return GatewayToolResponse(
                    http_status=200,
                    error_code="malformed_tool_result",
                    error_message="The tool returned a response that is not a valid result.",
                )
            return GatewayToolResponse(http_status=200, result=body)

        code, message, retryable = _error_fields(response)
        if response.status_code >= 500:
            self._metrics.gateway_failures.labels(
                operation="call_tool", reason=str(response.status_code)
            ).inc()
        return GatewayToolResponse(
            http_status=response.status_code,
            error_code=code,
            error_message=message,
            retryable=retryable,
        )

    # --- internals ------------------------------------------------------------------------------

    def _headers(self, api_key: SecretStr) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key.get_secret_value()}"}
        if (rid := current_request_id()) is not None:
            headers[REQUEST_ID_HEADER] = rid
        return headers

    async def _get(self, operation: str, path: str, api_key: SecretStr) -> httpx.Response:
        try:
            response = await self._http.get(path, headers=self._headers(api_key))
        except httpx.TimeoutException as exc:
            self._metrics.gateway_failures.labels(operation=operation, reason="timeout").inc()
            raise GatewayUnavailableError("The gateway did not answer in time.") from exc
        except httpx.HTTPError as exc:
            self._metrics.gateway_failures.labels(operation=operation, reason="network").inc()
            raise GatewayUnavailableError("The gateway could not be reached.") from exc
        if response.status_code == 200:
            return response
        code, message, _ = _error_fields(response)
        if response.status_code == 401:
            raise AuthenticationError()
        if response.status_code == 403:
            raise PermissionDeniedError(message or "The gateway denied the request.")
        self._metrics.gateway_failures.labels(
            operation=operation, reason=str(response.status_code)
        ).inc()
        logger.warning(
            "gateway request failed",
            extra={"operation": operation, "status": response.status_code, "code": code},
        )
        if response.status_code == 429:
            raise GatewayUnavailableError("The gateway rate limit for this API key was reached.")
        raise GatewayUnavailableError("The gateway could not complete the request.")

    def _bad_response(self, operation: str) -> GatewayUnavailableError:
        self._metrics.gateway_failures.labels(operation=operation, reason="bad_response").inc()
        return GatewayUnavailableError("The gateway returned an unexpected response.")


def _is_json(response: httpx.Response) -> bool:
    content_type: str = response.headers.get("content-type", "")
    return content_type.startswith("application/json")


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    if not _is_json(response):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _error_fields(response: httpx.Response) -> tuple[str, str, bool]:
    """``(code, message, retryable)`` from the gateway's ``{"error": {...}}`` body."""
    fallback = (f"http_{response.status_code}", f"Gateway returned HTTP {response.status_code}.")
    body = _json_object(response)
    error = body.get("error") if body else None
    if not isinstance(error, dict):
        return (*fallback, response.status_code >= 500)
    code = error.get("code")
    message = error.get("message")
    return (
        code[:64] if isinstance(code, str) else fallback[0],
        message[:_MAX_ERROR_MESSAGE_CHARS] if isinstance(message, str) else fallback[1],
        bool(error.get("retryable", False)),
    )
