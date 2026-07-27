# Shared text semantics

One deterministic service — `memcore.textsemantics` — owns normalization,
tokenization, lexical matching, and claim polarity for Persian, Arabic-script
Persian, English, and mixed technical text. Lite and Full call the same pure
functions, so neither runtime can drift into a second lexical authority.

The service is pure: no persistence, no I/O, no configuration lookups.

## Why it exists

Lexical rules used to be ad-hoc substring and alternation regexes at each call
site. Substring matching has no notion of a word, and Persian makes that fail
loudly:

- `تیم` ("team") occurs inside `گرفتیم` ("we took/decided"), so
  "تصمیم گرفتیم" ("we decided") resolved a **team** addressee and routed to
  `workflow_policy` + `team_obligation` instead of `task_constraint`;
- `تو` ("you") occurs inside `توسعه` ("development"), `شما` ("you") inside
  `شمارش` ("counting");
- `token` occurs inside `tokenizer`, so "tokenizer settings" classified as
  `SECRET`;
- `sk-` occurs inside `task-force`.

Token matching removes the whole class. `contains_token("گرفتیم", "تیم")` is
false; `contains_token("تیم محصول", "تیم")` is true.

## Normalization contract

`NormalizationContract` is versioned data describing one rule set:

| Rule | Behaviour |
| --- | --- |
| `unicode_form` | NFC, composed per base-plus-combining-mark cluster |
| `unify_persian_letters` | `ي`/`ى` → `ی`, `ك` → `ک` |
| `remove_arabic_diacritics` | Non-spacing marks **in the Arabic blocks only** |
| `zwnj_policy` | `boundary` (default) emits a space, so `توسعه‌دهنده` and `توسعه دهنده` tokenize alike; `remove` joins them |
| `normalize_digits` | Persian `۰-۹` and Arabic-Indic `٠-٩` → ASCII |
| `lowercase_letters` | Unicode lowercase (identity for uncased scripts) |
| `compress_whitespace` | Runs collapse to one space; leading/trailing dropped |
| `preserve_fenced_code` | Fenced blocks copied byte for byte |

Diacritic removal is scoped to the Arabic blocks by construction, derived from
Unicode character data at import. A blanket "drop every combining mark" rule
would turn `résumé` into `resume` and change meaning in other languages.

`contract.fingerprint` is a SHA-256 over the whole rule set. Downstream content
fingerprints should mix it in, so a future normalization change invalidates them
instead of producing a false "unchanged" comparison against text normalized
under older rules.

Contract version: `memorist.text.normalization.v1`.

## Raw evidence is never replaced

`normalize_with_mapping(raw)` returns a `NormalizedText` carrying, for every
normalized character, the raw `[start, end)` range that produced it. Callers
match on the normalized form and persist the **exact raw span**:

```python
normalized = normalize_with_mapping(raw)
match = TEAM_RECEIVER.search(normalized)
if match is not None:
    evidence = match.evidence          # exact raw substring
    span = (match.raw_start, match.raw_end)
```

Spans advance monotonically, so a normalized slice maps back through the start
of its first character and the end of its last. Consecutive spans may repeat
when one cluster emits several characters.

Raw text is never rewritten and normalized text is never stored as evidence.

## Token boundaries

A token is a maximal run of alphanumeric characters, plus any combining mark
NFC could not fold into its base. Punctuation, underscores, hyphens, dots, and
ZWNJ all separate.

That makes `api_key` and `api key` the same two-token phrase, while `tokenizer`
stays one token that `token` cannot match. `access_token` does expose a `token`
token — an underscore is a boundary, so a snake_case identifier reveals its
parts rather than hiding them from a lexical rule.

## Fenced code

