# Security

Memorist defaults to local-only operation and no telemetry.

Secrets:

- no default API keys
- Open WebUI provider secrets belong in Open WebUI Admin Settings
- Memorist status and logs redact secret-like strings

Trusted code warning:

Open WebUI Filters and Functions execute server-side Python. Install only trusted local Memorist integration files.

Verify package checksums before use:

```bash
sha256sum -c memorist-openwebui-0.2.0-beta.1.sha256
```

Report vulnerabilities with minimal reproduction steps and do not attach private memory databases unless explicitly requested.
