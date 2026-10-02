"""Registry mapping a product's `retailer` field to its stock-check coroutine.

Every monitor has the same signature:
    async def check_product(product: dict) -> dict
and returns at least {"in_stock": bool}; optional keys: price, image_url, name, url.
Raise an exception if the check itself failed (blocked, timeout, page changed),
so a failed check is never mistaken for "out of stock".
"""
from monitors import demo

MONITORS = {
    "demo": demo.check_product,
}
