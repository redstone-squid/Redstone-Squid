# https://github.com/Kappeh/RecordBot
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
from bot import config
config.DEV_MODE = True  # Have to be above the import of main

import asyncio
from bot.main import main


asyncio.run(main())
