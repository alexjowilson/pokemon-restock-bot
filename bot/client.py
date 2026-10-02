import logging

import discord
from discord import app_commands

from bot.notifier import send_restock_alert
from bot.scheduler import Scheduler
from utils.config import Settings

log = logging.getLogger(__name__)


class RestockBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents.default(), application_id=settings.app_id)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.scheduler = Scheduler(settings.products, settings.check_interval_seconds, self.alert)

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

    async def get_alert_channel(self) -> discord.abc.Messageable:
        channel_id = self.settings.alert_channel_id
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def alert(self, product: dict) -> None:
        await send_restock_alert(await self.get_alert_channel(), product)
