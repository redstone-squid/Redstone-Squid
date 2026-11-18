# Run your own bot instance

A self-hosted instance uses its own database. Use the bot already installed
in your Discord server if you want to contribute to that server's catalogue.

## Prepare Discord and the environment

Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications).
Enable the **Server Members Intent** and **Message Content Intent** on its **Bot**
page. Install it in your Discord server with the `bot` and `applications.commands`
scopes.

You need Git and Docker with the Compose plugin. Clone the repository and copy
its configuration template.

```console
git clone https://github.com/redstone-squid/Redstone-Squid.git
cd Redstone-Squid
cp .env.example .env
```

Set `SQUID_DISCORD_TOKEN` and replace the required secret placeholders in
`.env`. Set a database password and use the same credentials in the
`POSTGRES_*` settings and `SQUID_DATABASE_URL`. Keep `db` as the database
hostname when using the included PostgreSQL service. The template describes
the required settings and the format of each secret.

## Start the services

```console
docker compose up -d --build --wait
docker compose ps
```

The default Compose files start PostgreSQL, apply database migrations, and
then start the API, Discord bot, and background worker. If startup fails,
check the logs for the affected service.

```console
docker compose logs migrate bot
```

On a fresh Discord application, publish the slash commands from the bot
owner's Discord account. Send a message that mentions the bot followed by `s`.
Use `/settings` in Discord to configure the server and `/help` to verify
that commands are available.

For production image rollouts, shared gateway configuration, and optional media
processing, see the [deployment notes in the repository](https://github.com/redstone-squid/Redstone-Squid/blob/master/docs/docker.md).
