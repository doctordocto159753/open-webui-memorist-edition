# Security

Memorist stores local conversations, memory candidates, canonical memory versions, retrieval traces, attachments, import staging records, and Heritage export data according to enabled features.

Data lives in the local SQLite DB and object store configured by `.env`.

Deletion limits:

- logical deletion and privacy erasure remove/query-quarantine data inside Memorist tables
- `secure_delete` can improve SQLite deletion assurance but costs performance
- `VACUUM` should not run in hot paths
- WAL checkpointing matters for backup and file size
- filesystems, SSD wear leveling, and backups may retain physical remnants outside Memorist control

Import security:

- provider archives are staged first
- raw provider content remains untrusted data
- instruction-like memory is escaped and flagged, not promoted to directives
- I-JSON validation applies to structured payloads

Open WebUI integration:

- Filters and Functions execute Python server-side
- install only trusted local release files
- the integration injects bounded memory context; it does not change the original user prompt
