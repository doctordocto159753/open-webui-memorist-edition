#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/common.sh"
if [[ "${1:-}" != "--yes-i-understand" ]]; then
  echo "FAIL reset-dev-data is destructive. Re-run with --yes-i-understand."
  exit 2
fi
ROOT="$(memorist_root)"
MODE="$(memorist_mode "$ROOT")"
memorist_compose "$ROOT" "$MODE" down -v --remove-orphans
rm -rf data objects imports exports
echo "OK dev data reset"
