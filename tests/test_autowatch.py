import asyncio

import bot.scheduler as sched
from bot import autowatch
from monitors import walmart
from utils.config import Product, Search, walmart_calls_per_day


def wm(item_id, name, sold_by="Walmart.com", online=True, price=49.97):
    return {"itemId": item_id, "name": name, "salePrice": price,
            "stock": "Available" if online else "Not available", "availableOnline": online,
            "marketplace": sold_by != "Walmart.com", "sellerInfo": sold_by,
            "largeImage": f"https://img/{item_id}.jpg",
            "addToCartUrl": f"https://affil.walmart.com/cart?items={item_id}"}


def sets_search(**opts):
    return Search(id="walmart_sets", store_name="Walmart", store_url="https://www.walmart.com",
                  queries=("pokemon delta reign elite trainer box",), retailer="walmart",
                  require_all=("pokemon",), include_any=("elite trainer box", "etb"),
                  check_every_seconds=3600, options={"auto_watch": True, **opts})


CONFIGURED = [Product(id="wm_30th", name="30th ETB", retailer="walmart",
                      url="https://www.walmart.com/ip/x/111")]


def run_search(monkeypatch, state, items, now, search=None):
    async def fake(url, params):
        return {"items": items}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    sent = []

    async def send_alert(a):
        sent.append((a["kind"], a["name"]))
    asyncio.run(sched.run_search_checks([search or sets_search()], state, send_alert, now=now,
                                        configured_products=CONFIGURED))
    return sent


def test_search_finds_items_and_auto_watches_them(monkeypatch):
    state = {}
    run_search(monkeypatch, state, [
        wm(111, "Pokemon 30th ETB"),                                     # already configured
        wm(222, "Pokemon Delta Reign ETB", sold_by="Flipper", price=120),  # reseller only
        wm(333, "Pokemon Delta Reign ETB Pokemon Center"),
    ], now=1_000_000)
    watched = autowatch.auto_products(state, CONFIGURED)
    assert {p.extra["item_id"] for p in watched} == {"222", "333"}
    # Baselines come from the search, so joining the watch list can't trigger a restock alert.
    assert state["auto_walmart_222"]["in_stock"] is False
    assert state["auto_walmart_333"]["in_stock"] is True


def test_reseller_listing_never_announced_as_new(monkeypatch):
    state = {}
    run_search(monkeypatch, state, [], now=1_000_000)  # baseline pass
    sent = run_search(monkeypatch, state, [wm(222, "Pokemon Delta Reign ETB", sold_by="Flipper"),
                                           wm(333, "Pokemon Delta Reign ETB")], now=1_010_000)
    assert sent == [("new", "Pokemon Delta Reign ETB")]


def test_auto_watched_item_alerts_when_walmart_starts_selling(monkeypatch):
    state = {}
    run_search(monkeypatch, state, [wm(222, "Pokemon Delta Reign ETB", sold_by="Flipper", price=120)],
               now=1_000_000)
    [product] = autowatch.auto_products(state, CONFIGURED)

    async def fake(url, params):
        assert params == {"ids": "222"}
        return {"items": [wm(222, "Pokemon Delta Reign ETB")]}  # Walmart is the seller now
    monkeypatch.setattr(walmart, "_signed_get", fake)
    sent = []

    async def send_alert(a):
        sent.append((a["name"], a["retailer"], a["in_stock"]))
    asyncio.run(sched.run_checks([product], state, send_alert, now=1_000_060))
    assert sent == [("Pokemon Delta Reign ETB", "walmart", True)]


def test_auto_watch_respects_cap_and_prefers_walmart_sold(monkeypatch):
    state = {}
    items = [wm(i, f"Pokemon Set {i} ETB", sold_by="Flipper") for i in range(500, 505)]
    items.append(wm(999, "Pokemon Walmart ETB"))
    run_search(monkeypatch, state, items, now=1_000_000, search=sets_search(auto_watch_max_total=3))
    # 3 total, 1 already configured -> 2 slots; Walmart-sold item gets one first.
    ids = list(state["_auto_watch"])
    assert len(ids) == 2 and "999" in ids


def test_delisted_auto_item_is_out_of_stock_not_a_failure(monkeypatch):
    async def fake(url, params):
        return {"items": []}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    r = asyncio.run(walmart.check_product({"item_id": 222, "auto": True}))
    assert r["in_stock"] is False


def test_auto_item_failures_stay_out_of_discord(monkeypatch):
    product = Product(id="auto_walmart_222", name="x", retailer="walmart", url="u",
                      extra={"item_id": "222", "auto": True})

    async def boom(url, params):
        from monitors.errors import CheckFailed
        raise CheckFailed("walmart down")
    monkeypatch.setattr(walmart, "_signed_get", boom)
    health_msgs, health, state = [], {}, {}

    async def send_health(t):
        health_msgs.append(t)

    async def send_alert(a):
        raise AssertionError("no alert expected")
    for i in range(6):
        asyncio.run(sched.run_checks([product], state, send_alert, health, send_health, now=10**6 * i))
    assert health["auto_walmart_222"]["failures"] == 6 and health_msgs == []


def test_budget_counts_auto_watch_cap():
    searches = [sets_search(), Search(id="new", store_name="Walmart", store_url="u",
                                      queries=("a", "b"), retailer="walmart", check_every_seconds=300)]
    # 40 items = 2 calls/min (2,880) + 1 query/hour (24) + 2 queries/5 min (576)
    assert walmart_calls_per_day(CONFIGURED, searches, 60) == 3480


def test_removing_the_search_stops_its_auto_watch(monkeypatch):
    state = {}
    run_search(monkeypatch, state, [wm(333, "Pokemon Delta Reign ETB")], now=1_000_000)
    assert len(autowatch.auto_products(state, CONFIGURED, [sets_search()])) == 1
    assert autowatch.auto_products(state, CONFIGURED, []) == []
    assert state["_auto_watch"] == {} and "auto_walmart_333" not in state
