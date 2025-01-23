"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid"
"""

import sys
import asyncio
import logging
from importlib.util import find_spec
import multiprocessing

from bot.main import main
from api import main as api_main


if __name__ == "__main__":
    # Check bot/config.py for configuration, .env.example for environment variables
    multiprocessing.Process(target=api_main).start()

    if sys.platform == "win32" and find_spec("aiodns"):
        # https://github.com/Rapptz/discord.py/pull/9898 & https://github.com/aio-libs/aiodns/issues/86
        raise RuntimeError("aiodns is not needed on Windows. You can safely uninstall it. "
                           "Setting Windows Selector Event Loop Policy is not an option because ProactorEventLoop is needed for subprocesses.")
        # https://stackoverflow.com/questions/44633458/why-am-i-getting-notimplementederror-with-async-and-await-on-windows

    asyncio.run(main())
