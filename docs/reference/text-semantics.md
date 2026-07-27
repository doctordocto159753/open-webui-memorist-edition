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

## Sentences and clauses

A sentence is often not the unit a claim lives in. This one carries two
unrelated instructions:

```
الان فقط خیلی کوتاه بهم توضیح بده و یادت باشه بعدا درباره اش صحبت کنیم.
```

Kept whole, "answer briefly" and "remember to return to this later" cannot be
told apart, which is how a real Full-mode trace ended up storing the fragment
and losing everything it referred to. `segment_sentences` and `segment_clauses`
give a caller somewhere smaller to attach a claim, a polarity, and a
referential marker.

This is **not** a parser. It is a small set of high-precision boundary rules:

| Boundary | Reason code |
| --- | --- |
| Sentence terminator followed by whitespace | `sentence_start` |
| `:` followed by whitespace | `colon_explanation` |
| `;` / `؛` | `clause_terminator` |
| Line break inside a sentence | `line_break` |
| `اما`, `ولی`, `بنابراین`, `however`, `therefore` | `contrastive_connective` |
| `و` / `and` after a clause-final verb | `coordinating_conjunction_after_verb` |
| `و` / `and` after a comma | `coordinating_conjunction_after_comma` |

A period glued to content on both sides (`GPT-5.4`, `example.com`, `1.2.3`) and
a short abbreviation list do not end a sentence.

### Conjunctions are guarded, and declined splits are reported

`و` joins clauses and noun phrases equally, so an unguarded split would shred
`سرعت و عملکرد و تناسب با برنامه نویسی` into fragments and fabricate
propositions the writer never made. A split therefore requires evidence: a
comma, or a preceding token in the closed `CLAUSE_FINAL_VERBS` lexicon.

That lexicon is a word list, in the same spirit as the negation lexicon — not a
stemmer, not a morphological analyser — and it is deliberately under-inclusive.
A miss produces an unsplit clause plus a `coordinating_conjunction_not_split`
warning, never a wrong split. English `and` rarely follows a verb, so in
practice it splits only after a comma; that under-splitting is visible in the
warnings rather than hidden.

Ambiguity is reported, never resolved by guessing. Other warnings:
`sentence_without_terminator`, `unclosed_code_fence`.

### Clause kinds

`statement`, `instruction`, `question`, `explanation`, `code`, `unknown`.
Instructions are identified by a closed imperative lexicon (`توضیح بده`,
`یادت باشه`, `explain`, `remember`, …). Separating them is what lets a consumer
keep "explain briefly" from being stored as a fact about the world.

Contract version: `memorist.text.segmentation.v1`.

## Referential markers

Expressions that cannot be understood on their own are marked, with the spans
that could be what they point at:

```
میخوام بیشتر درباره این مزیت بدونم بعدا.
```

`این مزیت` is marked `demonstrative_phrase`, `requires_context=True`, and
carries `حذف لایه WSL2.` among its `antecedent_candidate_spans`.

A determiner is grown onto the noun it heads (`این مزیت`) but not onto a verb
(`این هست`), which separates the two cases without a parser. `درباره اش`
matches whether written with a space or a ZWNJ, because the normalization
contract already treats ZWNJ as a boundary.

### What this deliberately does not do

- It never asserts **which** candidate is the referent.
- It never reaches across messages.
- It never creates a claim because a marker exists.
- Candidates are ordered nearest-first and are **not** ranked — a rank would be
  a resolution wearing a different name.
- Instruction clauses are never offered as antecedents.

There is no field on the contract that can express a chosen referent, and a
test asserts that. Ambiguous markers (`it`, `that`, bare `این`/`آن`) are still
reported — a missed dependency is worse than a flagged one when nothing here
creates a memory — but labelled `marker_ambiguous`.

Choosing a referent needs conversation state and the authority to be wrong in a
way that corrupts a stored memory. That belongs to the package that owns
resolution and candidate completeness.

Contract version: `memorist.text.referential.v1`.

## Unicode transport integrity is not normalization

```
Unicode transport integrity  ≠  linguistic normalization
```

A field trace showed Persian arriving as mojibake in a Windows PowerShell
diagnostic export while the database rows behind it were intact. The cause was
`subprocess.run(..., text=True)` with no explicit encoding, which decodes
captured output with `locale.getpreferredencoding()` — an OEM/ANSI code page on
a Windows console. The mojibake was then written faithfully into a UTF-8
artifact.

That is a transport bug, fixed at the boundary that decoded the bytes wrongly.
The diagnostic exporters now pin `encoding="utf-8"` on every captured child.

**The runtime never repairs mojibake.** A repair rule has to decide that some
bytes were meant to be Persian; when it guesses right nobody notices, and when
it guesses wrong it has silently rewritten the raw record an auditor relies on.
Spelling correction is the same class of error, which is why the user's
`Kubunto` is never turned into `Kubuntu`.

## `TextSemanticsResult`

One typed, versioned, immutable, JSON-serializable view of one piece of text:

```python
from memcore.textsemantics import analyze_text

result = analyze_text(raw)
result.clauses                    # ClauseSpan, with exact raw offsets
result.referential_markers        # unresolved, with candidate spans
result.polarity_cues              # per clause, not per message
result.warnings                   # what the rules declined to decide
result.as_json()                  # audit-safe: offsets and codes, no raw text
```

Same raw text plus same `contract_version` gives byte-identical output, because
everything reached from `analyze_text` is pure. `as_dict()` carries offsets,
hashes, counts, and reason codes — never the text itself, so an audit payload
cannot leak a credential that appeared in the message.

Polarity is computed **per clause**, so one negated clause cannot drag its
neighbours negative.

The contract version rides the existing candidate metadata payload as
`text_semantics_contract_version`. Clause and referential views are recomputable
from immutable raw text, so nothing here needs a schema migration.

Contract version: `memorist.text.semantics.v2`.

## API

```python
from memcore.textsemantics import (
    normalize_text, normalize_with_mapping, tokenize,
    normalized_span_for_raw_span, raw_span_for_normalized_span,
    contains_token, contains_phrase, find_token, find_phrase,
    find_all_phrases, identifier_phrases,
    segment_sentences, segment_clauses, detect_referential_markers,
    analyze_text, TextSemanticsResult,
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
