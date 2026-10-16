"""Request dependencies: container access, authentication and rate limiting.

Every protected route depends on :func:`get_principal`, so credentials are verified and the
caller is rate limited on every request; authorization decisions are made in the services
using the principal and the team membership loaded fresh from the database.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gateway.container import Container
from gateway.errors import AuthenticationError, PermissionDeniedError
from gateway.security import TokenHasher, TokenKind
from gateway.services.principal import CredentialKind, Principal

API_PREFIX = "/v1"

_bearer = HTTPBearer(
    auto_error=False,
    description="A user session token (ugs_...) from /v1/auth/login, or a platform API key "
    "(urk_...) for tool discovery and invocation.",
)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_principal(
    request: Request,
    response: Response,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError()
    principal = await authenticate_bearer(container, credentials.credentials)
    status = await container.rate_limiter.hit(
        principal.rate_limit_bucket,
        limit=container.settings.rate_limit.per_principal_per_minute,
        scope=principal.credential.value,
    )
    if status is not None:
        response.headers["X-RateLimit-Limit"] = str(status.limit)
        response.headers["X-RateLimit-Remaining"] = str(status.remaining)
        response.headers["X-RateLimit-Reset"] = str(status.reset_seconds)
    request.state.principal = principal
    return principal


async def authenticate_bearer(container: Container, raw: str) -> Principal:
    kind = TokenHasher.kind_of(raw)
    try:
        if kind is TokenKind.SESSION:
            return await container.auth.authenticate_session(raw)
        raise AuthenticationError()
    except AuthenticationError:
        container.metrics.auth_failures.labels(kind=kind.name.lower() if kind else "unknown").inc()
        raise


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def require_session(principal: PrincipalDep) -> Principal:
    """Management endpoints accept user sessions only; API keys cannot manage anything."""
    if principal.credential is not CredentialKind.SESSION:
        raise PermissionDeniedError("This endpoint requires a user session, not an API key.")
    return principal


SessionPrincipalDep = Annotated[Principal, Depends(require_session)]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
