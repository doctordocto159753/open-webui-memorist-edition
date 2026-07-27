#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${MEMORIST_E2E_STUB_KEY:?MEMORIST_E2E_STUB_KEY is required}"

package_dir=""
stub_name="memorist-e2e-stub-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
project_name="memorist_ci_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}"
network_name="${project_name}_default"
provider_url="http://${stub_name}:9800/v1"
diagnostics="${RUNNER_TEMP}/product-diagnostics"
mkdir -p "$diagnostics" "${RUNNER_TEMP}/stub-logs"

compose_base() {
  if [[ -n "${MEMORIST_COMPOSE_BIN:-}" ]]; then
    printf '%s\n' "$MEMORIST_COMPOSE_BIN"
  else
    printf '%s\n' "docker compose"
  fi
}

collect_and_stop() {
  status=$?
  trap - EXIT
  set +e

  cp "${RUNNER_TEMP}/docker-build.log" "$diagnostics/" 2>/dev/null
  cp "${RUNNER_TEMP}/stub-logs/requests.jsonl" "$diagnostics/" 2>/dev/null
  docker logs "$stub_name" > "$diagnostics/stub-server.log" 2>&1

  if [[ -n "$package_dir" && -d "$package_dir" ]]; then
    pushd "$package_dir" >/dev/null || true
    if [[ -n "${MEMORIST_COMPOSE_BIN:-}" ]]; then
      "$MEMORIST_COMPOSE_BIN" -f compose.yml -f compose.full.yml logs --no-color 2>/dev/null
    else
      docker compose -f compose.yml -f compose.full.yml logs --no-color 2>/dev/null
    fi | sed \
      -e "s/${MEMORIST_E2E_STUB_KEY}/[redacted-canary]/g" \
      -e 's/\(SECRET\|TOKEN\|PASSWORD\|API_KEY\)=[^ ]*/\1=[redacted]/g' \
      > "$diagnostics/compose.log"

    pwsh -NoProfile -File ./Stop-Memorist.ps1 >/dev/null 2>&1
    popd >/dev/null || true
  fi

  docker rm -f "$stub_name" >/dev/null 2>&1
  exit "$status"
}
trap collect_and_stop EXIT

mapfile -t archives < <(
  find release/rc -maxdepth 1 -type f -name 'memorist-openwebui-*.zip' | sort
)
test "${#archives[@]}" -eq 1

target="${RUNNER_TEMP}/Memorist Final Test"
rm -rf "$target"
mkdir -p "$target"
python3 -c \
  "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
  "${archives[0]}" "$target"
package_dir=$(find "$target" -maxdepth 1 -mindepth 1 -type d | head -1)
test -n "$package_dir"

# Build once. The .env written below points Compose at this exact local image,
# so Start-Memorist does not rebuild it.
docker build \
  -t memorist/openwebui:0.2.0-beta.3-ci \
  -f "${package_dir}/runtime/openwebui-image/Dockerfile" \
  "${package_dir}/runtime" \
  2>&1 | tee "${RUNNER_TEMP}/docker-build.log"

docker run -d --name "$stub_name" \
  -p 127.0.0.1:9800:9800 \
  -e PORT=9800 \
  -e REQUEST_LOG_PATH=/logs/requests.jsonl \
  --mount "type=bind,src=${GITHUB_WORKSPACE}/tests/e2e/model-stub/server.py,dst=/stub/server.py,readonly" \
  --mount "type=bind,src=${RUNNER_TEMP}/stub-logs,dst=/logs" \
  --entrypoint python \
  memorist/openwebui:0.2.0-beta.3-ci \
  /stub/server.py

for _ in $(seq 1 30); do
  if curl -sf http://localhost:9800/health >/dev/null; then
    break
  fi
  sleep 1
done
curl -sf http://localhost:9800/health >/dev/null

pg_password=$(openssl rand -hex 16)
{
  printf 'COMPOSE_PROJECT_NAME=%s\n' "$project_name"
  printf 'MEMORIST_OPENWEBUI_IMAGE=memorist/openwebui:0.2.0-beta.3-ci\n'
  printf 'MEMORIST_MODE=full\n'
  printf 'MEMORIST_INSTALLATION_ID=%s\n' "$(python3 -c 'import uuid;print(uuid.uuid4())')"
  printf 'WEBUI_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
  printf 'MEMORIST_ACTOR_ASSERTION_SECRET=%s\n' "$(openssl rand -hex 32)"
  printf 'MEMORIST_ACTOR_SERVICE_TOKEN=%s\n' "$(openssl rand -hex 32)"
  printf 'MEMORIST_OPENWEBUI_WORKSPACE_UUID=%s\n' "$(python3 -c 'import uuid;print(uuid.uuid4())')"
  printf 'MEMORIST_POSTGRES_PASSWORD=%s\n' "$pg_password"
  printf 'MEMORIST_POSTGRES_DSN=postgresql://memorist:%s@postgres:5432/memorist\n' "$pg_password"
  printf 'OPENAI_API_BASE_URL=%s\n' "$provider_url"
  printf 'OPENAI_API_KEY=%s\n' "$MEMORIST_E2E_STUB_KEY"
  printf 'ENABLE_OLLAMA_API=false\n'
  printf 'MEMORIST_MEMORY_EXTRACTION_API_KEY=stub-extraction-env-value\n'
  printf 'OPEN_WEBUI_PORT=3000\n'
  printf 'MEMORIST_CORE_HOST_PORT=8777\n'
  printf 'MEMORIST_PORT=8777\n'
} > "${package_dir}/.env"

