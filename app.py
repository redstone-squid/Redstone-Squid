# https://github.com/Kappeh/RecordBot
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
from Discord import config
config.DEV_MODE = False

import asyncio
from dotenv import load_dotenv
import Discord.interface as discord


load_dotenv('squid.env')
asyncio.run(discord.main())
