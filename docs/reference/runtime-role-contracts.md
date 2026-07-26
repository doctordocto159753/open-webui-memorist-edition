# Runtime role contracts

Memorist processing roles must be certified against the contract they execute at
runtime. Connectivity or a generic JSON marker is not sufficient evidence that a
model can perform a role.

## Contract authorities

`memcore.model_control.runtime_contracts` is the contract authority for the
StageInvoker roles that use compact direct output objects:

- `high_confidence_extraction`;
- `privacy_sensitivity`;
- `block_compaction`.

Each role contract owns:

- a stable contract ID and version;
- the exact StageInvoker stage and runtime prompt version;
- a strict Pydantic output model;
- provider JSON Schema generated from that model;
- semantic validation;
- a synthetic, non-sensitive certification input;
- a contract hash included in the role-manifest fingerprint.

The existing prompt registry remains authoritative for registry-driven roles such
as preflight and import reconstruction. Jakobson v3 remains the authority for
`memory_extraction`. Embedding uses the vector contract rather than structured
chat output.

## Provider dispatch

For an OpenAI-compatible StageInvoker role:

1. The runtime prompt identifies `ROLE`, `STAGE`, and `PROMPT_VERSION`.
2. Memorist accepts a runtime contract only when all three values match its
   registered identity.
3. Profiles declaring structured output receive a strict `json_schema` request.
4. Profiles declaring JSON mode receive `json_object`; the prompt also carries
   the exact contract ID, version, and JSON Schema.
5. The returned object is validated against the same model used to generate the
   schema, then against the role's semantic validator.
6. Invalid output remains a visible provider failure and may use only the
   role-authorized deterministic fail-open path.

Certification uses the same prompt renderer, provider path, schema, validator,
and fixture as runtime. Changing the runtime contract or its prompt identity
changes the role-manifest hash and makes prior remote-profile certification
stale.

## Attempt replay and terminal internal defects

An unfinalized provider-attempt reservation remains `unknown_completion`; it is
not safe to repeat a possibly paid call. A finalized attempt encountered during
replay is classified separately as `completed`, and the provider is still not
called again.

A deterministic fallback that violates its own active contract is an internal
contract defect, not a transient provider failure. Memory jobs treat this class
as terminal instead of consuming the normal retry budget.

## Audit boundaries

`processing_provider_attempts` remains the authority for remote calls and their
transport, parse, schema, token, and latency evidence. `processing_stage_runs`
remains the authority for final stage status and fallback truth. Historical rows
are not rewritten by the role-manifest update.
