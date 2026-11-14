# Docker Setup for Redstone Squid

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/redstone-squid/Redstone-Squid.git
   cd Redstone-Squid
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

3. **Build and run with Docker Compose:**
   ```bash
   docker compose up -d
   ```

4. **Check logs:**
   ```bash
   docker compose logs -f server
   ```

## Manual Docker Commands

### Build the image:
```bash
docker build -t redstone-squid .
```

### Run the container:
```bash
docker run -d \
  --name redstone-squid-bot \
  --env-file .env \
  -p 8000:8000 \
  --restart unless-stopped \
  redstone-squid
```

The dedicated worker build includes the pinned FFmpeg/ffprobe toolchain but leaves media processing disabled:

```bash
docker build --build-arg WITH_MEDIA=1 --build-arg WITH_SCHEMATICS=1 -t redstone-squid-worker .
```

See [Media normalization deployment](media-normalization.md) before setting `SQUID_MEDIA_ENABLED=true`; it
documents the required paths, shared object storage, scratch-space arithmetic, and subprocess/container limits.

## Shared gateway hosts

The app repository owns the API, bot, worker, and website. Host infrastructure owns
Caddy, TLS, DNS, and the shared external `edge` network. Caddy routes API paths to
`redstone-api:8000` and catalogue pages to `redstone-web:4321`.

Set these values in this checkout's ignored `.env` once:

```dotenv
COMPOSE_PROJECT_NAME=redstone-squid
COMPOSE_FILE=compose.yml:compose.override.yml:deploy/compose.edge.yml
SITE_URL=https://squid.your-domain.com
```

If this host needs `compose.render.yml`, insert it before `deploy/compose.edge.yml`
in `COMPOSE_FILE`. Create the shared network with `docker network create edge` if
the host infrastructure has not already created it. Then run:

```sh
docker compose up -d --build --wait
```

The edge configuration starts the website and loopback-only pgweb, connects the API
to Caddy, and removes the API's host port. Website settings come from the same
`.env`; `API_BASE_URL` defaults to `SITE_URL` for same-origin browser authentication.
See `.env.example` for optional website and network settings.

Compose reads `COMPOSE_FILE` from `.env` for ordinary `up`, `logs`, and `ps`
commands. Explicit `-f` arguments replace that selection, so use plain commands on
configured hosts. See [Docker's Compose environment reference](https://docs.docker.com/compose/how-tos/environment-variables/envvars/#compose_file).

`scripts/deploy.sh` also preserves this file selection when appending its immutable
image configuration. Release cutover and rollback target the API, bot, worker, and
Redis; rebuild the website separately with `docker compose up -d --build --wait web`.

## Updating

To update the bot:

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose up -d --build
```

## Stopping the Bot

```bash
# Stop and remove containers
docker compose down

# Stop, remove containers, and remove volumes
docker compose down -v
```
