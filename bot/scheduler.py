"""The polling loop: check every product, alert on out-of-stock -> in-stock transitions.

Reliability rules:
- A failed check never changes stock state (no fake "back in stock" alerts).
- Repeated failures back off exponentially (harder when a site blocks us), with jitter.
- After FAIL_ALERT_THRESHOLD failures in a row we post a health warning, and a
  recovery note when checks work again, so a broken monitor never fails silently.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, Optional

from monitors import MONITORS
from monitors.errors import Blocked, CheckFailed
from monitors.shopify import search_listings
from utils.config import Product
from utils.state import load_state, save_state

log = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 45
FAIL_ALERT_THRESHOLD = 5
MAX_BACKOFF_SECONDS = 30 * 60

AlertFn = Callable[[dict], Awaitable[None]]
HealthFn = Callable[[str], Awaitable[None]]


async def check_once(product: Product) -> dict:
    """Run one product's monitor. Raises CheckFailed on any failure."""
    monitor = MONITORS.get(product.retailer)
    if monitor is None:
        raise CheckFailed(f"no monitor for retailer {product.retailer!r} (known: {', '.join(MONITORS)})")
    try:
        return await asyncio.wait_for(monitor(product.as_dict()), CHECK_TIMEOUT_SECONDS)
    except CheckFailed:
        raise
    except asyncio.TimeoutError:
        raise CheckFailed(f"check timed out after {CHECK_TIMEOUT_SECONDS}s") from None
    except Exception as e:
        log.exception("Unexpected error checking %s", product.id)
        raise CheckFailed(f"bug in {product.retailer} monitor: {type(e).__name__}: {e}") from e


async def _safe_check(product: Product):
    try:
        return await check_once(product), None
    except CheckFailed as e:
        return None, e


def backoff_seconds(failures: int, blocked: bool) -> float:
    if blocked:
        base = 120 * 2 ** (failures - 1)          # 2m, 4m, 8m, ...
    elif failures >= 3:
        base = 60 * 2 ** (failures - 3)           # retry normally twice, then 1m, 2m, 4m, ...
    else:
        return 0
    return min(MAX_BACKOFF_SECONDS, base) * random.uniform(1.0, 1.2)


async def run_checks(
    products: list,
    state: dict,
    send_alert: AlertFn,
    health: Optional[dict] = None,
    send_health: Optional[HealthFn] = None,
    now: Optional[float] = None,
) -> dict:
    """Run one pass. Mutates and returns `state` (persisted) and `health` (in-memory)."""
    health = {} if health is None else health
    now = time.time() if now is None else now

    due = [p for p in products if health.get(p.id, {}).get("next_try", 0) <= now]
    results = await asyncio.gather(*(_safe_check(p) for p in due))

    for product, (result, error) in zip(due, results):
        if error is not None:
            await _record_failure(product, error, health, send_health, now)
            continue
        await _record_success(product, health, send_health)

        prev = state.get(product.id, {})
        in_stock = bool(result.get("in_stock"))
        was_in_stock = prev.get("in_stock", False)

        if in_stock and not was_in_stock:
            try:
                await send_alert({**product.as_dict(), **result})
                log.info("Alert sent for %s", product.id)
            except Exception:
                # Don't record it as in stock, so we retry the alert next pass.
                log.exception("Failed to send alert for %s", product.id)
                continue
        elif was_in_stock and not in_stock:
            log.info("%s went out of stock", product.id)

        state[product.id] = {
            "in_stock": in_stock,
            "price": result.get("price"),
            "last_checked": int(now),
            "last_in_stock": int(now) if in_stock else prev.get("last_in_stock"),
        }
    return state


async def search_once(search) -> list:
    """Run every query for one store and merge results. Raises CheckFailed on failure."""
    merged = {}
    try:
        for query in search.queries:
            for listing in await asyncio.wait_for(search_listings(search.store_url, query),
                                                  CHECK_TIMEOUT_SECONDS):
                title = listing["name"].lower()
                if not any(word in title for word in search.exclude):
                    entry = merged.setdefault(listing["listing_id"], {**listing, "queries": []})
                    entry["queries"].append(query)
    except CheckFailed:
        raise
    except asyncio.TimeoutError:
        raise CheckFailed(f"search timed out after {CHECK_TIMEOUT_SECONDS}s") from None
    except Exception as e:
        log.exception("Unexpected error searching %s", search.id)
        raise CheckFailed(f"bug in search: {type(e).__name__}: {e}") from e
    return list(merged.values())


