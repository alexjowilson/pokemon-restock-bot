import asyncio

import pytest
from aiohttp import web

from monitors import bestbuy, shopify
from monitors.errors import Blocked, CheckFailed
from monitors.http import HttpClient

SHOP_URL = "https://cards.example.com/products/sv-151-etb"


def shopify_payload(available=True, variants=None):
    return {
        "title": "SV 151 ETB",
        "available": available,
        "price": 4999,
        "featured_image": "//cdn.shopify.com/s/files/etb.png",
        "variants": variants if variants is not None else [{"id": 111, "available": available, "price": 4999}],
    }


# ---------- Shopify ----------

@pytest.mark.parametrize("url", [
    SHOP_URL, SHOP_URL + "/", SHOP_URL + "?variant=111", SHOP_URL + ".json", SHOP_URL + ".js",
])
def test_shopify_js_url(url):
    assert shopify.product_js_url(url) == SHOP_URL + ".js"


def test_shopify_rejects_non_product_url():
    with pytest.raises(CheckFailed):
        shopify.product_js_url("https://cards.example.com/collections/pokemon")


def test_shopify_in_stock():
    r = shopify.parse_product(shopify_payload(True), {"url": SHOP_URL})
    assert r == {
        "in_stock": True,
        "price": 49.99,
        "image_url": "https://cdn.shopify.com/s/files/etb.png",
        "cart_url": "https://cards.example.com/cart/111:1",
    }


def test_shopify_out_of_stock_has_no_cart_link():
    r = shopify.parse_product(shopify_payload(False), {"url": SHOP_URL})
    assert r["in_stock"] is False and r["cart_url"] is None


def test_shopify_specific_variant():
    payload = shopify_payload(True, variants=[
        {"id": 1, "available": True, "price": 1000},
        {"id": 2, "available": False, "price": 2000},
    ])
    assert shopify.parse_product(payload, {"url": SHOP_URL, "variant_id": 2})["in_stock"] is False
    assert shopify.parse_product(payload, {"url": SHOP_URL, "variant_id": "1"})["in_stock"] is True
    with pytest.raises(CheckFailed):
        shopify.parse_product(payload, {"url": SHOP_URL, "variant_id": 3})


def test_shopify_check_product_rejects_non_product_json(monkeypatch):
    async def fake_get_json(url, params=None):
        return {"products": []}
    monkeypatch.setattr(shopify.http, "get_json", fake_get_json)
    with pytest.raises(CheckFailed):
        asyncio.run(shopify.check_product({"url": SHOP_URL}))


# ---------- Best Buy ----------

def test_bestbuy_requires_key(monkeypatch):
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    with pytest.raises(CheckFailed, match="BESTBUY_API_KEY"):
        asyncio.run(bestbuy.check_product({"sku": 1, "url": "u"}))


def test_bestbuy_parses_response(monkeypatch):
    calls = []

    async def fake_get_json(url, params=None):
        calls.append((url, params))
        return {"sku": 123, "onlineAvailability": True, "salePrice": 59.99,
                "addToCartUrl": "https://api.bestbuy.com/click/-/123/cart", "image": "https://img"}

    monkeypatch.setenv("BESTBUY_API_KEY", "k")
    monkeypatch.setattr(bestbuy.http, "get_json", fake_get_json)
    r = asyncio.run(bestbuy.check_product({"sku": "123", "url": "u"}))
    assert r == {"in_stock": True, "price": 59.99, "image_url": "https://img",
                 "cart_url": "https://api.bestbuy.com/click/-/123/cart"}
    assert calls[0][0] == "https://api.bestbuy.com/v1/products/123.json"
    assert calls[0][1]["apiKey"] == "k"


# ---------- HTTP client against a real local server ----------

async def _with_server(handler_map, fn):
    app = web.Application()
    for path, handler in handler_map.items():
        app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    client = HttpClient(min_interval_per_host=0, timeout_seconds=1)
    try:
        return await fn(client, f"http://127.0.0.1:{port}")
    finally:
        await client.close()
        await runner.cleanup()


def test_http_client_status_handling():
    async def ok(request):
        assert "pokemon-restock-bot" in request.headers["User-Agent"]
        return web.json_response({"available": True})

    async def slow(request):
        await asyncio.sleep(3)
        return web.json_response({})

    def status(code, **kw):
        async def handler(request):
            return web.Response(status=code, **kw)
        return handler

    handlers = {
        "/ok": ok,
        "/limited": status(429),
        "/forbidden": status(403),
        "/missing": status(404),
        "/broken": status(500),
        "/html": status(200, text="<html>captcha</html>", content_type="text/html"),
        "/slow": slow,
    }

    async def run(client, base):
        assert await client.get_json(base + "/ok") == {"available": True}
        outcomes = {}
        for path in ["/limited", "/forbidden", "/missing", "/broken", "/html", "/slow"]:
            try:
                await client.get_json(base + path)
                outcomes[path] = None
            except CheckFailed as e:
                outcomes[path] = type(e)
        return outcomes

    outcomes = asyncio.run(_with_server(handlers, run))
    assert outcomes == {
        "/limited": Blocked, "/forbidden": Blocked, "/missing": CheckFailed,
        "/broken": CheckFailed, "/html": CheckFailed, "/slow": CheckFailed,
    }


def test_http_client_spaces_requests_per_host():
    import time

    async def ok(request):
        return web.json_response({})

    async def run(client, base):
        client.min_interval = 0.3
        start = time.monotonic()
        await asyncio.gather(*(client.get_json(base + "/ok") for _ in range(3)))
        return time.monotonic() - start

    assert asyncio.run(_with_server({"/ok": ok}, run)) >= 0.6
