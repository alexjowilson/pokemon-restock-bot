"""Shared HTTP client for monitors: one connection pool, polite per-host spacing, clear errors."""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlsplit

import aiohttp

from monitors.errors import Blocked, CheckFailed

# Identify the bot honestly; don't pretend to be a browser.
USER_AGENT = "pokemon-restock-bot/1.0 (+https://github.com/alexjowilson/pokemon-restock-bot)"

# Official, quota-based APIs can be called faster than scraped store sites. Walmart's
# limit is per day, so 2 req/s keeps an hourly 28-query pass from blocking the
# per-minute restock lookup that shares this host.
HOST_INTERVALS = {"developer.api.walmart.com": 0.5}


class HttpClient:
    def __init__(self, min_interval_per_host: float = 2.0, timeout_seconds: float = 15):
        self.min_interval = min_interval_per_host
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._locks: dict = {}
        self._last_request: dict = {}

    def _session_for_loop(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._session

    async def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        host = urlsplit(url).hostname or ""
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:  # one request at a time per host, spaced out
            spacing = HOST_INTERVALS.get(host, self.min_interval)
            wait = self._last_request.get(host, 0) + spacing - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session_for_loop().get(url, params=params, headers=headers) as resp:
                    if resp.status in (403, 429):
                        raise Blocked(f"{host} refused the request (HTTP {resp.status})")
                    if resp.status == 404:
                        raise CheckFailed(f"{host} says the product doesn't exist (404); check the URL/SKU")
                    if resp.status >= 400:
                        # The API's own explanation (e.g. "invalid start"); never the URL,
                        # which may carry a key.
                        detail = " ".join((await resp.text())[:200].split())
                        raise CheckFailed(f"{host} returned HTTP {resp.status}"
                                          + (f": {detail}" if detail else ""))
                    text = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Deliberately not including the URL: it may carry an API key.
                raise CheckFailed(f"request to {host} failed: {type(e).__name__}") from e
            finally:
                self._last_request[host] = time.monotonic()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise CheckFailed(f"{host} didn't return JSON (bot wall or page changed)") from None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


http = HttpClient()
