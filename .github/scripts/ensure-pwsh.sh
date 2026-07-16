#!/usr/bin/env bash
set -euo pipefail

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
  exit 0
fi

version="7.4.6"
destination="${RUNNER_TEMP:-/tmp}/memorist-powershell-${version}"
archive="${RUNNER_TEMP:-/tmp}/powershell-${version}-linux-x64.tar.gz"
url="https://github.com/PowerShell/PowerShell/releases/download/v${version}/powershell-${version}-linux-x64.tar.gz"
mkdir -p "$destination"
curl --fail --silent --show-error --location "$url" --output "$archive"
tar -xzf "$archive" -C "$destination"
chmod +x "$destination/pwsh"
"$destination/pwsh" -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
echo "$destination" >> "$GITHUB_PATH"
