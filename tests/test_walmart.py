import asyncio
import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from monitors import walmart
from monitors.errors import CheckFailed

URL = "https://www.walmart.com/ip/Pokemon-TCG-30th-Celebration-Elite-Trainer-Box/5123456789"


def item(**overrides):
    base = {
        "itemId": 5123456789,
        "name": "Pokemon TCG 30th Celebration Elite Trainer Box",
        "salePrice": 49.97,
        "stock": "Available",
        "availableOnline": True,
        "marketplace": False,
        "sellerInfo": "Walmart.com",
        "largeImage": "https://i5.walmartimages.com/etb.jpg",
        "addToCartUrl": "https://affil.walmart.com/cart/addToCart?items=5123456789",
    }
    base.update(overrides)
    return base


# ---------- item id ----------

@pytest.mark.parametrize("product, expected", [
    ({"url": URL}, "5123456789"),
    ({"url": URL + "?classType=REGULAR&from=/search"}, "5123456789"),
    ({"url": "https://www.walmart.com/ip/5123456789"}, "5123456789"),
    ({"url": "https://www.walmart.com/whatever", "item_id": 42}, "42"),
])
def test_item_id(product, expected):
    assert walmart.item_id_from(product) == expected


def test_item_id_missing():
    with pytest.raises(CheckFailed):
        walmart.item_id_from({"url": "https://www.walmart.com/browse/toys"})


# ---------- stock logic ----------

def test_first_party_in_stock():
    r = walmart.parse_item(item(), {"url": URL})
    assert r["in_stock"] is True
    assert r["price"] == 49.97
    assert r["cart_url"].startswith("https://affil.walmart.com/cart")
    assert r["image_url"] == "https://i5.walmartimages.com/etb.jpg"


@pytest.mark.parametrize("stock", ["Limited Supply", "Last few items", "available"])
def test_low_stock_still_counts(stock):
    assert walmart.parse_item(item(stock=stock), {"url": URL})["in_stock"] is True


@pytest.mark.parametrize("overrides", [
    {"stock": "Not available"},
    {"availableOnline": False},
    {"stock": None},
])
def test_out_of_stock(overrides):
    r = walmart.parse_item(item(**overrides), {"url": URL})
    assert r["in_stock"] is False and r["cart_url"] is None


def test_reseller_ignored_by_default():
    reseller = item(marketplace=True, sellerInfo="PokeFlipz LLC", salePrice=129.99)
    assert walmart.parse_item(reseller, {"url": URL})["in_stock"] is False
    assert walmart.parse_item(reseller, {"url": URL, "allow_marketplace": True})["in_stock"] is True


def test_max_price_filter():
    assert walmart.parse_item(item(salePrice=89.99), {"url": URL, "max_price": 59.99})["in_stock"] is False
    assert walmart.parse_item(item(salePrice=49.97), {"url": URL, "max_price": 59.99})["in_stock"] is True


# ---------- signing ----------

