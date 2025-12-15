import os
import discord
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")

def run_bot():
    client.run(os.getenv("DISCORD_TOKEN"))
