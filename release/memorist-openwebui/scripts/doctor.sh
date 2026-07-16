#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/common.sh"
ROOT="$(memorist_root)"
MODE="$(memorist_mode "$ROOT")"
EXPECTED="${1:-$MODE}"
[[ "$EXPECTED" == "$MODE" ]] || { echo "FAIL requested $EXPECTED but installed mode is $MODE" >&2; exit 2; }

memorist_compose "$ROOT" "$MODE" config -q
memorist_compose "$ROOT" "$MODE" exec -T memorist-core python -c \
  "import json,urllib.request; c=json.load(urllib.request.urlopen('http://localhost:8777/memcore/config/effective')); required=('openwebui_adapter','memory_worker','import_system','memory_context_attachment','active_memory_blocks','user_profile_projection','forgetting_pipeline'); assert c['runtime_profile']=='$MODE'; assert all(c['features'][x] for x in required); print('OK Memorist Core effective runtime verified')"

if [[ "$MODE" == full ]]; then
  memorist_compose "$ROOT" "$MODE" exec -T postgres pg_isready -U memorist -d memorist >/dev/null
  memorist_compose "$ROOT" "$MODE" exec -T falkordb redis-cli ping >/dev/null
  memorist_compose "$ROOT" "$MODE" exec -T memorist-core python -c \
    "import json,urllib.request; c=json.load(urllib.request.urlopen('http://localhost:8777/memcore/config/effective')); h=json.load(urllib.request.urlopen('http://localhost:8777/memcore/health')); assert c['canonical_store']=='postgres' and c['postgres_dsn_configured'] and c['graph_backend']=='falkordb' and c['hot_scheduler']=='in_memory' and c['features']['graph_projection']; assert h['graph_status']=='ok' and not h['profile_warnings']; print('OK Full PostgreSQL/FalkorDB runtime verified')"
else
  echo "OK Lite SQLite runtime verified (PostgreSQL/FalkorDB not required)"
fi
