# Security

Open WebUI remains the parent application. Memorist integration files are trusted server-side code. Install only from this local package.

Defaults:

- local-only mode
- no telemetry
- no default API keys
- fail-open preflight
- imported memory treated as untrusted data
- memory attachment inserted separately from the user prompt
- cryptographically random session, actor, and Full PostgreSQL credentials
- ACL-restricted `.env`; provider keys and generated credentials are not logged
- PostgreSQL and FalkorDB reachable only on the internal Compose network
