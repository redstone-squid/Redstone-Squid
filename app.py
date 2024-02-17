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
# [discord]
# token = YOUR BOT TOKEN
# ```
# 5. Create a file called `client_secret.json` in the Google folder and add the credentials.

import Google.interface as google
import Discord.interface as discord