async def _safe_search(search):
    try:
        return await search_once(search), None
    except CheckFailed as e:
        return None, e


async def run_search_checks(
    searches: list,
    state: dict,
    send_alert: AlertFn,
    health: Optional[dict] = None,
    send_health: Optional[HealthFn] = None,
    now: Optional[float] = None,
) -> dict:
    """Alert on brand-new listings and on listings that flip to available.

    The first successful pass for a search only records what's already listed
    (otherwise adding a store would flood the channel with old products).
    """
    health = {} if health is None else health
    now = time.time() if now is None else now
    all_state = state.setdefault("_searches", {})

    due = [s for s in searches if health.get(s.id, {}).get("next_try", 0) <= now]
    results = await asyncio.gather(*(_safe_search(s) for s in due))

    for search, (listings, error) in zip(due, results):
        if error is not None:
            await _record_failure(search, error, health, send_health, now)
            continue
        await _record_success(search, health, send_health)

        st = all_state.setdefault(search.id, {"seen": {}})
        # Each query gets its own quiet first pass, so adding a keyword later
        # doesn't post every existing listing for it as "new".
        done = set(st.get("baselined_queries", []))
        fresh = [q for q in search.queries if q not in done]
        for listing in listings:
            lid = listing["listing_id"]
            prev = st["seen"].get(lid)
            only_fresh = all(q in fresh for q in listing.get("queries", []))
            kind = None
            if prev is None and not only_fresh:
                kind = "new"
            elif prev is not None and listing["in_stock"] and not prev["in_stock"]:
                kind = "restock"

            if kind:
                alert = {k: v for k, v in listing.items() if k != "queries"}
                try:
                    await send_alert({**alert, "id": f"{search.id}:{lid}", "kind": kind,
                                      "retailer": search.retailer, "store_name": search.store_name})
                    log.info("%s alert for %s: %s", kind, search.id, listing["name"])
                except Exception:
                    log.exception("Failed to send alert for %s", listing["name"])
                    continue  # leave state alone so it's retried next pass

            st["seen"][lid] = {
                "name": listing["name"],
                "in_stock": listing["in_stock"],
                "first_seen": prev["first_seen"] if prev else int(now),
            }
        if fresh:
            log.info("%s: recorded existing listings for %s; alerting on changes from now on",
                     search.id, ", ".join(repr(q) for q in fresh))
        st["baselined_queries"] = sorted(done | set(search.queries))
        st["last_checked"] = int(now)
    return state


async def _record_failure(product, error, health, send_health, now):
    h = health.setdefault(product.id, {"failures": 0, "alerted": False})
    h["failures"] += 1
    h["last_error"] = str(error)
    delay = backoff_seconds(h["failures"], isinstance(error, Blocked))
    h["next_try"] = now + delay
    log.warning("%s check failed (%d in a row%s): %s", product.id, h["failures"],
                f", backing off {int(delay)}s" if delay else "", error)

    if h["failures"] >= FAIL_ALERT_THRESHOLD and not h["alerted"] and send_health:
        try:
            await send_health(f"⚠️ **{product.name}** ({product.retailer}): {h['failures']} checks "
                              f"failed in a row, so restocks may be missed.\nLast error: `{error}`")
            h["alerted"] = True
        except Exception:
            log.exception("Failed to send health warning")


async def _record_success(product, health, send_health):
    h = health.pop(product.id, None)
    if h and h.get("alerted") and send_health:
        try:
            await send_health(f"✅ **{product.name}** ({product.retailer}): checks are working again.")
        except Exception:
            log.exception("Failed to send recovery note")


class Scheduler:
    def __init__(self, products: list, interval: int, send_alert: AlertFn,
                 send_health: Optional[HealthFn] = None, searches: Optional[list] = None):
        self.products = products
        self.searches = searches or []
        self.interval = interval
        self.send_alert = send_alert
        self.send_health = send_health
        self.state = load_state()
        self.health: dict = {}
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._loop())

    async def _loop(self) -> None:
        log.info("Monitoring %d product(s) and %d store search(es) every %ds",
                 len(self.products), len(self.searches), self.interval)
        while True:
            started = time.monotonic()
            try:
                await asyncio.gather(
                    run_checks(self.products, self.state, self.send_alert, self.health, self.send_health),
                    run_search_checks(self.searches, self.state, self.send_alert, self.health,
                                      self.send_health),
                )
                save_state(self.state)
            except Exception:
                log.exception("Check pass crashed; continuing")
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - started)))