pushd "$package_dir" >/dev/null
pwsh -NoProfile -File ./Start-Memorist.ps1 -NoBrowser
popd >/dev/null

for _ in $(seq 1 30); do
  if docker network inspect "$network_name" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker network connect --alias "$stub_name" "$network_name" "$stub_name"

pushd "$package_dir" >/dev/null
if [[ -n "${MEMORIST_COMPOSE_BIN:-}" ]]; then
  compose=("$MEMORIST_COMPOSE_BIN" -f compose.yml -f compose.full.yml)
else
  compose=(docker compose -f compose.yml -f compose.full.yml)
fi

"${compose[@]}" exec -T open-webui python -c \
  "import json,urllib.request; data=json.load(urllib.request.urlopen('${provider_url}/models', timeout=5)); assert data['data'][0]['id']=='memorist-e2e-stub'"
"${compose[@]}" exec -T memorist-core python -c \
  "import json,urllib.request; data=json.load(urllib.request.urlopen('${provider_url}/models', timeout=5)); assert data['data'][0]['id']=='memorist-e2e-stub'"
popd >/dev/null

curl -sf http://localhost:3000/health >/dev/null
code=$(curl -s -o /tmp/memorist-status.json -w "%{http_code}" \
  -H "Accept: application/json" \
  http://localhost:3000/api/v1/memorist/openwebui/status)
test "$code" = "401"
python3 -c "import json; d=json.load(open('/tmp/memorist-status.json')); assert 'detail' in d"
! grep -qi '<html' /tmp/memorist-status.json
curl -sf http://localhost:8777/memcore/health | python3 -m json.tool

npm ci --no-audit --no-fund
npm install --no-save @playwright/test@1.49.1
docker pull mcr.microsoft.com/playwright:v1.49.1-noble

docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e MEMORIST_E2E_BASE_URL=http://localhost:3000 \
  -e MEMORIST_E2E_STUB_URL=http://localhost:9800 \
  -e MEMORIST_E2E_PROVIDER_URL="$provider_url" \
  -v "$GITHUB_WORKSPACE":/work -w /work \
  mcr.microsoft.com/playwright:v1.49.1-noble \
  npx playwright test --config tests/e2e/playwright.config.ts tests/e2e/product.spec.ts

pushd "$package_dir" >/dev/null
captured=$("${compose[@]}" exec -T postgres psql -U memorist -d memorist -tA -c \
  "SELECT count(*) FROM openwebui_message_captures WHERE role='user' AND message_uuid IN (SELECT message_uuid FROM messages WHERE raw_text LIKE '%Alpha%')")
test "$captured" = "1"
off=$("${compose[@]}" exec -T postgres psql -U memorist -d memorist -tA -c \
  "SELECT count(*) FROM messages WHERE raw_text LIKE '%Umbra%'")
test "$off" = "0"
candidates=$("${compose[@]}" exec -T postgres psql -U memorist -d memorist -tA -c \
  "SELECT count(*) FROM memory_candidates")
test "$candidates" != "0"
secrets=$("${compose[@]}" exec -T postgres psql -U memorist -d memorist -tA -c \
  "SELECT count(*) FROM model_profiles WHERE model_profiles::text LIKE '%${MEMORIST_E2E_STUB_KEY}%'")
test "$secrets" = "0"
popd >/dev/null

# Restart the same deployment. This tests persistence but does not rebuild,
# re-download, or create a second stack.
docker network disconnect "$network_name" "$stub_name"
pushd "$package_dir" >/dev/null
pwsh -NoProfile -File ./Stop-Memorist.ps1
pwsh -NoProfile -File ./Start-Memorist.ps1 -NoBrowser
popd >/dev/null
docker network connect --alias "$stub_name" "$network_name" "$stub_name"

docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e MEMORIST_E2E_BASE_URL=http://localhost:3000 \
  -e MEMORIST_E2E_STUB_URL=http://localhost:9800 \
  -e MEMORIST_E2E_PROVIDER_URL="$provider_url" \
  -v "$GITHUB_WORKSPACE":/work -w /work \
  mcr.microsoft.com/playwright:v1.49.1-noble \
  npx playwright test --config tests/e2e/playwright.config.ts tests/e2e/post-restart.spec.ts

pushd "$package_dir" >/dev/null
captured=$("${compose[@]}" exec -T postgres psql -U memorist -d memorist -tA -c \
  "SELECT count(*) FROM openwebui_message_captures WHERE role='user' AND message_uuid IN (SELECT message_uuid FROM messages WHERE raw_text LIKE '%Alpha%')")
test "$captured" = "1"
popd >/dev/null
