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

Mount `memorist.backend.router.router` in the Open WebUI backend under the existing
authenticated API application. The Open WebUI authentication/membership middleware must set
`request.state.memorist_actor.user_uuid` and `.workspace_uuid`; never derive these values from
request headers, JSON, query parameters, or browser storage. The frontend uses
`/api/v1/memorist`, while only the backend Filter/router may reach `/memcore` with the service
credential and actor-assertion secret.
