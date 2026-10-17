"""Request dependencies: container access and caller authentication.

The agent accepts the same platform API keys (``urk_...``) the gateway issues, because the
gateway only lets API keys invoke tools. Each request's key is verified by the gateway
(``/v1/me``) and then forwarded on every gateway call made for that request; it is never stored
or logged, and the agent has no credential of its own that could bypass the user's permissions.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from agent.container import Container
from agent.errors import AuthenticationError
from agent.gateway.client import Caller

API_PREFIX = "/v1"
_API_KEY_PREFIX = "urk_"
_MAX_TOKEN_CHARS = 256

_bearer = HTTPBearer(
    auto_error=False,
    description="A gateway platform API key (urk_...) with the tools:read and tools:call scopes.",
)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_caller(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> Caller:
    if credentials is None:
        raise AuthenticationError()
    raw = credentials.credentials
    if not raw.startswith(_API_KEY_PREFIX) or len(raw) > _MAX_TOKEN_CHARS:
        # Session tokens (ugs_) cannot invoke tools at the gateway, so they are refused here.
        raise AuthenticationError("Use a platform API key (urk_...).")
    api_key = SecretStr(raw)
    identity = await container.gateway.whoami(api_key)
    return Caller(identity=identity, api_key=api_key)


CallerDep = Annotated[Caller, Depends(get_caller)]
