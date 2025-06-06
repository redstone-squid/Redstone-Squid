"""
Entry point of the bot and the API.

https://github.com/redstone-squid/Redstone-Squid
"""

import asyncio
import multiprocessing

from dotenv import load_dotenv

from squid.api import main as api_main
from squid.bot import ApplicationConfig, main as bot_main

if __name__ == "__main__":
    # Check .env.example for environment variables configuration
    config: ApplicationConfig = {
        "dev_mode": False,
        "dotenv_path": ".env",
        "bot_config": {
            "prefix": "!",
            "owner_id": 353089661175988224,
            "owner_server_id": 433618741528625152,
            "bot_name": "Redstone Squid",
            "bot_version": "1.5.7",
            "source_code_url": "https://github.com/redstone-squid/Redstone-Squid",
            "print_tracebacks": True,
        },
    }

    if config.get("dotenv_path"):
        load_dotenv(config.get("dotenv_path"))
    multiprocessing.Process(target=api_main).start()
    asyncio.run(bot_main(config=config), debug=config.get("dev_mode", False))
