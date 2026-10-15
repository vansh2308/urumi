"""Order use cases."""

from __future__ import annotations

from pydantic import BaseModel

from woocommerce_mcp.errors import InvalidInputError
from woocommerce_mcp.services.operations import OperationRunner, OperationSpec
from woocommerce_mcp.woocommerce.client import WooCommerceClient
from woocommerce_mcp.woocommerce.models import Order, OrderQuery, OrderStatus, OrderSummary, Page

UPDATE_ORDER_STATUS = OperationSpec(
    name="update_order_status", resource_type="order", reexecutable=True
)

# Statuses an agent may set directly. 'refunded' is excluded because a real refund must go through
# the refunds API (payment gateway + stock); 'checkout-draft' and 'trash' are internal states.
SETTABLE_ORDER_STATUSES = frozenset(
    {
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
        OrderStatus.ON_HOLD,
        OrderStatus.COMPLETED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    }
)


class OrderStatusChange(BaseModel):
    order_id: int
    previous_status: str
    status: str
    changed: bool
    order: Order


class OrderService:
    def __init__(
        self, client: WooCommerceClient, runner: OperationRunner, *, max_page_size: int
    ) -> None:
        self._client = client
        self._runner = runner
        self._max_page_size = max_page_size

    async def list_orders(self, query: OrderQuery) -> Page[OrderSummary]:
        if query.per_page > self._max_page_size:
            raise InvalidInputError(f"per_page must be at most {self._max_page_size}.")
        return await self._client.list_orders(query)

    async def get_order(self, order_id: int) -> Order:
        return await self._client.get_order(order_id)

    async def update_order_status(
        self, order_id: int, status: OrderStatus, *, idempotency_key: str | None
    ) -> OrderStatusChange:
        if status not in SETTABLE_ORDER_STATUSES:
            allowed = ", ".join(sorted(s.value for s in SETTABLE_ORDER_STATUSES))
            raise InvalidInputError(
                f"Status '{status.value}' cannot be set here. Allowed: {allowed}."
            )

        async def change() -> OrderStatusChange:
            current = await self._client.get_order(order_id)
            if current.status == status.value:
                return OrderStatusChange(
                    order_id=order_id,
                    previous_status=current.status,
                    status=current.status,
                    changed=False,
                    order=current,
                )
            updated = await self._client.update_order_status(order_id, status)
            return OrderStatusChange(
                order_id=order_id,
                previous_status=current.status,
                status=updated.status,
                changed=updated.status != current.status,
                order=updated,
            )

        return await self._runner.run(
            UPDATE_ORDER_STATUS,
            request={"order_id": order_id, "status": status.value},
            idempotency_key=idempotency_key,
            result_type=OrderStatusChange,
            resource_id=lambda change: str(change.order_id),
            action=change,
        )
