#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python "$ROOT/scripts/clean_artifacts.py" "$@"
