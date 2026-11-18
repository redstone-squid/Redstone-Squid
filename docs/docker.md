# Deploy Redstone Squid

Start with the [Compose setup guide](public/docker.md) for a new instance.
Keep the production `.env` on the host and use the same database credentials
across the services.

## Release deployments

The production workflow publishes two immutable images. The API and bot use
one image, and the worker uses a separate image with the optional schematic
engine. The host needs Docker Compose, `.env`, and the deployment files that
CI copies to it.

CI authenticates to the host using the repository secrets `SSH_HOST`,
`SSH_USER`, and `SSH_KEY`. Put the pinned OpenSSH host entry in
`SSH_KNOWN_HOSTS`.

The deploy script runs Alembic using the new image before replacing the
running services. It saves the previous image digests in
`.deploy/previous-release` and restores them if the new services fail readiness.
Plan schema changes so the previous application can still run against the
upgraded database.

## Shared gateway

The host provides Caddy, TLS, DNS, and the external Docker network `edge`.
Caddy sends API requests to `redstone-api:8000` and website requests to
`redstone-web:4321`. Set these values in `.env`.

```dotenv
COMPOSE_PROJECT_NAME=redstone-squid
COMPOSE_FILE=compose.yml:compose.override.yml:deploy/compose.edge.yml
SITE_URL=https://squid.your-domain.com
```

The edge configuration starts the website and loopback-only pgweb, attaches
the API to the proxy network, and removes the API's host port. Create the
`edge` network if the host does not already provide it. For same-origin browser
authentication, the site's `API_BASE_URL` defaults to `SITE_URL`.

Use plain `docker compose` commands to load the file list from `.env`.
The release deploy script appends its image configuration to that list.
Rebuild the website separately with `docker compose up -d --build --wait web`.

## Health and logs

The API serves `/livez` and `/readyz` on port 8000 by default. The bot and worker
serve the same endpoints inside their containers on ports 8001 and 8002.
Readiness checks include the database and schema. The API can fall back to
local rate limiting while Redis is unavailable.

Use `docker compose logs api bot worker` to inspect standard output. The
observability collector reads the rotating JSON files from the shared
`squid-logs` volume. Each service keeps five backups per log file and rotates
the active file at 32 MiB.

For optional media workers, see [Media processing deployment](media-processing.md).
