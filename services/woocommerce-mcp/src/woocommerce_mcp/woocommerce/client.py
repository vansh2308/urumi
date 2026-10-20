"""Asynchronous WooCommerce REST API (``/wp-json/wc/v3``) client.

Independent of MCP: it takes an injected ``httpx.AsyncClient`` and authenticator, returns typed
models and raises :mod:`woocommerce_mcp.errors` exceptions. It holds no per-request mutable state,
so one instance is shared by all concurrent requests.

Retry policy
------------
* A failure that happened before the request reached the store (connect error/timeout) is retried
  for every method.
* HTTP 429 is retried for every method (the store did not process it), honouring ``Retry-After``.
* Read timeouts, dropped connections and 502/503/504 are retried only for idempotent requests
  (GET, and PUTs that set absolute values). For non-idempotent requests (POST create) they raise
  :class:`OutcomeUnknownError` because the store may already have applied the write.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ValidationError

from woocommerce_mcp.errors import (
    NotFoundError,
    OutcomeUnknownError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamProtocolError,
    UpstreamRateLimitedError,
    UpstreamRejectedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from woocommerce_mcp.woocommerce.auth import RequestAuthenticator, encode_query
from woocommerce_mcp.woocommerce.models import (
    ORDER_FIELDS,
    ORDER_SUMMARY_FIELDS,
    PRODUCT_FIELDS,
    NewProduct,
    Order,
    OrderQuery,
    OrderStatus,
    OrderSummary,
    Page,
    Product,
    ProductQuery,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({502, 503, 504})
_MAX_UPSTREAM_MESSAGE = 300


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_base_seconds: float = 0.25
    backoff_max_seconds: float = 4.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.backoff_max_seconds)
        ceiling = min(self.backoff_max_seconds, self.backoff_base_seconds * 2 ** (attempt - 1))
        return random.uniform(0, ceiling)  # noqa: S311 - full jitter, not cryptographic


@dataclass(frozen=True, slots=True)
class _Response:
    body: Any
    headers: httpx.Headers


class _Retry(Exception):  # noqa: N818 - control-flow signal, never escapes
    """Internal signal: the attempt failed in a way that is safe to retry."""

    def __init__(self, error: UpstreamError, retry_after: float | None = None) -> None:
        self.error = error
        self.retry_after = retry_after


class WooCommerceClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_base_url: str,
        authenticator: RequestAuthenticator,
        retry_policy: RetryPolicy,
        connect_url: str | None = None,
    ) -> None:
        self._http = http
        self._api_base_url = api_base_url.rstrip("/")
        self._auth = authenticator
        self._retry = retry_policy
        # With a connect URL the request goes to that origin, but is signed for (and carries
        # the Host header of) the store's canonical URL, which is what WordPress checks.
        canonical = urlsplit(self._api_base_url)
        self._host_header: str | None = None
        self._send_base_url = self._api_base_url
        if connect_url is not None:
            target = urlsplit(connect_url)
            self._host_header = canonical.netloc
            self._send_base_url = urlunsplit((target.scheme, target.netloc, canonical.path, "", ""))

    # ------------------------------------------------------------------ products

    async def list_products(self, query: ProductQuery) -> Page[Product]:
        params = query.to_params() | {"_fields": ",".join(PRODUCT_FIELDS)}
        response = await self._request("GET", "/products", params=params, idempotent=True)
        return _page(response, Product, query.page, query.per_page)

    async def create_product(self, product: NewProduct) -> Product:
        response = await self._request(
            "POST", "/products", json=product.to_upstream(), idempotent=False
        )
        return _model(response.body, Product)

    # ------------------------------------------------------------------ orders

    async def list_orders(self, query: OrderQuery) -> Page[OrderSummary]:
        params = query.to_params() | {"_fields": ",".join(ORDER_SUMMARY_FIELDS)}
        response = await self._request("GET", "/orders", params=params, idempotent=True)
        return _page(response, OrderSummary, query.page, query.per_page)

    async def get_order(self, order_id: int) -> Order:
        response = await self._request(
            "GET",
            f"/orders/{_positive_id(order_id)}",
            params={"_fields": ",".join(ORDER_FIELDS)},
            idempotent=True,
            not_found=f"Order {order_id} was not found.",
        )
        return _model(response.body, Order)

    async def update_order_status(self, order_id: int, status: OrderStatus) -> Order:
        # Setting an absolute status is idempotent, so transient failures are retried.
        response = await self._request(
            "PUT",
            f"/orders/{_positive_id(order_id)}",
            params={"_fields": ",".join(ORDER_FIELDS)},
            json={"status": status.value},
            idempotent=True,
            not_found=f"Order {order_id} was not found.",
        )
        return _model(response.body, Order)

    # ------------------------------------------------------------------ health

    async def ping(self) -> None:
        """Cheap authenticated call used by the readiness probe."""
        await self._request(
            "GET",
            "/products",
            params={"per_page": "1", "_fields": "id"},
            idempotent=True,
            max_attempts=1,
        )

    # ------------------------------------------------------------------ transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        idempotent: bool,
        not_found: str = "The requested WooCommerce resource was not found.",
        max_attempts: int | None = None,
    ) -> _Response:
        attempts = max_attempts or self._retry.max_attempts
        url = f"{self._api_base_url}{path}"
        send_url = f"{self._send_base_url}{path}"
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                return await self._attempt(
                    method, url, send_url, params or {}, json, idempotent, not_found
                )
            except _Retry as retry:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                if attempt >= attempts:
                    logger.warning(
                        "woocommerce request failed, giving up",
                        extra={
                            "method": method,
                            "path": path,
                            "attempt": attempt,
                            "error_code": retry.error.code,
                            "cause": type(retry.__cause__).__name__,
                            "duration_ms": elapsed_ms,
                        },
                    )
                    raise retry.error from retry
                delay = self._retry.delay(attempt, retry.retry_after)
                logger.info(
                    "woocommerce request failed, retrying",
                    extra={
                        "method": method,
                        "path": path,
                        "attempt": attempt,
                        "error_code": retry.error.code,
                        "cause": type(retry.__cause__).__name__,
                        "retry_in_s": round(delay, 3),
                        "duration_ms": elapsed_ms,
                    },
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _attempt(
        self,
        method: str,
        url: str,
        send_url: str,
        params: Mapping[str, str],
        json: Mapping[str, Any] | None,
        idempotent: bool,
        not_found: str,
    ) -> _Response:
        signed = self._auth.authenticate(method, url, params)
        query = encode_query(signed.query)
        headers = {"Accept": "application/json", **signed.headers}
        if self._host_header is not None:
            headers["Host"] = self._host_header
        request = self._http.build_request(
            method,
            f"{send_url}?{query}" if query else send_url,
            headers=headers,
            json=json,
        )
        try:
            response = await self._http.send(request)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # The request never reached the store: always safe to retry.
            raise _Retry(UpstreamUnavailableError("WooCommerce store is unreachable.")) from exc
        except httpx.PoolTimeout as exc:
            raise UpstreamUnavailableError(
                "Too many concurrent WooCommerce requests; try again shortly."
            ) from exc
        except httpx.TimeoutException as exc:
            if idempotent:
                raise _Retry(UpstreamTimeoutError("WooCommerce did not respond in time.")) from exc
            raise _outcome_unknown() from exc
        except httpx.TransportError as exc:
            if idempotent:
                raise _Retry(UpstreamUnavailableError("Connection to WooCommerce failed.")) from exc
            raise _outcome_unknown() from exc

        return self._handle_response(response, idempotent=idempotent, not_found=not_found)

    def _handle_response(
        self, response: httpx.Response, *, idempotent: bool, not_found: str
    ) -> _Response:
        status = response.status_code
        if 200 <= status < 300:
            try:
                body = response.json() if response.content else None
            except ValueError as exc:
                raise UpstreamProtocolError("WooCommerce returned a non-JSON response.") from exc
            return _Response(body=body, headers=response.headers)

        upstream_code, upstream_message = _error_details(response)
        logger.info(
            "woocommerce returned an error",
            extra={"status_code": status, "upstream_code": upstream_code},
        )
        if status == 404 and upstream_code in {
            None,
            "woocommerce_rest_shop_order_invalid_id",
            "woocommerce_rest_product_invalid_id",
            "woocommerce_rest_invalid_id",
        }:
            raise NotFoundError(not_found)
        if status in {401, 403}:
            raise UpstreamAuthError(
                "WooCommerce rejected this server's API credentials or their permissions."
            )
        if status == 429:
            raise _Retry(
                UpstreamRateLimitedError("WooCommerce is rate limiting requests."),
                retry_after=_retry_after(response),
            )
        if status in _RETRYABLE_STATUS:
            error = UpstreamUnavailableError(f"WooCommerce is unavailable (HTTP {status}).")
            if idempotent:
                raise _Retry(error, retry_after=_retry_after(response))
            raise _outcome_unknown()
        if 400 <= status < 500:
            message = upstream_message or f"WooCommerce rejected the request (HTTP {status})."
            raise UpstreamRejectedError(message, upstream_code=upstream_code)
        if not idempotent:
            raise _outcome_unknown()
        raise UpstreamUnavailableError(
            f"WooCommerce failed to process the request (HTTP {status})."
        )


def _outcome_unknown() -> OutcomeUnknownError:
    return OutcomeUnknownError(
        "The request to WooCommerce failed after it was sent, so the write may or may not have "
        "been applied. Check the store before retrying."
    )


def _positive_id(value: int) -> int:
    if value < 1:
        raise ValueError("resource id must be a positive integer")
    return value


def _error_details(response: httpx.Response) -> tuple[str | None, str | None]:
    """Extract WooCommerce's ``{"code", "message"}`` error body, bounded in size."""
    try:
        body = response.json()
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    code = body.get("code")
    message = body.get("message")
    return (
        code[:100] if isinstance(code, str) else None,
        message[:_MAX_UPSTREAM_MESSAGE] if isinstance(message, str) else None,
    )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _model[M: BaseModel](body: Any, model: type[M]) -> M:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise UpstreamProtocolError(
            f"WooCommerce returned an unexpected {model.__name__} payload."
        ) from exc


def _page[M: BaseModel](response: _Response, model: type[M], page: int, per_page: int) -> Page[M]:
    if not isinstance(response.body, list):
        raise UpstreamProtocolError("WooCommerce returned an unexpected list payload.")
    return Page[M](
        items=[_model(item, model) for item in response.body],
        page=page,
        per_page=per_page,
        total=_int_header(response.headers, "X-WP-Total"),
        total_pages=_int_header(response.headers, "X-WP-TotalPages"),
    )


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
