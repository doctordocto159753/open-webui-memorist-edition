#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/common.sh"
cd "$ROOT"
MODE="$(memorist_mode "$ROOT")"
[[ "$MODE" == full ]] || { echo "FAIL: installed mode is $MODE, not full" >&2; exit 2; }
mkdir -p data objects imports exports
echo "Full mode starts PostgreSQL, FalkorDB, Memorist Core, and Open WebUI."
memorist_compose "$ROOT" "$MODE" up -d --build --remove-orphans
echo "Memorist Core: http://localhost:${MEMORIST_PORT:-8777}/memcore/health"
echo "Open WebUI: http://localhost:${OPEN_WEBUI_PORT:-3000}"
"$SCRIPT_DIR/doctor.sh" full
