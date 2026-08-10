#!/usr/bin/env bash
# Deploy one tested pair of immutable application images.

set -Eeuo pipefail

readonly STATE_DIRECTORY="${SQUID_DEPLOYMENT_STATE_DIRECTORY:-.deploy}"
readonly CURRENT_RELEASE_FILE="$STATE_DIRECTORY/current-release"
readonly PREVIOUS_RELEASE_FILE="$STATE_DIRECTORY/previous-release"
readonly COMPOSE_OVERRIDE="${SQUID_PRODUCTION_COMPOSE_FILE:-deploy/compose.production.yml}"
readonly LEGACY_PID_FILE=".app.pid"
readonly -a COMPOSE=(docker compose -f compose.yml -f "$COMPOSE_OVERRIDE")

usage() {
    printf 'Usage: %s APP_IMAGE@sha256:DIGEST WORKER_IMAGE@sha256:DIGEST\n' "$0" >&2
    exit 2
}

validate_image() {
    local image="$1"
    if [[ ! "$image" =~ ^[^[:space:]]+@sha256:[[:xdigit:]]{64}$ ]]; then
        printf 'Image must be pinned by sha256 digest: %s\n' "$image" >&2
        exit 2
    fi
}

read_release() {
    local release_file="$1"
    local -n app_result="$2"
    local -n worker_result="$3"
    [[ -f "$release_file" ]] || return 1
    IFS= read -r app_result < "$release_file"
    IFS= read -r worker_result < <(sed -n '2p' "$release_file")
    validate_image "$app_result"
    validate_image "$worker_result"
}

wait_for_legacy_process() {
    local pid="$1"
    local attempt
    for attempt in {1..20}; do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.5
    done
    printf 'Legacy launcher PID %s did not stop in time.\n' "$pid" >&2
    return 1
}

stop_legacy_launcher() {
    [[ -f "$LEGACY_PID_FILE" ]] || return 0
    local pid command
    IFS= read -r pid < "$LEGACY_PID_FILE"
    if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
        printf 'Refusing invalid legacy PID file contents.\n' >&2
        return 1
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        mv "$LEGACY_PID_FILE" "$STATE_DIRECTORY/stale-legacy.pid"
        return 0
    fi
    command=$(ps -p "$pid" -o args=)
    if [[ "$command" != *"app.py"* ]]; then
        printf 'Refusing to stop PID %s because it is not the legacy app.py launcher.\n' "$pid" >&2
        return 1
    fi
    kill "$pid"
    wait_for_legacy_process "$pid"
    mv "$LEGACY_PID_FILE" "$STATE_DIRECTORY/stopped-legacy.pid"
}

write_release() {
    local destination="$1"
    local app_image="$2"
    local worker_image="$3"
    local temporary
    temporary=$(mktemp "$STATE_DIRECTORY/release.XXXXXX")
    printf '%s\n%s\n' "$app_image" "$worker_image" > "$temporary"
    mv "$temporary" "$destination"
}

rollback() {
    local status=$?
    trap - ERR
    if [[ "$cutover_started" == true && -n "$previous_app_image" && -n "$previous_worker_image" ]]; then
        printf 'Cutover failed; restoring the previous service images.\n' >&2
        export SQUID_APP_IMAGE="$previous_app_image"
        export SQUID_WORKER_IMAGE="$previous_worker_image"
        # Older strict-config images do not know settings first introduced by the failed release.
        if ! SQUID_STRICT_UNKNOWN_KEYS=false "${COMPOSE[@]}" up -d --remove-orphans --wait --wait-timeout 180; then
            printf 'Automatic service rollback also failed; inspect Compose health and database compatibility.\n' >&2
        fi
    fi
    exit "$status"
}

[[ $# -eq 2 ]] || usage
app_image="$1"
worker_image="$2"
validate_image "$app_image"
validate_image "$worker_image"

mkdir -p "$STATE_DIRECTORY"
previous_app_image=""
previous_worker_image=""
read_release "$CURRENT_RELEASE_FILE" previous_app_image previous_worker_image || true
cutover_started=false
trap rollback ERR

export SQUID_APP_IMAGE="$app_image"
export SQUID_WORKER_IMAGE="$worker_image"

printf 'Pulling immutable release images.\n'
"${COMPOSE[@]}" pull api bot worker migrate redis

printf 'Applying database migrations from the application image.\n'
"${COMPOSE[@]}" run --rm migrate

stop_legacy_launcher
cutover_started=true
printf 'Starting independently supervised services.\n'
"${COMPOSE[@]}" up -d --remove-orphans --wait --wait-timeout 180 redis api bot worker

if [[ -n "$previous_app_image" && -n "$previous_worker_image" ]]; then
    write_release "$PREVIOUS_RELEASE_FILE" "$previous_app_image" "$previous_worker_image"
fi
write_release "$CURRENT_RELEASE_FILE" "$app_image" "$worker_image"
trap - ERR
printf 'Release is ready: %s / %s\n' "$app_image" "$worker_image"
