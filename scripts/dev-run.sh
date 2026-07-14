#!/usr/bin/env bash
# Local development helper for Aegis.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found; install from https://docs.astral.sh/uv/" >&2
  exit 1
fi

uv sync --all-extras
exec uv run aegis "$@"
