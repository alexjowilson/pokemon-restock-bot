"""Best Buy via the official Products API (https://bestbuyapis.github.io/api-documentation/).

Needs BESTBUY_API_KEY in .env. Note: Best Buy only issues keys to company
email addresses, not Gmail/Yahoo.

config.yaml example:
  - id: "bb_151_etb"
    name: "Scarlet & Violet 151 ETB"
    retailer: "bestbuy"
    url: "https://www.bestbuy.com/site/..."
    sku: 6548371
"""
from __future__ import annotations

import os

from monitors.errors import CheckFailed
from monitors.http import http

API_URL = "https://api.bestbuy.com/v1/products/{sku}.json"
FIELDS = "sku,name,salePrice,onlineAvailability,orderable,addToCartUrl,image,url"


async def check_product(product: dict) -> dict:
    api_key = os.getenv("BESTBUY_API_KEY")
    if not api_key:
        raise CheckFailed("BESTBUY_API_KEY isn't set in .env")
    sku = product.get("sku")
    if not sku:
        raise CheckFailed("Best Buy products need a `sku` in config.yaml")

    data = await http.get_json(API_URL.format(sku=int(sku)),
                               params={"apiKey": api_key, "show": FIELDS, "format": "json"})
    if not isinstance(data, dict) or "onlineAvailability" not in data:
        raise CheckFailed("unexpected response from Best Buy API")

    in_stock = bool(data["onlineAvailability"])
    return {
        "in_stock": in_stock,
        "price": data.get("salePrice"),
        "image_url": data.get("image"),
        "cart_url": data.get("addToCartUrl") if in_stock else None,
    }
