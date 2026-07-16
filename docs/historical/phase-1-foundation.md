# Phase 1 foundation

Phase 1 creates a clean repository layout and a minimal service surface.

## Implemented

- FastAPI application factory.
- `/memcore/health` endpoint.
- `/memcore/version` endpoint.
- `/memcore/config/effective` endpoint with secret redaction.
- Pydantic Settings configuration.
- Typed local-first feature flags.
- Local SQLite helper and initial migration.
- Local-only structured logging.
- pytest, ruff, and mypy configuration.
- Lite and full Docker Compose files.

## Not implemented

- Memory extraction.
- Graph projection.
- Open WebUI UI modifications.
- Import/export.
- Prompt augmentation.
