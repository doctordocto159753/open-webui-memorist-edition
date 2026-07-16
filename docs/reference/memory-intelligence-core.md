# Memory Intelligence Core

The Memory Intelligence Core is the first semantic layer of Memorist. It converts raw message evidence into sentence-level communication annotations before any memory candidate is accepted.

## Core Statement

Jakobson analysis is not memory. It is the first communication-aware annotation layer that routes later memory extraction.

## Data Flow

```text
messages.raw_text
  -> SentenceSegmenter
  -> text_units(unit_type='sentence', offsets, stable hash)
  -> memorist.jakobson_sentence_analysis v2.0
  -> jakobson_analysis_runs
  -> jakobson_sentence_annotations
  -> SignalRouter
  -> memory_signal_routes
  -> candidate extraction input adapter
  -> memory_candidates + candidate_evidence(annotation_uuid, route_uuid)
  -> consolidation
  -> graph_projection_outbox(jakobson_annotations_ready)
```

## Why Function Scores Were Not Enough

The legacy `memorist.unit_analysis` prompt produced aggregate function scores. That path is retained only as a derived compatibility summary. It is not precise enough to route graph-memory extraction. A single workflow message can contain product policy, AI instructions, process facts, terminology rules, and preferences. Prompt Pack v2 makes `memorist.jakobson_sentence_analysis` the central semantic lens, stores one annotation per sentence, and routes later extractor prompts from those sentence annotations.

## Stored Six Factors

| Factor | Purpose |
| --- | --- |
| `sender_addresser` | who appears to speak, ask, instruct, evaluate, or express |
| `receiver_addressee` | who the sentence targets |
| `message` | what the full sentence communicates |
| `context_referent` | what topic, fact, process, event, or object it refers to |
| `code` | language, register, genre, terminology, and shared code |
| `contact_channel` | implied communication channel or relation |

## Function-to-Route Examples

| Function | Example | Route |
| --- | --- | --- |
| `conative` | "AI must preserve the user prompt." | `prompt_instruction` |
| `conative` | "Product Team must log decisions in Jira." | `workflow_policy`, `team_obligation` |
| `referential` | "Jira is the project tracker." | `jira_configuration` |
| `metalingual` | "By route I mean extraction dispatch." | `terminology_rule` |
| `emotive` | "I prefer concise answers." | `user_preference` |
| `poetic` | "Precise memory, precise decisions." | `style_policy` |
| `phatic` | "Hello." | `ignore` |

## Evidence Lineage

Every route-aware candidate input includes:

```json
{
  "message_uuid": "...",
  "unit_uuid": "...",
  "annotation_uuid": "...",
  "route_uuid": "...",
  "quote": "...",
  "char_start": 0,
  "char_end": 0
}
```

This keeps downstream memory candidates auditable and prevents assistant speculation or unsupported interpretation from becoming memory.

## Graph Projection Preparation

Step 1 does not require a graph backend. It creates `jakobson_annotations_ready` outbox events and normalized columns so later Full Mode can project sentence annotations into graph nodes and edges without reprocessing raw text.
