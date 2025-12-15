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
├─ main.py # Entry point
├─ .env # Environment variables (not committed)
└─ requirements.txt
```

---

## ⚙️ Setup

### 1️⃣ Clone the repository
```bash
git clone https://github.com/alexjowilson/pokemon-restock-bot.git 
cd pokemon-restock-bot
```

### 🚧 Roadmap

 Walmart Pokémon TCG monitoring

 Target & Amazon support

 Slash commands (/status, /testalert)

 Rate-limit handling & retries

 Docker / systemd deployment

 ### ⚠️ Disclaimer

This project is for educational and personal use only.
Retailer websites may have terms of service regarding automated access.

Pokémon and Pokémon TCG are trademarks of Nintendo, Creatures, and GAME FREAK.