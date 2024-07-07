# https://github.com/Kappeh/RecordBot
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
from Discord import config
config.DEV_MODE = False

import asyncio
import Discord.main as discord


asyncio.run(discord.main())
