"""Composition root: builds the object graph once per process and owns resource lifetimes.

Nothing here is a global; ``create_app`` builds a container and passes its parts explicitly to
the transport layer. Constructing the HTTP client and engine is synchronous and opens no
connections, so the container can be built before the event loop starts serving.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from woocommerce_mcp import __version__
from woocommerce_mcp.config import Settings, WooCommerceAuthMethod, WooCommerceSettings
from woocommerce_mcp.db.engine import create_engine, create_session_factory
from woocommerce_mcp.db.operations import OperationStore
from woocommerce_mcp.services import OperationRunner, OrderService, ProductService
from woocommerce_mcp.woocommerce import (
    BasicAuthenticator,
    OAuth1Authenticator,
    RequestAuthenticator,
    RetryPolicy,
    WooCommerceClient,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    http_client: httpx.AsyncClient
    engine: AsyncEngine
    woocommerce: WooCommerceClient
    operation_store: OperationStore
    operations: OperationRunner
    products: ProductService
    orders: OrderService

    async def aclose(self) -> None:
        """Release pooled connections. Called once, after in-flight requests have drained."""
        try:
            await self.http_client.aclose()
        finally:
            await self.engine.dispose()
        logger.info("resources released")


def build_http_client(settings: WooCommerceSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.write_timeout_seconds,
            pool=settings.pool_timeout_seconds,
        ),
        limits=httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        ),
        verify=settings.verify_tls,
        follow_redirects=False,  # a redirect would invalidate the OAuth signature / leak auth
        headers={"User-Agent": f"woocommerce-mcp/{__version__}"},
        trust_env=False,
    )


def build_authenticator(settings: WooCommerceSettings) -> RequestAuthenticator:
    if settings.auth_method is WooCommerceAuthMethod.BASIC:
        return BasicAuthenticator(settings.consumer_key, settings.consumer_secret)
    return OAuth1Authenticator(
        settings.consumer_key,
        settings.consumer_secret,
        signature_method=settings.oauth_signature_method,
    )


def build_container(settings: Settings) -> Container:
    wc = settings.woocommerce
    http_client = build_http_client(wc)
    woocommerce = WooCommerceClient(
        http_client,
        api_base_url=wc.api_base_url,
        authenticator=build_authenticator(wc),
        retry_policy=RetryPolicy(
            max_attempts=wc.max_attempts,
            backoff_base_seconds=wc.retry_backoff_base_seconds,
            backoff_max_seconds=wc.retry_backoff_max_seconds,
        ),
    )
    engine = create_engine(settings.database, application_name=settings.app.service_name)
    store = OperationStore(create_session_factory(engine))
    runner = OperationRunner(
        store,
        retention=timedelta(hours=settings.operations.retention_hours),
        in_progress_lease=timedelta(seconds=settings.operations.in_progress_lease_seconds),
    )
    return Container(
        settings=settings,
        http_client=http_client,
        engine=engine,
        woocommerce=woocommerce,
        operation_store=store,
        operations=runner,
        products=ProductService(woocommerce, runner, max_page_size=wc.max_page_size),
        orders=OrderService(woocommerce, runner, max_page_size=wc.max_page_size),
    )
