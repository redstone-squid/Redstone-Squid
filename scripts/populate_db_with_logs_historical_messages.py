import os

from discord import TextChannel
from dotenv import load_dotenv

from squid.bot import RedstoneSquid
from squid.db.builds import Build


async def process_channel(channel: TextChannel, model: str):
    async for message in channel.history(oldest_first=True):
        if message.author.bot:
            continue

        await Build.ai_generate_from_message(message, model=model)


async def main():
    load_dotenv()

    model = "gpt-4.1-nano"
    build_logs_id = 726156829629087814
    record_logs_id = 667401499554611210

    async with RedstoneSquid() as bot:
        await bot.start(os.getenv("BOT_TOKEN"))
        build_logs = bot.get_channel(build_logs_id)
        record_logs = bot.get_channel(record_logs_id)

        tasks = [
            process_channel(build_logs, model),
            process_channel(record_logs, model),
        ]

        await asyncio.gather(*tasks)



if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
