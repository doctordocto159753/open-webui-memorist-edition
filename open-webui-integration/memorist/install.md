# Install

1. Start Memorist Core locally.
2. Copy `filter/memorist_memory_filter.py` and the `shared/` folder into the Open WebUI Filter environment according to your Open WebUI deployment.
3. Copy `function/memorist_status_function.py` and the `shared/` folder into the Open WebUI Function environment if you want a status helper.
4. Configure environment variables if defaults are not enough.

Required default URL:

```env
MEMORIST_CORE_URL=http://localhost:8777
```

Do not configure provider API keys in Memorist integration files. Use Open WebUI Admin Settings → Connections for model providers.
# Authenticated backend router

The shipped Compose files start Open WebUI through
`python -m memorist.backend.openwebui_entrypoint`. The entrypoint mounts the router under the
native authenticated API application, uses Open WebUI's `get_verified_user`, and supplies the
workspace only from `MEMORIST_OPENWEBUI_WORKSPACE_UUID`. Never derive either identity from
request headers, JSON, query parameters, or browser storage. The frontend uses
`/api/v1/memorist`, while only the backend Filter/router may reach `/memcore` with the service
credential and actor-assertion secret. A custom deployment must reproduce this same mount and
dependency binding.
