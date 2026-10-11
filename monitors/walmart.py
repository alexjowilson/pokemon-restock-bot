"""Walmart via the official Walmart.io Affiliate API (product lookup).

Setup (one time):
  1. Create an account and an application at https://walmart.io.
  2. Generate an RSA key pair (see README), upload the PUBLIC key, and Walmart
     gives you a Consumer ID.
  3. Put these in .env:
       WALMART_CONSUMER_ID=...
       WALMART_PRIVATE_KEY_PATH=/home/alexwilson/.keys/walmart_private_key.pem
       WALMART_KEY_VERSION=1

config.yaml example:
  - id: "wm_30th_etb"
    name: "30th Celebration ETB"
    retailer: "walmart"
    url: "https://www.walmart.com/ip/Some-Product-Name/1234567890"
    # item_id: 1234567890        # optional: read from the URL if missing
    # max_price: 59.99           # ignore listings priced above this (scalper filter)
    # allow_marketplace: false   # true = also alert on third-party sellers

By default only items sold by Walmart itself count as "in stock": the
marketplace is full of resellers at 2-3x MSRP.
"""
from __future__ import annotations

import base64
import os
import re
import time
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from monitors.errors import CheckFailed
from monitors.http import http

LOOKUP_URL = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/items"
IN_STOCK_VALUES = {"available", "limited supply", "last few items"}
_ITEM_ID_RE = re.compile(r"/ip/(?:[^/]+/)?(\d+)")


def item_id_from(product: dict) -> str:
    item_id = product.get("item_id")
    if item_id:
        return str(int(item_id))
    match = _ITEM_ID_RE.search(product.get("url", ""))
    if not match:
        raise CheckFailed("Walmart products need an `item_id`, or a URL like walmart.com/ip/<name>/<id>")
    return match.group(1)


@lru_cache(maxsize=4)
def _load_private_key(path: str):
    try:
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    except FileNotFoundError:
        raise CheckFailed(f"Walmart private key not found at {path}") from None
    except (ValueError, TypeError) as e:
        raise CheckFailed(f"couldn't read Walmart private key ({e}); it must be an unencrypted PEM") from None


def auth_headers(consumer_id: str, key_version: str, private_key, now_ms: int | None = None) -> dict:
    """Walmart.io signs "consumerId\\ntimestamp\\nkeyVersion\\n" with SHA256 + RSA PKCS#1 v1.5.

    Signatures expire after 180s, so we make a fresh one for every request.
    """
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    message = f"{consumer_id}\n{timestamp}\n{key_version}\n".encode()
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return {
        "WM_CONSUMER.ID": consumer_id,
        "WM_CONSUMER.INTIMESTAMP": timestamp,
        "WM_SEC.KEY_VERSION": key_version,
        "WM_SEC.AUTH_SIGNATURE": base64.b64encode(signature).decode(),
    }


def _credentials():
    consumer_id = os.getenv("WALMART_CONSUMER_ID")
    key_path = os.getenv("WALMART_PRIVATE_KEY_PATH")
    if not consumer_id or not key_path:
        raise CheckFailed("set WALMART_CONSUMER_ID and WALMART_PRIVATE_KEY_PATH in .env")
    return consumer_id, os.getenv("WALMART_KEY_VERSION", "1"), _load_private_key(os.path.expanduser(key_path))


def is_first_party(item: dict) -> bool:
    if item.get("marketplace"):
        return False
    seller = (item.get("sellerInfo") or "").strip().lower()
    return seller in ("", "walmart", "walmart.com")


def parse_item(item: dict, product: dict) -> dict:
    price = item.get("salePrice")
    price = round(float(price), 2) if isinstance(price, (int, float)) else None
    stock = str(item.get("stock") or "").strip().lower()

    in_stock = bool(item.get("availableOnline")) and stock in IN_STOCK_VALUES
    note = None
    if in_stock and not product.get("allow_marketplace") and not is_first_party(item):
        in_stock, note = False, "only resellers"  # a reseller has it, not Walmart
    max_price = product.get("max_price")
    if in_stock and max_price is not None and price is not None and price > float(max_price):
        in_stock, note = False, f"over max ${float(max_price):.2f}"  # marked up

    return {
        "in_stock": in_stock,
        "price": price,
        "image_url": item.get("largeImage") or item.get("mediumImage"),
        "cart_url": item.get("addToCartUrl") if in_stock else None,
        "seller": item.get("sellerInfo") or "Walmart",
        "stock_level": item.get("stock"),
        "note": note,  # why a buyable listing doesn't count (shown in /checknow)
    }


async def check_product(product: dict) -> dict:
    item_id = item_id_from(product)
    consumer_id, key_version, private_key = _credentials()
    data = await http.get_json(LOOKUP_URL, params={"ids": item_id},
                               headers=auth_headers(consumer_id, key_version, private_key))
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        raise CheckFailed(f"Walmart returned no item for id {item_id}; check the URL/item_id")
    return parse_item(items[0], product)
