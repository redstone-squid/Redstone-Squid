# RecordBot version 1.0
# Made by Kappeh
# https://github.com/Kappeh/RecordBot
# This is the main file of the bot. It is responsible for starting the bot and handling the commands.
# Setup:
# 1. Install the required libraries with `pip install -r requirements.txt` or `conda install --file environment.yml`.
# 2. Create a Discord bot and get the token.
# 3. Create a Google service account and get the credentials.
# 4. Create a file called `auth.ini` in the Discord folder and add the following:
# ```
# [discord]f
# token = YOUR BOT TOKEN
# ```
# 5. Create a file called `client_secret.json` in the Google folder and add the credentials.
# 6. To get the json keyfile, go to the Google Cloud Console, create a new project, enable the Google Sheets API, create a service account, and download the json file.
# See: https://stackoverflow.com/questions/35054259/access-not-configured-the-api-drive-api-is-not-enabled-for-your-project-plea
# https://console.cloud.google.com/home/dashboard
# https://console.cloud.google.com/apis/library/sheets.googleapis.com
# 7. Create a spreadsheet in Google Sheets and share it with the email in the json file, the name of the file is the `WORKBOOK_NAME` in `config.py`.
import asyncio

import Google.interface as google
import Discord.interface as discord

asyncio.run(discord.main())
