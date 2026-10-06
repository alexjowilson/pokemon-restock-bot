from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import discord

from bot.scheduler import check_once, search_once
from monitors.errors import CheckFailed

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

    @tree.command(name="checknow", description="Check every product right now and show what the bot sees")
    @discord.app_commands.default_permissions(manage_guild=True)
    async def checknow(interaction: discord.Interaction):
        # Read-only: doesn't change saved state or send alerts. Use it to verify new URLs.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def one(p):
            try:
                r = await check_once(p)
            except CheckFailed as e:
                return f"⚠️ **{p.name}**: `{e}`"
            price = f" at ${r['price']:.2f}" if r.get("price") is not None else ""
            return f"{'✅ in stock' if r['in_stock'] else '❌ out of stock'}{price}: **{p.name}**"

        async def one_search(srch):
            try:
                listings = await search_once(srch)
            except CheckFailed as e:
                return f"⚠️ 🔎 **{srch.store_name}**: `{e}`"
            if not listings:
                return f"🔎 **{srch.store_name}**: no matches for {', '.join(srch.queries)}"
            rows = [f"{'✅' if x['in_stock'] else '❌'} {x['name']}"
                    + (f" (${x['price']:.2f})" if x.get("price") is not None else "") for x in listings[:8]]
            more = f"\n…and {len(listings) - 8} more" if len(listings) > 8 else ""
            return f"🔎 **{srch.store_name}** ({len(listings)} matches):\n" + "\n".join(rows) + more

        blocks = await asyncio.gather(*(one(p) for p in bot.settings.products),
                                      *(one_search(s) for s in bot.settings.searches))
        for chunk in chunk_messages(blocks or ["Nothing configured."]):
            await interaction.followup.send(chunk, ephemeral=True)
