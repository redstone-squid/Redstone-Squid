# Deploy Redstone Squid

Start with the [Compose setup guide](public/docker.md) for a new instance.
Keep the production `.env` on the host and use the same database credentials
across the services.

## Release deployments

CI publishes amd64 app and worker images to GHCR. After smoke tests pass on
master, CI publishes their digests together in a small `release:production`
image. The VPS polls that image and runs migrations before replacing services.
A failed readiness check restores the last successful image pair. Migrations
must remain compatible with the previous release.

On the VPS, update the checkout and remove `compose.override.yml` from the
`COMPOSE_FILE` list in your host .env. Migrations now live in the base file.
Keep the project name, edge configuration and resource-pack override paths.
Keep this checkout current when deployment scripts or Compose files change.

As the deployment user, log in with `docker login ghcr.io` if the packages
are private (use a token with read:packages). From the project directory,
run the first deployment and inspect the result:

```sh
bash scripts/pull-release.sh ghcr.io/redstone-squid/redstone-squid/release:production
docker compose ps
```

Add the following line to that user's crontab, substituting the absolute
checkout path. The host needs Bash, Docker Compose and flock. Overlapping
polls exit and an already deployed image pair is skipped.

```cron
*/5 * * * * cd /path/to/Redstone-Squid && bash scripts/pull-release.sh ghcr.io/redstone-squid/redstone-squid/release:production
```

Use `docker compose restart bot` to restart the existing bot. To reapply
host mount or environment changes using the deployed images, run
`bash scripts/deploy.sh $(cat .deploy/current-release)` from the checkout.

## Shared gateway

The host provides Caddy, TLS, DNS, and the external Docker network `edge`.
Caddy sends API requests to `redstone-api:8000` and website requests to
`redstone-web:4321`. Set these values in `.env`.

```dotenv
COMPOSE_PROJECT_NAME=redstone-squid
COMPOSE_FILE=compose.yml:deploy/compose.edge.yml
SITE_URL=https://squid.your-domain.com
```

The edge configuration starts the website and loopback-only pgweb, attaches
the API to the proxy network, and removes the API's host port. Create the
`edge` network if the host does not already provide it. For same-origin browser
authentication, the site's `API_BASE_URL` defaults to `SITE_URL`.

The website renders pages by calling the API itself, so the API sees the website rather
than the visitor. The website forwards each visitor's address in `X-Forwarded-For` and calls
the API container directly (`API_INTERNAL_URL`), since Caddy would replace that header. The
API trusts the header from Docker's default address pools
(`SQUID_API_TRUSTED_PROXY_IPS`); if the host's Docker uses other pools, set it to them, or
every visitor shares one rate-limit quota.

Use plain `docker compose` commands to load the file list from `.env`.
The release deploy script appends its image configuration to that list.
Rebuild the website separately with `docker compose up -d --build --wait web`.

## Health and logs

The API serves `/livez` and `/readyz` on port 8000 by default. The bot and worker
serve the same endpoints inside their containers on ports 8001 and 8002.
Readiness checks include the database and schema. The API can fall back to
local rate limiting while Redis is unavailable.

Use `docker compose logs -f api bot worker migrate` to follow application and migration
output. Docker's `local` logging driver rotates logs per container. For retention across
container replacements, configure log forwarding to your backend. The optional
OpenTelemetry collector handles traces and metrics.

With the edge overlay, `SQUID_OBSERVABILITY_ENABLED=true` also enables Astro
request traces. Astro sends them to the same collector, using the `-web`
service name suffix and the configured sampling ratio. Start the collector
with the `observability` profile. To check propagation, open an API-backed
catalogue page and look for the same trace ID in the web and API spans.
The bundled collector logs received telemetry. Configure a trace exporter
in [the collector configuration](../deploy/otel-collector.yaml) for persistent
storage and search.

On 10% of catalogue page loads that have a sampled server trace, the browser
reports time to first byte, time to DOM completion, and the category of its
first uncaught JavaScript error or unhandled promise rejection, if one occurs.
The site sends these reports without cookies, URLs, error messages, or stacks.
The same-origin endpoint caps report size and volume per web process and exports
browser spans with a link to the page span. The web trace sampler also applies
to these spans.

For optional media workers, see [Media processing deployment](media-processing.md).
