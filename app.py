"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid
"""

import multiprocessing
import sys

from squid.api.app import main as api_main
from squid.bot.app import main as bot_main
from squid.config import load_application_config

if __name__ == "__main__":
    config = load_application_config()
    multiprocessing.Process(target=api_main, args=(config.api_process(),)).start()

    if sys.platform == "win32":
        import asyncio

        asyncio.run(bot_main(process_config=config.bot_process()), debug=config.development_mode)
    else:
        import uvloop  # pyright: ignore[reportMissingImports]

        uvloop.run(bot_main(process_config=config.bot_process()), debug=config.development_mode)
