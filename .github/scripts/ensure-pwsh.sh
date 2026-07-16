#!/usr/bin/env bash
set -euo pipefail

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
  exit 0
fi

version="7.4.6"
expected_sha256="6f6015203c47806c5cc444c19d8ed019695e610fbd948154264bf9ca8e157561"
destination="${RUNNER_TEMP:-/tmp}/memorist-powershell-${version}"
archive="${RUNNER_TEMP:-/tmp}/powershell-${version}-linux-x64.tar.gz"
url="https://github.com/PowerShell/PowerShell/releases/download/v${version}/powershell-${version}-linux-x64.tar.gz"
mkdir -p "$destination"
curl --fail --silent --show-error --location "$url" --output "$archive"
echo "${expected_sha256}  ${archive}" | sha256sum --check --strict
tar -xzf "$archive" -C "$destination"
chmod +x "$destination/pwsh"
"$destination/pwsh" -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
echo "$destination" >> "$GITHUB_PATH"
