#!/usr/bin/env bash
# Bootstrap uv, project dependencies, and prek hooks for OpenAI Codex Cloud.
# Can be run from any directory. See README, Development Setup, Codex Cloud.
#
# Usage: bash scripts/setup-codex-cloud-env.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

pip install --upgrade uv
uv sync --locked
uv tool install --force prek
prek install --overwrite --prepare-hooks
