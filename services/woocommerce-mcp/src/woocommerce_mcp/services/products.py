"""Product use cases."""

from __future__ import annotations

from woocommerce_mcp.errors import InvalidInputError
from woocommerce_mcp.services.operations import OperationRunner, OperationSpec
from woocommerce_mcp.woocommerce.client import WooCommerceClient
from woocommerce_mcp.woocommerce.models import NewProduct, Page, Product, ProductQuery

CREATE_PRODUCT = OperationSpec(name="create_product", resource_type="product", reexecutable=False)


class ProductService:
    def __init__(
        self, client: WooCommerceClient, runner: OperationRunner, *, max_page_size: int
    ) -> None:
        self._client = client
        self._runner = runner
        self._max_page_size = max_page_size

    async def list_products(self, query: ProductQuery) -> Page[Product]:
        _check_page_size(query.per_page, self._max_page_size)
        return await self._client.list_products(query)

    async def create_product(self, product: NewProduct, *, idempotency_key: str | None) -> Product:
        return await self._runner.run(
            CREATE_PRODUCT,
            request=product.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            result_type=Product,
            resource_id=lambda created: str(created.id),
            action=lambda: self._client.create_product(product),
        )


def _check_page_size(per_page: int, maximum: int) -> None:
    if per_page > maximum:
        raise InvalidInputError(f"per_page must be at most {maximum}.")
