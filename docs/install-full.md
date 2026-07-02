# Experimental Full Compose

This compose profile is an experimental PostgreSQL + FalkorDB Full Mode preview. It configures PostgreSQL as the canonical store and FalkorDB as a rebuildable graph projection. It must not be treated as a beta-supported runtime path until `python scripts/full_mode_check.py` reports every required Full gate as passed.

Current wording:

```text
Full Mode: experimental preview; external certification incomplete.
```

```sh
cp .env.example .env
make dev-up-full
```

Lite mode does not require PostgreSQL, FalkorDB, or embeddings. Full mode requires PostgreSQL and normally requires FalkorDB unless graph degradation is explicitly allowed.
