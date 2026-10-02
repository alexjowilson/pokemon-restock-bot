# Pokémon TCG Restock Bot 🚨

A lightweight Discord bot that monitors major retailers for Pokémon TCG restocks and sends real-time alerts to a Discord server.

---

## ✨ Features

- 🔍 Monitors multiple retailers (Walmart, Target, Amazon, Costco, etc.)
- 🚨 Sends instant Discord alerts when products come back in stock
- 🧠 Avoids duplicate alerts by tracking previous stock state
- 🖥 Runs 24/7 on a Raspberry Pi or any always-on machine
- ⚙️ Easily configurable via YAML

---

## 🧰 Tech Stack

- **Python 3.10+**
- **discord.py**
- **Requests / Playwright** (for scraping)
- **AsyncIO**
- **Raspberry Pi** (optional deployment)

---

## 📁 Project Structure
```text 
pokemon-restock-bot/
├─ bot/ # Discord client & notifications
├─ monitors/ # Retailer-specific stock checkers
├─ config/ # Configuration files
├─ data/ # Runtime state (ignored by git)
├─ utils/ # Shared helpers
├─ tests/ # pytest suite
├─ main.py # Entry point
├─ .env.example # Copy to .env (never commit .env)
└─ requirements.txt
```

---

## ⚙️ Setup

```bash
git clone https://github.com/alexjowilson/pokemon-restock-bot.git
cd pokemon-restock-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN and APP_ID
```

1. Turn on Developer Mode in Discord, right-click your alerts channel → **Copy Channel ID**, and put it in `config/config.yaml` (`discord.alert_channel_id`) or `ALERT_CHANNEL_ID` in `.env`.
2. Optional while developing: set `guild_id` to your server's ID so slash commands appear instantly.
3. Run `python main.py`, then try `/testalert` and `/status`.

### How it works

`bot/scheduler.py` checks every product in `config.yaml` every `check_interval_seconds`, using the monitor registered for its `retailer` in `monitors/__init__.py`. It alerts only when a product goes from out of stock to in stock, and stores last-known status in `data/state.json` so restarts don't re-alert. A failed check (timeout, blocked request) is logged and never treated as "out of stock".

### Adding a retailer

Write `monitors/<retailer>.py` with `async def check_product(product: dict) -> dict` returning `{"in_stock": bool, "price": ..., "image_url": ...}`, raise on failure, and register it in `MONITORS`.

### Tests

```bash
pip install pytest && pytest
```

## 🚧 Roadmap

- [x] Slash commands (`/status`, `/testalert`)
- [x] Polling loop with de-duplicated alerts
- [ ] First real retailer monitor
- [ ] Per-retailer rate limiting & backoff
- [ ] systemd / Docker deployment on the Pi

## ⚠️ Disclaimer

This project is for educational and personal use only.
Retailer websites may have terms of service regarding automated access.

Pokémon and Pokémon TCG are trademarks of Nintendo, Creatures, and GAME FREAK.