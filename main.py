import logging

from bot.client import RestockBot
from utils.config import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    bot = RestockBot(settings)
    bot.run(settings.discord_token, log_handler=None)  # we already configured logging


if __name__ == "__main__":
    main()
