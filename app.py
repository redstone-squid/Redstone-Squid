"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid"
"""

import asyncio
import multiprocessing
import sys
from importlib.util import find_spec

from squid.api import main as api_main
from squid.bot import main as bot_main
from squid.config import DEV_MODE

if __name__ == "__main__":
    # Check bot/config.py for configuration, .env.example for environment variables
    multiprocessing.Process(target=api_main).start()
    asyncio.run(bot_main(), debug=DEV_MODE)
