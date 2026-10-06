import asyncio

import bot.scheduler as sched
from bot.commands import chunk_messages
from monitors.errors import CheckFailed
from monitors.shopify import parse_search_results
from utils.config import Search
from utils.text import title_matches

SEALED = ("elite trainer box", "etb", "booster", "bundle", "tin", "collection", "box",
          "blister", "display", "pack")


def ok(title):
    return title_matches(title, require_all=("pokemon",), include_any=SEALED, exclude=("sleeve",))


def test_sealed_pokemon_products_match():
    assert ok("Pokémon TCG: 30th Celebration: Mini Tin")
    assert ok("Pokemon Tcg: 30th Celebration Elite Trainer Box")
    assert ok("Pokémon: 30th Celebration - Greninja & Sylveon ex Box [Set of 2]")
    assert ok("Pokemon TCG: Scarlet & Violet 10 - Destined Rivals: Elite Trainer Box")


def test_singles_other_games_and_accessories_dont():
    assert not ok("Seadra 117 - SV Scarlet & Violet 151")                     # single
    assert not ok("Umbreon ex 161/131 - Pokemon Prismatic Evolutions")      # single, no sealed word
    assert not ok("Dark Ritual (Retro Frame)")                               # Magic card
    assert not ok("30th Anniversary Edition Display")                        # not Pokémon
    assert not ok("Pokemon 30th Celebration Card Sleeves")                   # excluded


def test_word_start_matching():
    # "tin" must not match inside "Destined"
    assert not title_matches("Pokemon Destined Rivals Charizard", include_any=("tin",))
    assert title_matches("Pokemon Destined Rivals Tins", include_any=("tin",))


STORE = "https://cards.example.com"
SEARCH = Search(id="s1", store_name="Card Shop", store_url=STORE, queries=("151",),
                require_all=("pokemon",), include_any=SEALED, new_listing_max_age_hours=72)


def sr(*items):
    return {"resources": {"results": {"products": [
        {"id": i, "title": t, "available": True, "price": "9.99", "url": f"/products/p{i}"}
        for i, t in items]}}}


def run(monkeypatch, state, results, ages, sent):
    async def fake_search(store_url, query):
        return parse_search_results(results.pop(0), store_url)

    async def fake_age(url):
        a = ages[url.rsplit("/p", 1)[1]]
        if isinstance(a, Exception):
            raise a
        return a

    async def send_alert(a):
        sent.append(a["name"])

    monkeypatch.setattr(sched, "search_listings", fake_search)
    monkeypatch.setattr(sched, "listing_age_hours", fake_age)
    asyncio.run(sched.run_search_checks([SEARCH], state, send_alert, {}, None, now=1.0))


def test_old_listing_rotating_into_results_is_not_new(monkeypatch):
    state, sent = {}, []
    run(monkeypatch, state, [sr()], {}, sent)  # baseline
    run(monkeypatch, state, [sr((1, "Pokemon 151 Elite Trainer Box"), (2, "Pokemon 151 Booster Bundle"))],
        {"1": 900.0, "2": 3.0}, sent)
    assert sent == ["Pokemon 151 Booster Bundle"]
    run(monkeypatch, state, [sr((1, "Pokemon 151 Elite Trainer Box"), (2, "Pokemon 151 Booster Bundle"))],
        {}, sent)
    assert sent == ["Pokemon 151 Booster Bundle"]  # neither re-alerts


def test_unknown_age_still_alerts_and_failed_age_check_retries(monkeypatch):
    state, sent = {}, []
    run(monkeypatch, state, [sr()], {}, sent)
    run(monkeypatch, state, [sr((3, "Pokemon 151 Mini Tin"))], {"3": CheckFailed("timeout")}, sent)
    assert sent == []
    run(monkeypatch, state, [sr((3, "Pokemon 151 Mini Tin"))], {"3": None}, sent)
    assert sent == ["Pokemon 151 Mini Tin"]


def test_chunk_messages_respects_limit():
    blocks = [f"line {i} " + "x" * 50 for i in range(100)]
    chunks = chunk_messages(blocks, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks).split("\n") == blocks


def test_checknow_links_in_stock_items_first():
    from bot.commands import format_search_block
    block = format_search_block("Card Shop", [
        {"name": "151 ETB", "url": "https://shop/products/etb", "in_stock": False, "price": 49.99},
        {"name": "151 Booster Bundle", "url": "https://shop/products/bb", "in_stock": True, "price": 26.94},
    ])
    assert block.split("\n") == [
        "🔎 **Card Shop**: 1 in stock, 1 sold out",
        "✅ [151 Booster Bundle](<https://shop/products/bb>) ($26.94)",
        "❌ 151 ETB ($49.99)",
    ]
