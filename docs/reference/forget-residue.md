# Forget Residue Checks

Privacy erasure is implemented as a dependency-aware preview, confirmation, execution, receipt, and residue-check flow. Memorist does not expose erased raw content in receipts or residue reports.

## API Flow

```text
POST /memcore/privacy/forget/preview
POST /memcore/privacy/forget/{request_uuid}/confirm
POST /memcore/privacy/forget/{request_uuid}/execute
GET  /memcore/privacy/forget/{request_uuid}/closure
GET  /memcore/privacy/forget/{request_uuid}/residue
GET  /memcore/privacy/forget/{request_uuid}/receipt
```

The legacy `/memcore/privacy/requests/*` endpoints remain available. Mutation endpoints use `PrivacyRequestCommand` so confirmation, execution, and retry run through the SQLite writer actor.

## Residue Policy

The residue checker scans canonical SQLite content, memory-worker artifacts, FTS projections, Memory Context Attachments, Active Memory Blocks, block versions, hot cache fields, import records, and import mappings. It reports only table/column/row identifiers and never returns matched raw content.

Primary-key tombstones and audit receipts are allowed so the system can retain accountability and referential integrity after redaction. Release smoke focuses on raw-content residue across memory-derived layers.

## Multi-Layer Erasure

Memory-target erasure redacts:

- canonical `memories` status and `memory_versions` content;
- linked `memory_candidates`, `candidate_evidence`, and `text_units`;
- FTS rows and local embedding rows;
- Memory Context Attachment rendered and I-JSON payloads;
- Active Memory Block values, block versions, and block source links;
- session hot cache fields that can carry derived memory text;
- import record payloads that contain the forgotten memory fragment.

## Smoke

```sh
make forget-residue
```

The smoke seeds a rich memory fixture with a unique marker in canonical memory, memory version, evidence, attachment, block, hot cache, FTS, and import payloads. It forgets the memory target, verifies the receipt does not contain raw content, checks that the marker is absent from scanned storage layers, and exports Heritage after erasure to ensure the marker is not reintroduced into future portable packages.
