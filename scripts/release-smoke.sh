#!/usr/bin/env bash
# Exercise the exact published release images against a clean pgvector database.

set -Eeuo pipefail

usage() {
    printf 'Usage: %s APP_IMAGE@sha256:DIGEST WORKER_IMAGE@sha256:DIGEST\n' "$0" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
readonly APP_IMAGE="$1"
readonly WORKER_IMAGE="$2"
readonly DATABASE_CONTAINER="squid-release-db-${GITHUB_RUN_ID:-local}-$$"
readonly REDIS_CONTAINER="squid-release-redis-${GITHUB_RUN_ID:-local}-$$"
readonly API_CONTAINER="squid-release-api-${GITHUB_RUN_ID:-local}-$$"
readonly WORKER_CONTAINER="squid-release-worker-${GITHUB_RUN_ID:-local}-$$"
readonly DATABASE_PORT="${SQUID_RELEASE_DATABASE_PORT:-55432}"
readonly REDIS_PORT="${SQUID_RELEASE_REDIS_PORT:-56379}"
readonly API_PORT="${SQUID_RELEASE_API_PORT:-18000}"
readonly WORKER_PORT="${SQUID_RELEASE_WORKER_PORT:-18002}"
readonly DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:${DATABASE_PORT}/postgres"
readonly -a COMMON_ENV=(
    --env "SQUID_DATABASE_URL=$DATABASE_URL"
    --env SQUID_VERIFICATION_CODE_PEPPER=release-smoke-verification
    --env SQUID_CURSOR_SECRET=release-smoke-cursor-secret
    --env SQUID_SCHEMATIC_ENABLED=false
)

cleanup() {
    docker rm -f "$API_CONTAINER" "$WORKER_CONTAINER" "$REDIS_CONTAINER" "$DATABASE_CONTAINER" \
        >/dev/null 2>&1 || true
}

wait_for_command() {
    local description="$1"
    shift
    local attempt
    for attempt in {1..60}; do
        if "$@" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    printf '%s did not become ready.\n' "$description" >&2
    return 1
}

trap cleanup EXIT
docker run --detach --name "$DATABASE_CONTAINER" --publish "${DATABASE_PORT}:5432" \
    --env POSTGRES_PASSWORD=postgres \
    pgvector/pgvector:0.8.1-pg17@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21 \
    >/dev/null
wait_for_command "PostgreSQL" docker exec "$DATABASE_CONTAINER" pg_isready --username postgres
docker run --detach --name "$REDIS_CONTAINER" --publish "${REDIS_PORT}:6379" \
    redis:8.8.0-alpine@sha256:9d317178eceac8454a2284a9e6df2466b93c745529947f0cd42a0fa9609d7005 \
    redis-server --save "" --appendonly no --maxmemory 128mb --maxmemory-policy noeviction >/dev/null
wait_for_command "Redis" docker exec "$REDIS_CONTAINER" redis-cli ping

printf 'Applying the release schema.\n'
docker run --rm --network host "${COMMON_ENV[@]}" "$APP_IMAGE" alembic upgrade head
printf 'Checking process imports and native worker capability.\n'
docker run --rm "$APP_IMAGE" python -c 'import squid.api.app; import squid.bot.app'
docker run --rm "$WORKER_IMAGE" python -c 'import squid.worker.app; import nucleation'

printf 'Checking API readiness.\n'
docker run --detach --name "$API_CONTAINER" --network host "${COMMON_ENV[@]}" \
    --env SQUID_API_PORT="$API_PORT" \
    --env SQUID_API_SECRET=release-smoke-api-secret \
    --env SQUID_API_KEY_PEPPER=release-smoke-key-pepper \
    --env SQUID_API_SESSION_PEPPER=release-smoke-session-pepper \
    --env "SQUID_RATE_LIMIT_REDIS_URL=redis://127.0.0.1:${REDIS_PORT}/0" \
    "$APP_IMAGE" python -m squid.api.app >/dev/null
wait_for_command "API readiness" curl --fail --silent --show-error "http://127.0.0.1:${API_PORT}/readyz"
rate_limit_headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null \
    "http://127.0.0.1:${API_PORT}/openapi.json")
if ! grep -qi '^ratelimit:' <<< "$rate_limit_headers"; then
    printf 'API response did not include rate-limit quota headers.\n' >&2
    exit 1
fi

printf 'Checking worker readiness.\n'
docker run --detach --name "$WORKER_CONTAINER" --network host "${COMMON_ENV[@]}" \
    --env SQUID_WORKER_HEALTH_PORT="$WORKER_PORT" \
    --env SQUID_WORKER_EVENT_INTERVAL_SECONDS=0.1 \
    --env SQUID_WORKER_MAINTENANCE_INTERVAL_SECONDS=0.1 \
    "$WORKER_IMAGE" python -m squid.worker.app >/dev/null
wait_for_command "worker readiness" curl --fail --silent --show-error "http://127.0.0.1:${WORKER_PORT}/readyz"

docker stop --time 20 "$API_CONTAINER" "$WORKER_CONTAINER" >/dev/null
printf 'Release image smoke test passed.\n'
