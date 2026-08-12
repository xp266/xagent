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
uv run --frozen python -c "from src.utils.catalog import fetch_catalog_sync, load_catalog; ok = fetch_catalog_sync(); print(f'OK, {len(load_catalog())} providers' if ok else 'catalog fetch failed')"

echo "==> done"
