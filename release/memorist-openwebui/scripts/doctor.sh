#!/usr/bin/env bash
set -euo pipefail
MODE="${1:-lite}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
status() { printf "%s %s\n" "$1" "$2"; }
command -v docker >/dev/null 2>&1 && status OK "Docker available" || status FAIL "Docker unavailable"
docker compose version >/dev/null 2>&1 && status OK "Compose available" || status FAIL "Compose unavailable"
for dir in data objects imports exports; do mkdir -p "$dir" && [[ -w "$dir" ]] && status OK "$dir writable" || status FAIL "$dir not writable"; done
python - <<'PY'
import socket
for port in (8777, 3000):
    s=socket.socket();
    try:
        s.bind(('127.0.0.1', port)); print(f'OK port {port} free')
    except OSError:
        print(f'WARN port {port} already in use')
    finally:
        s.close()
PY
python - <<'PY'
import json, urllib.request
checks=[('Memorist Core','http://localhost:8777/memcore/health'),('Open WebUI','http://localhost:3000/health')]
for name,url in checks:
    try:
        print('OK', name, urllib.request.urlopen(url, timeout=2).status)
    except Exception as exc:
        print('WARN', name, 'not reachable')
try:
    data=json.load(urllib.request.urlopen('http://localhost:8777/memcore/config/effective', timeout=2))
    print('OK local_only', data.get('local_only'))
    print('OK schema reachable')
except Exception:
    print('WARN Memorist config unavailable')
PY
if [[ "$MODE" == "full" ]]; then
  docker compose -f compose.yml exec -T falkordb redis-cli ping >/dev/null 2>&1 && status OK "FalkorDB healthy" || status WARN "FalkorDB not reachable"
else
  status OK "Graph disabled in Lite"
fi
