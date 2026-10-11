"""TCGplayer market prices for sealed products, via TCGCSV's free daily dump.

TCGCSV (https://tcgcsv.com) republishes TCGplayer's catalog and prices as JSON once
a day. Once every 24 hours (their rule) the bot downloads only the sets it needs:

    /tcgplayer/3/groups                     every Pokémon set (category 3)
    /tcgplayer/3/{groupId}/products         that set's products
    /tcgplayer/3/{groupId}/prices           their market prices

Each watched product is matched by name: "Destined Rivals Elite Trainer Box" finds
the set "SV10: Destined Rivals", then its "Destined Rivals Elite Trainer Box"
(Pokémon Center exclusives, cases and displays are skipped). A product's
`tcgplayer_id:` in config.yaml overrides the match, and `set:` overrides the set name.
Results are cached in data/market.json, so restarts don't download again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from monitors.errors import CheckFailed
from monitors.http import http
from utils.config import PROJECT_ROOT
from utils.text import has_word, normalize

log = logging.getLogger(__name__)

BASE = "https://tcgcsv.com/tcgplayer/3"
CACHE_PATH = PROJECT_ROOT / "data" / "market.json"
REFRESH_SECONDS = 24 * 3600
RETRY_SECONDS = 3600            # after a failed refresh
TYPES = ("elite trainer box", "booster bundle")
SKIP_WORDS = ("pokemon center", "case", "display", "bundle of", "set of")


def product_type(name: str) -> str | None:
    for kind in TYPES:
        if has_word(name, kind):
            return kind
    return "elite trainer box" if has_word(name, "etb") else None


def set_and_variants(name: str, kind: str) -> tuple:
    """"Mega Evolution Elite Trainer Box (Lucario)" -> ("Mega Evolution", ["lucario"])."""
    variants = re.findall(r"[(\[]([^)\]]+)[)\]]", name)
    base = re.sub(r"[(\[][^)\]]*[)\]]", " ", name)
    base = re.sub(re.escape(kind) + "|etb", " ", normalize(base))
    words = [w for v in variants for w in re.findall(r"[a-z0-9]+", normalize(v))]
    return " ".join(base.split()), words


def _results(data) -> list:
    if isinstance(data, dict):
        data = data.get("results")
    if not isinstance(data, list):
        raise CheckFailed("unexpected response from TCGCSV")
    return data


def pick_group(groups: list, set_name: str) -> dict | None:
    """The set whose name (after "SV10: ") ends with set_name; main sets beat supplements."""
    want = normalize(set_name)
    matches = []
    for g in groups:
        title = normalize(g.get("name", "")).split(":", 1)[-1].strip()
        if title == want or title.endswith(" " + want):
            matches.append(g)
    matches.sort(key=lambda g: (bool(g.get("isSupplemental")), normalize(g["name"]).split(":", 1)[-1].strip() != want))
    return matches[0] if matches else None


def pick_product(products: list, kind: str, variants: list) -> dict | None:
    """The plain retail ETB/Booster Bundle; variant words (e.g. "lucario") pick between versions."""
    candidates = [p for p in products
                  if has_word(p.get("name", ""), kind)
                  and not any(has_word(p.get("name", ""), w) for w in SKIP_WORDS)]
    if variants:
        candidates = [p for p in candidates if all(has_word(p["name"], v) for v in variants)]
    if len(candidates) == 1:
        return candidates[0]
    unbracketed = [p for p in candidates if "[" not in p["name"]]
    return unbracketed[0] if len(unbracketed) == 1 else None


def market_price(price_rows: list, product_id: int):
    """TCGplayer's market price; falls back to the mid price for brand-new products."""
    rows = [r for r in price_rows if r.get("productId") == product_id]
    rows.sort(key=lambda r: r.get("subTypeName") != "Normal")
    for key in ("marketPrice", "midPrice"):
        for r in rows:
            if r.get(key):
                return round(float(r[key]), 2)
    return None


class MarketPrices:
    def __init__(self, cache_path: Path = CACHE_PATH):
        self.cache_path = cache_path
        self.by_product: dict = {}    # our product id -> {"price", "tcgplayer_id", "name"}
        self.fetched_at = 0.0
        self.last_error: str | None = None
        self._next_try = 0.0
        self._task = None
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text())
            self.by_product, self.fetched_at = data["by_product"], float(data["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": self.fetched_at, "by_product": self.by_product}, indent=2))
        tmp.replace(self.cache_path)

    def get(self, product_id: str):
        entry = self.by_product.get(product_id)
        return entry.get("price") if entry else None

    def refresh_in_background(self, products: list, now: float | None = None) -> None:
        """Start a refresh if the data is over a day old (or the watch list changed)."""
        now = time.time() if now is None else now
        wanted = {p.id for p in products if product_type(p.name)}
        stale = now - self.fetched_at >= REFRESH_SECONDS or not wanted <= set(self.by_product)
        if not stale or now < self._next_try or (self._task and not self._task.done()):
            return
        self._task = asyncio.ensure_future(self._refresh(products, now))

    async def _refresh(self, products: list, now: float) -> None:
        try:
            self.by_product = await fetch_market(products)
            self.fetched_at, self.last_error = now, None
            self._save()
            matched = sum(1 for v in self.by_product.values() if v.get("price"))
            log.info("Market prices updated from TCGplayer: %d product(s) priced", matched)
        except Exception as e:
            self.last_error = str(e)
            self._next_try = now + RETRY_SECONDS
            log.warning("Couldn't update market prices (%s); retrying in an hour", e)


async def fetch_market(products: list) -> dict:
    """Download the needed sets and match every product. ~2 requests per set."""
    groups = _results(await http.get_json(f"{BASE}/groups"))
    plans = {}
    for p in products:
        kind = product_type(p.name)
        if not kind:
            continue
        derived_set, variants = set_and_variants(p.name, kind)
        group = pick_group(groups, p.extra.get("set") or derived_set)
        plans[p.id] = (p, kind, variants, group)

    catalogs = {}
    for _, _, _, group in plans.values():
        if group and group["groupId"] not in catalogs:
            gid = group["groupId"]
            catalogs[gid] = (_results(await http.get_json(f"{BASE}/{gid}/products")),
                             _results(await http.get_json(f"{BASE}/{gid}/prices")))
            await asyncio.sleep(0.1)  # TCGCSV asks for a short pause between requests

    out = {}
    for pid, (p, kind, variants, group) in plans.items():
        if not group:
            out[pid] = {"price": None, "why": "set not found on TCGplayer"}
            continue
        prods, prices = catalogs[group["groupId"]]
        if p.extra.get("tcgplayer_id"):
            match = next((x for x in prods if x.get("productId") == int(p.extra["tcgplayer_id"])), None)
        else:
            match = pick_product(prods, kind, variants)
        if not match:
            out[pid] = {"price": None, "why": f"no single {kind} match in {group['name']}"}
            continue
        out[pid] = {"price": market_price(prices, match["productId"]),
                    "tcgplayer_id": match["productId"], "name": match["name"]}
    return out


market_prices = MarketPrices()
