#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/common.sh"
ROOT="$(memorist_root)"
OUT="${1:-$ROOT/exports/backup.sqlite}"
mkdir -p "$(dirname "$OUT")"
cd "$ROOT"
MODE="$(memorist_mode "$ROOT")"
if [[ "$MODE" == full ]]; then
  OUT="${1:-$ROOT/exports/memorist-postgres.dump}"
  memorist_compose "$ROOT" "$MODE" exec -T postgres pg_dump -U memorist -d memorist -Fc > "$OUT"
else
  memorist_compose "$ROOT" "$MODE" exec -T memorist-core python -m memcore.reliability backup --out /exports/backup.sqlite
fi
echo "OK backup requested: $OUT"
