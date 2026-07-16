#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/common.sh"
ROOT="$(memorist_root)"
MODE="$(memorist_mode "$ROOT")"
memorist_compose "$ROOT" "$MODE" logs -f --tail=200 "$@"
