# Contributing to Memorist

Thanks for your interest. Memorist is an early public alpha; contributions
that improve reliability, honesty of documentation, test coverage, and the
local-first experience are especially welcome.

## Ground rules

- **Don't weaken the contracts.** The semantic authority (gate-before-
  candidate, shared routing/candidate policy, trust/provenance), the
  server-side Memory On/Off consent ceiling, the read-only redacted attachment
  display, and the env-var-reference secret model are product guarantees.
  CI enforces them; PRs that erode them will not merge.
- **Local-first stays first.** Every processing role keeps a local
  deterministic fallback; remote providers remain optional and consent-gated.
- **No secrets in the repo.** `.env` is git-ignored; the forbidden-file
  scanner also blocks key-like content from release packages. Use
  `.env.example` for new variables.
- **Honest docs.** Don't document capabilities that don't exist; label
  previews as previews.

## Getting started

Follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for setup, test commands,
repository layout, and the release checklist. The short version:

```sh
cd memorist-core && uv sync --all-extras --dev && uv run pytest
npm ci && npm test
```

## Pull requests

1. Branch from `main`, keep the change scoped, and include tests for behavior
   changes.
2. Run the relevant suites locally (`make check`, `npm test`,
   `npm run lint`, `npm run typecheck`).
3. Open the PR against `main` with a clear description of what changed and
   why. Certification workflows must pass.
4. Note: CI runs on the maintainer's self-hosted runners, so workflows may
   not execute on forks until a maintainer triggers them. The suites are
   plain pytest/vitest and can be run locally.

## Issues

- **Bugs:** include your OS, Docker Desktop version, Lite/Full mode, the
  installer/doctor output, and the first error from the logs — with any API
  keys redacted.
- **Security issues:** please use private reporting instead — see
  [SECURITY.md](SECURITY.md).
- **Feature ideas:** explain the user problem first; local-first,
  consent-respecting proposals fit the project best.

## Code style

- Python: `ruff` + `mypy` (`make lint`, `make typecheck`, `make format`).
- TypeScript: `eslint` + `tsc` (`npm run lint`, `npm run typecheck`).
- Match the surrounding code's conventions; prefer small, auditable changes.
