# Memorist OpenWebUI v0.2.0-beta.1 Development Baseline Notes

This baseline packages Memorist Core with a local Open WebUI Filter/Function integration, a supported SQLite Lite compose path, an experimental Full Mode preview, local diagnostics, import/export support, Heritage verification, Model Control Plane runtime integration, sentence-level Jakobson Memory Intelligence Core, and Memory Worker Prompt Pack v2.

This is not Public Beta GO. It is a GitHub development baseline intended for independent audit and continued development.

Current status:

- Lite Mode is the beta-candidate local path.
- Full Mode is an experimental PostgreSQL/FalkorDB preview until real external Full gates pass.
- Memory Intelligence Core / sentence-level Jakobson pipeline is implemented as the current semantic routing baseline.
- Model Control Plane backend/runtime baseline is implemented; UI/product polish remains future work.
- Prompt Pack v2 is implemented as the non-chat prompt contract and audit baseline.
- Open WebUI integration is contract-tested with fixtures; the pinned real container smoke remains manual/pending.

Included hardening:

- explicit FastAPI TestClient dependency and reproducible `make check`;
- durable Open WebUI session aliases for stable IDs, temporary IDs, client nonce, and first-message fingerprints;
- atomic local job claiming, SQLite WAL/busy-timeout/busy-retry hardening, and SQLite write actor diagnostics;
- import progress, pause/resume/cancel, bounded commit, backpressure, and heavy import CI smoke;
- model-aware Memory Context Attachment budget calculation and preflight fail-open behavior;
- local Model Control roles, defaults, usage, cost, health, privacy acknowledgement, and provider profiles;
- sentence-level Jakobson annotation tables, memory signal routes, and candidate evidence lineage;
- Prompt Pack v2 registry, schemas, validators, role mapping, prompt execution audit, and specialized extractor prompts;
- Heritage roundtrip, forget residue, consistency, recovery, source package, RC schema, and forbidden-file scans.

Generated packages under `release/source/` and `release/rc/` are reproducible build outputs. They should be rebuilt before publishing and are not evidence by themselves; the release reports and checks determine readiness.
