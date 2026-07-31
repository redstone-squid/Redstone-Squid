#!/usr/bin/env bash
# Bootstrap just, uv, project dependencies, and prek hooks for Linux agent sandboxes.
#
# Usage: bash scripts/setup-claude-web-env.sh
# Override paths if needed: JUST_INSTALL_DIR=... REPO_DIR=... bash scripts/setup-claude-web-env.sh

set -euo pipefail

JUST_INSTALL_DIR="${JUST_INSTALL_DIR:-/home/user/bin}"
REPO_DIR="${REPO_DIR:-/home/user/Redstone-Squid}"

curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to "$JUST_INSTALL_DIR"
export PATH="$PATH:$JUST_INSTALL_DIR"

cd "$REPO_DIR"
# Sandbox images can include a stale uv; the installer does not depend on GitHub.
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --locked
uv tool install --force prek
prek install --overwrite --prepare-hooks
