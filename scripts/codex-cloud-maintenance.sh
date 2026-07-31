#!/usr/bin/env bash
# Refresh project dependencies when Codex Cloud resumes a cached container.
# Can be run from any directory. See README, Development Setup, Codex Cloud.
#
# Usage: bash scripts/codex-cloud-maintenance.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

uv sync --locked
