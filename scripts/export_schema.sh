#!/usr/bin/env bash
# Rebuild the two generated views of the database schema from the Alembic migrations:
#
#   schema/schema_dump.sql  one file, for grep and for pasting into a review
#   schema/structure/       one file per table and function, for browsing and readable diffs
#
# Both come from the same throwaway PostgreSQL container, so they always agree with the migrations.
# CI runs this script too (.github/workflows/schema-artifacts.yml). Needs docker, curl and uv.
set -euo pipefail

# Our fork of https://github.com/johto/pg_split_dump with --no-owner, --pg-dump-arg and --fk-in-table.
# Bumping the tag means updating the checksum from the release's .sha256 asset.
PG_SPLIT_DUMP_TAG="v0.2.1-squid.2"
PG_SPLIT_DUMP_SHA256="97d673532dcc237d6131df2e3c1fbc8578307c9c8cd3e3ee65659060233ac465"
POSTGRES_IMAGE="pgvector/pgvector@sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21" # 0.8.1-pg17

cd "$(dirname "$0")/.."
work="$(mktemp -d)"
container=""
cleanup() {
    if [ -n "$container" ]; then
        docker rm -f "$container" >/dev/null 2>&1 || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT

# Download the splitter once and check it against the pinned checksum.
cache="${XDG_CACHE_HOME:-$HOME/.cache}/redstone-squid"
splitter="$cache/pg_split_dump-$PG_SPLIT_DUMP_TAG"
if [ ! -x "$splitter" ]; then
    mkdir -p "$cache"
    curl --fail --silent --show-error --location --output "$work/pg_split_dump" \
        "https://github.com/Glinte/pg_split_dump/releases/download/$PG_SPLIT_DUMP_TAG/pg_split_dump-x86_64-linux"
    echo "$PG_SPLIT_DUMP_SHA256  $work/pg_split_dump" | sha256sum --check --quiet -
    chmod +x "$work/pg_split_dump"
    mv "$work/pg_split_dump" "$splitter"
fi

container="$(docker run --detach \
    --env POSTGRES_DB=redstone_squid --env POSTGRES_PASSWORD=postgres --env POSTGRES_USER=postgres \
    --publish 127.0.0.1::5432 "$POSTGRES_IMAGE")"
port="$(docker port "$container" 5432/tcp | head -n 1 | sed 's/.*://')"

# The image starts a temporary server that listens only on its socket while it initializes the database,
# so wait for the TCP port to answer.
until docker exec "$container" pg_isready --host 127.0.0.1 --username postgres --dbname redstone_squid >/dev/null 2>&1; do
    sleep 1
done

SQUID_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:$port/redstone_squid" uv run --locked alembic upgrade head

docker exec "$container" pg_dump \
    --username=postgres \
    --dbname=redstone_squid \
    --schema-only \
    --no-owner \
    --no-privileges \
    --exclude-table=public.alembic_version \
    --restrict-key=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
    --encoding=UTF8 > "$work/schema_dump.sql"

# The host's pg_dump may be older than the server's, so run the container's. pg_split_dump connects from the
# host, but the container's pg_dump must connect locally, so swap in a local connection string.
cat > "$work/pg_dump" <<EOF
#!/usr/bin/env bash
args=()
while [ \$# -gt 0 ]; do
    if [ "\$1" = "--dbname" ]; then
        args+=(--username postgres --dbname redstone_squid)
        shift 2
    else
        args+=("\$1")
        shift
    fi
done
exec docker exec --interactive "$container" pg_dump "\${args[@]}"
EOF
chmod +x "$work/pg_dump"

"$splitter" \
    --pg-dump-binary="$work/pg_dump" \
    --no-owner \
    --fk-in-table \
    --pg-dump-arg=--no-privileges \
    --pg-dump-arg=--exclude-table=public.alembic_version \
    "host=127.0.0.1 port=$port user=postgres password=postgres dbname=redstone_squid" \
    "$work/structure"

# Replace the outputs only after everything has succeeded.
rm -rf schema/structure
mv "$work/structure" schema/structure
mv "$work/schema_dump.sql" schema/schema_dump.sql
