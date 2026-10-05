from bot.notifier import build_alert


def test_alert_layout_with_cart_link_and_role():
    content, embed = build_alert({
        "name": "SV 151 ETB", "url": "https://shop/products/etb", "retailer": "shopify",
        "store_name": "Card Shop", "price": 49.99, "cart_url": "https://shop/cart/1:1",
        "image_url": "https://img",
    }, role_id=42, now=1_700_000_000)
    assert content == "<@&42> SV 151 ETB [Buy Now](<https://shop/cart/1:1>)"
    assert embed.title == "SV 151 ETB"
    assert "<t:1700000000:T> • <t:1700000000:R>" in embed.description
    assert "[Click here to buy](https://shop/cart/1:1)" in embed.description
    assert "Price: $49.99" in embed.description and "Card Shop" in embed.description
    assert embed.thumbnail.url == "https://img"


def test_alert_without_cart_or_price_links_product_page():
    content, embed = build_alert({"name": "Tin", "url": "https://shop/products/tin", "retailer": "demo"})
    assert content == "Tin [Buy Now](<https://shop/products/tin>)"
    assert "Price" not in embed.description and "Product page" not in embed.description
