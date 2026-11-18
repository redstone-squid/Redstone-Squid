If you want to contribute to this bot PLEASE contact papetoast on discord. I will be happy to onboard you.

Read this in other languages: [🇮🇸](./docs/readme/README-is.md)

# Redstone Squid

(Click [here](https://discordapp.com/oauth2/authorize?client_id=528946065668308992&scope=bot&permissions=8) to invite the main instance to your server.)

Redstone Squid is a Discord bot for browsing Minecraft redstone builds,
submitting designs for review, and tracking records.

- [Use the bot](docs/public/use-the-bot.md) to find builds and submit your own.
- [Run your own instance](docs/public/docker.md) with Docker Compose.
- [Build with Squid UI](docs/public/squid-ui.md), the Python framework behind
  the bot's interactive screens.

Setting up your own version of this bot is **NOT RECOMMENDED** as there is already an instance running which you can invite to your discord server. If you create your own instance, it will have a separate database to the already running instance.


### Setting Up Channels

Before the bot can post any records to your server, you must tell it here to post each category. Multiple categories can be set to a single channel. Use `/settings` to set them up.

## Contributing

See the [contributing guide](.github/CONTRIBUTING.md) for development setup,
the [deployment notes](docs/docker.md) for operating a production instance,
and the [code of conduct](.github/CODE_OF_CONDUCT.md).

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

###  Adding The Bot To Servers

You can add the bot to your server by going to `https://discordapp.com/oauth2/authorize?client_id=<REPLACE WITH YOUR BOT'S ID>&scope=bot`.

## License

Redstone Squid is available under the [MIT License](LICENSE).
