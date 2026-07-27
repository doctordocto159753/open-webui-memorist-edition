# CI runners and workflow topology

## Decision

Memorist is a public repository. Pull-request code must run on GitHub-hosted
runners, not on a persistent machine owned by a maintainer.

The default workflow is `.github/workflows/ci-consolidated.yml`. It has four
isolation boundaries:

1. `quality-unit-ui` — Python quality/unit/integration tests, frontend tests,
   documentation, and repository hygiene;
2. `postgres-graph` — one dependency installation, one fresh PostgreSQL suite,
   and a separate clean database for real FalkorDB assertions;
3. `package-release` — one PowerShell/Pester setup, one production frontend
   build, and one release ZIP assembly;
4. `product-e2e` — downloads that already-built ZIP, builds the derivative image
   once, starts one isolated Full stack, runs both Playwright phases around a
   restart of that same stack, and then stops it.

A job is the security and machine-isolation unit in GitHub Actions. A live
Docker stack is therefore never shared across jobs. Build outputs cross the
boundary only as immutable workflow artifacts.

Dependency caches are enabled through `setup-uv` and `setup-node`. The package
ZIP is assembled once and passed to the product job as an artifact. The product
job uses a run-scoped Compose project name, so two runs cannot reuse the same
network or named volumes.

## Why persistent self-hosted runners are not used for public pull requests

A pull request can modify source code, scripts, tests, Dockerfiles, and workflow
inputs. On a persistent self-hosted runner that code executes inside an
environment owned by the maintainer and may leave persistence for later jobs.
Fork approval, read-only `GITHUB_TOKEN`, environments, and secret withholding
are useful layers, but they do not turn the host into an isolated disposable
machine.

The consolidated workflow therefore uses `ubuntu-24.04` for all pull-request
jobs. Standard GitHub-hosted runners are free for public repositories and each
job receives a clean hosted environment.

## Repository settings

Configure these once under **Repository → Settings → Actions → General**:

- Actions permissions: allow only the actions required by the workflows;
- Workflow permissions: **Read repository contents and packages permissions**;
- keep **Allow GitHub Actions to create and approve pull requests** disabled;
- under **Approval for running fork pull request workflows from contributors**,
  select **Require approval for all external contributors**.

Before approving an external run, inspect changes to `.github/workflows/`,
`.github/scripts/`, Dockerfiles, installer scripts, and test helpers.

## Optional self-hosted runner for trusted manual diagnostics

A self-hosted runner is acceptable only as an additional trusted diagnostic
surface, not as the default public-PR executor.

Required host properties:

- dedicated disposable VM or machine; never a personal workstation or the
  production Memorist server;
- dedicated unprivileged account;
- no SSH keys, cloud credentials, personal files, production secrets, or access
  to private LAN services;
- a separate Docker daemon/VM treated as root-equivalent;
- no automatic `pull_request` trigger;
- only `workflow_dispatch` from `main` or a protected release tag;
- no artifact downloaded from an untrusted pull-request workflow;
- preferably one job per fresh VM (ephemeral/JIT runner).

### Registration

1. Open **Repository → Settings → Actions → Runners**.
2. Select **New self-hosted runner**.
3. Choose **Linux** and **x64**.
4. On the dedicated VM, create a runner account and directory:

```bash
sudo adduser --disabled-password --gecos "" gha-memorist
sudo mkdir -p /opt/actions-runner-memorist
sudo chown -R gha-memorist:gha-memorist /opt/actions-runner-memorist
sudo -iu gha-memorist
cd /opt/actions-runner-memorist
```

5. Copy the download, checksum, and extraction commands shown by GitHub on that
   page. Do not copy an old runner version or registration token from this
   document.
6. Run the configuration command using the one-hour token shown by GitHub:

```bash
./config.sh \
  --url https://github.com/doctordocto159753/open-webui-memorist-edition \
  --token <TOKEN_FROM_GITHUB_UI> \
  --name memorist-trusted-01 \
  --labels memorist-trusted,linux,x64 \
  --work _work \
  --unattended \
  --replace
```

7. Exit the runner account and install it as a service:

```bash
exit
cd /opt/actions-runner-memorist
sudo ./svc.sh install gha-memorist
sudo ./svc.sh start
sudo ./svc.sh status
```

Membership in the Linux `docker` group is effectively root access. Add the
runner account to that group only inside the dedicated disposable VM, never on a
personal or production host.

A trusted-only workflow must use a distinct label and reject arbitrary refs:

```yaml
"on":
  workflow_dispatch:

permissions:
  contents: read

jobs:
  trusted-diagnostic:
    if: github.repository == 'doctordocto159753/open-webui-memorist-edition'
    environment: trusted-ci
    runs-on: [self-hosted, linux, x64, memorist-trusted]
    steps:
      - name: Ref guard
        shell: bash
        run: |
          case "$GITHUB_REF" in
            refs/heads/main|refs/tags/v*) ;;
            *) echo "Ref is not trusted: $GITHUB_REF"; exit 1 ;;
          esac
```

Create the `trusted-ci` environment with a required reviewer and restrict it to
`main` and protected release tags. Environment approval is an additional gate;
it is not a substitute for VM isolation.

## Retiring the legacy workflow fan-out

After `Consolidated CI` passes on a real pull request, disable the following
legacy workflows from **Actions → select workflow → … → Disable workflow**:

- PR3 Import Runtime Certification
- PR4-B Memory Control Contract
- PR4-D Semantic Baseline
- PR5-A Memory Attachment UX
- PR5-B Memory Workflow Toggle
- PR5-C Memory Node Configuration
- PR5-D One-Click Installer
- PR5-F Full One-Click Certification
- PR5-G Real OpenWebUI Product Integration
- Public Release Readiness

Do not delete them in the first migration. Keeping them disabled preserves a
reviewable fallback while preventing duplicate checkouts, dependency installs,
Docker builds, package assemblies, and deployments.

Update branch protection after the migration: remove legacy required-check names
and require the four `Consolidated CI` checks instead.

## Rollout gates

The migration is complete only after all of the following are observed on the
same commit:

- all four consolidated jobs pass;
- PostgreSQL-gated tests execute rather than skip;
- both real FalkorDB assertions execute on their clean database;
- the release ZIP is assembled once and consumed by `product-e2e`;
- the derivative image is built once in `product-e2e`;
- the Full stack starts once, then only restarts for persistence testing;
- no legacy workflow remains enabled on pull requests;
- branch protection requires only the intended consolidated checks.
