"""WooCommerce REST client: error mapping and the retry policy for reads and writes."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from tests.conftest import STORE_URL, FakeStore
from woocommerce_mcp.errors import (
    NotFoundError,
    OutcomeUnknownError,
    UpstreamAuthError,
    UpstreamProtocolError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from woocommerce_mcp.woocommerce import OAuth1Authenticator, RetryPolicy, WooCommerceClient
from woocommerce_mcp.woocommerce.models import NewProduct, OrderStatus, ProductQuery


def _client(
    handler: httpx.MockTransport | None = None, store: FakeStore | None = None
) -> WooCommerceClient:
    transport = handler or httpx.MockTransport((store or FakeStore()).handler)
    return WooCommerceClient(
        httpx.AsyncClient(transport=transport),
        api_base_url=f"{STORE_URL}/wp-json/wc/v3",
        authenticator=OAuth1Authenticator(SecretStr("ck_test"), SecretStr("cs_test")),
        retry_policy=RetryPolicy(max_attempts=3, backoff_base_seconds=0, backoff_max_seconds=0),
    )


def _new_product() -> NewProduct:
    return NewProduct(name="Mug", regular_price=Decimal("12.50"))


async def test_list_products_returns_typed_page_without_extra_fields() -> None:
    store = FakeStore()

    page = await _client(store=store).list_products(ProductQuery(per_page=5, search="mug"))

    assert [p.id for p in page.items] == [1]
    assert (page.total, page.total_pages, page.per_page) == (1, 1, 5)
    assert "billing_phone" not in page.items[0].model_dump()
    sent = store.requests[0].url.params
    assert sent["search"] == "mug"
    assert sent["per_page"] == "5"
    assert "id" in sent["_fields"].split(",")


async def test_reads_are_retried_after_transient_failures() -> None:
    store = FakeStore(fail_next=[503, 502])

    page = await _client(store=store).list_products(ProductQuery())

    assert len(page.items) == 1
    assert len(store.calls("GET")) == 3


async def test_reads_give_up_after_max_attempts() -> None:
    store = FakeStore(fail_next=[503, 503, 503])

    with pytest.raises(UpstreamUnavailableError):
        await _client(store=store).list_products(ProductQuery())
    assert len(store.calls("GET")) == 3


async def test_create_is_not_resent_when_the_store_may_have_applied_it() -> None:
    store = FakeStore(fail_next=[503])

    with pytest.raises(OutcomeUnknownError):
        await _client(store=store).create_product(_new_product())
    assert len(store.calls("POST")) == 1


async def test_create_is_retried_when_the_request_never_reached_the_store() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            201, json={"id": 5, "name": "Mug", "type": "simple", "status": "draft"}
        )

    product = await _client(httpx.MockTransport(handler)).create_product(_new_product())

    assert product.id == 5
    assert attempts == 2


async def test_create_sends_a_simple_product_payload() -> None:
    store = FakeStore()

    await _client(store=store).create_product(
        NewProduct(name="Mug", regular_price=Decimal("12.5"), sku="MUG-1", category_ids=[3])
    )

    body = httpx.Response(200, content=store.calls("POST")[0].content).json()
    assert body["type"] == "simple"
    assert body["status"] == "draft"
    assert body["regular_price"] == "12.50"
    assert body["categories"] == [{"id": 3}]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, {"code": "woocommerce_rest_shop_order_invalid_id"}, NotFoundError),
        (401, {"code": "woocommerce_rest_cannot_view"}, UpstreamAuthError),
        (403, {"code": "woocommerce_rest_cannot_view"}, UpstreamAuthError),
        (
            400,
            {"code": "rest_invalid_param", "message": "Invalid parameter(s): status"},
            UpstreamRejectedError,
        ),
    ],
)
async def test_error_statuses_map_to_application_errors(
    status: int, body: dict[str, str], error: type[Exception]
) -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(status, json=body)))

    with pytest.raises(error) as raised:
        await client.get_order(7)
    if status == 400:
        assert raised.value.public_message == "Invalid parameter(s): status"  # type: ignore[attr-defined]


async def test_non_json_success_is_a_protocol_error() -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200, text="<html>login</html>")))

    with pytest.raises(UpstreamProtocolError):
        await client.get_order(7)


async def test_update_order_status_puts_the_absolute_status() -> None:
    store = FakeStore()

    order = await _client(store=store).update_order_status(7, OrderStatus.COMPLETED)

    assert order.status == "completed"
    assert httpx.Response(200, content=store.calls("PUT")[0].content).json() == {
        "status": "completed"
    }


def test_retry_after_is_honoured_but_capped() -> None:
    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.25, backoff_max_seconds=4.0)

    assert policy.delay(1, retry_after=2.0) == 2.0
    assert policy.delay(1, retry_after=60.0) == 4.0
    assert 0 <= policy.delay(5) <= 4.0


async def test_connect_url_routes_the_connection_but_signs_the_canonical_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[], headers={"X-WP-Total": "0"})

    secret = "cs_test"
    client = WooCommerceClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base_url="http://acme.localtest.me/wp-json/wc/v3",
        authenticator=OAuth1Authenticator(SecretStr("ck_test"), SecretStr(secret)),
        retry_policy=RetryPolicy(max_attempts=1),
        connect_url="http://store-wordpress.store-acme.svc.cluster.local:80",
    )

    await client.list_products(ProductQuery())

    sent = seen[0]
    assert sent.url.host == "store-wordpress.store-acme.svc.cluster.local"
    assert sent.url.path == "/wp-json/wc/v3/products"
    assert sent.headers["Host"] == "acme.localtest.me"
    params = dict(sent.url.params)
    expected = OAuth1Authenticator(SecretStr("ck_test"), SecretStr(secret)).signature(
        "GET", "http://acme.localtest.me/wp-json/wc/v3/products", params
    )
    assert params["oauth_signature"] == expected


async def test_without_connect_url_the_host_header_is_the_store_host() -> None:
    store = FakeStore()

    await _client(store=store).list_products(ProductQuery())

    assert store.requests[0].headers["Host"] == "store.test"
