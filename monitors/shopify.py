"""Shopify stores (most independent card shops).

Every Shopify product page has a public JSON twin at /products/<handle>.js
(Shopify's Ajax API), so no scraping or API key is needed.

config.yaml example:
  - id: "ccg_151_etb"
    name: "Scarlet & Violet 151 ETB"
    retailer: "shopify"
    url: "https://some-card-shop.com/products/sv-151-elite-trainer-box"
    # variant_id: 1234567890   # optional: watch one specific variant
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from monitors.errors import CheckFailed
from monitors.http import http


def product_js_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    for suffix in (".js", ".json"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    if "/products/" not in path:
        raise CheckFailed("Shopify URL must look like https://store.com/products/<handle>")
    return urlunsplit((parts.scheme or "https", parts.netloc, path + ".js", "", ""))


def parse_product(data: dict, product: dict) -> dict:
    variants = data.get("variants") or []
    wanted = product.get("variant_id")
    if wanted is not None:
        variants = [v for v in variants if str(v.get("id")) == str(wanted)]
        if not variants:
            raise CheckFailed(f"variant_id {wanted} not found on this product")
        in_stock = bool(variants[0].get("available"))
    else:
        in_stock = bool(data.get("available"))

    buyable = next((v for v in variants if v.get("available")), variants[0] if variants else None)
    price_cents = buyable.get("price") if buyable else data.get("price")

    parts = urlsplit(product["url"])
    store = f"{parts.scheme or 'https'}://{parts.netloc}"
    image = data.get("featured_image")
    if image and image.startswith("//"):
        image = "https:" + image

    return {
        "in_stock": in_stock,
        "price": round(price_cents / 100, 2) if isinstance(price_cents, (int, float)) else None,
        "image_url": image,
        # Shopify cart permalink: opens the store's cart with the item already in it.
        "cart_url": f"{store}/cart/{buyable['id']}:1" if in_stock and buyable and buyable.get("id") else None,
    }


async def check_product(product: dict) -> dict:
    data = await http.get_json(product_js_url(product["url"]))
    if not isinstance(data, dict) or "available" not in data:
        raise CheckFailed("response doesn't look like a Shopify product")
    return parse_product(data, product)
