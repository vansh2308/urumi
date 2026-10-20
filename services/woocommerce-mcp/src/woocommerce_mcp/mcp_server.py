"""MCP transport layer: tool definitions, argument schemas and error translation.

Tool handlers only parse arguments, call a service and return its typed result. Business rules
live in :mod:`woocommerce_mcp.services`; WooCommerce access lives in
:mod:`woocommerce_mcp.woocommerce`.

This server performs no client authentication of its own. It is an upstream behind the platform
gateway, which authenticates callers and enforces per-user tool permissions; the endpoint must be
reachable only from the gateway (private network + NetworkPolicy), never publicly.
"""

import functools
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from woocommerce_mcp import __version__
from woocommerce_mcp.config import ServerSettings
from woocommerce_mcp.errors import AppError, InvalidInputError
from woocommerce_mcp.log import correlation_id_var, tool_name_var
from woocommerce_mcp.services import OrderService, OrderStatusChange, ProductService
from woocommerce_mcp.woocommerce.models import (
    NewProduct,
    Order,
    OrderQuery,
    OrderStatus,
    OrderSummary,
    Page,
    Product,
    ProductQuery,
    ProductStatus,
    SortOrder,
    StockStatus,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tools for one WooCommerce store. Read tools are safe to call freely. Write tools
(create_product, update_order_status) accept an optional idempotency_key: reuse the same key
when retrying the same request so the write is applied at most once. Errors are returned as
'<code>: <message>'; codes marked '(retryable)' may succeed if retried later."""

_CORRELATION_HEADERS = ("x-request-id", "x-correlation-id")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

IdempotencyKey = Annotated[
    str | None,
    Field(
        default=None,
        pattern=r"^[A-Za-z0-9._:\-]{8,128}$",
        description="Client-generated key (e.g. a UUID). Retrying with the same key and the same "
        "arguments returns the original result instead of repeating the write.",
    ),
]
PageNumber = Annotated[int, Field(ge=1, le=10_000, description="1-based page number.")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Results per page.")]
OrderId = Annotated[int, Field(gt=0, description="WooCommerce order id.")]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def build_mcp_server(
    settings: ServerSettings,
    *,
    products: ProductService,
    orders: OrderService,
    store_name: str | None = None,
) -> FastMCP[Any]:
    label = _store_label(store_name)
    mcp: FastMCP[Any] = FastMCP(
        name=f"woocommerce ({store_name})" if store_name else "woocommerce",
        instructions=_instructions(store_name),
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.path,
        stateless_http=settings.stateless_http,
        json_response=settings.json_response,
        max_request_body_size=settings.max_request_body_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=settings.allowed_origins,
        ),
    )
    mcp._mcp_server.version = __version__

    @mcp.tool(title="List products", annotations=READ_ONLY)
    @label
    @_tool_boundary
    async def list_products(
        ctx: Context[Any, Any, Any],
        page: PageNumber = 1,
        per_page: PageSize = 20,
        search: Annotated[str | None, Field(max_length=100, description="Text search.")] = None,
        sku: Annotated[str | None, Field(max_length=100, description="Exact SKU.")] = None,
        status: ProductStatus | None = None,
        stock_status: StockStatus | None = None,
        category_id: Annotated[int | None, Field(gt=0)] = None,
    ) -> Page[Product]:
        """List products, newest first, with optional filters. Returns one page and totals."""
        query = _parse(
            ProductQuery,
            page=page,
            per_page=per_page,
            search=search,
            sku=sku,
            status=status,
            stock_status=stock_status,
            category_id=category_id,
        )
        return await products.list_products(query)

    @mcp.tool(
        title="Create product",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    @label
    @_tool_boundary
    async def create_product(
        ctx: Context[Any, Any, Any],
        name: Annotated[str, Field(min_length=1, max_length=200)],
        regular_price: Annotated[
            Decimal, Field(ge=0, max_digits=14, decimal_places=2, description="e.g. 19.99")
        ],
        sale_price: Annotated[Decimal | None, Field(ge=0, max_digits=14, decimal_places=2)] = None,
        sku: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
        status: Annotated[
            ProductStatus,
            Field(description="Defaults to draft so nothing is published by accident."),
        ] = ProductStatus.DRAFT,
        description: Annotated[str, Field(max_length=20_000)] = "",
        short_description: Annotated[str, Field(max_length=2_000)] = "",
        manage_stock: bool = False,
        stock_quantity: Annotated[int | None, Field(ge=0, le=10_000_000)] = None,
        category_ids: Annotated[list[int] | None, Field(max_length=20)] = None,
        idempotency_key: IdempotencyKey = None,
    ) -> Product:
        """Create a simple product. Prices are in the store currency."""
        product = _parse(
            NewProduct,
            name=name,
            regular_price=regular_price,
            sale_price=sale_price,
            sku=sku,
            status=status,
            description=description,
            short_description=short_description,
            manage_stock=manage_stock,
            stock_quantity=stock_quantity,
            category_ids=category_ids or [],
        )
        return await products.create_product(product, idempotency_key=idempotency_key)

    @mcp.tool(title="List orders", annotations=READ_ONLY)
    @label
    @_tool_boundary
    async def list_orders(
        ctx: Context[Any, Any, Any],
        page: PageNumber = 1,
        per_page: PageSize = 20,
        statuses: Annotated[
            list[OrderStatus] | None, Field(max_length=10, description="Match any of these.")
        ] = None,
        customer_id: Annotated[int | None, Field(gt=0)] = None,
        search: Annotated[str | None, Field(max_length=100)] = None,
        created_after: Annotated[
            datetime | None, Field(description="ISO 8601 with offset, e.g. 2026-01-01T00:00:00Z")
        ] = None,
        created_before: Annotated[
            datetime | None, Field(description="ISO 8601 with offset")
        ] = None,
        order: SortOrder = SortOrder.DESC,
    ) -> Page[OrderSummary]:
        """List orders by creation date with optional filters. Returns summaries and totals."""
        query = _parse(
            OrderQuery,
            page=page,
            per_page=per_page,
            statuses=statuses or [],
            customer_id=customer_id,
            search=search,
            created_after=created_after,
            created_before=created_before,
            order=order,
        )
        return await orders.list_orders(query)

    @mcp.tool(title="Get order", annotations=READ_ONLY)
    @label
    @_tool_boundary
    async def get_order(ctx: Context[Any, Any, Any], order_id: OrderId) -> Order:
        """Get one order with its line items and totals."""
        return await orders.get_order(order_id)

    @mcp.tool(
        title="Update order status",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
        ),
    )
    @label
    @_tool_boundary
    async def update_order_status(
        ctx: Context[Any, Any, Any],
        order_id: OrderId,
        status: Annotated[
            OrderStatus,
            Field(
                description="Target status. Allowed: pending, processing, on-hold, completed, "
                "cancelled, failed. Store emails/stock hooks fire as in wp-admin."
            ),
        ],
        idempotency_key: IdempotencyKey = None,
    ) -> OrderStatusChange:
        """Change an order's status. A no-op (changed=false) if it already has that status."""
        return await orders.update_order_status(order_id, status, idempotency_key=idempotency_key)

    return mcp


def _instructions(store_name: str | None) -> str:
    if not store_name:
        return INSTRUCTIONS
    return INSTRUCTIONS.replace(
        "Tools for one WooCommerce store.",
        f"Tools for the WooCommerce store {store_name!r}; every call acts on that store only.",
        1,
    )


def _store_label[F: Callable[..., Any]](store_name: str | None) -> Callable[[F], F]:
    """Prefix a tool's description (its docstring) with the store it acts on."""

    def apply(fn: F) -> F:
        if store_name and fn.__doc__:
            fn.__doc__ = f"[Store: {store_name}] {fn.__doc__}"
        return fn

    return apply


def _tool_boundary[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Bind request context for logging and turn exceptions into safe MCP tool errors."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        ctx = kwargs.get("ctx")
        cid = _correlation_id(ctx if isinstance(ctx, Context) else None)
        cid_token = correlation_id_var.set(cid)
        tool_token = tool_name_var.set(fn.__name__)
        started = time.perf_counter()
        outcome = "ok"
        try:
            return await fn(*args, **kwargs)
        except AppError as exc:
            outcome = exc.code
            suffix = " (retryable)" if exc.retryable else ""
            raise ToolError(f"{exc.code}: {exc.public_message}{suffix}") from exc
        except Exception as exc:
            outcome = "internal_error"
            logger.exception("unhandled error in tool")
            raise ToolError(f"internal_error: Unexpected server error (reference {cid}).") from exc
        finally:
            logger.info(
                "tool call finished",
                extra={
                    "outcome": outcome,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            tool_name_var.reset(tool_token)
            correlation_id_var.reset(cid_token)

    return wrapper


def _parse[M: BaseModel](model: type[M], **values: Any) -> M:
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
            for err in exc.errors(include_url=False, include_input=False)
        )
        raise InvalidInputError(f"Invalid arguments: {details}") from exc


def _correlation_id(ctx: Context[Any, Any, Any] | None) -> str:
    """Prefer the gateway's request id header so logs can be joined across services."""
    if ctx is not None:
        try:
            request = ctx.request_context.request
        except ValueError:
            request = None
        headers = getattr(request, "headers", None)
        if headers is not None:
            for name in _CORRELATION_HEADERS:
                value = headers.get(name)
                if value and _CORRELATION_RE.match(value):
                    return str(value)
    return uuid.uuid4().hex
