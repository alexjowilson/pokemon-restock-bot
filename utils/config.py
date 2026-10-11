"""Loads config/config.yaml and secrets from .env into one typed object."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


DEFAULT_WALMART_FIND_QUERIES = (
    "30th celebration booster bundle", "30th celebration elite trainer box",
    "chaos rising booster bundle", "pitch black booster bundle", "perfect order booster bundle",
    "delta reign booster bundle", "ascended heroes booster bundle",
    "prismatic evolutions booster bundle", "phantasmal flames booster bundle",
    "151 booster bundle", "white flare booster bundle", "black bolt booster bundle",
)


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    retailer: str
    url: str
    extra: dict = field(default_factory=dict)  # retailer-specific fields (sku, tcin, ...)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "retailer": self.retailer, "url": self.url, **self.extra}


@dataclass(frozen=True)
class Search:
    """Watch a store's search (Shopify or Walmart) for new/restocked listings matching keywords."""
    id: str
    store_name: str
    store_url: str
    queries: tuple
    exclude: tuple = ()
    retailer: str = "shopify"
    channel_id: int | None = None   # post this store's alerts here instead of the default channel
    role_id: int | None = None      # role to ping for this store (overrides alert_role_id)
    require_all: tuple = ()         # title must contain every one of these words
    include_any: tuple = ()         # ...and at least one of these (e.g. sealed product types)
    new_listing_max_age_hours: float = 72  # older listings that drift into results aren't "new"
    check_every_seconds: int | None = None  # slower than check_interval_seconds (API quotas)
    options: dict = field(default_factory=dict)  # retailer-specific (Walmart: max_price, ...)

    @property
    def name(self) -> str:  # used in health messages, like Product.name
        return f"{self.store_name} search"


@dataclass(frozen=True)
class Settings:
    discord_token: str
    app_id: int
    alert_channel_id: int
    guild_id: int | None
    health_channel_id: int | None
    alert_role_id: int | None
    check_interval_seconds: int
    products: list[Product]
    searches: list[Search] = field(default_factory=list)
    vote_reactions: bool = True
    walmart_find_queries: tuple = ()  # what plain /walmartfind searches for
    # Default channel/role per retailer, e.g. {"walmart": 123}. A search's or product's own
    # channel_id/role_id still wins; anything unlisted uses alert_channel_id/alert_role_id.
    retailer_channels: dict = field(default_factory=dict)
    retailer_roles: dict = field(default_factory=dict)

    def channel_for(self, alert: dict) -> int:
        return int(alert.get("channel_id") or self.retailer_channels.get(alert.get("retailer"))
                   or self.alert_channel_id)

    def role_for(self, alert: dict) -> int | None:
        role = (alert.get("role_id") or self.retailer_roles.get(alert.get("retailer"))
                or self.alert_role_id)
        return int(role) if role else None


def _optional_int(value) -> int | None:
    """Config IDs: missing, empty or 0 all mean "not set"."""
    return int(value) if value not in (None, "", 0, "0") else None


# Stores whose search API doesn't need a store_url in config.
DEFAULT_STORES = {"walmart": ("Walmart", "https://www.walmart.com")}
# Per-search default for retailers with daily API quotas.
DEFAULT_SEARCH_EVERY = {"walmart": 300}
WALMART_DAILY_LIMIT = 5000
SEARCH_KEYS = {"id", "retailer", "store_name", "store_url", "queries", "exclude", "require_all",
               "include_any", "new_listing_max_age_hours", "channel_id", "role_id",
               "check_every_seconds"}


