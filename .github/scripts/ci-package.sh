#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

python3 installer/scripts/validate_installer.py --dry-run
python3 installer/scripts/validate_compose.py
python3 release/memorist-openwebui/scripts/gen_checksums.py --check
find release/memorist-openwebui/scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n

url=$(python3 -c "import json;print(json.load(open('release/openwebui-image/source-pin.json'))['source_snapshot']['url'])")
sha=$(python3 -c "import json;print(json.load(open('release/openwebui-image/source-pin.json'))['source_snapshot']['sha256'])")
curl -sSL --retry 4 -o "${RUNNER_TEMP}/openwebui-src.tar.gz" "$url"
echo "$sha  ${RUNNER_TEMP}/openwebui-src.tar.gz" | sha256sum -c -

sh release/openwebui-image/prepare_frontend_tree.sh \
  "${RUNNER_TEMP}/openwebui-src.tar.gz" \
  "${RUNNER_TEMP}/src-tree" \
  "$(pwd)/release/openwebui-image" \
  "$(pwd)/open-webui-integration/memorist/ui"

pushd "${RUNNER_TEMP}/src-tree" >/dev/null
npm ci --force --fetch-retries=5
npm run build
popd >/dev/null

build="${RUNNER_TEMP}/src-tree/build"
test -f "$build/index.html"
for marker in \
  "Memory Setup" "Processing Nodes" "Memory used" \
  "memorist-memory-workflow-toggle" "memorist-message-disclosure" \
  "memorist-diagnostics" "/api/v1/memorist"; do
  grep -rl --include='*.js' -- "$marker" "$build/_app" >/dev/null \
    || { echo "MISSING BUNDLE MARKER: $marker"; exit 1; }
done

# A hosted runner starts from a clean checkout. Remove only generated archive
# outputs, never the tracked SBOM and release inputs under release/rc.
rm -f release/rc/memorist-openwebui-*.zip release/rc/memorist-openwebui-*.sha256
python3 installer/scripts/assemble_rc.py

mapfile -t archives < <(
  find release/rc -maxdepth 1 -type f -name 'memorist-openwebui-*.zip' | sort
)
test "${#archives[@]}" -eq 1

python3 installer/scripts/validate_package.py "${archives[0]}"
python3 release/tests/rc_package_schema.py > "${RUNNER_TEMP}/rc-package-schema.json"
python3 release/tests/version_consistency.py > "${RUNNER_TEMP}/version-consistency.json"
python3 release/tests/upgrade_contract.py > "${RUNNER_TEMP}/upgrade-contract.json"
sha256sum "${archives[0]}" > "${RUNNER_TEMP}/zip-digest.txt"
