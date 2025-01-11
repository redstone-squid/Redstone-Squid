"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid"
"""

import asyncio
import multiprocessing

from bot.main import main
from api import main as api_main


if __name__ == "__main__":
    # Check bot/config.py for configuration, .env.example for environment variables
    multiprocessing.Process(target=api_main).start()
    asyncio.run(main())
