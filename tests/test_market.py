import asyncio

import pytest

import bot.scheduler as sched
from bot.commands import format_product_row
from bot.notifier import build_alert
from monitors import market, walmart
from monitors.market import (MarketPrices, fetch_market, market_price, pick_group, pick_product,
                             set_and_variants)
from utils.config import Product
from utils.pricing import market_verdict

# Real names from tcgcsv.com (TCGplayer category 3), trimmed.
GROUPS = [
    {"groupId": 24269, "name": "SV10: Destined Rivals", "isSupplemental": False},
    {"groupId": 24380, "name": "ME01: Mega Evolution", "isSupplemental": False},
    {"groupId": 24451, "name": "ME: Mega Evolution Promo", "isSupplemental": False},
    {"groupId": 24722, "name": "ME: 30th Celebration", "isSupplemental": False},
    {"groupId": 24837, "name": "ME: 30th Celebration Classic Collection", "isSupplemental": True},
    {"groupId": 23237, "name": "SV: Scarlet & Violet 151", "isSupplemental": False},
]
DR = [
    {"productId": 624675, "name": "Destined Rivals Pokemon Center Elite Trainer Box (Exclusive)"},
    {"productId": 624676, "name": "Destined Rivals Elite Trainer Box"},
    {"productId": 625670, "name": "Destined Rivals Booster Bundle"},
    {"productId": 628398, "name": "Destined Rivals Elite Trainer Box Case"},
    {"productId": 628428, "name": "Destined Rivals Booster Bundle Case"},
]
ME = [
    {"productId": 644279, "name": "Mega Evolution Elite Trainer Box [Mega Gardevoir]"},
    {"productId": 644282, "name": "Mega Evolution Pokemon Center Elite Trainer Box (Exclusive) [Mega Lucario]"},
    {"productId": 644362, "name": "Mega Evolution Booster Bundle"},
    {"productId": 644727, "name": "Mega Evolution Elite Trainer Box Case"},
    {"productId": 648394, "name": "Mega Evolution Elite Trainer Box [Mega Lucario]"},
]


@pytest.mark.parametrize("name, kind, expected", [
    ("Destined Rivals Elite Trainer Box", "elite trainer box", ("destined rivals", [])),
    ("Mega Evolution Elite Trainer Box (Lucario)", "elite trainer box", ("mega evolution", ["lucario"])),
    ("30th Celebration Booster Bundle", "booster bundle", ("30th celebration", [])),
])
def test_set_and_variants(name, kind, expected):
    assert set_and_variants(name, kind) == expected


def test_pick_group():
    assert pick_group(GROUPS, "destined rivals")["groupId"] == 24269
    assert pick_group(GROUPS, "mega evolution")["groupId"] == 24380      # not the Promo set
    assert pick_group(GROUPS, "30th celebration")["groupId"] == 24722    # not the supplement
    assert pick_group(GROUPS, "151")["groupId"] == 23237
    assert pick_group(GROUPS, "black bolt & white flare") is None


def test_pick_product_skips_center_cases_and_uses_variant():
    assert pick_product(DR, "elite trainer box", [])["productId"] == 624676
    assert pick_product(DR, "booster bundle", [])["productId"] == 625670
    assert pick_product(ME, "elite trainer box", ["lucario"])["productId"] == 648394
    assert pick_product(ME, "elite trainer box", ["gardevoir"])["productId"] == 644279
    assert pick_product(ME, "elite trainer box", []) is None  # ambiguous: two versions


def test_promo_cards_named_after_the_box_are_ignored():
    promo = {"productId": 9, "name": "Eevee - 173 (Destined Rivals Elite Trainer Box Exclusive)",
             "extendedData": [{"name": "Number", "value": "173"}, {"name": "Rarity", "value": "Promo"}]}
    assert pick_product(DR + [promo], "elite trainer box", [], "destined rivals")["productId"] == 624676


def test_exact_name_wins_when_several_sealed_match():
    extra = {"productId": 8, "name": "Destined Rivals Elite Trainer Box (Japanese Import)"}
    assert pick_product(DR + [extra], "elite trainer box", [], "destined rivals")["productId"] == 624676


def test_market_price_prefers_market_then_mid():
    rows = [{"productId": 1, "marketPrice": None, "midPrice": 61.5, "subTypeName": "Normal"},
            {"productId": 2, "marketPrice": 72.349, "midPrice": 70, "subTypeName": "Normal"}]
    assert market_price(rows, 2) == 72.35 and market_price(rows, 1) == 61.5
    assert market_price(rows, 3) is None


