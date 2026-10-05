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


# ---------- keyword search (new listings) ----------
# Shopify's predictive search (Ajax API): /search/suggest.json?q=...
# Returns up to 10 products per query, including sold-out ones, with `available`.

def _price(value):
    """Search results give price as a "24.99" string on most themes, cents on some."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return round(float(value), 2)
        except ValueError:
            return None
    return round(value / 100, 2) if isinstance(value, int) else round(float(value), 2)


def parse_search_results(data: dict, store_url: str) -> list:
    try:
        products = data["resources"]["results"]["products"]
    except (KeyError, TypeError):
        raise CheckFailed("search response doesn't look like Shopify predictive search") from None

    listings = []
    for p in products:
        path = urlsplit(p.get("url") or f"/products/{p.get('handle', '')}").path  # drop tracking params
        image = p.get("image") or p.get("featured_image")
        if isinstance(image, dict):
            image = image.get("url")
        if image and image.startswith("//"):
            image = "https:" + image
        listings.append({
            "listing_id": str(p["id"]),
            "name": p.get("title", "Untitled"),
            "url": store_url + path,
            "in_stock": bool(p.get("available")),
            "price": _price(p.get("price")),
            "image_url": image,
        })
    return listings


async def search_listings(store_url: str, query: str) -> list:
    data = await http.get_json(f"{store_url}/search/suggest.json", params={
        "q": query,
        "resources[type]": "product",
        "resources[limit]": "10",
        "resources[options][unavailable_products]": "show",  # we want sold-out listings too
    })
    return parse_search_results(data, store_url)
