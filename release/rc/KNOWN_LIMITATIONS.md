# Known Limitations

- `v0.2.0-beta.1` is still a beta candidate, not a stable public release.
- The default supported runtime is local Lite mode with SQLite and local object storage.
- Full FalkorDB graph-backed mode is optional and experimental.
- Provider export formats can change without notice; import adapters are defensive but not guaranteed to parse every future format.
- Heavy import is actor-batched and resumable, but runtime speed depends on disk latency, SQLite WAL behavior, antivirus scanning, and machine load.
- Some bounded developer/admin write paths remain direct repository writes. `make consistency-check` audits those locations and requires justifications.
- Privacy forget redacts local canonical content and projections, but physical deletion is bounded by SQLite WAL/checkpoints, filesystem behavior, SSD wear leveling, and any backups made before erasure.
- Open WebUI compatibility is fixture-tested against the documented filter contract. The optional local container-smoke target is pinned to `ghcr.io/open-webui/open-webui:v0.9.6`, but broad version-matrix certification and automatic Filter installation are still pending.
- Prompt injection cannot be eliminated. Memorist labels retrieved/imported context as untrusted data, escapes attachment rendering, and tests adversarial cases.
