# Threat Model

Memorist is local-first. The main risks are malicious memory content, unsafe imports, scope leakage, local file exposure, prompt injection, insecure Open WebUI Functions/Filters, deletion expectations, and dependency supply chain issues.

Controls:

- no default API keys
- no remote telemetry
- enforced local-only mode
- I-JSON validation for structured payloads
- ZIP import path and size validation
- prompt-injection adversarial fixtures
- scope-leak eval/security gates
- Heritage checksums
- SQLite consistency checks and safe backup

Open WebUI Tools and Functions execute Python on the server. Install Memorist integration files only from a trusted local release.
