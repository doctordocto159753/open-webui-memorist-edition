# FalkorDB Graph Projection

FalkorDB is the graph memory map, not the canonical ledger. PostgreSQL remains
the Full source of truth.

Projected graph shape includes:

```text
Workspace -[:IN_PROJECT]-> Project
Project -> Session -> Message -[:HAS_UNIT]-> TextUnit
TextUnit -[:HAS_JAKOBSON_ANNOTATION]-> JakobsonAnnotation
JakobsonAnnotation -[:HAS_DOMINANT_FUNCTION]-> CommunicativeFunction
JakobsonAnnotation -[:ADDRESSES]-> Addressee
JakobsonAnnotation -[:REFERS_TO]-> ContextReferent
JakobsonAnnotation -[:USES_CODE]-> CodeRegister
JakobsonAnnotation -[:ROUTES_TO]-> MemorySignalRoute
MemorySignalRoute -[:DERIVED_FROM]-> MemoryCandidate
MemoryCandidate -[:EVIDENCED_BY]-> JakobsonAnnotation
Memory -[:HAS_VERSION]-> MemoryVersion
MemoryVersion -[:EVIDENCED_BY]-> MemoryCandidate
```

If FalkorDB is down:

- Full diagnostics report `degraded`.
- Chat capture continues.
- Canonical PostgreSQL data is not lost.
- Graph outbox entries remain pending/retryable.
- Retrieval can fall back to PostgreSQL paths.
- Full certification remains incomplete unless the degraded behavior is tested.

Commands:

```sh
curl http://localhost:8777/memcore/graph/status
curl http://localhost:8777/memcore/graph/diagnostics
curl -X POST http://localhost:8777/memcore/graph/project-pending
curl -X POST http://localhost:8777/memcore/graph/rebuild
cd memorist-core
uv run python -m memcore.graph rebuild --store postgres
```

External smoke:

```sh
set MEMORIST_TEST_POSTGRES_DSN=postgresql://...
set MEMORIST_TEST_FALKORDB_URL=redis://localhost:6379
python release/tests/full_falkordb_projection_smoke.py
python release/tests/full_falkordb_rebuild_smoke.py
python release/tests/full_graph_forget_residue_smoke.py
```

Skipped FalkorDB gates block Full beta support.
