"""The OAuth endpoints, assembled from the MCP SDK's handlers.

Equivalent to ``mcp.server.auth.routes.create_auth_routes`` plus the RFC 9728 protected
resource metadata for ``/v1/mcp``, with two gateway additions: per-IP rate limits on the
unauthenticated endpoints, and (local clusters only) a plain-HTTP issuer, which the SDK's
helper refuses for anything but localhost.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.routes import build_metadata, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, request_response
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.config import OAuthSettings
from gateway.errors import RateLimitedError
from gateway.oauth.service import DEFAULT_SCOPES, SCOPES, OAuthService
from gateway.services.rate_limit import RateLimiter

DOCS_URL = "https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization"


def validate_issuer(settings: OAuthSettings) -> AnyHttpUrl:
    url = AnyHttpUrl(settings.issuer_url.rstrip("/"))
    host = url.host or ""
    insecure = url.scheme != "https" and host not in {"localhost", "127.0.0.1"}
    if insecure and not settings.allow_http_issuer:
        raise ValueError("OAUTH_ISSUER_URL must use HTTPS (or set OAUTH_ALLOW_HTTP_ISSUER).")
    if url.query or url.fragment or urlsplit(str(url)).path not in {"", "/"}:
        raise ValueError("OAUTH_ISSUER_URL must be a bare origin, e.g. https://gateway.example.com")
    return url


class _RateLimited:
    """Count each request against a per-IP budget before the SDK handler sees it."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter, limit: int) -> None:
        self._app = app
        self._limiter = limiter
        self._limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") != "OPTIONS":
            client = scope.get("client")
            ip = client[0] if client else "unknown"
            try:
                await self._limiter.hit(f"oauth:{ip}", limit=self._limit, scope="oauth")
            except RateLimitedError as exc:
                response = JSONResponse(
                    {"error": "temporarily_unavailable", "error_description": "Too many requests."},
                    status_code=429,
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def _cors(app: ASGIApp, methods: list[str]) -> ASGIApp:
    # Browser-based clients (e.g. the MCP Inspector) call these endpoints cross-origin. No
    # cookies are involved, so allowing any origin exposes nothing.
    return CORSMiddleware(app, allow_origins=["*"], allow_methods=methods, allow_headers=["*"])


def build_oauth_routes(
    service: OAuthService, settings: OAuthSettings, limiter: RateLimiter
) -> list[Route]:
    issuer = validate_issuer(settings)
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=SCOPES, default_scopes=DEFAULT_SCOPES
    )
    revocation = RevocationOptions(enabled=True)
    metadata = build_metadata(issuer, AnyHttpUrl(DOCS_URL), registration, revocation)
    authenticator = ClientAuthenticator(service)

    def limited(handler: ASGIApp) -> ASGIApp:
        return _RateLimited(handler, limiter, settings.requests_per_minute)

    routes = [
        Route(
            "/.well-known/oauth-authorization-server",
            _cors(request_response(MetadataHandler(metadata).handle), ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        ),
        Route(
            "/authorize",
            limited(request_response(AuthorizationHandler(service).handle)),
            methods=["GET", "POST"],
        ),
        Route(
            "/token",
            _cors(
                limited(request_response(TokenHandler(service, authenticator).handle)),
                ["POST", "OPTIONS"],
            ),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/register",
            _cors(
                limited(
                    request_response(RegistrationHandler(service, options=registration).handle)
                ),
                ["POST", "OPTIONS"],
            ),
            methods=["POST", "OPTIONS"],
        ),
        Route(
            "/revoke",
            _cors(
                limited(request_response(RevocationHandler(service, authenticator).handle)),
                ["POST", "OPTIONS"],
            ),
            methods=["POST", "OPTIONS"],
        ),
    ]
    routes += create_protected_resource_routes(
        resource_url=AnyHttpUrl(service.resource_url),
        authorization_servers=[issuer],
        scopes_supported=SCOPES,
        resource_name="Urumi MCP gateway",
    )
    return routes
