#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required but not found. Install it: https://docs.astral.sh/uv/" >&2
    exit 1
fi

index="${XAGENT_PYPI_INDEX:-}"
if [ -n "$index" ]; then
    echo "==> using custom index: $index"
    uv lock --index-url "$index"
fi

echo "==> syncing dependencies"
if [ -f uv.lock ]; then
    uv sync --frozen
else
    uv sync
fi

echo "==> prefetching model catalog"
uv run --frozen python scripts/prefetch_catalog.py

echo "==> done"
