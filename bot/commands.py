from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import discord

from bot.notifier import RETAILER_NAMES
from bot.scheduler import check_once, search_once
from monitors import walmart
from monitors.errors import CheckFailed
from utils.text import has_word

if TYPE_CHECKING:
    from bot.client import RestockBot


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago"


def chunk_messages(blocks, limit: int = 1900) -> list:
    """Pack text blocks into Discord-sized messages without splitting a line."""
    messages, current = [], ""
    for line in "\n".join(blocks).split("\n"):
        line = line[:limit]
        if current and len(current) + 1 + len(line) > limit:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        messages.append(current)
    return messages


def store_label(product) -> str:
    extra = getattr(product, "extra", {}) or {}
    return extra.get("store_name") or RETAILER_NAMES.get(product.retailer, product.retailer)


def _price(x) -> str:
    return f"${x:.2f}" if x is not None else ""


def format_product_row(product, result: dict) -> str:
    """One linked row for a single-product monitor. Always a link, even when only resellers have it."""
    link = f"[{product.name}](<{product.url}>)"  # <...> stops Discord adding a big link preview
    price = _price(result.get("price"))
    if result["in_stock"]:
        return f"✅ {link}" + (f" · {price}" if price else "")
    if result.get("note"):
        return f"🟡 {link} · {result['note']}" + (f" {price}" if price else "")
    return f"❌ {link}"


def format_product_block(store: str, rows: list) -> str:
    """rows: (product, result_or_None, error_or_None). Grouped under one store header."""
    lines, in_stock, resellers = [], 0, 0
    for product, result, error in rows:
        if error is not None:
            lines.append(f"⚠️ {product.name}: `{error}`")
            continue
        in_stock += bool(result["in_stock"])
        resellers += bool(not result["in_stock"] and result.get("note"))
        lines.append(format_product_row(product, result))
    summary = f"{in_stock} in stock" + (f", {resellers} resellers only" if resellers else "")
    return "\n".join([f"🏪 **{store}**: {summary}"] + lines)


def format_search_block(store_name: str, listings: list, limit: int = 25,
                        only_in_stock: bool = False) -> str:
    """In-stock first. Every row links to the listing so you can check it yourself."""
    ordered = sorted(listings, key=lambda x: not x["in_stock"])
    in_stock = sum(1 for x in listings if x["in_stock"])
    shown = [x for x in ordered if x["in_stock"]] if only_in_stock else ordered
    rows = []
    for x in shown[:limit]:
        price = f" · {_price(x.get('price'))}" if x.get("price") is not None else ""
        rows.append(f"{'✅' if x['in_stock'] else '❌'} [{x['name']}](<{x['url']}>){price}")
    more = [f"…and {len(shown) - limit} more"] if len(shown) > limit else []
    header = f"🔎 **{store_name}**: {in_stock} in stock, {len(listings) - in_stock} sold out"
    return "\n".join([header] + rows + more)


def store_names(settings) -> list:
    names = [store_label(p) for p in settings.products] + [s.store_name for s in settings.searches]
    return list(dict.fromkeys(names))  # unique, config order


def store_matches(name: str, wanted: str | None) -> bool:
    return not wanted or wanted.strip().lower() in name.lower()


def format_walmart_find(query: str, results: list) -> str:
    """/walmartfind output: who sells it right now, price, item id to paste into config."""
    if not results:
        return f"🔎 **Walmart** search `{query}`: nothing found"
    lines = [f"🔎 **Walmart** search `{query}`: {len(results)} result(s). "
             f"Copy an `item_id` into config.yaml to watch it."]
    for r in results:
        seller = "🏪 Walmart" if r["first_party"] else f"🔁 {r['seller']}"
        stock = "✅" if r["available"] else "❌"
        price = f" · {_price(r['price'])}" if r.get("price") is not None else ""
        lines.append(f"{stock} [{r['name']}](<{r['url']}>){price} · {seller} · `item_id: {r['item_id']}`")
    return "\n".join(lines)


def matches_filter(title: str, text: str | None) -> bool:
    """/checknow filter: every word must appear in the title ("30th etb")."""
    if not text:
        return True
    return all(has_word(title, w) for w in text.split())


