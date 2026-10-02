"""The polling loop: check every product, alert on out-of-stock -> in-stock transitions."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from monitors import MONITORS
from utils.config import Product
from utils.state import load_state, save_state

log = logging.getLogger(__name__)
CHECK_TIMEOUT_SECONDS = 30

AlertFn = Callable[[dict], Awaitable[None]]


async def _check_one(product: Product) -> dict | None:
    monitor = MONITORS.get(product.retailer)
    if monitor is None:
        log.error("No monitor registered for retailer %r (product %s)", product.retailer, product.id)
        return None
    try:
        return await asyncio.wait_for(monitor(product.as_dict()), CHECK_TIMEOUT_SECONDS)
    except Exception:
        log.exception("Check failed for %s", product.id)
        return None


async def run_checks(products: list[Product], state: dict, send_alert: AlertFn) -> dict:
    """Run one pass. Mutates and returns `state`. Failed checks leave state untouched."""
    results = await asyncio.gather(*(_check_one(p) for p in products))
    now = int(time.time())

    for product, result in zip(products, results):
        if result is None:
            continue
        prev = state.get(product.id, {})
        in_stock = bool(result.get("in_stock"))
        was_in_stock = prev.get("in_stock", False)

        if in_stock and not was_in_stock:
            alert = {**product.as_dict(), **result}
            try:
                await send_alert(alert)
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
            "last_checked": now,
            "last_in_stock": now if in_stock else prev.get("last_in_stock"),
        }
    return state


class Scheduler:
    def __init__(self, products: list[Product], interval: int, send_alert: AlertFn):
        self.products = products
        self.interval = interval
        self.send_alert = send_alert
        self.state = load_state()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="restock-scheduler")

    async def _loop(self) -> None:
        log.info("Monitoring %d product(s) every %ds", len(self.products), self.interval)
        while True:
            started = time.monotonic()
            try:
                await run_checks(self.products, self.state, self.send_alert)
                save_state(self.state)
            except Exception:
                log.exception("Check pass crashed; continuing")
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - started)))
