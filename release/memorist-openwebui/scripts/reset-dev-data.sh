#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "--yes-i-understand" ]]; then
  echo "FAIL reset-dev-data is destructive. Re-run with --yes-i-understand."
  exit 2
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose -f compose.yml down -v
rm -rf data objects imports exports
echo "OK dev data reset"
