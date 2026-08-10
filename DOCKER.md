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

See [Media normalization deployment](docs/media-normalization.md) before setting `SQUID_MEDIA_ENABLED=true`; it
documents the required paths, shared object storage, scratch-space arithmetic, and subprocess/container limits.

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
