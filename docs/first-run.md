# First Run

Check these items on first run:

- local data directories exist
- SQLite DB initializes and migrations apply
- object store initializes
- graph backend status is clear
- feature flags match the intended profile
- Open WebUI connectivity is optional and local
- model/API configuration may be missing without blocking local tests
- local-only mode is confirmed
- telemetry is disabled by default
- import folder permissions are local and user-controlled

Health check:

```sh
curl http://localhost:8777/memcore/health
```
