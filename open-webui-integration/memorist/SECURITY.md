# Security

- Memory content is treated as untrusted data.
- The Filter never evals memory content.
- The Filter never concatenates memory into the original user prompt.
- Attachments are inserted as separate context messages.
- Delimiter-closing text inside memory is escaped.
- Secrets and API-key-like strings are redacted from local logs and status output.
- The integration is local-only by default and rejects remote Memorist Core URLs.
- If Memorist Core is unavailable, default behavior is fail-open so chat continues.

Open WebUI Filters/Functions are trusted server-side code. Install only from a trusted local package.
