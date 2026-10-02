from __future__ import annotations

import time
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.client import RestockBot


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
                "price": None,
                "image_url": None,
            })
        except discord.DiscordException as e:
            await interaction.followup.send(f"❌ Couldn't post to the alert channel: {e}", ephemeral=True)
            return
        await interaction.followup.send("✅ Test alert sent.", ephemeral=True)

    @tree.command(name="status", description="Show what the bot is watching")
    async def status(interaction: discord.Interaction):
        state = bot.scheduler.state
        lines = []
        for p in bot.settings.products:
            s = state.get(p.id)
            if not s or "last_checked" not in s:
                lines.append(f"⏳ **{p.name}** ({p.retailer}): not checked yet")
                continue
            icon = "✅" if s["in_stock"] else "❌"
            ago = int(time.time()) - s["last_checked"]
            lines.append(f"{icon} **{p.name}** ({p.retailer}): checked {ago}s ago")
        body = "\n".join(lines) or "No products configured."
        await interaction.response.send_message(
            f"Checking every {bot.settings.check_interval_seconds}s\n{body}", ephemeral=True
        )
