# Open WebUI Memorist Edition Architecture

## Storage Runtime Split

Memorist has two explicit ledgers:

```text
SQLite is the Lite ledger.
PostgreSQL is the Full ledger.
FalkorDB is the graph memory map.
```

Lite keeps SQLite, SQLite FTS and the SQLite write actor. Full uses PostgreSQL
as the canonical source of truth, PostgreSQL durable jobs/outboxes, FalkorDB as
a rebuildable projection, and an in-memory hot scheduler that stores runnable
references only. Graph, embeddings, active blocks and attachments are derived
artifacts and must be rebuildable or invalidatable from canonical records.

## Memory Intelligence Core

Memorist treats the raw message as evidence, not memory. The first semantic layer is now deterministic sentence segmentation followed by sentence-level Jakobson six-factor annotation.

Jakobson analysis is not memory. It is the first communication-aware annotation layer that routes later memory extraction.

```text
Raw Message
  -> deterministic Sentence Units with offsets
  -> memorist.jakobson_sentence_analysis v2.0
  -> jakobson_sentence_annotations
  -> memory_signal_routes
  -> route-specific candidate extraction
  -> memory_candidates with annotation/route evidence
  -> consolidation and graph projection
```

## Why Sentence-Level

The sentence is the communication unit. Aggregate function scores cannot reliably distinguish an instruction to the AI, a product-team workflow rule, a Jira process fact, a terminology definition, and a style preference when they appear in the same message. Sentence-level offsets keep every downstream candidate traceable to exact evidence.

## Six Factors Stored

Each annotation stores sender/addresser, receiver/addressee, message, context/referent, code/register, contact/channel, dominant function, secondary functions, reason, notes, raw sentence output, and the source text unit. These normalized fields are queryable and also preserve raw I-JSON output for audit.

## Route Examples

| Dominant function | Example signal | Route |
| --- | --- | --- |
| conative | direct instruction to AI | `prompt_instruction` or `task_constraint` |
| conative | Product Team obligation | `workflow_policy`, `team_obligation` |
| referential | Jira/process fact | `jira_configuration`, `process_fact` |
| metalingual | definition or prompt wording | `terminology_rule`, `prompt_instruction` |
| emotive | preference or frustration | `user_preference`, `emotional_stance` |
| phatic | greeting/contact only | `ignore` |

## Graph Preparation

This step does not require FalkorDB. It emits `jakobson_annotations_ready` outbox events so a later graph backend can project:

```text
(:SentenceAnnotation)-[:HAS_DOMINANT_FUNCTION]->(:CommunicativeFunction)
(:SentenceAnnotation)-[:ADDRESSES]->(:Addressee)
(:SentenceAnnotation)-[:REFERS_TO]->(:ContextReferent)
(:SentenceAnnotation)-[:USES_CODE]->(:CodeRegister)
(:SentenceAnnotation)-[:ROUTES_TO]->(:ExtractorType)
(:MemoryCandidate)-[:DERIVED_FROM]->(:SentenceAnnotation)
```

## Difference From Normal RAG

Normal RAG retrieves chunks. Memorist records communication-aware annotations, routes extraction by intent/function, preserves evidence lineage, and keeps Memory Context Attachment separate from user text.
