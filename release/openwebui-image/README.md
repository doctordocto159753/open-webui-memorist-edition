# Memorist Open WebUI derivative image

This directory builds the Open WebUI image that the Memorist release actually
ships: pinned upstream Open WebUI v0.9.6 with the Memorist product compiled
into the production frontend and the integration package baked into the
runtime layer.

## Reproducibility

Every input is immutable and verified (see `source-pin.json`):

| Input | Pin |
| --- | --- |
| Frontend source snapshot | Open WebUI 0.9.6 PyPI sdist, SHA-256 verified at build time |
| Runtime base image | `ghcr.io/open-webui/open-webui:v0.9.6` by digest |
| Frontend builder image | `node:22-alpine3.20` by digest |
| Patch layer | `patches/*.patch`, applied with `--fuzz=0` (any drift fails the build) |

The build never clones a git repository. `prepare_frontend_tree.sh` verifies
the sdist hash, extracts it, copies the Memorist UI modules into
`src/lib/memorist/`, copies the `frontend-overlay/` route/components, applies
the patch layer, and asserts the integration markers exist before `npm run
build` compiles the same bundle that ships.

## Upstream integration points (the complete patch surface)

1. `src/lib/components/admin/Settings.svelte` — one navigation entry
   ("Memorist") in the admin settings list, linking to
   `/settings/memorist/memory-setup`.
2. `src/lib/components/chat/MessageInput.svelte` — mounts
   `MemoristComposerToggle` (the Memory On/Off control) in the composer
   toolbar.
3. `src/lib/components/chat/Messages/ResponseMessage.svelte` — mounts
   `MemoristMessageDisclosure` (the truthful "Memory used" disclosure) under
   each assistant message.

Everything else is additive: new SvelteKit routes under
`src/routes/(app)/settings/memorist/` and new components under
`src/lib/components/memorist/` and `src/lib/memorist/`.

## Runtime layer

The final stage starts from the pinned upstream image by digest and only:

- replaces `/app/build` with the Memorist-integrated frontend build;
- adds `/memorist-integration/memorist` (backend router, production
  entrypoint, route-order guard, filter provisioning, shared client, managed
  filter) and sets `PYTHONPATH`;
- sets the default command to the Memorist production entrypoint, which
  fail-closes on Open WebUI version drift, route-order violations, and filter
  provisioning failures.

## Building

From the repository:

```sh
python3 installer/scripts/assemble_rc.py   # stages runtime/ inside the package
docker build -t memorist/openwebui:<version> \
  -f <extracted-package>/runtime/openwebui-image/Dockerfile \
  <extracted-package>/runtime
```

The shipped package contains this directory under `runtime/openwebui-image/`;
`compose.yml` declares it as the build context, so a user installation needs
only Docker Desktop. A prebuilt pinned image can be substituted via the
`MEMORIST_OPENWEBUI_IMAGE` variable in `.env`.
