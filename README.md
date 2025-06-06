
# Redstone Squid

This is a discord bot designed to make the process of submitting, confirming and denying submissions easier. In addition to this, it manages a database of records automatically.

Read this in other languages: [🇮🇸](./docs/readme/README-is.md)

## Getting Started

Setting up your own version of this bot is **NOT RECOMMENDED** as there is already an instance running which you can invite to your discord server. If you create your own instance, it will have a separate database to the already running instance. If you want to utilise this bot, skip to `Discord Set Up`.

To get this bot up and running on your machine, you will need a copy of this repository. To clone the repository, use:
```bash
git clone https://github.com/redstone-squid/Redstone-Squid.git
```
Then you can move to the repository's root directory with
```bash
cd Redstone-Squid
```

### Virtual Environment

There are a list of required python packages in requirements.txt. You can install them onto your machine directly or into a virtual environment (recommended)

If you want to use a virtual environment, first create the environment in the root directory and activate it.
```bash
python -m venv .venv
source .venv/bin/activate
```

### Installing Packages

In the root directory of the repository you can use the following command to install all the required packages. You can remove `requirements/dev.txt` if you just want to run the bot and not help develop it.
```bash
pip install -r requirements/base.txt requirements/dev.txt
```

Alternatively, if you use `uv`, you can run `uv sync`. The requirements folder and `uv.lock` are kept in sync.

### Credential Files

Google services requires a Google service account. You can read about google service accounts at https://cloud.google.com/iam/docs/understanding-service-accounts. Download the credentials JSON file and rename it `client_secret.json` and move it to the `Google` directory.

Discord requires a discord bot account. You can learn how to make bot accounts at https://github.com/reactiflux/discord-irc/wiki/Creating-a-discord-bot-&-getting-a-token. You will need the token to be placed in a file called `.env` in the root directory with the following contents:
```
BOT_TOKEN = <Replace this with your discord access token>
```

Supabase is the database used for this bot. You can sign up for a free account at https://supabase.com/. Once you have an account, you can create a new project and navigate to **Project Settings | API** and copy the URL and API key (secret, not public) to the same `.env` with the following contents:
```
SUPABASE_URL = <Replace this with your supabase url>
SUPABASE_KEY = <Replace this with your supabase api key>
```
The schema for the database can be obtained by applying the SQL files in Database/migrations in order.

Catbox is used as a free file hosting service.
```
CATBOX_USERHASH = <Replace this with your catbox user hash>
```

### Running The Application

The application can now be run simply with:
```
python app.py
```

## Discord Set Up

###  Adding Bot To Servers
You can add your bot to your server by going to `https://discordapp.com/oauth2/authorize?client_id=<REPLACE WITH YOUR BOT'S ID>&scope=bot`. It is recommended to give the bot administrator permissions but is not required for its functionality.

If you wish to invite the main instance to your server, click [here](https://discordapp.com/oauth2/authorize?client_id=528946065668308992&scope=bot&permissions=8).

### Setting Up Channels

Before the bot can post any records to your server, you must tell it here to post each category. Multiple categories can be set to a single channel.

As an example, let's pretend you want to set all categories to post to a channel called `#records`. Within the discord server you would run:
```
!settings smallest_channel set #records
!settings fastest_channel set #records
!settings first_channel set #records
```
Whenever a submission is confirmed by the bot's admins, it will be posted in the respective channel.

You can unset a channel by either setting it to another channel or running the unset command e.g.
```
!settings unset smallest_channel
```
In addition to this, you can check which channel a setting is currently set to via the query command e.g.
```
!settings query fastest_channel
```
If you want to query all settings at once, you can run:
```
!settings query_all
```

## Other Commands

This list of commands is subject to change due to improvements and new features. In fact, `discord.py` provides self-documenting help messages for each command, so you can always run `!help` to see the most up-to-date list of commands.

* `!invite_link` gives the user a link which they can use to add the bot to their servers.
* `!source_code` links a user to this GitHub repository.
* `!submit_record` provides a user to the Google Form which is used for collecting record submissions.
* `!settings` has been discussed above.
* `!submissions` is a server specific, role specific set of commands used to view, confirm and deny submissions. _This will be discussed below._
* `!help <command>` provides a user with a help message. If a command is provided, a help message for that command will be provided.

### Submissions Commands

`!submissions open` provides an overview submissions that are open for review.
`!submissions view <index>` displays the full submission with a given index.
`!submission confirm <index>` confirms a submission and posts it to the correct channels.
`!submissions deny <index>` denies a submission.

## Contributing

Please read [CODE_OF_CONDUCT.md](https://github.com/redstone-squid/Redstone-Squid/blob/master/CODE_OF_CONDUCT.md) for details on our code of conduct, and the process for submitting pull requests to us.

## License

This project is licensed under the MIT License - see the [LICENSE.md](https://github.com/redstone-squid/Redstone-Squid/blob/master/LICENSE) file for details
