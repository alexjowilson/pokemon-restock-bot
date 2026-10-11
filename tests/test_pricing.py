import asyncio

import bot.scheduler as sched
from bot.commands import format_product_row
from bot.notifier import build_alert
from monitors import walmart
from utils.config import Product, load_settings
from utils.pricing import msrp_for, verdict


def test_msrp_by_product_type_and_override():
    assert msrp_for("Destined Rivals Elite Trainer Box") == 49.99
    assert msrp_for("Pokemon 151 ETB") == 49.99
    assert msrp_for("Chaos Rising Booster Bundle") == 26.94
    assert msrp_for("Ultra-Premium Collection") is None
    assert msrp_for("Perfect Order Elite Trainer Box", explicit=59.99) == 59.99


def test_verdicts():
    assert verdict(49.99, 49.99) == "🟢 +0% vs $49.99 MSRP"
    assert verdict(69.99, 49.99) == "🟡 +40% vs $49.99 MSRP"
    assert verdict(84.97, 26.94) == "🔴 +215% vs $26.94 MSRP"
    assert verdict(39.99, 49.99) == "🟢 −20% vs $49.99 MSRP"
    assert verdict(None, 49.99) == "" and verdict(10, None) == ""


def test_checknow_row_shows_msrp_comparison():
    p = Product(id="x", name="Perfect Order Booster Bundle", retailer="walmart", url="u",
                extra={"msrp": 26.94})
    row = format_product_row(p, {"in_stock": False, "price": 39.95, "note": "only resellers"})
    assert row == "🟡 [Perfect Order Booster Bundle](<u>) · only resellers $39.95 (🟡 +48% vs $26.94 MSRP)"


def wm(item_id, price, sold_by="Flipper"):
    return {"itemId": item_id, "name": "x", "salePrice": price, "stock": "Available",
            "availableOnline": True, "marketplace": sold_by != "Walmart.com", "sellerInfo": sold_by}


def load(tmp_path, monkeypatch, body):
    for k in ("ALERT_CHANNEL_ID", "GUILD_ID", "HEALTH_CHANNEL_ID", "ALERT_ROLE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("APP_ID", "1")
    cfg = tmp_path / "c.yaml"
    cfg.write_text("discord: {alert_channel_id: 1}\n" + body)
    return load_settings(cfg)


def test_config_attaches_msrp_and_deal_threshold(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, """
reseller_deals: {max_over_msrp_pct: 15}
walmart_watch:
  - {name: "Perfect Order Elite Trainer Box", item_ids: [1], msrp: 59.99}
  - {name: "Chaos Rising Booster Bundle", item_ids: [2]}
""")
    a, b = s.products
    assert (a.extra["msrp"], a.extra["deal_pct"]) == (59.99, 15.0)
    assert b.extra["msrp"] == 26.94


def test_reseller_deal_alerts_once_when_price_drops_near_msrp(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, """
reseller_deals: {max_over_msrp_pct: 15}
walmart_watch:
  - {name: "Chaos Rising Booster Bundle", item_ids: [2]}
""")
    prices = iter([41.00, 29.99, 28.50, 45.00, 30.00])

    async def fake(url, params):
        return {"items": [wm(2, next(prices))]}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    sent, state = [], {}

    async def send_alert(a):
        sent.append((a["kind"], a["price"]))
    for i in range(5):
        asyncio.run(sched.run_checks(s.products, state, send_alert, now=1000 + 60 * i))
    # 41 (first look, too high) -> 29.99 deal -> 28.50 still deal -> 45 out -> 30 deal again
    assert sent == [("deal", 29.99), ("deal", 30.0)]


def test_deals_off_by_default(tmp_path, monkeypatch):
    s = load(tmp_path, monkeypatch, "walmart_watch:\n  - {name: \"X Booster Bundle\", item_ids: [2]}\n")
    assert "deal_pct" not in s.products[0].extra


def test_deal_alert_layout():
    content, embed = build_alert({"name": "Chaos Rising Booster Bundle", "url": "https://w/2",
                                  "retailer": "walmart", "in_stock": False, "kind": "deal",
                                  "price": 29.99, "msrp": 26.94}, now=0)
    assert content.startswith("💸 Reseller near MSRP: Chaos Rising Booster Bundle [Buy Now]")
    assert "🔁 Sold by a reseller" in embed.description
    assert "💲 Price: $29.99 (🟢 +11% vs $26.94 MSRP)" in embed.description
