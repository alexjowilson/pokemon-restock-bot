from bot.client import client, tree, run_bot
import bot.commands  # registers slash commands

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")

    # Sync slash commands (global)
    await tree.sync()
    print("✅ Slash commands synced")

if __name__ == "__main__":
    run_bot()