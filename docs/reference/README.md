# Reference documentation

Deep-dive documents for contributors and operators. Start with the canonical
docs — [ARCHITECTURE](../ARCHITECTURE.md), [MEMORY_MACHINE](../MEMORY_MACHINE.md),
[INSTALLATION](../INSTALLATION.md), [DEVELOPMENT](../DEVELOPMENT.md),
[TROUBLESHOOTING](../TROUBLESHOOTING.md) — and drop down here when you need
implementation-level detail.

Current source-of-truth documents live in `docs/` and `docs/reference/`.
`docs/historical/` records earlier baselines and is not runtime authority.
Markdown under source `prompts/` is executable prompt-contract content and
must not be edited as ordinary prose documentation.

## Memory engine

- [memory-engine-architecture.md](memory-engine-architecture.md) — full essay-form architecture of the memory engine
- [core-memory-processing-walkthrough.md](core-memory-processing-walkthrough.md) — one real prompt/response through inlet, recall, model, outlet, worker, persistence, and later retrieval
- [memory-intelligence-core.md](memory-intelligence-core.md) — sentence-level Jakobson layer and data flow
- [text-semantics.md](text-semantics.md) — shared normalization, token boundaries, and claim polarity
- [semantic-analysis-contract.md](semantic-analysis-contract.md) — strict whole-message semantic v1 contract
- [semantic-candidate-authority.md](semantic-candidate-authority.md) — frozen WP02 authority, coverage, and identity interfaces
- [concept-glossary.md](concept-glossary.md) — frozen baseline terminology
- [prompt-pack.md](prompt-pack.md) / [memory-worker-prompts.md](memory-worker-prompts.md) — schema-bound prompt contracts
- [prompt-safety.md](prompt-safety.md) — untrusted-content and injection defenses
- [preflight.md](preflight.md) — pre-send retrieval/attachment step

## Model control

- [model-control-plane.md](model-control-plane.md) — roles, profiles, defaults, testing
- [model-costs.md](model-costs.md) — usage and cost diagnostics
- [memory-control-contract.md](memory-control-contract.md) — authenticated control-plane contract
- [full-mode-memory-extraction.md](full-mode-memory-extraction.md) — Full-mode extraction path

## Storage and runtimes

- [storage-profiles.md](storage-profiles.md) — Lite/Full profile matrix
- [sqlite-runtime.md](sqlite-runtime.md) / [sqlite-heavy-workloads.md](sqlite-heavy-workloads.md) — Lite ledger behavior
- [postgres.md](postgres.md) / [sqlite-to-postgres.md](sqlite-to-postgres.md) — Full ledger and migration
- [falkordb.md](falkordb.md) — graph projection
- [full-mode.md](full-mode.md) — Full mode status and gates
- [hot-scheduler.md](hot-scheduler.md) — in-memory scheduling

## Data lifecycle and operations

- [import.md](import.md) / [heavy-import.md](heavy-import.md) — provider archive import
- [heritage-roundtrip.md](heritage-roundtrip.md) — portable export/verify/restore
- [backup-restore.md](backup-restore.md) — safe backup practices
- [forget-residue.md](forget-residue.md) — forgetting without residue
- [consistency-checker.md](consistency-checker.md) / [diagnostics.md](diagnostics.md) — health and audit tooling
- [openwebui-compatibility.md](openwebui-compatibility.md) — pinned Open WebUI target
