#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"
cp -n .env.example .env 2>/dev/null || true
mkdir -p data objects imports exports
docker compose --profile lite -f compose.yml up -d --build memorist-core open-webui
echo "Memorist Core: http://localhost:${MEMORIST_PORT:-8777}/memcore/health"
echo "Open WebUI: http://localhost:${OPEN_WEBUI_PORT:-3000}"
"$SCRIPT_DIR/doctor.sh" lite || true
