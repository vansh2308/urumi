"""Pure-ASGI middleware (no ``BaseHTTPMiddleware``, which breaks streaming and context vars).

* :class:`RequestContextMiddleware` assigns the request id, binds it and the client IP to
  context variables, echoes ``X-Request-ID`` and records HTTP metrics.
* :class:`BodySizeLimitMiddleware` rejects request bodies over the configured size.
"""

from __future__ import annotations

import json
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gateway.context import (
    REQUEST_ID_HEADER,
    accept_request_id,
    client_ip_var,
    request_id_var,
)
from gateway.metrics import Metrics


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = accept_request_id(headers.get(REQUEST_ID_HEADER)) or uuid.uuid4().hex
        client = scope.get("client")
        rid_token = request_id_var.set(request_id)
        ip_token = client_ip_var.set(client[0] if client else None)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope["method"]
            self.metrics.http_requests.labels(method=method, route=template, status=status).inc()
            self.metrics.http_latency.labels(method=method, route=template).observe(
                time.perf_counter() - started
            )
            client_ip_var.reset(ip_token)
            request_id_var.reset(rid_token)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await _too_large(send)


class _BodyTooLargeError(Exception):
    pass


async def _too_large(send: Send) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": "Request body is too large.",
                "request_id": request_id_var.get(),
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"connection", b"close")],
        }
    )
    await send({"type": "http.response.body", "body": body})
