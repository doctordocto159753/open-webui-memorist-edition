# Handoff — v0.2.0-beta.1 Development Baseline

This repository is a v0.2.0-beta.1 development baseline, not a certified public
beta release.

## Current Status

- Version: `0.2.0-beta.1`
- Storage schema version: `18`
- Lite Mode: beta-candidate local path
- Full Mode: experimental preview
- Open WebUI integration: contract-tested; pinned container smoke pending/manual
- Memory Intelligence Core / Jakobson Pipeline: implemented baseline, needs
  real-world evaluation
- Model Control Plane: implemented backend/runtime baseline, UI polish pending
- Prompt Pack v2: implemented contract baseline, semantic evaluation pending

## Implemented Baseline

- SQLite Lite runtime with local object storage.
- Sentence-level Jakobson memory intelligence and signal routing.
- Model role/profile/default/usage/privacy backend runtime.
- Schema-bound Memory Worker Prompt Pack v2 and prompt execution audit metadata.
- Open WebUI fail-open Filter/Function contract tests.
- Heavy import ci-small, Heritage roundtrip, forget residue, consistency, and
  recovery gates.

## Experimental Or Manual

- PostgreSQL canonical Full Mode path.
- FalkorDB projection and graph retrieval/residue evidence.
- Pinned Open WebUI real container smoke.
- Production semantic evaluation for Prompt Pack v2/Jakobson routing.

## Commands Before Pushing

```bash
python scripts/clean_artifacts.py --check
python -B scripts/scan_source_tree.py
python scripts/baseline_check.py
```

If GNU Make is available, also run:

```bash
make check
make model-control-tests
make memory-worker-prompt-pack-test
make openwebui-contract-tests
make smoke-daily
make smoke-import-heavy-ci
make heritage-roundtrip
make forget-residue
make consistency-check
make recovery-tests
make source-package
make source-tree-scan
make assemble-rc
make rc-schema-test
make version-consistency
```

## Rebuild Packages

```bash
python release/source_package.py --out release/source/open-webui-memorist-edition-source.zip
python installer/scripts/assemble_rc.py
python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.2.0-beta.1.zip
python -m release.scan_source_tree release/source/open-webui-memorist-edition-source.zip
cd memorist-core
python -m uv run python ../release/tests/rc_package_schema.py
python -m uv run python ../release/tests/version_consistency.py
```

Generated package archives are reproducible release artifacts and should not be
committed unless a maintainer intentionally changes release-artifact policy.

## Initial Commit Recommendation

```bash
git status --short
git add .
git status --short
git commit -m "chore: establish v0.2.0-beta.1 development baseline"
```

Before committing, verify staged files exclude `.env`, runtime DBs, caches,
virtual environments, `release/source/*.zip`, `release/source/*.sha256`,
`release/rc/*.zip`, and `release/rc/*.sha256`.

## Reference Documents

- `GITHUB_BASELINE.md`
- `RELEASE_DECISION.md`
- `KNOWN_LIMITATIONS.md`
- `release/rc/HANDOFF.md`
