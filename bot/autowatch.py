"""Auto-watch: items a search finds become watched products, checked every pass.

A Walmart search with `auto_watch: true` remembers every matching item it finds
(Walmart-sold or reseller-only). Those items then join the per-minute batched
Walmart lookup, so a restock is caught within a minute instead of waiting for the
next search, and a reseller-only listing alerts the moment Walmart itself sells it.
You never have to paste item ids into config.yaml.

The list is capped (auto_watch_max_total, default 40 Walmart items including the
ones you configured) because 20 items cost one API call per pass.
"""
from __future__ import annotations

import logging
import time

from monitors.errors import CheckFailed
from monitors.walmart import item_id_from
from utils.config import Product

log = logging.getLogger(__name__)

AUTO_KEY = "_auto_watch"
DEFAULT_MAX_TOTAL = 40
PASS_THROUGH = ("max_price", "allow_marketplace")


def product_id(item_id: str) -> str:
    return f"auto_walmart_{item_id}"


def configured_walmart_ids(products) -> set:
    ids = set()
    for p in products:
        if p.retailer == "walmart" and not p.extra.get("auto"):
            try:
                ids.add(item_id_from(p.as_dict()))
            except CheckFailed:
                pass
    return ids


def register(state: dict, search, listings: list, configured_products, now: float | None = None) -> int:
    """Add newly found items to the watch list. Returns how many were added."""
    if not search.options.get("auto_watch"):
        return 0
    now = time.time() if now is None else now
    auto = state.setdefault(AUTO_KEY, {})
    configured = configured_walmart_ids(configured_products)
    max_total = int(search.options.get("auto_watch_max_total", DEFAULT_MAX_TOTAL))
    room = max_total - len(configured) - len(auto)
    added = 0
    # Walmart-sold items take the remaining slots before reseller-only ones.
    for listing in sorted(listings, key=lambda x: not x.get("first_party")):
        lid = listing["listing_id"]
        if lid in auto or lid in configured:
            continue
        if room <= 0:
            if not state.get("_auto_watch_full_logged"):
                log.warning("Auto-watch list is full (%d Walmart items); raise auto_watch_max_total "
                            "or use fewer set queries to watch more", max_total)
                state["_auto_watch_full_logged"] = True
            break
        auto[lid] = {
            "name": listing["name"],
            "url": listing["url"],
            "search": search.id,
            "channel_id": search.channel_id,
            "role_id": search.role_id,
            "options": {k: search.options[k] for k in PASS_THROUGH if k in search.options},
            "added": int(now),
        }
        # Baseline from the search result, so being added never looks like a restock.
        state.setdefault(product_id(lid), {"in_stock": listing["in_stock"],
                                           "price": listing.get("price"),
                                           "last_checked": int(now)})
        room -= 1
        added += 1
    if added:
        log.info("Auto-watching %d more Walmart item(s) from %s (%d total)", added, search.id, len(auto))
    return added


def auto_products(state: dict, configured_products, searches=None) -> list:
    """Watched products for every item an auto_watch search found.

    Items whose search is no longer an auto_watch search in config.yaml are dropped,
    so removing a search also stops its lookups.
    """
    configured = configured_walmart_ids(configured_products)
    active = None if searches is None else {s.id for s in searches if s.options.get("auto_watch")}
    auto = state.get(AUTO_KEY, {})
    if active is not None:
        for lid in [lid for lid, e in auto.items() if e.get("search") not in active]:
            del auto[lid]
            state.pop(product_id(lid), None)
    products = []
    for lid, entry in auto.items():
        if lid in configured:
            continue
        extra = {"item_id": lid, "auto": True, **entry.get("options", {})}
        for key in ("channel_id", "role_id"):
            if entry.get(key):
                extra[key] = entry[key]
        products.append(Product(id=product_id(lid), name=entry["name"], retailer="walmart",
                                url=entry["url"], extra=extra))
    return products
