"""Registry mapping a product's `retailer` field to its stock-check coroutine.

Every monitor has the same signature:
    async def check_product(product: dict) -> dict
and returns at least {"in_stock": bool}; optional keys: price, image_url, cart_url.
Raise monitors.errors.CheckFailed (or Blocked) if the check itself failed, so a
failed check is never mistaken for "out of stock".
"""
from monitors import bestbuy, demo, shopify

MONITORS = {
    "demo": demo.check_product,
    "shopify": shopify.check_product,
    "bestbuy": bestbuy.check_product,
}
