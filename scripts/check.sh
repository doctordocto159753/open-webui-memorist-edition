#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../memorist-core"
UV_CMD="${UV:-python -m uv}"
$UV_CMD sync --all-extras --dev
$UV_CMD run ruff check .
$UV_CMD run mypy src/memcore
$UV_CMD run pytest -q
