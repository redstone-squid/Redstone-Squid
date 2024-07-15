# https://github.com/redstone-squid/Redstone-Squid
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
from bot import config

config.DEV_MODE = False  # Have to be above the import of main

import asyncio  # noqa: E402
from bot.main import main  # noqa: E402


asyncio.run(main())
