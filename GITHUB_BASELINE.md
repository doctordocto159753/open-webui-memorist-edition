# GitHub Development Baseline

Status: **CONDITIONAL GO for independent audit and continued GitHub development**.

- Version: `0.2.0-beta.1`
- Schema version: `18`
- Recommended label: `v0.2.0-beta.1 development baseline`
- Lite Mode: beta-candidate local path
- Full Mode: experimental preview; external certification incomplete
- Open WebUI integration: contract-tested; pinned real container smoke remains manual/pending

## Implemented

- SQLite Lite local runtime and write actor hardening
- Memory Intelligence Core with sentence-level Jakobson analysis and signal routing
- Model Control Plane backend/runtime baseline
- Memory Worker Prompt Pack v2 with schemas, validators, role mappings, and prompt execution audit
- Import dry-run/commit hardening and heavy-import CI smoke
- Heritage export/verify/restore roundtrip
- Forget residue, consistency, and recovery gates
- Open WebUI Filter/Function contract tests

## Experimental

- PostgreSQL canonical Full Mode path
- FalkorDB graph projection, graph diagnostics, and rebuild command
- Full compose/runtime external smoke scripts
- Graph retrieval and graph forget-residue certification scripts

## Not Yet Certified

- Public Beta readiness
- Full Mode production support
- Pinned Open WebUI real container smoke
- Long-running real-world semantic quality evaluation
- Full Mode beta support unless all gates in
  `release/artifacts/full-mode-certification-report.ijson` pass

## Commands

```bash
python scripts/clean_artifacts.py --check
python scripts/baseline_check.py
python scripts/full_mode_check.py
make check
make source-package
make assemble-rc
make rc-schema-test
make version-consistency
```

If `make` is unavailable on Windows, run the equivalent Python/uv commands shown in `Makefile`.

## Package Outputs

Generated outputs are reproducible and should be rebuilt before publishing:

- source package: `release/source/open-webui-memorist-edition-source.zip`
- RC package: `release/rc/memorist-openwebui-0.2.0-beta.1.zip`
- RC checksum: `release/rc/memorist-openwebui-0.2.0-beta.1.sha256`

## Do Not Commit

- `.env` files with secrets
- SQLite DBs, WAL/SHM files, logs, imports, exports, runtime data
- `.venv`, caches, bytecode, coverage output
- generated source/RC packages unless intentionally attached as release artifacts

## Next Priorities

1. Run independent audit on the clean GitHub baseline.
2. Run Full Mode PostgreSQL/FalkorDB/compose gates in a Docker-capable or
   DSN-configured environment and attach the certification report.
3. Add pinned Open WebUI container smoke automation.
4. Expand semantic evaluation for Prompt Pack v2 and Jakobson routing.
