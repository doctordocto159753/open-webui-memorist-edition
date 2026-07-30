# The Memory Machine

This document explains how a chat turn becomes reusable, inspectable context in
Memorist — the full path from message capture to the "Memory used" display —
and where the user's consent boundaries sit.

For a code-grounded prompt/response trace with concrete records and Lite/Full
adapters, read
[Walkthrough پردازش حافظه در موتور مرکزی](reference/core-memory-processing-walkthrough.md).

The one-line thesis:

```text
Jakobson analysis is not memory. A candidate is not truth.
Memory is a versioned, evidence-linked claim that must earn its way
through gate, route, trust, and consolidation before it can be recalled.
```

## Pipeline overview

```text
1. capture           raw user/assistant message, unchanged, as evidence
2. unitization       deterministic sentence units with exact offsets
3. Jakobson analysis six-factor communication annotation per sentence
4. canonical route   persisted server-owned route
5. gate decision     persisted server-owned gate (always before candidate)
6. bounded context   2 prior units, or 6 only for dependency hints
7. semantic v1       one whole-message model call with strict local validation
8. coverage plan     one deterministic disposition for every accepted unit
9. proposal          deterministic UUIDv5, privacy/provenance bounded
10. persistence      crash-safe proposal/candidate link in SQLite or PostgreSQL
11. review paths     privacy/forget/manual-review handled outside ordinary memory
12. consolidation    candidates become versioned canonical memories
13. retrieval        scoped, budget-aware, rank-fused candidate selection
14. attachment       bounded Memory Context Attachment, provenance-tagged
15. display          read-only, redacted "Memory used" panel in chat
```

Consent wraps the whole machine: the per-chat **Memory On / Memory Off**
switch is enforced server-side before step 1. Recall and learning are distinct:
preflight can use only already-consolidated memory, while current user and
assistant messages become eligible for later turns only after their background
jobs complete.

## 1–2. Capture and unitization

Raw messages are evidence, not memory. Every captured user or assistant
message is preserved unchanged in the local ledger (message versions, session
events). Deterministic sentence segmentation then produces **sentence units
with exact character offsets**, so every downstream claim can be traced to the
exact words that produced it.

Why sentence-level? Aggregate scores over a whole message cannot reliably
distinguish an instruction to the AI, a team workflow rule, a process fact, a
terminology definition, and a style preference when they appear in the same
message.

## 3–4. Jakobson analysis and receiver/context resolution

Each sentence unit gets a six-factor annotation based on Jakobson's
communication model: sender/addresser, receiver/addressee, message,
context/referent, code/register, contact/channel, plus dominant and secondary
communicative functions, with reason and raw output preserved for audit.

The **receiver/context resolver** determines who a sentence addresses (the AI,
a teammate, the user themselves) and what it refers to. An instruction aimed at
the AI and an obligation describing a team process are different memories even
when the words look similar.

Jakobson analysis is an annotation lens, not memory. The complementary
`StructuredAnalyzer` produces auxiliary structured annotations and is **not**
the semantic authority — the canonical semantic contract owns routing and
gating.

## 5–6. Canonical route selection and the gate

A single canonical semantic authority maps annotations to **memory signal
routes** — shared by Lite and Full so both profiles make identical semantic
decisions:

| Dominant function | Example signal | Route |
| --- | --- | --- |
| conative | direct instruction to the AI | `prompt_instruction`, `task_constraint` |
| conative | team obligation | `workflow_policy`, `team_obligation` |
| referential | process/configuration fact | `process_fact`, `jira_configuration` |
| metalingual | definition, prompt wording | `terminology_rule`, `prompt_instruction` |
| emotive | preference, frustration | `user_preference`, `emotional_stance` |
| phatic | greeting/contact only | `ignore` |

The **gate decides before any candidate is constructed** ("gate before
candidate"). Signals that should never become ordinary memory are stopped
here:

- phatic/greeting-only turns create no memory;
- privacy requests, forget requests, and manual-review paths are routed to
  their own workflows, never to ordinary memory;
- weak or out-of-scope signals are dropped with a recorded reason.

## 6–10. Semantic coverage, candidate construction, and persistence

Only `analyze` and `analyze_high_confidence` gates enter the shared
`SemanticCandidatePlanningService`; terminal gates skip the semantic provider.
Lite and Full send the same whole-message prompt and a bounded same-user,
same-session, same-workspace/project context manifest. Memory attachments,
system prompts, hidden/deleted versions, tool output, and cross-session text are
never semantic context.

The model proposes units, references, relations, durability, polarity, and
epistemic status. Local code retains authority over exact evidence, gate/route,
privacy, provenance, disposition, UUID identity, and persistence. Every accepted
unit receives exactly one of `durable_candidate`, `context_only`,
`transient_instruction`, `unresolved_reference`, `rejected_by_gate`,
`needs_review`, or `unsupported`; omitted material is explicit rather than
silently dropped.

Only `durable_candidate` produces a proposal. The shared candidate adapter
mechanically maps that proposal through the already-persisted route. A candidate
carries:

- the claim text and memory type;
- evidence links back to exact sentence units and messages;
- the route and gate decision that produced it;
- a **trust/provenance policy** classification — user statements, assistant
  output, tool output, system text, and imported history have different
  provenance and different default trust. Assistant/tool/system-derived text
  never silently gains user-level authority.

Proposal UUIDs are deterministic and become candidate UUIDs. Coverage,
reservation, candidate, and evidence linking are replay-safe; a linked replay
verifies stored content instead of trusting the link alone. Assistant context
remains `assistant_claim` unless the current user explicitly ratifies or
corrects one uniquely resolved assistant item. Candidates remain routed
interpretations, not truth.

## 10–11. Review paths and consolidation

Sensitive candidates can be classified by the optional `privacy_sensitivity`
role and restricted or sent to manual review. The forget workflow
(preview → confirm → quarantine → erase → residue check) removes memory with a
receipt and without leaving residue in derived indexes.

Consolidation turns surviving candidates into canonical **memories** with
**memory versions**: corrections and updates create new versions instead of
rewriting history; contradictions and supersession are recorded, so "latest
summary wins" never destroys historical context. Projections (FTS, embeddings,
active memory blocks, graph) are derived from canonical versions and are
always rebuildable.

