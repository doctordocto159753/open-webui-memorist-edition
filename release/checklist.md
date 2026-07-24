# Release Checklist

A release candidate is not ready until every gate below is complete.

## Automated Gates

- Reproducible uv-managed core checks pass: `make check`
- Model Control Plane tests pass: `make model-control-tests`
- Memory Worker prompt-pack contract tests pass: `make memory-worker-prompt-pack-test`
- Version/schema/package consistency passes: `make version-consistency`
- Clean source package builds and scans: `make source-package source-tree-scan`
- Baseline audit/check reports are refreshed: `make baseline-check`
- Open WebUI integration contract tests pass: `make openwebui-contract-tests`
- Daily local product smoke passes: `make smoke-daily`
- CI-sized heavy import gate passes: `make smoke-import-heavy-ci`
- Rich Heritage roundtrip gate passes: `make heritage-roundtrip`
- Multi-layer forget residue gate passes: `make forget-residue`
- Consistency and recovery gates pass: `make consistency-check` and `make recovery-tests`
- RC package is assembled: `make assemble-rc`
- RC ZIP schema regression passes: `make rc-schema-test`
- Release package forbidden-file scan passes: `python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.2.0-beta.3.zip`
- Release gate report is generated with placeholder smoke scripts marked not-counted: `python -m release.tests.report --manifest release/test_manifest.ijson --external-gates-passed`
- Package manifest exists: `release/package-manifest.ijson`
- Docker build context excludes local DBs, env files, caches and VCS metadata via `.dockerignore`.
- Unit tests pass through `make check`; do not use ambient Python.
- Lint passes through `make check`.
- Typecheck passes through `make check`.
- Security fixtures pass: `make test-security`
- Eval baseline passes: `cd memorist-core && uv run python -m memcore.eval run --dataset src/memcore/eval/fixtures/basic.ijsonl`
- Performance smoke passes: `make perf-smoke`
- Consistency checker passes: `cd memorist-core && uv run python -m memcore.reliability check`
- Import round-trip tests pass in pytest.
- Heritage verify/restore tests pass in pytest.
- Optional pinned Open WebUI container smoke is either run explicitly or documented as skipped: `make openwebui-container-smoke`

## Manual Smoke

Placeholder smoke scripts under `release/tests/smoke_*.py` are not release evidence unless they are reclassified as `real` in `release/test_manifest.ijson`.

- Start Lite package.
- Verify health endpoint.
- Open Open WebUI.
- If container smoke is needed, use the pinned target `ghcr.io/open-webui/open-webui:v0.9.6`.
- Install and enable Memorist filter/function from trusted local files.
- Create a session and send a message.
- Confirm memory capture event appears.
- Create memory through the test pipeline.
- Confirm preflight attachment appears.
- Import a small ChatGPT or Open WebUI fixture.
- Export a Heritage package.
- Restore Heritage into a clean DB.
- Run a forget request.
- Verify forgotten content does not appear.
- Shut down and restart.
- Confirm data persists.