def register_commands(bot: RestockBot) -> None:
    tree = bot.tree

    @tree.command(name="testalert", description="Send a test restock alert to the alerts channel")
    @discord.app_commands.default_permissions(manage_guild=True)
    async def testalert(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await bot.alert({
                "name": "Pikachu Demo Product",
                "retailer": "demo",
                "url": "https://example.com/pikachu",
                "price": 49.99,
                "image_url": None,
            })
        except discord.DiscordException as e:
            await interaction.followup.send(f"❌ Couldn't post to the alert channel: {e}", ephemeral=True)
            return
        await interaction.followup.send("✅ Test alert sent.", ephemeral=True)

    @tree.command(name="status", description="Show what the bot is watching")
    async def status(interaction: discord.Interaction):
        state, health = bot.scheduler.state, bot.scheduler.health
        lines = []
        for p in bot.settings.products:
            s, h = state.get(p.id) or {}, health.get(p.id)
            if h:
                lines.append(f"⚠️ **{p.name}** ({p.retailer}): failing ({h['failures']}x): `{h['last_error']}`")
            elif "last_checked" not in s:
                lines.append(f"⏳ **{p.name}** ({p.retailer}): not checked yet")
            else:
                icon = "✅ in stock" if s["in_stock"] else "❌ out of stock"
                lines.append(f"{icon}: **{p.name}** ({p.retailer}), checked {_ago(s['last_checked'])}")
        searches_state = state.get("_searches", {})
        for srch in bot.settings.searches:
            st, h = searches_state.get(srch.id) or {}, health.get(srch.id)
            if h:
                lines.append(f"⚠️ 🔎 **{srch.store_name}**: failing ({h['failures']}x): `{h['last_error']}`")
            elif "last_checked" not in st:
                lines.append(f"⏳ 🔎 **{srch.store_name}**: not checked yet")
            else:
                n = len(st.get("seen", {}))
                lines.append(f"🔎 **{srch.store_name}** ({', '.join(srch.queries)}): "
                             f"{n} listing(s) tracked, checked {_ago(st['last_checked'])}")
        body = "\n".join(lines) or "Nothing configured."
        await interaction.response.send_message(
            f"Checking every {bot.settings.check_interval_seconds}s\n{body}"[:2000], ephemeral=True
        )

    @tree.command(name="checknow", description="Check everything right now and show what the bot sees")
    @discord.app_commands.describe(
        store="Only check this store, e.g. Walmart",
        filter="Only show titles containing these words, e.g. 30th celebration",
        only_in_stock="Hide sold-out listings",
    )
    @discord.app_commands.default_permissions(manage_guild=True)
    async def checknow(interaction: discord.Interaction, store: str | None = None,
                       filter: str | None = None, only_in_stock: bool = False):
        # Read-only: doesn't change saved state or send alerts. Use it to verify new URLs.
        await interaction.response.defer(ephemeral=True, thinking=True)

        products = [p for p in bot.settings.products
                    if store_matches(store_label(p), store) and matches_filter(p.name, filter)]
        searches = [srch for srch in bot.settings.searches if store_matches(srch.store_name, store)]

        async def one(p):
            try:
                return p, await check_once(p), None
            except CheckFailed as e:
                return p, None, e

        async def one_search(srch):
            try:
                listings = await search_once(srch)
            except CheckFailed as e:
                return f"⚠️ 🔎 **{srch.store_name}**: `{e}`"
            listings = [x for x in listings if matches_filter(x["name"], filter)]
            if only_in_stock and not any(x["in_stock"] for x in listings):
                return None
            if not listings:
                return None if filter else f"🔎 **{srch.store_name}**: no matching listings"
            return format_search_block(srch.store_name, listings, only_in_stock=only_in_stock)

        results = await asyncio.gather(*(one(p) for p in products))
        by_store: dict = {}
        for row in results:
            _, result, error = row
            if only_in_stock and error is None and not result["in_stock"]:
                continue
            by_store.setdefault(store_label(row[0]), []).append(row)
        blocks = [format_product_block(store, rows) for store, rows in by_store.items()]
        blocks += [b for b in await asyncio.gather(*(one_search(s) for s in searches)) if b]

        empty = "Nothing matched." if (store or filter or only_in_stock) else "Nothing configured."
        for chunk in chunk_messages(["\n\n".join(blocks)] if blocks else [empty]):
            await interaction.followup.send(chunk, ephemeral=True)

    @checknow.autocomplete("store")
    async def store_autocomplete(interaction: discord.Interaction, current: str):
        return [discord.app_commands.Choice(name=n, value=n)
                for n in store_names(bot.settings) if store_matches(n, current)][:25]

    @tree.command(name="walmartfind", description="Search Walmart to find item ids worth watching")
    @discord.app_commands.describe(query="e.g. chaos rising booster bundle")
    @discord.app_commands.default_permissions(manage_guild=True)
    async def walmartfind(interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            results = await walmart.search_items(query, limit=10)
        except CheckFailed as e:
            await interaction.followup.send(f"⚠️ Walmart search failed: `{e}`", ephemeral=True)
            return
        for chunk in chunk_messages([format_walmart_find(query, results)]):
            await interaction.followup.send(chunk, ephemeral=True)
