If you want to contribute to this bot PLEASE contact papetoast on discord. I will be happy to onboard you.

# Redstone Squid

This is a discord bot designed to make the process of submitting, confirming and denying submissions easier. In addition to this, it manages a database of records automatically.

Read this in other languages: [🇮🇸](./docs/readme/README-is.md)

## Getting Started

Setting up your own version of this bot is **NOT RECOMMENDED** as there is already an instance running which you can invite to your discord server. If you create your own instance, it will have a separate database to the already running instance. If you want to utilise this bot, skip to `Discord Set Up`.

To get this bot up and running on your machine, you will need a copy of this repository. To clone the repository, use:
```bash
git clone https://github.com/redstone-squid/Redstone-Squid.git
```

> [!NOTE]  
> If you have `submodule.recurse` set to `true`, you will pull [supabase](https://github.com/supabase/supabase) (2GB) for no reason other than running integration tests. It is recommended to set `submodule.recurse` to `false` before cloning this repository.

Then you can move to the repository's root directory with
```bash
cd Redstone-Squid
```

### Virtual Environment

> [!NOTE]  
> Node.js is not required even for development, it is only used in an experimental and optional tool [pgstrap](https://github.com/seveibar/pgstrap) to dump the database schema.

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

### Configuration

Copy the example environment file and replace its required placeholders:

```console
cp .env.example .env
```

The complete deployment requires `SQUID_DATABASE_URL`, `SQUID_VERIFICATION_CODE_PEPPER`,
`SQUID_DISCORD_TOKEN`, `SQUID_API_SECRET`, `SQUID_API_KEY_PEPPER`, `SQUID_API_SESSION_PEPPER`, and
`SQUID_CURSOR_SECRET`. Exported environment variables take precedence over `.env`. Discord OAuth,
REST voting, OpenAI, embedding, Catbox, and Google settings are documented in `.env.example`.

Configuration uses a strict `SQUID_`-prefixed contract. Previous deployments must rename their settings:

| Previous setting | Replacement |
| --- | --- |
| `DATABASE_URL` | `SQUID_DATABASE_URL` |
| `VERIFICATION_CODE_PEPPER` | `SQUID_VERIFICATION_CODE_PEPPER` |
| `BOT_TOKEN` | `SQUID_DISCORD_TOKEN` |
| `SYNERGY_SECRET` | `SQUID_API_SECRET` |
| `API_PORT` | `SQUID_API_PORT` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | `SQUID_OPENAI_API_KEY` / `SQUID_OPENAI_BASE_URL` |
| `EMBEDDING_OPENAI_API_KEY` / `EMBEDDING_OPENAI_BASE_URL` | `SQUID_EMBEDDING_API_KEY` / `SQUID_EMBEDDING_BASE_URL` |
| `EMBEDDING_MODEL` | `SQUID_EMBEDDING_MODEL` |
| `DB_CONNECTION` | `SQUID_VECTOR_DATABASE_URL` |
| `CATBOX_USERHASH` | `SQUID_CATBOX_USER_HASH` |
| `GOOGLE_CREDENTIALS` | `SQUID_GOOGLE_CREDENTIALS_JSON` |
| `LOG_DIR` / `LOG_LEVEL` / `ROOT_LOG_LEVEL` | `SQUID_LOG_DIRECTORY` / `SQUID_LOG_LEVEL` / `SQUID_ROOT_LOG_LEVEL` |
| `LOG_FILE` / `LOG_ACCESS_FILE` | `SQUID_BOT_LOG_FILE` / `SQUID_API_ACCESS_LOG_FILE` |

`DB_DRIVER_SYNC`, `DB_DRIVER_ASYNC`, `EMBEDDING_DIMENSION`, `SUPABASE_URL`, and `SUPABASE_KEY` have no
replacement. The application owns its PostgreSQL drivers, and vector dimension 1536 is fixed by the database schema.

Google service-account credentials can be supplied as JSON through `SQUID_GOOGLE_CREDENTIALS_JSON` or by setting
`SQUID_GOOGLE_CREDENTIALS_FILE` to an explicit path. Configure at most one.

Database schema changes are managed by Alembic. After configuring `SQUID_DATABASE_URL`, create or upgrade a database with:

```console
just db-upgrade
```

The baseline is portable PostgreSQL 15+ SQL and requires the
[pgvector](https://github.com/pgvector/pgvector) extension. Supabase remains a supported PostgreSQL host, but the
Supabase migration CLI is no longer used to apply application schema changes.

To get the database URL from Supabase, click **Connect** and copy a PostgreSQL connection string. For example:

```
SQUID_DATABASE_URL=postgresql://postgres.example:[YOUR-PASSWORD]@aws-0-us-west-1.pooler.supabase.com:5432/postgres
```

### Running the Application

For local development, the supervisor starts the API, Discord bot, and database worker as separate child processes and
stops the other two if any process exits:

```console
python app.py
```

Production uses separate service units so each process can be restarted and checked independently:

```console
docker compose up --build
```

The API exposes `/livez` and database/schema-aware `/readyz` on port 8000. The bot and worker expose the same endpoints
on their process-local `SQUID_BOT_HEALTH_PORT` (8001) and `SQUID_WORKER_HEALTH_PORT` (8002) listeners.

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
