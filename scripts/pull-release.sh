#!/usr/bin/env bash
# Poll the production channel from the Compose project directory.
set -Eeuo pipefail
image=${1:?pass the GHCR release image}
state=${SQUID_DEPLOYMENT_STATE_DIRECTORY:-.deploy}
mkdir -p "$state"
exec 9>"$state/pull.lock"
flock -n 9 || exit 0
docker pull "$image"
container=$(docker create "$image")
release=$(mktemp "$state/pull.XXXXXX")
trap 'docker rm "$container" >/dev/null; rm -f "$release"' EXIT
docker cp "$container:/release" "$release"
if ! cmp -s "$release" "$state/current-release"; then
    mapfile -t images < "$release"
    bash scripts/deploy.sh "${images[@]}"
fi
