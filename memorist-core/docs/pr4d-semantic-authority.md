# PR4-D semantic authority boundary

Memory-candidate creation is governed by one runtime-neutral chain:

1. persisted Jakobson annotation;
2. deterministic selected memory-signal route;
3. gate/candidate policy;
4. trust and provenance policy;
5. shared candidate-draft construction.

StructuredAnalyzer is complementary only. Its modality, temporal and
abstention fields can refine a draft, but its presence or classification cannot
authorize a candidate. Lite resolves the canonical context explicitly through
LiteCandidateAuthorityResolver; Full maps its persisted rows into the same
context. Both then call build_candidate_draft.

The shared draft owns candidate type, subject, predicate, object payload,
normalized text, authority, explicitness, confidence, importance, status,
sensitivity, reason codes, evidence lineage and versioned audit metadata.
SQLite and PostgreSQL adapters own only persistence.

Every route-backed draft records semantic authority, gate and route decisions,
annotation/route/run/prompt identifiers, source authority, explicitness, route
mapping version, and provenance policy version. Assistant claims are never user
facts. Tool observations, system instructions and imported records retain their
own authority labels. Ignore/discard/retain/manual-gate decisions cannot create
automatic candidates; privacy/manual-review routes cannot create automatic
normal memories.
