"""Request-scoped context variables.

They are set by the request middleware and are per-task in asyncio, so concurrent requests never
see each other's values. Tasks spawned while handling a request (for example the MCP client's
transport tasks) inherit a copy, which is how the request id reaches upstream calls.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")


def accept_request_id(value: str | None) -> str | None:
    """Return a client-supplied request id only if it is safe to log and forward."""
    if value and _REQUEST_ID_RE.match(value):
        return value
    return None


def current_request_id() -> str | None:
    return request_id_var.get()


def current_client_ip() -> str | None:
    return client_ip_var.get()
