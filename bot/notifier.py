from __future__ import annotations

import time
from typing import Optional

import discord

RETAILER_NAMES = {"bestbuy": "Best Buy", "shopify": "Shop", "demo": "Demo"}


def build_alert(product: dict, role_id: Optional[int] = None, now: Optional[float] = None):
    """Returns (content, embed). Layout: pings + name + Buy Now link above a compact embed."""
    ts = int(now if now is not None else time.time())
    buy_url = product.get("cart_url") or product["url"]
    store = product.get("store_name") or RETAILER_NAMES.get(product.get("retailer"), product.get("retailer"))

    ping = f"<@&{role_id}> " if role_id else ""
    content = f"{ping}{product['name']} [Buy Now](<{buy_url}>)"  # <...> stops Discord adding a link preview

    lines = [f"✅ In Stock (<t:{ts}:T> • <t:{ts}:R>)"]  # Discord renders these in each viewer's timezone
    lines.append(f"🔗 [Click here to buy]({buy_url})")
    if product.get("cart_url"):
        lines.append(f"📄 [Product page]({product['url']})")
    if product.get("price") is not None:
        lines.append(f"💲 Price: ${product['price']:.2f}")
    lines.append(f"🏪 {store or 'unknown'}")

    embed = discord.Embed(
        title=product["name"],
        url=product["url"],
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    if product.get("image_url"):
        embed.set_thumbnail(url=product["image_url"])
    return content, embed


async def send_restock_alert(channel: discord.abc.Messageable, product: dict,
                             role_id: Optional[int] = None) -> None:
    content, embed = build_alert(product, role_id)
    await channel.send(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
    )