def test_fetch_market_matches_watch_products(monkeypatch):
    calls = []

    async def fake_get_json(url, params=None, headers=None):
        calls.append(url.rsplit("/tcgplayer/3", 1)[1])
        if url.endswith("/groups"):
            return {"success": True, "results": GROUPS}
        gid = int(url.split("/")[-2])
        if url.endswith("/products"):
            return {"results": DR if gid == 24269 else ME}
        rows = [{"productId": p["productId"], "marketPrice": 70.0 + i, "subTypeName": "Normal"}
                for i, p in enumerate(DR if gid == 24269 else ME)]
        return {"results": rows}
    monkeypatch.setattr(market.http, "get_json", fake_get_json)
    products = [
        Product(id="a", name="Destined Rivals Elite Trainer Box", retailer="walmart", url="u"),
        Product(id="b", name="Mega Evolution Elite Trainer Box (Lucario)", retailer="walmart", url="u"),
        Product(id="c", name="Black Bolt & White Flare Elite Trainer Box", retailer="walmart", url="u"),
        Product(id="d", name="Destined Rivals Booster Bundle", retailer="walmart", url="u",
                extra={"tcgplayer_id": 625670}),
    ]
    out = asyncio.run(fetch_market(products))
    assert out["a"] == {"price": 71.0, "tcgplayer_id": 624676, "name": "Destined Rivals Elite Trainer Box"}
    assert out["b"]["tcgplayer_id"] == 648394
    assert out["c"]["price"] is None and "set not found" in out["c"]["why"]
    assert out["d"]["tcgplayer_id"] == 625670
    assert calls == ["/groups", "/24269/products", "/24269/prices", "/24380/products", "/24380/prices"]


def test_refresh_is_daily_and_cached(tmp_path, monkeypatch):
    mp = MarketPrices(cache_path=tmp_path / "market.json")
    started = []

    async def fake_refresh(products, now):
        started.append(now)
        mp.by_product, mp.fetched_at = {"a": {"price": 50}}, now
    monkeypatch.setattr(mp, "_refresh", fake_refresh)
    products = [Product(id="a", name="X Booster Bundle", retailer="walmart", url="u")]

    async def run():
        for now in (1000, 1060, 1000 + 86400):
            mp.refresh_in_background(products, now=now)
            await asyncio.sleep(0)
    asyncio.run(run())
    assert started == [1000, 1000 + 86400]


def test_market_verdicts():
    assert market_verdict(60, 72) == "📈 market $72.00: 🟢 17% below"
    assert market_verdict(70, 72) == "📈 market $72.00: ⚪ about market"
    assert market_verdict(112.98, 72) == "📈 market $72.00: 🔴 57% above"
    assert market_verdict(50, None) == ""


def test_checknow_and_alert_show_market(monkeypatch):
    monkeypatch.setattr(market.market_prices, "by_product", {"x": {"price": 72.0}})
    p = Product(id="x", name="Destined Rivals Elite Trainer Box", retailer="walmart", url="u",
                extra={"msrp": 49.99})
    row = format_product_row(p, {"in_stock": False, "price": 112.98, "note": "only resellers"})
    assert row.endswith("$112.98 (🔴 +126% vs $49.99 MSRP · 📈 market $72.00: 🔴 57% above)")
    _, embed = build_alert({"name": "n", "url": "u", "in_stock": True, "price": 49.97,
                            "msrp": 49.99, "market": 72.0}, now=0)
    assert "📈 market $72.00: 🟢 31% below" in embed.description


def test_below_market_reseller_deal_alert(monkeypatch):
    monkeypatch.setattr(market.market_prices, "by_product", {"wmw_2": {"price": 100.0}})
    p = Product(id="wmw_2", name="Prismatic Evolutions Elite Trainer Box", retailer="walmart",
                url="u", extra={"item_id": "2", "msrp": 49.99, "below_market_pct": 10.0})
    prices = iter([95.0, 88.0])

    async def fake(url, params):
        return {"items": [{"itemId": 2, "name": "x", "salePrice": next(prices), "stock": "Available",
                           "availableOnline": True, "marketplace": True, "sellerInfo": "Flipper"}]}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    sent, state = [], {}

    async def send_alert(a):
        sent.append((a["kind"], a["price"], a["market"]))
    for i in range(2):
        asyncio.run(sched.run_checks([p], state, send_alert, now=1000 + 60 * i))
    assert sent == [("deal", 88.0, 100.0)]  # 95 is only 5% under market; 88 is 12% under


def test_cache_from_older_matching_rules_is_ignored(tmp_path):
    import json
    path = tmp_path / "market.json"
    path.write_text(json.dumps({"fetched_at": 1, "by_product": {"a": {"price": 1}}}))
    assert MarketPrices(cache_path=path).by_product == {}
    mp = MarketPrices(cache_path=path)
    mp.by_product, mp.fetched_at = {"a": {"price": 5}}, 9
    mp._save()
    assert MarketPrices(cache_path=path).get("a") == 5
