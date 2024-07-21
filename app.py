"""https://github.com/redstone-squid/Redstone-Squid

This is the main entry point of the bot for Heroku."""

from bot import config

config.DEV_MODE = True  # Have to be above the import of main

import asyncio  # noqa: E402
from bot.main import main  # noqa: E402


asyncio.run(main())
