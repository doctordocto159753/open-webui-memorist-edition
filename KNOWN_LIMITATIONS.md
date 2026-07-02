# Known Limitations

- `v0.2.0-beta.1` is a conditional GitHub development baseline, not a stable
  public release or Public Beta GO.
- The default supported runtime is local Lite mode with SQLite and local object
  storage.
- PostgreSQL canonical Full Mode, FalkorDB projection, hot scheduler,
  PostgreSQL job/outbox DDL, SQLite-to-PostgreSQL migration tooling, and
  external Full certification scripts are implemented, but the current claim is:
  `Full Mode: experimental preview; external certification incomplete.`
- Full Mode is not beta-supported yet unless `python scripts/full_mode_check.py`
  reports every required external gate as passed. Skipped/manual Full gates do
  not count as pass.
- Full beta support is blocked by any missing/skipped/failed PostgreSQL
  canonical smoke, FalkorDB projection smoke, full compose smoke, or graph
  forget-residue smoke.
- Sentence-level Jakobson analysis is implemented as the primary semantic
  memory-intelligence layer. The `memorist.unit_analysis` prompt is retained
  only as aggregate/legacy compatibility.
- Memory Worker Prompt Pack v2 is the current contract baseline with versioned
  prompt definitions, schema-bound I-JSON outputs, and prompt execution audit
  metadata. LLM quality, provider latency, and production prompt tuning remain
  iterative work.
- Model Control Plane is implemented as a backend/runtime baseline for role
  specs, profile CRUD, preflight lifecycle checks, extraction lifecycle checks,
  usage events, and privacy acknowledgement. Broader provider orchestration and
  operator UX remain future hardening work.
- Provider export formats can change without notice; import adapters are
  defensive but not guaranteed to parse every future format.
- Heavy import is actor-batched and resumable, but runtime speed depends on
  disk latency, SQLite WAL behavior, antivirus scanning, and machine load.
- Some bounded developer/admin write paths remain direct repository writes.
  `make consistency-check` audits those locations and requires justifications.
- Privacy forget redacts local canonical content and projections, but physical
  deletion is bounded by SQLite WAL/checkpoints, filesystem behavior, SSD wear
  leveling, and any backups made before erasure.
- Open WebUI compatibility is fixture-tested against the documented filter
  contract. The optional local container-smoke target is pinned to
  `ghcr.io/open-webui/open-webui:v0.9.6`, but broad version-matrix
  certification and automatic Filter installation are still pending.
- Prompt injection cannot be eliminated. Memorist labels retrieved/imported
  context as untrusted data, escapes attachment rendering, and tests adversarial
  cases.
- Dirty development roots can contain `.git`, caches, virtualenvs, bytecode and
  local SQLite databases. Use `python scripts/clean_artifacts.py --apply` and
  `python release/source_package.py --out release/source/open-webui-memorist-edition-source.zip`
  before sharing source.
