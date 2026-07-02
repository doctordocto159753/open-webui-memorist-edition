# Release Decision

Current decision: CONDITIONAL GO FOR GITHUB DEVELOPMENT BASELINE

Recommended label: `v0.2.0-beta.1 development baseline`.

This baseline can be uploaded for independent tester/code-review audit when required local gates pass. It is not Public Beta GO because Full Mode external certification and pinned Open WebUI container smoke are still manual/pending.

Required local gates:

- `make check`
- `make model-control-tests`
- `make memory-worker-prompt-pack-test`
- `make openwebui-contract-tests`
- `make smoke-daily`
- `make smoke-import-heavy-ci`
- `make heritage-roundtrip`
- `make forget-residue`
- `make consistency-check`
- `make recovery-tests`
- `make source-package`
- `make source-tree-scan`
- `make assemble-rc`
- `make rc-schema-test`
- `make version-consistency`
- `make baseline-check`

Manual or non-certifying gates:

- Full external PostgreSQL/FalkorDB smoke.
- Pinned Open WebUI real container smoke.
- Placeholder smoke scripts classified as placeholders in `release/test_manifest.ijson`.

No-GO triggers:

- Makefile target points to a missing script.
- Source tree scan fails.
- README, release notes, and version metadata contradict each other.
- RC package cannot be generated or schema check fails.
- Current docs present Full Mode as stable.
- Current docs present Prompt Pack v2, Jakobson, or Model Control as merely planned.
- Package contains local DBs, caches, `.env` files, bytecode, or secret-like content.