def walmart_calls_per_day(products, searches, interval: int) -> int:
    """Product lookups are batched 20 ids per call; each search query is one call per pass."""
    day = 86400
    n_products = sum(1 for p in products if p.retailer == "walmart")
    auto_caps = [int(s.options.get("auto_watch_max_total", 40)) for s in searches
                 if s.retailer == "walmart" and s.options.get("auto_watch")]
    if auto_caps:  # auto-watch fills the list up to its cap (configured items included)
        n_products = max([n_products] + auto_caps)
    calls = -(-n_products // 20) * day / interval
    for s in searches:
        if s.retailer == "walmart":
            calls += len(s.queries) * day / max(interval, s.check_every_seconds or interval)
    return int(calls)


def _id_map(raw, what: str) -> dict:
    """{retailer: id} from YAML, dropping unset (0/empty) entries."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"discord.{what} should look like `walmart: 123456789`")
    return {str(k).lower(): v for k, raw_v in raw.items() if (v := _optional_int(raw_v))}


def _with_msrp(products, table, deals: dict) -> list:
    """Give every product an `msrp` (its own, or by type from the table) and the
    reseller-deal threshold, so checks and alerts can compare prices."""
    from utils.pricing import DEFAULT_MSRP, msrp_for
    table = {str(k).lower(): float(v) for k, v in (table or DEFAULT_MSRP).items()}
    enabled = deals.get("enabled", True)
    deal_pct = deals.get("max_over_msrp_pct") if enabled else None
    below_market = deals.get("below_market_pct") if enabled else None
    out = []
    for p in products:
        msrp = msrp_for(p.name, p.extra.get("msrp"), table)
        extra = dict(p.extra)
        if below_market is not None and p.retailer == "walmart":
            extra["below_market_pct"] = float(below_market)
        if msrp is not None:
            extra["msrp"] = msrp
            if deal_pct is not None and p.retailer == "walmart":
                extra["deal_pct"] = float(deal_pct)
        out.append(Product(id=p.id, name=p.name, retailer=p.retailer, url=p.url, extra=extra))
    return out


def _walmart_watch(entries, configured) -> list:
    """Expand `walmart_watch:` groups (one product name, several Walmart pages) into products.

    Walmart lists the same ETB on several pages and only one is usually sold by Walmart
    itself, so each group watches every page and the alert uses the group's clean name.
    These are checked quietly: a page Walmart removes reads as unavailable, failures
    only log (one outage shouldn't post 40 warnings), and the first check records the
    current state without alerting.
    """
    taken = {str(p.extra.get("item_id") or "") for p in configured if p.retailer == "walmart"}
    taken |= {p.url.rstrip("/").rsplit("/", 1)[-1] for p in configured if p.retailer == "walmart"}
    products = []
    for entry in entries:
        name = entry.get("name")
        ids = entry.get("item_ids") or []
        if not name or not ids:
            raise RuntimeError("Each walmart_watch entry needs a `name` and `item_ids`")
        options = {k: v for k, v in entry.items()
                   if k in ("max_price", "allow_marketplace", "msrp", "set", "tcgplayer_id")}
        for raw_id in ids:
            item_id = str(int(raw_id))
            if item_id in taken:
                continue  # already watched (e.g. listed under products:)
            taken.add(item_id)
            products.append(Product(
                id=f"wmw_{item_id}", name=name, retailer="walmart",
                url=f"https://www.walmart.com/ip/{item_id}",
                extra={"item_id": item_id, "auto": True, "baseline_first": True, **options},
            ))
    return products


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (set it in .env)")
    return value


def load_settings(config_path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    raw = yaml.safe_load(config_path.read_text()) or {}

    discord_cfg = raw.get("discord", {})
    # Env var wins over YAML so the channel can differ between dev and prod servers.
    channel_id = os.getenv("ALERT_CHANNEL_ID") or discord_cfg.get("alert_channel_id")
    if not channel_id:
        raise RuntimeError("Set discord.alert_channel_id in config.yaml or ALERT_CHANNEL_ID in .env")
    guild_id = os.getenv("GUILD_ID") or discord_cfg.get("guild_id")
    health_channel_id = os.getenv("HEALTH_CHANNEL_ID") or discord_cfg.get("health_channel_id")
    alert_role_id = os.getenv("ALERT_ROLE_ID") or discord_cfg.get("alert_role_id")

    products = []
    seen_ids = set()
    for p in raw.get("products", []):
        p = dict(p)
        pid = p.pop("id")
        if pid in seen_ids:
            raise RuntimeError(f"Duplicate product id in config: {pid}")
        seen_ids.add(pid)
        retailer = p.pop("retailer")
        url = p.pop("url", None)
        if not url and retailer == "walmart" and p.get("item_id"):
            url = f"https://www.walmart.com/ip/{int(p['item_id'])}"
        if not url:
            raise RuntimeError(f"Product {pid} needs a `url`")
        products.append(Product(id=pid, name=p.pop("name"), retailer=retailer, url=url, extra=p))

    products += _walmart_watch(raw.get("walmart_watch") or [], products)
    products = _with_msrp(products, raw.get("msrp"), raw.get("reseller_deals") or {})

    searches = []
    for raw_search in raw.get("searches", []) or []:
        sid = raw_search["id"]
        if sid in seen_ids:
            raise RuntimeError(f"Duplicate id in config: {sid}")
        seen_ids.add(sid)
        queries = raw_search.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        if not queries:
            raise RuntimeError(f"Search {sid} needs at least one entry in `queries`")
        retailer = str(raw_search.get("retailer", "shopify")).lower()
        default_name, default_url = DEFAULT_STORES.get(retailer, (None, None))
        store_url = raw_search.get("store_url") or default_url
        if not store_url:
            raise RuntimeError(f"Search {sid} needs a `store_url`")
        searches.append(Search(
            id=sid,
            retailer=retailer,
            store_name=raw_search.get("store_name") or default_name or store_url,
            store_url=store_url.rstrip("/"),
            queries=tuple(queries),
            exclude=tuple(raw_search.get("exclude", []) or []),
            require_all=tuple(raw_search.get("require_all", []) or []),
            include_any=tuple(raw_search.get("include_any", []) or []),
            new_listing_max_age_hours=float(raw_search.get("new_listing_max_age_hours", 72)),
            channel_id=_optional_int(raw_search.get("channel_id")),
            role_id=_optional_int(raw_search.get("role_id")),
            check_every_seconds=_optional_int(raw_search.get("check_every_seconds",
                                                             DEFAULT_SEARCH_EVERY.get(retailer))),
            options={k: v for k, v in raw_search.items() if k not in SEARCH_KEYS},
        ))

    interval = max(30, int(raw.get("check_interval_seconds", 60)))
    calls = walmart_calls_per_day(products, searches, interval)
    if calls > WALMART_DAILY_LIMIT * 0.9:
        log.warning("Walmart config needs ~%d API calls/day (limit %d). Raise check_every_seconds "
                    "on Walmart searches or use fewer queries.", calls, WALMART_DAILY_LIMIT)

    return Settings(
        discord_token=_require_env("DISCORD_TOKEN"),
        app_id=int(_require_env("APP_ID")),
        alert_channel_id=int(channel_id),
        guild_id=int(guild_id) if guild_id else None,
        health_channel_id=int(health_channel_id) if health_channel_id else None,
        alert_role_id=int(alert_role_id) if alert_role_id else None,
        check_interval_seconds=interval,
        products=products,
        searches=searches,
        vote_reactions=bool(discord_cfg.get("vote_reactions", True)),
        walmart_find_queries=tuple(raw.get("walmart_find_queries") or DEFAULT_WALMART_FIND_QUERIES),
        retailer_channels=_id_map(discord_cfg.get("retailer_channels"), "retailer_channels"),
        retailer_roles=_id_map(discord_cfg.get("retailer_roles"), "retailer_roles"),
    )
