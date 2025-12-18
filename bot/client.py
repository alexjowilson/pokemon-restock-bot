import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()

client = discord.Client(
    intents=intents,
    application_id=int(os.getenv("APP_ID"))
)

tree = app_commands.CommandTree(client)

def run_bot():
    client.run(os.getenv("DISCORD_TOKEN"))