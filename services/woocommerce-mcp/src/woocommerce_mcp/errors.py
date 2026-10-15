"""Application exceptions.

Every exception here carries a stable machine-readable ``code`` and a ``public_message`` that is
safe to return to an MCP client: it never contains credentials, signed URLs, SQL or stack traces.
The transport layer translates these into MCP tool errors; anything else becomes a generic error.
"""

from __future__ import annotations

from typing import ClassVar


class AppError(Exception):
    code: ClassVar[str] = "internal_error"
    retryable: ClassVar[bool] = False

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class InvalidInputError(AppError):
    code = "invalid_input"


class NotFoundError(AppError):
    code = "not_found"


class IdempotencyConflictError(AppError):
    """The idempotency key was already used with a different request."""

    code = "idempotency_conflict"


class OperationInProgressError(AppError):
    """Another request with the same idempotency key is still running."""

    code = "operation_in_progress"
    retryable = True


class OutcomeUnknownError(AppError):
    """A non-idempotent write may or may not have been applied upstream.

    Retrying blindly could duplicate the write, so the caller must verify the store state first.
    """

    code = "outcome_unknown"


class UpstreamError(AppError):
    """Base class for failures talking to WooCommerce."""

    code = "upstream_error"


class UpstreamRejectedError(UpstreamError):
    """WooCommerce refused the request as invalid (HTTP 400/409/422)."""

    code = "upstream_rejected"

    def __init__(self, public_message: str, *, upstream_code: str | None = None) -> None:
        super().__init__(public_message)
        self.upstream_code = upstream_code


class UpstreamAuthError(UpstreamError):
    code = "upstream_auth_failed"


class UpstreamRateLimitedError(UpstreamError):
    code = "upstream_rate_limited"
    retryable = True


class UpstreamUnavailableError(UpstreamError):
    code = "upstream_unavailable"
    retryable = True


class UpstreamTimeoutError(UpstreamError):
    code = "upstream_timeout"
    retryable = True


class UpstreamProtocolError(UpstreamError):
    """WooCommerce answered with something that is not the expected JSON shape."""

    code = "upstream_bad_response"


class PersistenceUnavailableError(AppError):
    code = "persistence_unavailable"
    retryable = True
