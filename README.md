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
'''text 
pokemon-restock-bot/
├─ bot/ # Discord client & notifications
├─ monitors/ # Retailer-specific stock checkers
├─ config/ # Configuration files
├─ data/ # Runtime state (ignored by git)
├─ utils/ # Shared helpers
├─ main.py # Entry point
├─ .env # Environment variables (not committed)
└─ requirements.txt
'''


---

## ⚙️ Setup

### 1️⃣ Clone the repository
```bash
git clone https://github.com/your-username/pokemon-restock-bot.git
cd pokemon-restock-bot
