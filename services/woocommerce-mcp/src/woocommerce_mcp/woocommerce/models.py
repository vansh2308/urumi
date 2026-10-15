"""Typed views of WooCommerce resources and request payloads.

Response models deliberately keep a small, explicit subset of WooCommerce's fields
(``extra="ignore"``): this bounds response size, keeps secrets/PII such as phone numbers, postal
addresses, IPs and payment metadata out of tool results, and gives callers a stable contract that
does not change when a plugin adds fields upstream. Status fields stay plain strings in responses
because stores can register custom statuses; requests use the enums below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class ProductStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    PRIVATE = "private"
    PUBLISH = "publish"


class StockStatus(StrEnum):
    IN_STOCK = "instock"
    OUT_OF_STOCK = "outofstock"
    ON_BACKORDER = "onbackorder"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ON_HOLD = "on-hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED = "failed"
    CHECKOUT_DRAFT = "checkout-draft"
    TRASH = "trash"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


def _assume_gmt(value: datetime) -> datetime:
    # WooCommerce's *_gmt fields are UTC but serialised without an offset.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


GmtDatetime = Annotated[datetime, AfterValidator(_assume_gmt)]


class _Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class CategoryRef(_Upstream):
    id: int
    name: str = ""


class Product(_Upstream):
    id: int
    name: str
    slug: str = ""
    permalink: str | None = None
    type: str
    status: str
    sku: str = ""
    price: str = ""
    regular_price: str = ""
    sale_price: str = ""
    on_sale: bool = False
    manage_stock: bool = False
    stock_quantity: int | None = None
    stock_status: str = ""
    categories: list[CategoryRef] = Field(default_factory=list)
    date_created_gmt: GmtDatetime | None = None
    date_modified_gmt: GmtDatetime | None = None


class CustomerContact(_Upstream):
    first_name: str = ""
    last_name: str = ""
    email: str = ""


class LineItem(_Upstream):
    id: int
    name: str
    product_id: int = 0
    variation_id: int = 0
    sku: str | None = None
    quantity: int
    subtotal: str = ""
    total: str = ""


class OrderSummary(_Upstream):
    id: int
    number: str
    status: str
    currency: str
    total: str
    customer_id: int = 0
    billing: CustomerContact = Field(default_factory=CustomerContact)
    payment_method_title: str = ""
    date_created_gmt: GmtDatetime | None = None
    date_modified_gmt: GmtDatetime | None = None


class Order(OrderSummary):
    total_tax: str = ""
    discount_total: str = ""
    shipping_total: str = ""
    customer_note: str = ""
    date_paid_gmt: GmtDatetime | None = None
    date_completed_gmt: GmtDatetime | None = None
    line_items: list[LineItem] = Field(default_factory=list)


class Page[T: BaseModel](BaseModel):
    items: list[T]
    page: int
    per_page: int
    total: int | None = Field(default=None, description="Total matching records, if reported.")
    total_pages: int | None = None


# Fields requested from WooCommerce via ``_fields`` so the store does less work and sends less.
PRODUCT_FIELDS = tuple(Product.model_fields)
ORDER_SUMMARY_FIELDS = tuple(OrderSummary.model_fields)
ORDER_FIELDS = tuple(Order.model_fields)


Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class NewProduct(BaseModel):
    """Payload for creating a simple product. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    regular_price: Money
    sale_price: Money | None = None
    sku: Sku | None = None
    status: ProductStatus = ProductStatus.DRAFT
    description: Annotated[str, StringConstraints(max_length=20_000)] = ""
    short_description: Annotated[str, StringConstraints(max_length=2_000)] = ""
    manage_stock: bool = False
    stock_quantity: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    category_ids: Annotated[list[Annotated[int, Field(gt=0)]], Field(max_length=20)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.sale_price is not None and self.sale_price >= self.regular_price:
            raise ValueError("sale_price must be lower than regular_price")
        if self.stock_quantity is not None and not self.manage_stock:
            raise ValueError("stock_quantity requires manage_stock=true")
        return self

    def to_upstream(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "type": "simple",
            "status": self.status.value,
            "regular_price": f"{self.regular_price:.2f}",
            "description": self.description,
            "short_description": self.short_description,
            "manage_stock": self.manage_stock,
        }
        if self.sale_price is not None:
            payload["sale_price"] = f"{self.sale_price:.2f}"
        if self.sku is not None:
            payload["sku"] = self.sku
        if self.stock_quantity is not None:
            payload["stock_quantity"] = self.stock_quantity
        if self.category_ids:
            payload["categories"] = [{"id": cid} for cid in self.category_ids]
        return payload


class ProductQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=20, ge=1, le=100)
    search: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    sku: Sku | None = None
    status: ProductStatus | None = None
    stock_status: StockStatus | None = None
    category_id: Annotated[int, Field(gt=0)] | None = None

    def to_params(self) -> dict[str, str]:
        params = {"page": str(self.page), "per_page": str(self.per_page)}
        if self.search:
            params["search"] = self.search
        if self.sku:
            params["sku"] = self.sku
        if self.status:
            params["status"] = self.status.value
        if self.stock_status:
            params["stock_status"] = self.stock_status.value
        if self.category_id:
            params["category"] = str(self.category_id)
        return params


class OrderQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=20, ge=1, le=100)
    statuses: Annotated[list[OrderStatus], Field(max_length=len(OrderStatus))] = Field(
        default_factory=list
    )
    customer_id: Annotated[int, Field(gt=0)] | None = None
    search: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    order: SortOrder = SortOrder.DESC

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        for value in (self.created_after, self.created_before):
            if value is not None and value.tzinfo is None:
                raise ValueError("dates must include a timezone offset, e.g. 2026-01-31T00:00:00Z")
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after >= self.created_before
        ):
            raise ValueError("created_after must be earlier than created_before")
        return self

    def to_params(self) -> dict[str, str]:
        params = {
            "page": str(self.page),
            "per_page": str(self.per_page),
            "order": self.order.value,
            "orderby": "date",
        }
        if self.statuses:
            params["status"] = ",".join(dict.fromkeys(s.value for s in self.statuses))
        if self.customer_id:
            params["customer"] = str(self.customer_id)
        if self.search:
            params["search"] = self.search
        if self.created_after is not None or self.created_before is not None:
            params["dates_are_gmt"] = "true"
        if self.created_after is not None:
            params["after"] = _gmt_iso(self.created_after)
        if self.created_before is not None:
            params["before"] = _gmt_iso(self.created_before)
        return params


def _gmt_iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
