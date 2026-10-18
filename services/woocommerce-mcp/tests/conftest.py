"""Shared fixtures.

Tests never read the service's ``.env`` and never reach a real store or database:

* WooCommerce is an in-process fake behind ``httpx.MockTransport``;
* PostgreSQL is a throwaway database created for the test session on the server named by
  ``TEST_DATABASE_URL`` (a local container, see the README) and dropped afterwards. Tests that
  need it are skipped when the variable is unset, and refuse to run against a non-local host.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config

from woocommerce_mcp.config import (
    AppSettings,
    DatabaseSettings,
    OperationSettings,
    ServerSettings,
    Settings,
    WooCommerceSettings,
)

SERVICE_DIR = Path(__file__).resolve().parents[1]
STORE_URL = "http://store.test"
_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


# --- PostgreSQL -----------------------------------------------------------------------------------


def _admin_dsn() -> str:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL is not set; see 'Tests' in the README")
    parts = urlsplit(raw.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parts.hostname not in _LOCAL_DB_HOSTS:
        pytest.fail("TEST_DATABASE_URL must point at a local, disposable PostgreSQL server")
    return urlunsplit(parts)


def _with_database(dsn: str, name: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A freshly migrated database for this test session (asyncpg URL)."""
    admin = _admin_dsn()
    name = f"wcmcp_test_{uuid.uuid4().hex[:12]}"

    async def admin_exec(sql: str) -> None:
        conn = await asyncpg.connect(admin)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(admin_exec(f'CREATE DATABASE "{name}"'))
    url = _with_database(admin, name).replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        _migrate(url, tmp_path_factory.mktemp("alembic"))
        yield url
    finally:
        asyncio.run(admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _migrate(url: str, workdir: Path) -> None:
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_DIR / "alembic"))
    previous_cwd, previous_url = Path.cwd(), os.environ.get("DB_URL")
    # alembic/env.py reads DB_URL; running from an empty directory keeps a local .env out of it.
    os.environ["DB_URL"] = url
    os.chdir(workdir)
    try:
        command.upgrade(config, "head")
    finally:
        os.chdir(previous_cwd)
        if previous_url is None:
            os.environ.pop("DB_URL", None)
        else:
            os.environ["DB_URL"] = previous_url


@pytest.fixture
async def clean_operations(database_url: str) -> AsyncIterator[None]:
    yield
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("TRUNCATE operations")
    finally:
        await conn.close()


# --- settings -------------------------------------------------------------------------------------


def make_settings(database_url: str, **woocommerce: Any) -> Settings:
    wc: dict[str, Any] = {
        "base_url": STORE_URL,
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
        "allow_insecure_http": True,
        "retry_backoff_base_seconds": 0,
        "retry_backoff_max_seconds": 0,
    } | woocommerce
    return Settings(
        app=AppSettings(_env_file=None, log_level="WARNING", log_format="console"),
        server=ServerSettings(_env_file=None),
        woocommerce=WooCommerceSettings(_env_file=None, **wc),
        database=DatabaseSettings(_env_file=None, url=database_url, pool_size=2),
        operations=OperationSettings(_env_file=None, purge_interval_seconds=0),
    )


# --- fake WooCommerce -----------------------------------------------------------------------------

_ORDER_PATH = re.compile(r"^/wp-json/wc/v3/orders/(\d+)$")


def product_json(product_id: int, **overrides: Any) -> dict[str, Any]:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "type": "simple",
        "status": "publish",
        "regular_price": "10.00",
        "price": "10.00",
        "billing_phone": "+1 555 0100",  # not part of the response model; must be dropped
    } | overrides


def order_json(order_id: int, status: str = "processing") -> dict[str, Any]:
    return {
        "id": order_id,
        "number": str(order_id),
        "status": status,
        "currency": "USD",
        "total": "25.00",
        "billing": {"first_name": "Ada", "email": "ada@example.com", "phone": "+1 555 0101"},
        "line_items": [{"id": 1, "name": "Mug", "quantity": 2, "total": "25.00"}],
    }


@dataclass
class FakeStore:
    """Minimal WooCommerce REST API. Records every request; ``fail_next`` queues error statuses."""

    products: list[dict[str, Any]] = field(default_factory=lambda: [product_json(1)])
    orders: dict[int, dict[str, Any]] = field(default_factory=lambda: {7: order_json(7)})
    requests: list[httpx.Request] = field(default_factory=list)
    fail_next: list[int] = field(default_factory=list)

    def calls(self, method: str, path_suffix: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(path_suffix)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        query = dict(parse_qsl(request.url.query.decode()))
        if not {"oauth_consumer_key", "oauth_signature", "oauth_nonce"} <= query.keys():
            return _error(401, "woocommerce_rest_authentication_error", "Missing OAuth parameters")
        if self.fail_next:
            return _error(self.fail_next.pop(0), "upstream_failure", "Injected failure")
        path = request.url.path
        if path == "/wp-json/wc/v3/products":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json=self.products,
                    headers={"X-WP-Total": str(len(self.products)), "X-WP-TotalPages": "1"},
                )
            body = json.loads(request.content)
            created = product_json(100 + len(self.products), **body)
            self.products.append(created)
            return httpx.Response(201, json=created)
        if match := _ORDER_PATH.match(path):
            order = self.orders.get(int(match.group(1)))
            if order is None:
                return _error(404, "woocommerce_rest_shop_order_invalid_id", "Invalid ID.")
            if request.method == "PUT":
                order["status"] = json.loads(request.content)["status"]
            return httpx.Response(200, json=order)
        return _error(404, "rest_no_route", "No route was found.")


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message, "data": {}})


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def store_http(store: FakeStore) -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(store.handler))

    return factory
