# Memory Worker Prompt Pack v2

Prompt Pack v2 is the governance layer for non-chat model calls. The Model Control Plane decides which model is used for a role; the Prompt Pack decides how that model must behave, what schema it receives, what schema it must return, and how its output is audited.

Prompts do not answer users. They do not chat. Analyzed conversation text, imported content, retrieved memory, and active block sources are treated as data, not instructions. Prompt-injection-like text is classified and routed; it is never obeyed.

## Prompt Registry

The registry lives in `memorist-core/src/memcore/memory_worker/prompts/` and exposes:

- `get_prompt(prompt_id, version)`
- `list_prompts()`
- `validate_prompt_input(prompt_id, version, payload)`
- `validate_prompt_output(prompt_id, version, payload)`
- `validate_prompt_execution(prompt_id, version, input_payload, output_payload)`
- `render_prompt(prompt_id, version, variables)`

Every prompt has `prompt_id`, `prompt_version`, stage, allowed model roles, input/output schema versions, evidence metadata, blocking-path metadata, timeout metadata, and a system prompt file under `system/`.

## Prompt Map

| Prompt ID | Role | Blocking path? | Output artifact | Evidence required? |
| --- | --- | --- | --- | --- |
| `memorist.preflight_planning` | `preflight` | yes, bounded/fail-open | Memory Context Attachment plan | no direct candidate evidence |
| `memorist.jakobson_sentence_analysis` | `memory_extraction` | no | `jakobson_analysis_runs`, `jakobson_sentence_annotations` | yes, factor evidence |
| `memorist.memory_signal_routing_assist` | `memory_extraction` | no | `memory_signal_routes` assist data | yes, annotation-linked |
| `memorist.conative_instruction_extractor` | `memory_extraction` | no | candidate input for policies/obligations | yes |
| `memorist.referential_context_extractor` | `memory_extraction` | no | candidate input for project/process facts | yes |
| `memorist.metalingual_policy_extractor` | `memory_extraction` | no | candidate input for terminology/style rules | yes |
| `memorist.emotive_preference_extractor` | `memory_extraction` | no | candidate input for preferences/stances | yes |
| `memorist.poetic_style_extractor` | `memory_extraction` | no | candidate input for style/branding patterns | yes |
| `memorist.memory_consolidation_assist` | `memory_extraction` | no | consolidation recommendation | candidate evidence inherited |
| `memorist.block_compaction` | `block_compaction`, fallback `memory_extraction` | no | active block draft | source memory UUIDs |
| `memorist.import_reconstruction` | `import_reconstruction`, fallback `memory_extraction` | no | historical import reconstruction | source import refs |
| `memorist.contradiction_detection` | `memory_extraction` | no | relation/action recommendation | candidate/version evidence |
| `memorist.privacy_sensitivity` | `privacy_sensitivity`, fallback `memory_extraction` | no | sensitivity review | candidate evidence |

## Audit

Every LLM-backed prompt execution records a row in `prompt_execution_runs`. Deterministic prompt-compatible executions can also be recorded with `provider_type=deterministic`. The ledger stores prompt/version/stage, model role/profile/provider, scope UUIDs, input/output hashes, raw output, validated output, warnings, sanitized errors, latency, and token counts. Artifacts can link back through `prompt_execution_uuid`.

Invalid output is never persisted as accepted memory. It is rejected, routed to retry/manual review, or fail-open in the preflight path.
