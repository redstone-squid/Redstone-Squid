"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid"
"""

import sys
import asyncio
import multiprocessing

from bot.main import main
from api import main as api_main


if __name__ == "__main__":
    # Check bot/config.py for configuration, .env.example for environment variables
    multiprocessing.Process(target=api_main).start()

    if sys.platform == 'win32':  # https://github.com/aio-libs/aiodns/issues/86
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
