# Development

Practical guide for working on Memorist from a source checkout.

## Repository structure

```text
memorist-core/               FastAPI memory engine (Python 3.12, uv)
  src/memcore/               runtime: api, memory_worker, model_control, imports, …
  tests/                     backend test suite (pytest)
  migrations/                schema migrations
open-webui-integration/      trusted server-side Open WebUI integration
  memorist/filter/           memory Filter (inlet/outlet)
  memorist/function/         status Function
  memorist/backend/          authenticated /api/v1/memorist/* router
  memorist/frontend/         chat-side components (toggle, attachments, setup)
  memorist/tests/            integration contract tests
tests/frontend/              vitest suites for the frontend components
docs/                        canonical docs + reference/ + fa/ + historical/
installer/                   release assembly + installer validation scripts
release/                     packaging, package manifest, release tests
  memorist-openwebui/        the shippable local package (installer, compose)
scripts/                     repo maintenance and audit scripts
.github/workflows/           certification workflows (self-hosted CI)
```

## Prerequisites

- Python **3.12+** and [`uv`](https://docs.astral.sh/uv/)
- Node 22+ and npm (frontend tests/lint)
- Docker with Compose (container runs; optional for pure-Python Lite work)

## Backend setup and run

```sh
cd memorist-core
uv sync --all-extras --dev
uv run uvicorn memcore.main:app --host 0.0.0.0 --port 8777 --reload
```

Health check: `curl http://localhost:8777/memcore/health`

Or via Make from the repo root: `make install && make dev`.

## Running Lite / Full with containers

Developer compose files live at the repo root:

```sh
cp .env.example .env
make dev-up-lite        # docker compose -f docker-compose.lite.yml up --build
make dev-up-full        # experimental PostgreSQL + FalkorDB preview
```

The release-oriented orchestration is `docker-compose.release.yml`
(profiles `lite`/`full`), which mirrors the shipped package compose. Full mode
must not be described as supported unless
`python scripts/full_mode_check.py` reports all external gates passed.

## Tests

Backend (from `memorist-core/`):

```sh
uv run pytest                     # full suite
uv run pytest tests/test_pr4d_semantic_baseline.py -q   # targeted
make check                        # lint + typecheck + tests (repo root)
```

Frontend (repo root):

```sh
npm ci
npm test                          # vitest: toggle, attachments, setup, import
npm run typecheck && npm run lint
```

Useful audit targets (repo root Makefile):

```sh
make smoke-daily                  # daily-use smoke against a temp SQLite app
make openwebui-contract-tests     # Filter/Function contract fixtures
make heritage-roundtrip           # export/verify/restore roundtrip
make forget-residue               # forget leaves no residue in projections
make consistency-check            # local consistency checker
make test-security                # security-focused suite
```

## Certification workflows (CI)

Every pull request to `main` runs the contract workflows under
`.github/workflows/`:

| Workflow file | Contract |
| --- | --- |
| `pr4d-semantic-baseline.yml` | canonical semantic authority, gate/route/candidate parity, trust/provenance |
| `pr4b-memory-control.yml` | memory control, scope isolation, actor authentication, attachments E2E |
| `pr5a-memory-attachment-ux.yml` | attachment display backend + frontend contract |
| `pr5b-memory-workflow-toggle.yml` | Memory On/Off toggle backend + frontend contract |
| `pr5c-memory-node-config.yml` | memory node setup, secret redaction |
| `pr5d-one-click-installer.yml` | installer static checks, compose config, dry run, manifest |
| `full-postgres-import.yml` | import runtime certification incl. Full PostgreSQL E2E |
| `public-release-readiness.yml` | docs/link integrity, repo hygiene, lint/type, frontend smoke |

All workflows use `runs-on: [self-hosted, linux, x64, memorist-ci]`.
**Public forks will not have these runners** and need to either provision
equivalent self-hosted runners or adapt the `runs-on` labels to hosted
runners; the suites themselves are plain pytest/vitest/python and portable.

## Release packaging

The shippable package lives in `release/memorist-openwebui/` (installer
scripts, compose, docs, checksums).

```sh
python installer/scripts/validate_installer.py --dry-run   # installer static checks
python installer/scripts/validate_compose.py               # compose config, lite+full
python release/memorist-openwebui/scripts/gen_checksums.py # refresh package checksums
python installer/scripts/assemble_rc.py                    # build the RC zip
```

Generated zips and staging directories under `release/rc/` and
`release/source/` are build outputs — never commit them. The forbidden-file
scanner (`release/scan_forbidden_files.py`) blocks secrets and runtime
artifacts from entering a package.

## Coding conventions

- Python: `ruff` (lint + format) and `mypy` on typed-core modules —
  `make lint`, `make typecheck`, `make format`.
- TypeScript: `npm run lint` (eslint) and `npm run typecheck`.
- Keep changes consistent with the guarantees in
  [ARCHITECTURE.md](ARCHITECTURE.md) and
  [MEMORY_MACHINE.md](MEMORY_MACHINE.md); semantic-authority, consent, and
  secret-handling contracts are CI-enforced.
- Tests accompany behavior changes; contract tests are the release gate.

## Opening a pull request

1. Branch from `main`; keep changes scoped.
2. Run the relevant suites locally (`make check`, `npm test`).
3. Open the PR against `main`; certification workflows must pass.
4. Do not include generated artifacts, `.env` files, or secrets — CI and the
   forbidden-file scanner will reject them.

See [../CONTRIBUTING.md](../CONTRIBUTING.md) for expectations and
[../SECURITY.md](../SECURITY.md) for vulnerability reporting.

## Public release checklist

Before tagging a release:

- [ ] All certification workflows green on `main`
- [ ] `python installer/scripts/validate_installer.py --dry-run` passes
- [ ] `python installer/scripts/validate_compose.py` passes (lite + full)
- [ ] Package checksums regenerated (`gen_checksums.py --check` clean)
- [ ] RC zip assembled and forbidden-file scan clean
- [ ] README/INSTALLATION status claims match reality (no overclaiming)
- [ ] RELEASE_NOTES.md updated
- [ ] Manual Windows install smoke on a clean machine (documented in
      [local package docs](../release/memorist-openwebui/README-LOCAL.md))
