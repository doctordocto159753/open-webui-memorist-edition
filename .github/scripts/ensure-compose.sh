#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--require-docker" ]]; then
  docker info >/dev/null
fi

if docker compose version >/dev/null 2>&1; then
  docker compose version
  exit 0
fi

if command -v docker-compose >/dev/null 2>&1; then
  docker-compose version
  echo "MEMORIST_COMPOSE_BIN=$(command -v docker-compose)" >> "$GITHUB_ENV"
  exit 0
fi

version="v2.29.7"
expected_sha256="383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0"
destination="${RUNNER_TEMP:-/tmp}/memorist-compose-${version}"
url="https://github.com/docker/compose/releases/download/${version}/docker-compose-linux-x86_64"
curl --fail --silent --show-error --location "$url" --output "$destination"
echo "${expected_sha256}  ${destination}" | sha256sum --check --strict
chmod +x "$destination"
"$destination" version
echo "MEMORIST_COMPOSE_BIN=$destination" >> "$GITHUB_ENV"
echo "$(dirname "$destination")" >> "$GITHUB_PATH"
