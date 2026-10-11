from __future__ import annotations

import logging
import time
from typing import Optional

import discord

from utils.pricing import verdict

log = logging.getLogger(__name__)

RETAILER_NAMES = {"bestbuy": "Best Buy", "walmart": "Walmart", "shopify": "Shop", "demo": "Demo"}
VOTE_REACTIONS = ("✅", "❌")  # community vote: legit / not legit


def build_alert(product: dict, role_id: Optional[int] = None, now: Optional[float] = None):
    """Returns (content, embed). Layout: pings + name + Buy Now link above a compact embed."""
    ts = int(now if now is not None else time.time())
    in_stock = product.get("in_stock", True)
    is_new = product.get("kind") == "new"
    is_deal = product.get("kind") == "deal"
    buy_url = product.get("cart_url") or product["url"]
    store = product.get("store_name") or RETAILER_NAMES.get(product.get("retailer"), product.get("retailer"))

    ping = f"<@&{role_id}> " if role_id else ""
    label = "🆕 New listing: " if is_new else ("💸 Reseller near MSRP: " if is_deal else "")
    link = "Buy Now" if in_stock or is_deal else "View"
    content = f"{ping}{label}{product['name']} [{link}](<{buy_url}>)"  # <...> stops Discord adding a link preview

    when = f"(<t:{ts}:T> • <t:{ts}:R>)"  # Discord renders these in each viewer's timezone
    if is_deal:
        status = f"🔁 Sold by a reseller {when}"
    else:
        status = f"✅ In Stock {when}" if in_stock else f"⏳ Listed, not available yet {when}"
    lines = [status, f"🔗 [{'Click here to buy' if in_stock else 'Product page'}]({buy_url})"]
    if product.get("cart_url"):
        lines.append(f"📄 [Product page]({product['url']})")
    if product.get("price") is not None:
        compare = verdict(product["price"], product.get("msrp"))
        lines.append(f"💲 Price: ${product['price']:.2f}" + (f" ({compare})" if compare else ""))
    if str(product.get("stock_level") or "").lower() in ("limited supply", "last few items"):
        lines.append(f"🔥 {product['stock_level']}: move fast")
    lines.append(f"🏪 {store or 'unknown'}")

    embed = discord.Embed(
        title=product["name"],
        url=product["url"],
        description="\n".join(lines),
        color=(discord.Color.green() if in_stock
               else discord.Color.blurple() if is_deal else discord.Color.gold()),
    )
    if product.get("image_url"):
        embed.set_thumbnail(url=product["image_url"])
    return content, embed


async def send_restock_alert(channel: discord.abc.Messageable, product: dict,
                             role_id: Optional[int] = None, vote: bool = True) -> None:
    content, embed = build_alert(product, role_id)
    message = await channel.send(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
    )
    if vote:
        try:
            for emoji in VOTE_REACTIONS:
                await message.add_reaction(emoji)
        except discord.Forbidden:
            log.warning("Can't add vote reactions: give the bot 'Add Reactions' and "
                        "'Read Message History' in the alert channel")
        except discord.HTTPException:
            log.warning("Couldn't add vote reactions", exc_info=True)
