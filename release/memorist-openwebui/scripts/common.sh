#!/usr/bin/env bash
set -euo pipefail

memorist_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

memorist_mode() {
  local root="$1" value
  [[ -f "$root/.env" ]] || { echo "FAIL: no .env; run the installer first" >&2; return 1; }
  value="$(sed -n 's/^MEMORIST_MODE=//p' "$root/.env" | tail -n 1)"
  [[ "$value" == lite || "$value" == full ]] || {
    echo "FAIL: MEMORIST_MODE must be lite or full" >&2; return 1;
  }
  printf '%s' "$value"
}

memorist_compose() {
  local root="$1" mode="$2"; shift 2
  docker compose -f "$root/compose.yml" -f "$root/compose.${mode}.yml" "$@"
}
