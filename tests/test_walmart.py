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
