"""https://github.com/redstone-squid/Redstone-Squid

This is the main entry point of the bot for Heroku."""

from bot import config

config.DEV_MODE = False  # Have to be above the import of main

import asyncio
import multiprocessing

from bot.main import main
from api import main as api_main


if __name__ == "__main__":
    multiprocessing.Process(target=api_main).start()
    asyncio.run(main())
