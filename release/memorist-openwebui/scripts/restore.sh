#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/common.sh"
ROOT="$(memorist_root)"
MODE="$(memorist_mode "$ROOT")"
if [[ "$MODE" == full ]]; then
  echo "FAIL Full PostgreSQL restore is operator-managed; stop services and use pg_restore after verifying the dump."
  exit 2
fi
if [[ -z "${1:-}" ]]; then echo "FAIL restore requires backup path"; exit 2; fi
if [[ "${2:-}" != "--yes-i-understand" ]]; then
  echo "WARN restore runs dry-run first. Re-run with --yes-i-understand to execute after reviewing."
  memorist_compose "$ROOT" "$MODE" exec -T memorist-core python -m memcore.heritage restore "$1" --db-path /data/restore-preview.sqlite --dry-run || true
  exit 2
fi
memorist_compose "$ROOT" "$MODE" exec -T memorist-core python -m memcore.heritage restore "$1" --db-path /data/memorist.sqlite
