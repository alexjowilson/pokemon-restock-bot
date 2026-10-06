import asyncio

import pytest

import bot.scheduler as sched
from bot.notifier import build_alert
from monitors.errors import CheckFailed
from monitors.shopify import parse_search_results
from utils.config import Search

STORE = "https://cards.example.com"


@pytest.fixture(autouse=True)
def _no_age_lookup(monkeypatch):
    """These tests are about search logic; treat every listing's publish date as unknown."""
    async def unknown_age(url):
        return None
    monkeypatch.setattr(sched, "listing_age_hours", unknown_age)


def sr(*items):
    return {"resources": {"results": {"products": [
        {"id": i, "title": t, "available": a, "price": "24.99",
         "url": f"/products/p{i}?_pos=1&_sid=abc&_ss=r", "image": "//cdn.shopify.com/x.png"}
        for i, t, a in items
    ]}}}


def test_parse_search_results():
    [x] = parse_search_results(sr((7, "30th Celebration Mini Tin", True)), STORE)
    assert x == {"listing_id": "7", "name": "30th Celebration Mini Tin",
                 "url": STORE + "/products/p7", "in_stock": True, "price": 24.99,
                 "image_url": "https://cdn.shopify.com/x.png"}


def test_parse_search_rejects_other_json():
    with pytest.raises(CheckFailed):
        parse_search_results({"products": []}, STORE)


SEARCH = Search(id="s1", store_name="Card Shop", store_url=STORE,
                queries=("30th celebration",), exclude=("sleeve",))


def _pass(monkeypatch, state, responses, sent, now=1000.0, fail_alert=False):
    async def fake_search(store_url, query):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return parse_search_results(r, store_url)

    async def send_alert(a):
        if fail_alert:
            raise RuntimeError("discord down")
        sent.append((a["kind"], a["name"], a["store_name"]))

    monkeypatch.setattr(sched, "search_listings", fake_search)
    asyncio.run(sched.run_search_checks([SEARCH], state, send_alert, {}, None, now=now))


def test_first_pass_is_baseline_then_new_listing_alerts(monkeypatch):
    state, sent = {}, []
    _pass(monkeypatch, state, [sr((1, "30th Celebration ETB", False))], sent)
    assert sent == []  # baseline only
    _pass(monkeypatch, state, [sr((1, "30th Celebration ETB", False), (2, "30th Celebration UPC", False))], sent)
    assert sent == [("new", "30th Celebration UPC", "Card Shop")]


def test_existing_listing_going_available_alerts_restock(monkeypatch):
    state, sent = {}, []
    _pass(monkeypatch, state, [sr((1, "30th Celebration ETB", False))], sent)
    _pass(monkeypatch, state, [sr((1, "30th Celebration ETB", True))], sent)
    _pass(monkeypatch, state, [sr((1, "30th Celebration ETB", True))], sent)  # still in stock: no repeat
    assert sent == [("restock", "30th Celebration ETB", "Card Shop")]


def test_excluded_titles_are_ignored(monkeypatch):
    state, sent = {}, []
    _pass(monkeypatch, state, [sr()], sent)
    _pass(monkeypatch, state, [sr((3, "30th Celebration Card Sleeves", True))], sent)
    assert sent == []


def test_failed_alert_is_retried(monkeypatch):
    state, sent = {}, []
    _pass(monkeypatch, state, [sr()], sent)
    _pass(monkeypatch, state, [sr((2, "30th Celebration UPC", True))], sent, fail_alert=True)
    _pass(monkeypatch, state, [sr((2, "30th Celebration UPC", True))], sent)
    assert sent == [("new", "30th Celebration UPC", "Card Shop")]


def test_failed_search_doesnt_touch_state(monkeypatch):
    state, sent = {}, []
    _pass(monkeypatch, state, [CheckFailed("blocked")], sent)
    assert state == {"_searches": {}} and sent == []


def test_new_listing_alert_layout():
    content, embed = build_alert({"name": "30th UPC", "url": STORE + "/products/upc", "in_stock": False,
                                  "kind": "new", "store_name": "Card Shop", "price": 119.99}, now=1)
    assert content == "🆕 New listing: 30th UPC [View](<https://cards.example.com/products/upc>)"
    assert embed.description.startswith("⏳ Listed, not available yet")


def test_adding_a_keyword_later_does_not_flood(monkeypatch):
    two = Search(id="s1", store_name="Card Shop", store_url=STORE,
                 queries=("30th celebration", "destined rivals"))
    state, sent = {}, []

    def run(search, responses):
        async def fake_search(store_url, query):
            return parse_search_results(responses[query], store_url)

        async def send_alert(a):
            sent.append((a["kind"], a["name"]))

        monkeypatch.setattr(sched, "search_listings", fake_search)
        asyncio.run(sched.run_search_checks([search], state, send_alert, {}, None, now=1.0))

    run(SEARCH, {"30th celebration": sr((1, "30th ETB", True))})          # baseline 30th
    run(two, {"30th celebration": sr((1, "30th ETB", True)),
              "destined rivals": sr((5, "Destined Rivals ETB", True), (6, "Destined Rivals Box", False))})
    assert sent == []                                                       # new keyword: quiet first pass
    run(two, {"30th celebration": sr((1, "30th ETB", True), (2, "30th UPC", True)),
              "destined rivals": sr((5, "Destined Rivals ETB", True), (6, "Destined Rivals Box", True))})
    assert sorted(sent) == [("new", "30th UPC"), ("restock", "Destined Rivals Box")]


def test_store_channel_and_role_are_passed_to_alerts(monkeypatch):
    routed = Search(id="s2", store_name="Card Shop", store_url=STORE,
                    queries=("30th celebration",), channel_id=111, role_id=222)
    state, sent = {}, []

    async def fake_search(store_url, query):
        return parse_search_results(responses.pop(0), store_url)

    async def send_alert(a):
        sent.append((a["channel_id"], a["role_id"]))

    responses = [sr(), sr((9, "30th Celebration UPC", True))]
    monkeypatch.setattr(sched, "search_listings", fake_search)
    for _ in range(2):
        asyncio.run(sched.run_search_checks([routed], state, send_alert, {}, None, now=1.0))
    assert sent == [(111, 222)]


def test_client_routes_alert_to_store_channel():
    from types import SimpleNamespace
    from bot.client import RestockBot
    import bot.client as client_mod

    calls = []

    async def fake_send(channel, product, role_id, vote):
        calls.append((channel, role_id))

    async def fake_channel(self, cid):
        return f"channel-{cid}"

    bot = RestockBot.__new__(RestockBot)
    bot.settings = SimpleNamespace(alert_channel_id=1, alert_role_id=5, vote_reactions=True)
    orig = client_mod.send_restock_alert
    client_mod.send_restock_alert = fake_send
    RestockBot._channel, orig_channel = fake_channel, RestockBot._channel
    try:
        asyncio.run(bot.alert({"name": "x", "url": "u", "channel_id": 111, "role_id": 222}))
        asyncio.run(bot.alert({"name": "x", "url": "u", "channel_id": None, "role_id": None}))
    finally:
        client_mod.send_restock_alert = orig
        RestockBot._channel = orig_channel
    assert calls == [("channel-111", 222), ("channel-1", 5)]
