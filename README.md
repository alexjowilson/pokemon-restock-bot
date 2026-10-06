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

`bot/scheduler.py` checks every product in `config/config.yaml` every `check_interval_seconds`, using the monitor registered for its `retailer` in `monitors/__init__.py`. It alerts only when a product goes from out of stock to in stock, and stores last-known status in `data/state.json` so restarts don't re-alert.

Reliability:
- A failed check (timeout, blocked, bad URL) never counts as "out of stock".
- Failures back off exponentially with jitter, harder when a site returns 403/429.
- After 5 failures in a row the bot posts a warning (to `health_channel_id`, or the alert channel), and a note when checks recover.
- Requests to the same site are spaced at least 2s apart, with an honest User-Agent.

### Supported retailers

| `retailer` | What to put in config | Notes |
|---|---|---|
| `shopify` | `url` of the product page (`/products/<handle>`) | Most independent card shops. No key needed. Optional `variant_id`, `store_name`. |
| `bestbuy` | `sku` (+ `url` for the link) | Official API. Needs `BESTBUY_API_KEY`; Best Buy only issues keys to company emails. |
| `demo` | `demo_in_stock: true/false` | For testing the pipeline. |

### Filtering store searches

Card shops list thousands of single cards, and a search only returns the top 10 matches, so results shift between checks. Per search you can set:
- `require_all`: words every title must contain (e.g. `["pokemon"]`)
- `include_any`: at least one must appear (e.g. sealed types: `["elite trainer box", "booster", "bundle", "tin", "collection", "box"]`)
- `exclude`: skip titles containing any of these
- `new_listing_max_age_hours` (default 72): before posting 🆕, the bot checks the product's publish date, so old listings that drift into the results aren't announced as new.

Matching ignores case and accents ("Pokémon" = "pokemon") and matches word starts ("tin" matches "Tins" but not "Destined").

### Separate channels per store

Add `channel_id:` (and optionally `role_id:`) to any entry under `searches:` or `products:` to send its alerts to its own channel and ping its own role. Entries without one use `alert_channel_id` / `alert_role_id`.

### Slash commands

- `/status`: what's being watched, last check, and any failing monitors
- `/checknow`: checks everything right now and shows the result without alerting (use it to verify new URLs)
- `/testalert`: posts a sample alert embed

### Adding a retailer

Write `monitors/<retailer>.py` with `async def check_product(product: dict) -> dict` returning `{"in_stock": bool, "price": ..., "image_url": ..., "cart_url": ...}`. Fetch with `monitors.http.http.get_json`, raise `monitors.errors.CheckFailed` on failure, and register it in `MONITORS`.

### Tests

```bash
pip install pytest && pytest
```

## 🚧 Roadmap

- [x] Slash commands (`/status`, `/testalert`)
- [x] Polling loop with de-duplicated alerts
- [x] Shopify + Best Buy monitors
- [x] Backoff, per-host rate limiting, health alerts
- [ ] `/watch add` / `/watch remove` commands
- [ ] systemd / Docker deployment on the Pi

## ⚠️ Disclaimer

This project is for educational and personal use only.
Retailer websites may have terms of service regarding automated access.

Pokémon and Pokémon TCG are trademarks of Nintendo, Creatures, and GAME FREAK.