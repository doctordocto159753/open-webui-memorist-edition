# Historical: Final Four Implementation Plan

This document is retained for audit history. It described the pre-implementation plan before Steps 1-4 were added to the development baseline. Current status is documented in `README.md`, `docs/architecture.md`, `docs/model-control-plane.md`, and `docs/prompt-pack.md`.

Do not start Public Beta Readiness until all four steps below are implemented, tested, and reflected in the release gate matrix.

## Step 1 — Memory Intelligence Core / Sentence-Level Jakobson Pipeline

- Status: implemented in the Step 1 sprint.
- Goal: make sentence-level Jakobson analysis the primary memory intelligence stage.
- Scope: deterministic sentence segmentation, sentence offset tables, `memorist.jakobson_sentence_analysis`, six-function schema, signal routes, extractor hooks, candidate lineage, annotation outbox.
- Non-goals: PostgreSQL Full Mode, model runtime rewrite, Prompt Pack v2 formalization beyond minimal Jakobson prompt definition.
- Files likely touched: `memcore/memory_worker/`, `memcore/models/`, `memcore/repositories/`, `memcore/api/routes_memory.py`, `docs/phase-2-memory-worker.md`.
- New migrations expected: `jakobson_analysis_runs`, `jakobson_sentence_annotations`, `memory_signal_routes`, candidate lineage columns.
- Tests expected: sentence offsets, six-function validation, route selection, evidence lineage, legacy `unit_analysis` compatibility.
- Dependencies: current SQLite Lite baseline and Prompt Pack v1 registry.
- Rollback strategy: keep old text-unit pipeline behind compatibility path until Step 1 gates pass.
- Acceptance gate: all existing gates plus new Jakobson sentence pipeline tests.

## Step 2 — Full Mode Storage/Core Runtime: PostgreSQL + FalkorDB + Hot Scheduler

- Goal: add a real Full Mode with PostgreSQL canonical store and FalkorDB projection.
- Scope: runtime profile split, `CanonicalStore` abstraction, PostgreSQL migrations, durable outbox/jobs, hot scheduler, FalkorDB projection/retrieval, Lite-to-Full migration, Full compose smoke.
- Non-goals: replacing Lite mode, weakening SQLite local baseline, changing Open WebUI ownership.
- Files likely touched: `memcore/storage/`, `memcore/repositories/`, `memcore/jobs/`, `docker-compose.full.yml`, `docs/deployment-guide.md`.
- New migrations expected: PostgreSQL migration set parallel to SQLite canonical schema.
- Tests expected: canonical store contract tests, Full compose smoke, graph projection, graph forget residue, Lite-to-Full migration.
- Dependencies: Step 1 schema decisions should be stable.
- Rollback strategy: keep Lite mode default and isolate Full Mode behind explicit profile flag.
- Acceptance gate: Lite gates unchanged plus Full compose/storage/projection smoke.

## Step 3 — Model Control Plane Runtime Integration

- Goal: deepen the current model control scaffold into runtime execution control for Memorist roles.
- Scope: provider adapters, role-to-profile resolution, preflight timeout/fail-open enforcement, extraction lifecycle, embedding re-index, usage/cost/privacy events, UI contract.
- Non-goals: controlling Open WebUI main chat model, storing raw secrets, bypassing privacy acknowledgement.
- Files likely touched: `memcore/model_control/`, `memcore/memory_worker/`, `memcore/attachments/`, `open-webui-integration/memorist/ui/`.
- New migrations expected: provider runtime capabilities, invocation lifecycle, richer usage/cost events if needed.
- Tests expected: provider resolution, local/remote privacy acknowledgement, timeout behavior, usage recording, embedding stale/re-index.
- Dependencies: Step 1 prompt needs and Step 2 runtime profile constraints.
- Rollback strategy: deterministic local providers remain default; remote/non-local roles stay disabled unless acknowledged.
- Acceptance gate: existing model-control tests plus runtime invocation tests.

## Step 4 — Memory Worker Prompt Pack v2

- Goal: formalize the full v2 prompt registry after Step 1 and Step 3 runtime semantics are known.
- Scope: official Jakobson prompt, route-specific extractors, consolidation, preflight, block compaction, import reconstruction, privacy sensitivity, role-to-prompt mapping, schema validation, prompt execution linkage.
- Non-goals: introducing new memory architecture outside Step 1 schema, executing prompts without model control safety.
- Files likely touched: `memcore/memory_worker/prompts/`, `memcore/model_control/`, `memcore/validators/`, `docs/phase-2-memory-worker.md`.
- New migrations expected: prompt pack v2 metadata and execution linkage if v1 tables are insufficient.
- Tests expected: v2 registry completeness, output schema validation, injection resistance, evidence requirements, model role mapping.
- Dependencies: Step 1 Jakobson schemas and Step 3 runtime model control.
- Rollback strategy: keep Prompt Pack v1 registry available as legacy baseline until v2 gates pass.
- Acceptance gate: all existing prompt-pack gates plus v2-specific contract fixtures.

## Beta Blockers

- Step 1 Jakobson sentence pipeline must remain green.
- Missing Step 2 PostgreSQL canonical Full Mode gates.
- Partial Step 3 runtime model-control integration.
- Missing Step 4 Prompt Pack v2.
- Any failing real gate in `release/test_manifest.ijson`.
- Dirty source package or RC package scan failure.
