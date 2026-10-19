"""Application exceptions.

Every exception carries a stable machine-readable ``code``, the HTTP status it maps to and a
``public_message`` that is safe to return to a client: it never contains credentials, upstream
URLs, SQL or stack traces. The API layer renders these as ``{"error": {...}}``; the MCP proxy
renders them as ``<code>: <message>`` tool errors, the same shape the upstream servers use.
"""

from __future__ import annotations

from typing import ClassVar


class AppError(Exception):
    code: ClassVar[str] = "internal_error"
    status_code: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class InvalidInputError(AppError):
    code = "invalid_input"
    status_code = 422


class AuthenticationError(AppError):
    """Missing, malformed, expired or revoked credentials. Always the same vague message."""

    code = "unauthenticated"
    status_code = 401

    def __init__(self, public_message: str = "Authentication required.") -> None:
        super().__init__(public_message)


class PermissionDeniedError(AppError):
    code = "forbidden"
    status_code = 403


class SignupDisabledError(PermissionDeniedError):
    code = "signup_disabled"

    def __init__(self) -> None:
        super().__init__("Self-registration is disabled; ask a team admin for an invitation.")


class NotFoundError(AppError):
    """Also used for resources in another team, so their existence is not revealed."""

    code = "not_found"
    status_code = 404


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    retryable = True

    def __init__(self, public_message: str, *, retry_after_seconds: int, limit: int) -> None:
        super().__init__(public_message)
        self.retry_after_seconds = retry_after_seconds
        self.limit = limit


class PersistenceUnavailableError(AppError):
    code = "persistence_unavailable"
    status_code = 503
    retryable = True


class UpstreamError(AppError):
    """Base class for failures talking to a registered MCP server."""

    code = "upstream_error"
    status_code = 502


class UpstreamUnavailableError(UpstreamError):
    code = "upstream_unavailable"
    status_code = 502
    retryable = True


class UpstreamTimeoutError(UpstreamError):
    code = "upstream_timeout"
    status_code = 504
    retryable = True


class UpstreamBusyError(UpstreamError):
    """This replica already has the connector's maximum number of calls in flight."""

    code = "upstream_busy"
    status_code = 503
    retryable = True


class UpstreamProtocolError(UpstreamError):
    """The upstream answered with something that is not valid MCP."""

    code = "upstream_bad_response"
    status_code = 502


class UpstreamRejectedError(UpstreamError):
    """The upstream returned a JSON-RPC error (unknown tool, invalid params, ...)."""

    code = "upstream_rejected"
    status_code = 502


class UpstreamBlockedError(UpstreamError):
    """The connector's host resolves to an address the gateway may not connect to."""

    code = "upstream_blocked"
    status_code = 502


class UpstreamAuthError(UpstreamError):
    """The upstream refused the gateway's credential (HTTP 401/403), or it cannot be used."""

    code = "upstream_unauthorized"
    status_code = 502
