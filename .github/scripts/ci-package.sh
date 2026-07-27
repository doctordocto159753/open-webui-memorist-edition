#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

python3 installer/scripts/validate_installer.py --dry-run
python3 installer/scripts/validate_compose.py
python3 release/memorist-openwebui/scripts/gen_checksums.py --check
find release/memorist-openwebui/scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n

# The product job builds the derivative image and production frontend exactly
# once from this package. Rebuilding the same frontend here would duplicate the
# largest download/install/build path without increasing isolation.
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