`scan_blocks` marks fenced ranges (```` ``` ```` and `~~~`, including an
unclosed fence running to end of text). Inside them normalization copies bytes
verbatim.

Lexical matching skips code by default so prose rules never fire on a snippet.
Callers that must see code pass `include_code=True` — sensitivity
classification does, because a credential pasted into a fence is exactly the
case that must not slip through. Code tokens still compare case-insensitively:
the comparison key is normalized while the stored text and evidence stay
byte-exact.

## Polarity

Polarity is independent of extraction confidence:

- **confidence** answers "did we extract this claim correctly?";
- **polarity** answers "does the claim assert or deny?".

`extract_polarity` returns `affirmed`, `negated`, or `unknown` with the exact
raw evidence that decided it. Text with no tokens at all is `unknown` — there
is no claim to assert.

Persian negation is handled both ways it is written: `نمی‌کنیم` tokenizes to a
standalone `نمی`, and the joined spelling `نمیکنیم` is caught by a rule anchored
at the start of a whole token, never mid-token.

Contract version: `memorist.text.polarity.v1`.

### Polarity is stored, not scored

`memory_candidates.polarity` and `memory_versions.polarity` hold the value in
both Lite and Full (SQLite `0036_claim_polarity.sql`, PostgreSQL
`0023_claim_polarity.sql`). Both migrations are additive and default existing
rows to `unknown`: polarity was never recorded for them, and backfilling
`affirmed` would invent a decision the old pipeline never made. No historical
confidence is recalculated.

The former `-0.05` negation penalty is removed. "We never deploy on Friday" is
asserted as certainly as "we deploy on Friday", so the two now score the same
confidence and differ only in polarity. Every other confidence coefficient is
unchanged; broader recalibration remains deferred.

Reading a stored modality payload goes through one shared reader so Lite and
Full cannot interpret it differently:

| Payload | Polarity |
| --- | --- |
| `{"polarity": "negated"}` | `negated` |
| `{"negated": true}` (pre-polarity row) | `negated` |
| `{"negated": false}` (pre-polarity row) | `affirmed` |
| `{}` | `unknown` |

## Sentence spans: typography, not syntax

`segment_sentences` reports where the writer put explicit boundary marks. That
is transcription, and code can do it exactly.

| Boundary | Reason code |
| --- | --- |
| Terminator (`.` `!` `?` `؟`) followed by whitespace | `sentence_terminator` |
| Blank line | `blank_line` |
| Line beginning with a bullet or enumerator | `list_item` |
| Fenced code block | `code_block` |
| Start of a block | `block_start` |

A period glued to content on both sides (`GPT-5.4`, `example.com`, `1.2.3`), a
short abbreviation list, and a line-leading enumerator (`2.`) do not end a span.

**A bare newline is not a boundary.** It is usually a text editor wrapping a
line, and `the pipeline runs on\nFriday afternoons` is one proposition broken by
line width, not two propositions. Splitting it produces two half-sentences that
then look like claims the writer never made. The wrap is recorded as
`line_break_not_a_boundary` and the text is left alone.

### What this module used to do, and why it stopped

An earlier revision also decided where *clauses* began and ended, whether a
clause was a statement or an instruction, whether `و` joined clauses or nouns,
and which earlier span a pronoun pointed at. It did this with hand-written lists
of Persian and English verb forms, conjunctions, and imperatives.

Every new example needed another lexicon entry, and every entry was a new way to
be confidently wrong. That is the signature of responsibility sitting in the
wrong layer: deterministic code cannot weigh what a conversation has been about.

Those questions now belong to the model-equipped semantic node. See
[Semantic analysis contract](semantic-analysis-contract.md).

Contract version: `memorist.text.segmentation.v2`.

## Context-dependency hints

`detect_context_dependency` reports that a closed-class deictic — a pronoun or a
demonstrative — occurs at an offset. That is all.

It does not say what the word refers to, does not offer candidates, and does not
rank anything. Its one intended use is routing: a message containing deictics
should be sent to the model with a wider history window, which is what
`TextEnvelope.requires_conversation_context` answers.

Pronouns and demonstratives are a **closed class** in both target languages, so
unlike verb forms this list does not grow with every new example — which is
precisely why it is safe to hard-code where the others were not.

Every record is stamped `non_authoritative`, and a test asserts that no
production path outside this package consumes it.

Contract version: `memorist.text.referential.v2`.

## `TextEnvelope`

One typed, versioned, immutable, JSON-serializable **technical** view:

```python
from memcore.textsemantics import build_envelope

envelope = build_envelope(raw)
envelope.sentences                     # spans on explicit marks, exact offsets
envelope.blocks                        # prose vs fenced code
envelope.tokens, envelope.phrases      # boundaries and written identifiers
envelope.context_dependency_hints      # non-authoritative routing signal
envelope.requires_conversation_context # how much history to send the model
envelope.warnings                      # what was seen and declined
envelope.as_json()                     # audit-safe: offsets and codes, no raw text
```

It carries **no** propositions, clause kinds, instruction/statement labels,
referents, or antecedent candidates, and a test asserts their absence. Those are
the model's output, checked against this envelope by
`validate_semantic_analysis`.

Same raw text plus same `contract_version` gives byte-identical output, because
everything reached from `build_envelope` is pure. `as_dict()` carries offsets,
hashes, counts, and reason codes — never the text itself, so an audit payload
cannot leak a credential that appeared in the message.

The contract version rides the existing candidate metadata payload as
`text_semantics_contract_version`. Nothing here needs a schema migration.

Contract version: `memorist.text.envelope.v3`.

## Validating a model's analysis

The deterministic responsibility that replaced the rule engine:

```python
from memcore.textsemantics import validate_semantic_analysis

report = validate_semantic_analysis(raw, model_output)
report.accepted_unit_ids     # admissible units
report.rejections            # what was thrown out, and why
report.fallback              # abstain / retain_raw_only when nothing survived
```

Spans must lie inside the text, quoted evidence must be byte-identical to its
slice, units must not overlap, and a resolved reference must target a unit the
model itself proposed. Nothing is repaired by guessing.

Full specification: [Semantic analysis contract](semantic-analysis-contract.md).

## API

```python
from memcore.textsemantics import (
    normalize_text, normalize_with_mapping, tokenize,
    normalized_span_for_raw_span, raw_span_for_normalized_span,
    contains_token, contains_phrase, find_token, find_phrase,
    find_all_phrases, identifier_phrases,
    segment_sentences, detect_context_dependency,
    build_envelope, TextEnvelope,
    validate_semantic_analysis, ValidationReport, SemanticFallback,
    extract_polarity, Polarity, Lexicon, scan_blocks,
)
```

`identifier_phrases` recovers what tokenization split: `GPT-5.4` and
`MEMORIST_MEMORY_EXTRACTION_API_KEY` come back as single spans. The written
form is recovered as a span rather than by weakening the token boundaries that
keep `token` out of `tokenizer`.

`Lexicon` is the token-aware replacement for an alternation regex and keeps the
same `search(...)` shape call sites already used.

Two patterns stay regexes on purpose: `FORGET_DIRECTIVE` and
`RESOURCE_CONTEXT` encode structure and ordering — an imperative at the start
of a sentence, a URL or path shape — rather than a word list.
