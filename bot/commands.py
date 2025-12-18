import discord
from discord import app_commands
from bot.client import tree, client
from bot.notifier import send_restock_alert


def _get_alert_channel():
    # If you switched to channel ID config, use that here.
    # For now, this finds by name:
    return discord.utils.get(client.get_all_channels(), name="pokemon-alerts")

@tree.command(
    name="testalert",
    description="Send a test restock alert embed"
)
async def testalert(interaction: discord.Interaction):
    channel = _get_alert_channel()
    if not channel:
        await interaction.response.send_message(
            "❌ I couldn't find a #pokemon-alerts channel (or I don't have permission to view it).",
            ephemeral=True
        )
        return

    demo_product = {
        "name": "Pikachu Demo Product",
        "retailer": "demo",
        "url": "https://example.com/pikachu",
        "price": None,
        "image_url": None,
        "in_stock": True,
    }

    # Acknowledge the command privately
    await interaction.response.send_message("✅ Sending a test alert…", ephemeral=True)

    # Send the embed to your alerts channel
    await send_restock_alert(channel, demo_product)