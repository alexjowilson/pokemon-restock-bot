import asyncio

import bot.scheduler as sched
from monitors import walmart
from utils.config import load_settings


def wm(item_id, sold_by="Walmart.com", online=True, price=49.97):
    return {"itemId": item_id, "name": "Some Long Walmart Title ETB", "salePrice": price,
            "stock": "Available" if online else "Not available", "availableOnline": online,
            "marketplace": sold_by != "Walmart.com", "sellerInfo": sold_by,
            "addToCartUrl": f"https://affil.walmart.com/cart?items={item_id}"}


def settings(tmp_path, monkeypatch, body):
    for k in ("ALERT_CHANNEL_ID", "GUILD_ID", "HEALTH_CHANNEL_ID", "ALERT_ROLE_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("APP_ID", "1")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("discord: {alert_channel_id: 1}\n" + body)
    return load_settings(cfg)


def test_groups_expand_and_skip_pages_already_listed(tmp_path, monkeypatch):
    s = settings(tmp_path, monkeypatch, """
products:
  - {id: wm_30th, name: "30th ETB", retailer: walmart, url: "https://www.walmart.com/ip/x/111"}
walmart_watch:
  - name: "Destined Rivals ETB"
    item_ids: [111, 222, 333, 222]
    max_price: 59.99
""")
    watch = [p for p in s.products if p.id.startswith("wmw_")]
    assert [p.id for p in watch] == ["wmw_222", "wmw_333"]
    assert watch[0].name == "Destined Rivals ETB" and watch[0].extra["max_price"] == 59.99
    assert watch[0].url == "https://www.walmart.com/ip/222"


def test_first_check_is_quiet_then_walmart_selling_alerts(tmp_path, monkeypatch):
    s = settings(tmp_path, monkeypatch, """
walmart_watch:
  - {name: "Destined Rivals ETB", item_ids: [222, 333]}
""")
    pages = {"222": wm(222), "333": wm(333, sold_by="Flipper", price=150)}

    async def fake(url, params):
        return {"items": [pages[i] for i in params["ids"].split(",")]}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    sent, state = [], {}

    async def send_alert(a):
        sent.append((a["name"], a["url"], a["retailer"]))

    asyncio.run(sched.run_checks(s.products, state, send_alert, now=1000))
    assert sent == []  # 222 was already in stock when added: recorded, not alerted

    pages["333"] = wm(333)  # Walmart starts selling the other page
    asyncio.run(sched.run_checks(s.products, state, send_alert, now=1060))
    assert sent == [("Destined Rivals ETB", "https://www.walmart.com/ip/333", "walmart")]


def test_removed_page_reads_as_unavailable(tmp_path, monkeypatch):
    s = settings(tmp_path, monkeypatch, "walmart_watch:\n  - {name: X, item_ids: [222]}\n")

    async def fake(url, params):
        return {"items": []}
    monkeypatch.setattr(walmart, "_signed_get", fake)
    state = {}

    async def send_alert(a):
        raise AssertionError("no alert")
    health = {}
    asyncio.run(sched.run_checks(s.products, state, send_alert, health, now=1000))
    assert state["wmw_222"]["in_stock"] is False and health == {}