## 12–13. Retrieval and Memory Context Attachment

On each Memory-On turn, before the main model runs:

```text
current message → retrieval plan (intents, entities, temporal hints, scope)
→ hybrid candidate generation (canonical keys, active constraints,
  FTS BM25, optional embeddings, recent session, optional graph)
→ reciprocal rank fusion + explainable deterministic scoring
→ selection with diversity filtering — or explicit abstention
→ Memory Context Attachment (bounded by the model's token budget)
```

The attachment is rendered with provenance, source UUIDs, trust separation,
escaped delimiters, and instruction-like-content detection. **Attachments are
context, not answer text**: retrieved memory is untrusted data by default, the
original user prompt is never modified, and if Memorist is down or slow the
chat fails open with no attachment.

When evidence is weak, stale, conflicting, or out of scope, Memorist prefers
**abstention** over injecting noise.

## 14. Transparent display

When memory was attached to a turn, the chat shows a read-only **"Memory
used"** panel: which memories were attached, with redacted summaries and
provenance. The display is a window into what the model actually received —
it cannot edit memory, and it never reveals more than the attachment policy
allows. If nothing was attached, nothing is claimed.

## The consent boundary: Memory On / Memory Off

The **Memory On / Memory Off** switch sits beside the chat composer and is
persisted per authenticated user and chat, server-side:

- **Memory Off is a consent ceiling, not a UI hint.** An Off turn creates no
  capture, no processing job, no retrieval, and no attachment — even if a
  forged request claims otherwise, and even when remote providers are fully
  configured.
- Regeneration honors the workflow state recorded on the **original** turn: a
  response generated with Memory Off is regenerated without capture or
  attachment.
- New chats default to Memory On unless a user or system default says
  otherwise. The toggle does not control Open WebUI's separate native-memory
  feature.

## Where models fit (and where they don't)

Every processing role has a **local deterministic fallback** — a fresh install
can run the whole machine with no API key and no remote calls. Remote
OpenAI-compatible providers are optional per role (`memory_extraction`,
`high_confidence_extraction`, `embedding`, `preflight`, `privacy_sensitivity`,
`block_compaction`, `import_reconstruction`) and must be configured consciously: an explicit
privacy acknowledgement is required before a remote endpoint becomes a role
default, because role-specific conversation or memory text may leave the
machine. Secrets are referenced by environment-variable name only — see
[SECURITY.md](../SECURITY.md) and [INSTALLATION.md](INSTALLATION.md).

A remote default must also have a persisted successful certification for the
exact execution fingerprint. Endpoint/model/capability/enabled/secret-reference
edits make it stale; the resolver records the reason and follows documented
inheritance or a bounded built-in fallback. The seven processing roles receive
independent environment-variable pass-throughs.

The main chat model always belongs to Open WebUI; Memorist only observes its
metadata to size attachment budgets.

Provider output does not bypass the memory machine. The shared invocation
boundary first resolves the effective scoped role, then validates structured
output, records prompt/stage/usage audit rows, and only then permits the
existing gate and provenance policy to act. Consequential candidates receive
privacy and high-confidence passes before consolidation. Assistant-produced
project artifacts remain `assistant_claim` evidence with an explicit
`not_user_fact` marker and preceding-request link.

## Going deeper

- [reference/memory-engine-architecture.md](reference/memory-engine-architecture.md) — full essay-form architecture of the memory engine
- [reference/core-memory-processing-walkthrough.md](reference/core-memory-processing-walkthrough.md) — exact inlet, model, outlet, worker, persistence, and next-turn retrieval sequence
- [reference/memory-intelligence-core.md](reference/memory-intelligence-core.md) — Jakobson layer data flow
- [reference/concept-glossary.md](reference/concept-glossary.md) — frozen terminology
- [reference/memory-control-contract.md](reference/memory-control-contract.md) — authenticated control-plane contract
- [reference/prompt-pack.md](reference/prompt-pack.md) / [reference/prompt-safety.md](reference/prompt-safety.md) — schema-bound prompts and injection defenses
- [reference/import.md](reference/import.md) / [reference/heritage-roundtrip.md](reference/heritage-roundtrip.md) / [reference/forget-residue.md](reference/forget-residue.md) — import, portability, and forgetting
# Real-provider failure semantics

Memory extraction is asynchronous and fail-open, but never audit-open. Each
remote initial or repair attempt is durably reserved before network I/O and
finalized with transport, parse, schema, token, latency, and sanitized validation
metadata. The canonical memory pipeline receives only active-contract-valid
output. Provider failures therefore cannot poison the whole job or turn a
completed chat capture into a Core HTTP 500.
