#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"

pushd memorist-core >/dev/null
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
git diff --check
uv run pytest -q --tb=short \
  --junitxml="${RUNNER_TEMP}/core-unit-results.xml"
uv run pytest ../open-webui-integration/memorist/tests -q --tb=short \
  --junitxml="${RUNNER_TEMP}/openwebui-integration-results.xml"
popd >/dev/null

npm test -- --reporter=junit --outputFile="${RUNNER_TEMP}/frontend-results.xml"
npm run typecheck
npm run lint

for file in \
  README.md LICENSE SECURITY.md CONTRIBUTING.md RELEASE_NOTES.md \
  docs/ARCHITECTURE.md docs/INSTALLATION.md docs/MEMORY_MACHINE.md \
  docs/DEVELOPMENT.md docs/TROUBLESHOOTING.md docs/reference/README.md; do
  test -f "$file" || { echo "MISSING $file"; exit 1; }
done

python3 scripts/check_doc_links.py

bad=$(git ls-files | grep -E '(^|/)\.env$|(^|/)\.env\.[^e]|\.zip$|\.sqlite$|\.db$|\.log$|\.tmp$' || true)
test -z "$bad" || { echo "FORBIDDEN tracked files:"; echo "$bad"; exit 1; }

bad=$(git grep -lIE \
  "claude\.ai/code/session|Generated with \[Claude Code\]|Co-Authored-By: Claude" \
  -- . ':!.github/workflows/public-release-readiness.yml' || true)
test -z "$bad" || { echo "Agent artifacts found in:"; echo "$bad"; exit 1; }

bad=$(git grep -nIE \
  "sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|BEGIN (RSA |EC )?PRIVATE KEY" \
  -- . | grep -vF 'sk-memorist-e2e-canary-000000000000' || true)
test -z "$bad" || { echo "Secret-like content found:"; echo "$bad"; exit 1; }
