"""Application exceptions.

Every exception carries a stable machine-readable ``code``, the HTTP status it maps to and a
``public_message`` that is safe to return to a client: it never contains credentials, provider
responses, gateway URLs, SQL or stack traces. The API layer renders these as
``{"error": {...}}``; inside a streamed chat they become an ``error`` event with the same body.
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
    """Missing, malformed, expired or revoked credentials. Always a vague message."""

    code = "unauthenticated"
    status_code = 401

    def __init__(self, public_message: str = "Authentication required.") -> None:
        super().__init__(public_message)


class PermissionDeniedError(AppError):
    code = "forbidden"
    status_code = 403


class NotFoundError(AppError):
    """Also used for conversations of other users or teams, so their existence is not revealed."""

    code = "not_found"
    status_code = 404


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    retryable = True

    def __init__(self, public_message: str, *, retry_after_seconds: int) -> None:
        super().__init__(public_message)
        self.retry_after_seconds = retry_after_seconds


class PersistenceUnavailableError(AppError):
    code = "persistence_unavailable"
    status_code = 503
    retryable = True


class GatewayUnavailableError(AppError):
    """The gateway could not be reached or answered with something unexpected."""

    code = "gateway_unavailable"
    status_code = 502
    retryable = True


class LlmError(AppError):
    """Base class for model provider failures. Provider error bodies are never exposed."""

    code = "llm_error"
    status_code = 502


class LlmUnavailableError(LlmError):
    code = "llm_unavailable"
    status_code = 503
    retryable = True


class LlmRateLimitedError(LlmError):
    code = "llm_rate_limited"
    status_code = 503
    retryable = True


class LlmTimeoutError(LlmError):
    code = "llm_timeout"
    status_code = 504
    retryable = True


class LlmRejectedError(LlmError):
    """The provider refused the request (bad request, safety block, misconfiguration)."""

    code = "llm_rejected"
    status_code = 502


class RunTimeoutError(AppError):
    code = "run_timeout"
    status_code = 504
