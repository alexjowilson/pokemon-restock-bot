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

import asyncio
import base64
import os
import re
import time
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from monitors.errors import CheckFailed
from monitors.http import http
from utils.text import title_matches

API_BASE = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2"
LOOKUP_URL = f"{API_BASE}/items"
SEARCH_URL = f"{API_BASE}/search"
MAX_IDS_PER_CALL = 20       # Walmart's limit for one lookup request
BATCH_WINDOW_SECONDS = 0.1  # checks started together share one request
IN_STOCK_VALUES = {"available", "limited supply", "last few items"}
_ITEM_ID_RE = re.compile(r"/ip/(?:[^/]+/)?(\d+)")


def item_id_from(product: dict) -> str:
    item_id = product.get("item_id")
    if item_id:
        return str(int(item_id))
    match = _ITEM_ID_RE.search(product.get("url") or "")
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


def item_url(item_id) -> str:
    return f"https://www.walmart.com/ip/{item_id}"


async def _signed_get(url: str, params: dict):
    consumer_id, key_version, private_key = _credentials()
    return await http.get_json(url, params=params,
                               headers=auth_headers(consumer_id, key_version, private_key))


class _LookupBatcher:
    """Coalesces lookups that start at the same moment into one API call (up to 20 ids).

    The scheduler checks every product at once, so 20 Walmart products cost one call
    per pass instead of 20. That's what keeps a long watch list under 5,000 calls/day.
    """

    def __init__(self):
        self._pending: dict = {}
        self._task = None

    async def get(self, item_id: str):
        loop = asyncio.get_running_loop()
        if self._task is not None and self._task.get_loop() is not loop:
            self._pending, self._task = {}, None  # a new event loop (tests, restarts)
        future = loop.create_future()
        self._pending.setdefault(item_id, []).append(future)
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._flush())
        return await future

    async def _flush(self):
        await asyncio.sleep(BATCH_WINDOW_SECONDS)
        pending, self._pending = self._pending, {}
        ids = list(pending)
        for start in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = ids[start:start + MAX_IDS_PER_CALL]
            try:
                data = await _signed_get(LOOKUP_URL, {"ids": ",".join(chunk)})
                items = data.get("items") if isinstance(data, dict) else None
                by_id = {str(i.get("itemId")): i for i in items or []}
                outcome = [(by_id.get(i), None) for i in chunk]
            except Exception as e:  # every waiter gets the same failure
                outcome = [(None, e) for _ in chunk]
            for item_id, (item, error) in zip(chunk, outcome):
                for future in pending[item_id]:
                    if future.done():  # the check already timed out
                        continue
                    if error is not None:
                        future.set_exception(error)
                    else:
                        future.set_result(item)


_batcher = _LookupBatcher()


async def check_product(product: dict) -> dict:
    item_id = item_id_from(product)
    item = await _batcher.get(item_id)
    if not item and product.get("auto"):
        # An auto-watched listing Walmart took down: treat as unavailable, not as a failure.
        return {"in_stock": False, "price": None, "note": "no longer listed"}
    if not item:
        raise CheckFailed(f"Walmart returned no item for id {item_id}; check the URL/item_id")
    return parse_item(item, product)


ACCESSORY_WORDS = ("case", "protector", "acrylic", "display", "sleeve", "sleeves", "binder",
                   "album", "portfolio", "deck box", "playmat", "storage")


def _search_items_from(data) -> list:
    """Walmart leaves `items` out entirely when nothing matches; that's 0 results, not an error."""
    if not isinstance(data, dict):
        raise CheckFailed("unexpected response from Walmart search")
    if "items" in data:
        return data["items"] or []
    errors = data.get("errors") or data.get("error")
    if errors:
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            errors = errors[0].get("message") or errors[0]
        raise CheckFailed(f"Walmart search error: {str(errors)[:200]}")
    return []


async def search_items(query: str, limit: int = 10, relevant_only: bool = True) -> list:
    """Walmart search, for finding item ids to watch. One API call.

    relevant_only keeps titles containing every query word and drops accessories
    (cases, protectors, sleeves...), which otherwise crowd out the real product.
    """
    data = await _signed_get(SEARCH_URL, {"query": query, "numItems": "25"})
    words = [w for w in query.split() if w]
    results = []
    for item in _search_items_from(data):
        if not item.get("itemId"):
            continue
        name = item.get("name", "Untitled")
        if relevant_only and (not title_matches(name, require_all=words)
                              or title_matches(name, include_any=ACCESSORY_WORDS)):
            continue
        parsed = parse_item(item, {"allow_marketplace": True})
        results.append({
            "item_id": str(item["itemId"]),
            "name": name,
            "url": item_url(item["itemId"]),
            "price": parsed["price"],
            "available": parsed["in_stock"],
            "first_party": is_first_party(item),
            "seller": item.get("sellerInfo") or "Walmart",
        })
    results.sort(key=lambda r: not r["first_party"])  # Walmart's own listings first
    return results[:limit]


async def search_many(queries, per_query: int = 10) -> tuple:
    """Run several searches (one call each), merge and de-duplicate by item id.

    Returns (results, failed_queries). One failed query doesn't sink the rest.
    """
    merged, failed = {}, []
    for query in queries:
        try:
            for r in await search_items(query, limit=per_query):
                merged.setdefault(r["item_id"], {**r, "query": query})
        except CheckFailed as e:
            failed.append((query, str(e)))
    results = sorted(merged.values(), key=lambda r: (not r["first_party"], not r["available"]))
    return results, failed


# ---------- new-listing searches (searches: with retailer: "walmart") ----------

async def search_listings(search, query: str) -> list:
    """One signed search call, newest listings first, shaped for the scheduler.

    Only Walmart's own listings are announced as new, unless the search sets
    allow_marketplace: true, so a reseller posting a 30th ETB at $150 never pings
    anyone. Reseller-only listings are still returned (quietly) and count as out of
    stock, so if Walmart later sells one, it's alerted as a restock.
    """
    opts = search.options
    data = await _signed_get(SEARCH_URL, {"query": query, "numItems": "25",
                                          "sort": str(opts.get("sort", "new"))})
    listings = []
    for item in _search_items_from(data):
        if not item.get("itemId") or not item.get("name"):
            continue
        # Reseller-only listings are kept (so auto-watch can notice when Walmart itself
        # starts selling one) but never announced as "new".
        first_party = is_first_party(item)
        parsed = parse_item(item, opts)
        listings.append({
            "listing_id": str(item["itemId"]),
            "first_party": first_party,
            "quiet_new": not first_party and not opts.get("allow_marketplace"),
            "name": item["name"],
            "url": item_url(item["itemId"]),
            "in_stock": parsed["in_stock"],
            "price": parsed["price"],
            "image_url": parsed["image_url"],
            "cart_url": parsed["cart_url"],
        })
    return listings
