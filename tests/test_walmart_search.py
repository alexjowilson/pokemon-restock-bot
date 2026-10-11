import asyncio

import pytest

import bot.scheduler as sched
from monitors import walmart
from utils.config import Product, Search, Settings, load_settings, walmart_calls_per_day


def wm(item_id, name, sold_by="Walmart.com", online=True, price=49.97):
    return {"itemId": item_id, "name": name, "salePrice": price, "stock": "Available" if online
            else "Not available", "availableOnline": online, "marketplace": sold_by != "Walmart.com",
            "sellerInfo": sold_by, "largeImage": f"https://img/{item_id}.jpg",
            "addToCartUrl": f"https://affil.walmart.com/cart?items={item_id}"}


def search(**opts):
    return Search(id="walmart", store_name="Walmart", store_url="https://www.walmart.com",
                  queries=("pokemon etb",), retailer="walmart", check_every_seconds=300,
                  require_all=("pokemon",), options=opts)


@pytest.fixture
def api(monkeypatch):
    calls = []

    def respond(items):
        async def fake(url, params):
            calls.append(params)
            return {"items": items}
        monkeypatch.setattr(walmart, "_signed_get", fake)
    respond.calls = calls
    return respond


def test_search_listings_shape_and_newest_first(api):
    api([wm(1, "Pokemon 30th ETB")])
    [x] = asyncio.run(walmart.search_listings(search(), "pokemon etb"))
    assert x == {"listing_id": "1", "first_party": True, "quiet_new": False,
                 "name": "Pokemon 30th ETB", "url": "https://www.walmart.com/ip/1",
                 "in_stock": True, "price": 49.97, "image_url": "https://img/1.jpg",
                 "cart_url": "https://affil.walmart.com/cart?items=1", "note": None}
    assert api.calls[0] == {"query": "pokemon etb", "numItems": "25", "sort": "new"}


def test_resellers_kept_quietly_unless_allowed(api):
    api([wm(1, "Pokemon ETB", sold_by="CardFlipper LLC", price=149.0), wm(2, "Pokemon ETB")])
    reseller, walmart_sold = asyncio.run(walmart.search_listings(search(), "q"))
    assert (reseller["in_stock"], reseller["quiet_new"]) == (False, True)
    assert (walmart_sold["in_stock"], walmart_sold["quiet_new"]) == (True, False)
    reseller, _ = asyncio.run(walmart.search_listings(search(allow_marketplace=True), "q"))
    assert (reseller["in_stock"], reseller["quiet_new"]) == (True, False)


def test_max_price_means_not_buyable(api):
    api([wm(1, "Pokemon ETB", price=89.0)])
    [x] = asyncio.run(walmart.search_listings(search(max_price=60), "q"))
    assert x["in_stock"] is False and x["cart_url"] is None


def test_no_results_is_empty_not_error(monkeypatch):
    async def fake(url, params):
        return {"query": "x", "totalResults": 0}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    assert asyncio.run(walmart.search_listings(search(), "q")) == []


def test_walmart_search_runs_on_its_own_schedule_and_alerts_new(monkeypatch):
    pages = [[wm(1, "Pokemon Old ETB")],
             [wm(1, "Pokemon Old ETB"), wm(2, "Pokemon 30th Celebration ETB")]]

    async def fake_search(s, q):
        return await _listings(pages.pop(0))

    async def _listings(items):
        async def fake(url, params):
            return {"items": items}
        monkeypatch.setattr(walmart, "_signed_get", fake)
        return await walmart.search_listings(search(), "q")

    async def no_lookup(url):
        raise AssertionError("Walmart listings must not use the Shopify age lookup")

    sent = []

    async def send_alert(a):
        sent.append((a["kind"], a["name"], a["retailer"], a["store_name"]))

    monkeypatch.setitem(sched.SEARCHERS, "walmart", fake_search)
    monkeypatch.setattr(sched, "listing_age_hours", no_lookup)
    state, s = {}, search()
    asyncio.run(sched.run_search_checks([s], state, send_alert, now=1000))      # baseline
    asyncio.run(sched.run_search_checks([s], state, send_alert, now=1060))      # not due yet
    assert len(pages) == 1 and sent == []
    asyncio.run(sched.run_search_checks([s], state, send_alert, now=1300))
    assert sent == [("new", "Pokemon 30th Celebration ETB", "walmart", "Walmart")]


def test_alerts_route_to_walmart_channel():
    s = Settings(discord_token="t", app_id=1, alert_channel_id=1, guild_id=None,
                 health_channel_id=None, alert_role_id=None, check_interval_seconds=60,
                 products=[], retailer_channels={"walmart": 99}, retailer_roles={"walmart": 7})
    assert (s.channel_for({"retailer": "walmart"}), s.role_for({"retailer": "walmart"})) == (99, 7)
    assert (s.channel_for({"retailer": "shopify"}), s.role_for({"retailer": "shopify"})) == (1, None)
    assert s.channel_for({"retailer": "walmart", "channel_id": 5}) == 5


def test_config_walmart_search(tmp_path, monkeypatch):
    for k in ("ALERT_CHANNEL_ID", "GUILD_ID", "HEALTH_CHANNEL_ID", "ALERT_ROLE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("APP_ID", "1")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
discord:
  alert_channel_id: 10
  retailer_channels: {Walmart: 20, shopify: 0}
searches:
  - id: wm
    retailer: walmart
    queries: ["pokemon etb"]
    max_price: 69.99
""")
    s = load_settings(cfg)
    [w] = s.searches
    assert (w.store_name, w.check_every_seconds, w.options) == ("Walmart", 300, {"max_price": 69.99})
    assert s.retailer_channels == {"walmart": 20}


def test_walmart_call_budget():
    products = [Product(id=f"p{i}", name="x", retailer="walmart", url="u") for i in range(21)]
    searches = [Search(id="w", store_name="Walmart", store_url="u", queries=("a", "b"),
                       retailer="walmart", check_every_seconds=300)]
    # 21 products = 2 batched calls/min (2,880) + 2 queries every 5 min (576)
    assert walmart_calls_per_day(products, searches, 60) == 3456
