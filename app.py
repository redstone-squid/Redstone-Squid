# RecordBot version 1.0
# Made by Kappeh
# https://github.com/Kappeh/RecordBot
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
import asyncio
import Discord.interface as discord

DEV_BOT = False
if DEV_BOT:
    asyncio.run(discord.main(prefix='.'))
else:
    asyncio.run(discord.main())
