#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${1:-}" ]]; then echo "FAIL restore requires backup path"; exit 2; fi
if [[ "${2:-}" != "--yes-i-understand" ]]; then
  echo "WARN restore runs dry-run first. Re-run with --yes-i-understand to execute after reviewing."
  docker compose -f compose.yml exec -T memorist-core python -m memcore.heritage restore "$1" --db-path /data/restore-preview.sqlite --dry-run || true
  exit 2
fi
docker compose -f compose.yml exec -T memorist-core python -m memcore.heritage restore "$1" --db-path /data/memorist.sqlite
