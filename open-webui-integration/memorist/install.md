# Install

The Memorist chat integration is fully automatic in the shipped product. No
Filter or Function file is copied or installed by hand.

1. The release Compose stack runs the derivative Memorist Open WebUI image
   (see `release/openwebui-image/`), which contains this integration package
   and the compiled Memorist frontend.
2. At every Open WebUI startup, `memorist.backend.filter_provisioning`
   idempotently upserts the managed global chat filter
   (`memorist_memory_filter`) into Open WebUI's own Functions table. Manual
   edits to that row are overwritten; deactivating it is repaired on the next
   start. Provisioning failures abort startup loudly.
3. The same startup path registers the authenticated proxy under
   `/api/v1/memorist`, reorders it ahead of the SPA catch-all mount, and
   asserts the ordering invariant (`memorist.backend.route_order`). A
   misordered application refuses to start.

Required environment (provided by the release Compose files):

```env
MEMORIST_CORE_URL=http://memorist-core:8777
MEMORIST_OPENWEBUI_WORKSPACE_UUID=<installer-generated UUID>
MEMORIST_ACTOR_ASSERTION_SECRET=<installer-generated>
MEMORIST_ACTOR_SERVICE_TOKEN=<installer-generated>
```

Do not configure provider API keys in Memorist integration files. Model
providers for chat are configured in Open WebUI Admin Settings → Connections;
Memorist processing-node providers are configured in Settings → Memorist →
Processing Nodes using an environment-variable **name** resolved inside
Memorist Core.

# Authenticated backend router

The shipped image starts Open WebUI through
`python -m memorist.backend.openwebui_entrypoint`. The entrypoint verifies the
pinned Open WebUI version, mounts the router under the native authenticated
API application, uses Open WebUI's `get_verified_user`, and supplies the
workspace only from `MEMORIST_OPENWEBUI_WORKSPACE_UUID`. Never derive either
identity from request headers, JSON, query parameters, or browser storage. The
frontend uses `/api/v1/memorist`, while only the backend Filter/router may
reach `/memcore` with the service credential and actor-assertion secret. A
custom deployment must reproduce this same mount and dependency binding.

# Manual development installs

For development against a stock Open WebUI without the derivative image, the
same entrypoint works with `PYTHONPATH` pointing at this package. The legacy
copy-the-filter flow is no longer supported for the packaged product.