def test_auth_headers_signature_verifies():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    headers = walmart.auth_headers("consumer-123", "1", key, now_ms=1760000000000)
    assert headers["WM_CONSUMER.ID"] == "consumer-123"
    assert headers["WM_CONSUMER.INTIMESTAMP"] == "1760000000000"
    assert headers["WM_SEC.KEY_VERSION"] == "1"
    # Raises InvalidSignature if the signed string or algorithm is wrong.
    key.public_key().verify(
        base64.b64decode(headers["WM_SEC.AUTH_SIGNATURE"]),
        b"consumer-123\n1760000000000\n1\n",
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("WALMART_CONSUMER_ID", raising=False)
    monkeypatch.delenv("WALMART_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(CheckFailed, match="WALMART_CONSUMER_ID"):
        asyncio.run(walmart.check_product({"url": URL}))


def test_missing_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WALMART_CONSUMER_ID", "abc")
    monkeypatch.setenv("WALMART_PRIVATE_KEY_PATH", str(tmp_path / "nope.pem"))
    with pytest.raises(CheckFailed, match="not found"):
        asyncio.run(walmart.check_product({"url": URL}))


# ---------- end to end with a fake API ----------

def test_check_product_sends_signed_request(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "wm.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.setenv("WALMART_CONSUMER_ID", "consumer-123")
    monkeypatch.setenv("WALMART_PRIVATE_KEY_PATH", str(pem))

    seen = {}

    async def fake_get_json(url, params=None, headers=None):
        seen.update(url=url, params=params, headers=headers)
        return {"items": [item()]}

    monkeypatch.setattr(walmart.http, "get_json", fake_get_json)
    r = asyncio.run(walmart.check_product({"url": URL}))
    assert r["in_stock"] is True
    assert seen["url"] == walmart.LOOKUP_URL
    assert seen["params"] == {"ids": "5123456789"}
    assert seen["headers"]["WM_CONSUMER.ID"] == "consumer-123"
    assert "WM_SEC.AUTH_SIGNATURE" in seen["headers"]


def test_check_product_no_items(monkeypatch, tmp_path):
    monkeypatch.setattr(walmart, "_credentials", lambda: ("c", "1", None))
    monkeypatch.setattr(walmart, "auth_headers", lambda *a, **k: {})

    async def fake_get_json(url, params=None, headers=None):
        return {"items": []}

    monkeypatch.setattr(walmart.http, "get_json", fake_get_json)
    with pytest.raises(CheckFailed, match="no item"):
        asyncio.run(walmart.check_product({"url": URL}))


# ---------- /checknow lines ----------

def test_reseller_note_and_checknow_block():
    from bot.commands import format_product_block
    from utils.config import Product

    p = Product(id="wm", name="30th Celebration ETB", retailer="walmart", url=URL)
    resold = walmart.parse_item(item(marketplace=True, sellerInfo="Flipper LLC", salePrice=237.98), {"url": URL})
    assert resold["note"] == "only resellers"
    walmart_has_it = walmart.parse_item(item(), {"url": URL})
    gone = walmart.parse_item(item(stock="Not available", availableOnline=False), {"url": URL})

    block = format_product_block("Walmart", [(p, walmart_has_it, None), (p, resold, None),
                                             (p, gone, None), (p, None, CheckFailed("HTTP 401"))])
    assert block.split("\n") == [
        "🏪 **Walmart**: 1 in stock, 1 resellers only",
        f"✅ [30th Celebration ETB](<{URL}>) · $49.97",
        f"🟡 [30th Celebration ETB](<{URL}>) · only resellers $237.98",
        f"❌ [30th Celebration ETB](<{URL}>)",
        "⚠️ 30th Celebration ETB: `HTTP 401`",
    ]

    over = walmart.parse_item(item(salePrice=89.99), {"url": URL, "max_price": 59.99})
    assert over["note"] == "over max $59.99"


# ---------- batching ----------

def _fake_api(monkeypatch, items_by_id, calls, fail=None):
    monkeypatch.setattr(walmart, "_credentials", lambda: ("c", "1", None))
    monkeypatch.setattr(walmart, "auth_headers", lambda *a, **k: {})
    monkeypatch.setattr(walmart, "BATCH_WINDOW_SECONDS", 0)

    async def fake_get_json(url, params=None, headers=None):
        calls.append((url, params))
        if fail:
            raise fail
        ids = params["ids"].split(",")
        return {"items": [items_by_id[i] for i in ids if i in items_by_id]}

    monkeypatch.setattr(walmart.http, "get_json", fake_get_json)


def test_concurrent_checks_share_one_call(monkeypatch):
    calls = []
    _fake_api(monkeypatch, {"1": item(itemId=1), "2": item(itemId=2, stock="Not available")}, calls)

    async def run():
        return await asyncio.gather(walmart.check_product({"item_id": 1}),
                                    walmart.check_product({"item_id": 2}))

    a, b = asyncio.run(run())
    assert a["in_stock"] is True and b["in_stock"] is False
    assert len(calls) == 1 and sorted(calls[0][1]["ids"].split(",")) == ["1", "2"]


def test_more_than_20_ids_split_into_chunks(monkeypatch):
    calls = []
    _fake_api(monkeypatch, {str(i): item(itemId=i) for i in range(1, 26)}, calls)

    async def run():
        return await asyncio.gather(*(walmart.check_product({"item_id": i}) for i in range(1, 26)))

    assert all(r["in_stock"] for r in asyncio.run(run()))
    assert [len(c[1]["ids"].split(",")) for c in calls] == [20, 5]


def test_batch_failure_reaches_every_check(monkeypatch):
    _fake_api(monkeypatch, {}, [], fail=CheckFailed("HTTP 401"))

    async def run():
        return await asyncio.gather(walmart.check_product({"item_id": 1}),
                                    walmart.check_product({"item_id": 2}), return_exceptions=True)

    assert all(isinstance(r, CheckFailed) for r in asyncio.run(run()))


def test_missing_item_in_batch(monkeypatch):
    _fake_api(monkeypatch, {"1": item(itemId=1)}, [])

    async def run():
        return await asyncio.gather(walmart.check_product({"item_id": 1}),
                                    walmart.check_product({"item_id": 999}), return_exceptions=True)

    ok, missing = asyncio.run(run())
    assert ok["in_stock"] is True
    assert isinstance(missing, CheckFailed) and "999" in str(missing)


# ---------- search / find ----------

def test_search_items_and_find_output(monkeypatch):
    monkeypatch.setattr(walmart, "_credentials", lambda: ("c", "1", None))
    monkeypatch.setattr(walmart, "auth_headers", lambda *a, **k: {})
    seen = {}

    async def fake_get_json(url, params=None, headers=None):
        seen.update(url=url, params=params)
        return {"items": [
            item(itemId=111, name="Pokemon TCG Chaos Rising Booster Bundle", salePrice=26.94),
            item(itemId=222, name="Collectible Chaos Rising Booster Bundle", marketplace=True,
                 sellerInfo="Flipper LLC", salePrice=79.99),
        ]}

    monkeypatch.setattr(walmart.http, "get_json", fake_get_json)
    results = asyncio.run(walmart.search_items("chaos rising booster bundle"))
    assert seen["url"] == walmart.SEARCH_URL and seen["params"]["query"] == "chaos rising booster bundle"
    assert [(r["item_id"], r["first_party"], r["available"]) for r in results] == [
        ("111", True, True), ("222", False, True)]

    from bot.commands import format_walmart_find
    lines = format_walmart_find("chaos rising", results).split("\n")
    assert "🏪 Walmart" in lines[1] and "`item_id: 111`" in lines[1] and "$26.94" in lines[1]
    assert "🔁 Flipper LLC" in lines[2] and "`item_id: 222`" in lines[2]


def test_config_accepts_item_id_without_url(tmp_path, monkeypatch):
    from utils.config import load_settings
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("APP_ID", "1")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("discord:\n  alert_channel_id: 5\nproducts:\n"
                   "  - id: wm_cr\n    name: Chaos Rising Booster Bundle\n"
                   "    retailer: walmart\n    item_id: 17344505131\n")
    p = load_settings(cfg).products[0]
    assert p.url == "https://www.walmart.com/ip/17344505131"
    assert walmart.item_id_from(p.as_dict()) == "17344505131"


def test_store_filter_helpers():
    from bot.commands import store_matches
    assert store_matches("Walmart", "walmart") and store_matches("Zulu's Games", "zulu")
    assert store_matches("Walmart", None) and not store_matches("Walmart", "zulu")


def test_commands_register():
    """The slash commands (with autocomplete and options) build without errors."""
    import discord
    from discord import app_commands
    from types import SimpleNamespace
    from bot.commands import register_commands

    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)
    register_commands(SimpleNamespace(tree=tree, settings=SimpleNamespace(products=[], searches=[])))
    names = {c.name for c in tree.get_commands()}
    assert {"checknow", "walmartfind", "status", "testalert"} <= names
    checknow = tree.get_command("checknow")
    assert [p.name for p in checknow.parameters] == ["store", "filter", "only_in_stock"]
