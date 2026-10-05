import logging

import discord
from discord import app_commands

from bot.notifier import send_restock_alert
from bot.scheduler import Scheduler
from monitors.http import http
from utils.config import Settings

log = logging.getLogger(__name__)


class RestockBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents.default(), application_id=settings.app_id)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.scheduler = Scheduler(settings.products, settings.check_interval_seconds,
                                   self.alert, self.health_message, settings.searches)

    async def setup_hook(self) -> None:
        # Runs once per process (on_ready can fire again on every reconnect).
        from bot.commands import register_commands
        register_commands(self)

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)  # instant in that server
            log.info("Slash commands synced to guild %s", self.settings.guild_id)
        else:
            await self.tree.sync()  # global: can take a while to show up
            log.info("Slash commands synced globally")

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        self.scheduler.start()

    async def close(self) -> None:
        await http.close()
        await super().close()

    async def _channel(self, channel_id: int):
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def alert(self, product: dict) -> None:
        # Per-store/per-product channel and role (from config) win over the defaults.
        channel_id = int(product.get("channel_id") or self.settings.alert_channel_id)
        role_id = product.get("role_id") or self.settings.alert_role_id
        channel = await self._channel(channel_id)
        await send_restock_alert(channel, product, int(role_id) if role_id else None,
                                 vote=self.settings.vote_reactions)

    async def health_message(self, text: str) -> None:
        # Falls back to the alert channel: a noisy warning beats a silent failure.
        channel_id = self.settings.health_channel_id or self.settings.alert_channel_id
        channel = await self._channel(channel_id)
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
