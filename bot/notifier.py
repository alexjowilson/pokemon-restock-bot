import discord

async def send_restock_alert(channel: discord.abc.Messageable, product: dict) -> None:
    embed = discord.Embed(
        title="🚨 Restock Alert!",
        description=product["name"],
        url=product["url"],
    )
    embed.add_field(name="Retailer", value=product.get("retailer", "unknown"), inline=True)
    embed.add_field(name="Status", value="✅ In Stock", inline=True)

    # Optional fields (only show if present)
    if product.get("price") is not None:
        embed.add_field(name="Price", value=f'${product["price"]}', inline=True)

    if product.get("image_url"):
        embed.set_thumbnail(url=product["image_url"])

    await channel.send(embed=embed)
