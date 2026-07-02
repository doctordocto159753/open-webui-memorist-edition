#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/exports/backup.sqlite}"
mkdir -p "$(dirname "$OUT")"
cd "$ROOT"
docker compose -f compose.yml exec -T memorist-core python -m memcore.reliability backup --out /exports/backup.sqlite
echo "OK backup requested: $OUT"
