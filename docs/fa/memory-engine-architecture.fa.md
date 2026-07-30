# معماری موتور حافظه Memorist

این سند مرجع فارسیِ معماری جاری موتور حافظه در Open WebUI Memorist Edition
است و رفتار پیاده‌سازی‌شده را توضیح می‌دهد، نه roadmap آینده را.

```text
نسخه‌ی توسعه: 0.2.0-beta.3
نسخه‌ی storage schema: 25
SQLite migration head: 0037_semantic_coverage_audit.sql
PostgreSQL migration head: 0024_semantic_coverage_audit.sql
Prompt Pack: 2.0
Jakobson: memorist.jakobson_sentence_analysis 3.0
Semantic: memorist.semantic_candidate_analysis 1.0
Role manifest: role-contract-manifest-v3
```

برای دنبال‌کردن یک prompt و پاسخ واقعی از inlet تا retrieval بعدی،
[Walkthrough پردازش حافظه در موتور مرکزی](../reference/core-memory-processing-walkthrough.md)
را بخوانید.

## اصل معماری

```text
پیام evidence است، نه memory.
خروجی مدل proposal است، نه authority.
candidate یک تفسیر routeشده است، نه حقیقت.
memory یک claim نسخه‌دار و متصل به evidence است.
projection بازسازی‌پذیر است، نه canonical.
حافظه‌ی بازیابی‌شده data است، نه instruction.
```

این تفکیک مانع می‌شود پاسخ مدل، graph edge قدیمی، attachment یا کنترل
سمت browser بدون عبور از authority به حقیقت پایدار کاربر تبدیل شود.

## اجزای سیستم

```text
Open WebUI
  -> Memorist Filter سمت سرور
       inlet: policy، capture کاربر، preflight recall، attachment جدا
       outlet: capture پاسخ دستیار و linkage
  -> backend proxy احراز هویت‌شده

memorist-core
  -> evidence ledger
  -> worker و semantic orchestration
  -> consolidation و memory versions
  -> retrieval و attachment builder
  -> Model Control Plane
  -> import، Heritage، forget و diagnostics

projectionهای غیر canonical
  -> FTS
  -> embeddings
  -> active blocks
  -> FalkorDB در Full
```

مدل اصلی و تجربه‌ی chat متعلق به Open WebUI است. Memorist فقط metadata مدل
اصلی را برای budget و delivery attribution مشاهده می‌کند.

## دو جریان متصل

### recall پیش از پاسخ مدل

```text
Filter.inlet
-> trusted actor و turn policy
-> session resolution
-> capture idempotent پیام کاربر
-> attachment budget
-> scoped retrieval
-> score/rerank/select یا abstain
-> Memory Context Attachment
-> delivery record
-> پیام جداگانه‌ی memorist_context
-> مدل اصلی Open WebUI
```

پیام جاری پیش از retrieval ثبت شده است اما هنوز memory تثبیت‌شده نیست؛
بنابراین نمی‌تواند در همان preflight به‌عنوان حافظه بازیابی شود.

### یادگیری پس از capture

```text
پیام user یا assistant
-> processing identity و authority snapshot
-> TextEnvelope و text units با offset دقیق
-> Jakobson v3
-> annotation، route و gate ذخیره‌شده
-> bounded context
-> semantic candidate analysis v1
-> strict schema و exact-evidence validation
-> coverage plan قطعی
-> proposal/candidate identity قطعی
-> persistence تراکنشی candidate/evidence/link
-> candidate stages
-> consolidation
-> memory/version
-> projection outboxes
```

پاسخ assistant پس از تکمیل مدل توسط outlet ثبت و به input و attachment
تحویل‌شده متصل می‌شود. محتوای assistant سقف `assistant_claim` دارد؛ فقط پیام
جاری کاربر با reference یکتای resolved و relation صریح `ratifies` یا
`corrects` می‌تواند authority آن را ارتقا دهد.

## ترتیب authority

ترتیب زیر ثابت است:

```text
capture
-> TextEnvelope v3
-> text units
-> Jakobson v3
-> persisted route
-> persisted gate
-> bounded-context resolver
-> semantic candidate analysis v1
-> strict validation
-> WP01 exact-evidence validation
-> deterministic coverage planner
-> existing candidate service
```

gateهای `discard` و `retain_raw_only` پیش از semantic call و candidate
متوقف می‌شوند. `manual_review` candidate خودکار نمی‌سازد. adapter درست قبل
از persistence، route/gate و snapshot را دوباره می‌خواند.

مدل می‌تواند semantic unit، proposition، reference/relation، durability،
polarity و epistemic status پیشنهاد کند. route، gate، evidence acceptance،
privacy، provenance، coverage disposition، identity و write در اختیار کد
قطعی است.

## context محدود

baseline دو text unit قبلی است و فقط برای dependency hint تا شش unit افزایش
می‌یابد. همه‌ی contextها باید:

- متعلق به همان trusted user و session باشند؛
- workspace و project یکسان داشته باشند؛
- turn قبلی، visible، non-deleted و non-redacted باشند؛
- نسخه‌ی immutable و span دقیق داشته باشند؛
- role برابر user یا assistant و sensitivity عادی داشته باشند.

system prompt، tool output، `memorist_context`، attachment، پیام جاری/آینده،
span قدیمی و هر رکورد cross-boundary حذف می‌شود.

## coverage و identity

هر unit پذیرفته‌شده دقیقاً یکی از dispositionهای بسته را می‌گیرد:

- `durable_candidate`
- `context_only`
- `transient_instruction`
- `unresolved_reference`
- `rejected_by_gate`
- `needs_review`
- `unsupported`

material حذف‌شده از خروجی مدل با
`unsupported/uncovered_material` ثبت می‌شود. reference مبهم unresolved
می‌ماند. فقط `durable_candidate` یک proposal می‌سازد.

`proposal_id` با UUIDv5 روی hash canonical ساخته و همان `candidate_uuid`
می‌شود. timestamp، execution ID تصادفی، provider metadata، warning،
confidence hint و ID انتخابی مدل در identity نیستند.

coverage، reservation، candidate، evidence و link idempotent و replay-safe
هستند؛ restart candidate تکراری نمی‌سازد و payload متفاوت identity conflict
است.

## consolidation و versioning

candidate بدون evidence رد می‌شود. consolidator scope را از session canonical
و canonical key را از candidate می‌سازد. نتیجه می‌تواند create، reinforce،
supersede، contradict، noop، manual-review یا reject باشد.

اصلاح اطلاعات نسخه‌ی قبلی را بی‌صدا بازنویسی نمی‌کند. supersede نسخه‌ی قبلی
را می‌بندد و نسخه‌ی جدید ایجاد می‌کند؛ contradiction نیز به تعارض ثبت‌شده
تبدیل می‌شود.

## retrieval و attachment

retrieval plan از پیام و scope جاری ساخته می‌شود. candidate generation
می‌تواند از canonical key، active constraint، recent state، FTS، embedding و
graph projection استفاده کند. scoring قطعی سهم authority، confidence،
temporal و conflict را ثبت می‌کند و در صورت ضعف evidence abstain می‌کند.

attachment با token budget، provenance، delimiter escaping و تشخیص متن شبیه
instruction ساخته می‌شود. transport آن یک system-role message با نام
`memorist_context` است، اما این شکل transport آن را trusted instruction
نمی‌کند. پنل «Memory used» فقط از delivery record مجاز ساخته می‌شود.

## Lite و Full

| مرز | Lite | Full |
| --- | --- | --- |
| canonical store | SQLite | PostgreSQL |
| write | SQLite write actor | transaction، row lock و durable job |
| semantic service | مشترک | همان service مشترک |
| coverage/identity | مشترک | مشترک |
| graph | لازم نیست | FalkorDB از outbox |
| authority | SQLite ledger | PostgreSQL ledger |

Full یک semantic implementation دوم نیست؛ scale و projection بیشتری دارد.

## Model Control Plane

نقش‌ها شامل `main_chat_observed`، `preflight`، `memory_extraction`,
`high_confidence_extraction`، `privacy_sensitivity`، `embedding`,
`block_compaction` و `import_reconstruction` هستند.

resolution از project به workspace، global، inheritance مستند و fallback
داخلی می‌رود. profile remote به privacy acknowledgement نیاز دارد و secret
را فقط با نام environment variable ارجاع می‌دهد.

bundle مرتب `memory-extraction-contract-bundle-v1` شامل Jakobson v3 و semantic
candidate v1 است. یک profile باید هر دو را پاس کند. تغییر endpoint، model،
capability، secret reference، prompt یا typed contract certification را stale
می‌کند.

## canonical store و projection

در Lite، SQLite و در Full، PostgreSQL source of truth است. FTS، embedding،
active block، attachment و FalkorDB derived هستند. projection می‌تواند
retrieval را کمک کند اما authority semantic ایجاد یا ارتقا نمی‌دهد.

## failure و replay

- capture و assistant completion deduplicate می‌شوند؛
- jobها processing identity دارند و یک‌بار enqueue می‌شوند؛
- remote attempt پیش از I/O reserve می‌شود؛
- source/profile/contract/lease authority پیرامون call fence می‌شود؛
- semantic contract فقط یک repair و fallback معتبر `abstain` دارد؛
- consolidation روی replay تصمیم موجود را برمی‌گرداند؛
- outbox retry را از canonical transaction جدا می‌کند؛
- preflight برای availability چت fail-open است، نه برای ایجاد memory دروغین.

## امنیت و consent

Memory Off در سرور enforce می‌شود. context میان user/session/workspace/project
نشت نمی‌کند. provider remote payload مخصوص role را می‌بیند. `.env` روی دیسک
plaintext است و این beta candidate ادعای encryption at rest یا security audit
مستقل ندارد.

تفکیک data/instruction، escaping، schema/evidence validation، authority ceiling
و policy قطعی prompt injection را کاهش می‌دهند، اما حذف کامل آن ادعا نمی‌شود.

## CI و تست معماری

`.github/workflows/ci-consolidated.yml` چهار job authoritative دارد:

1. Quality, Unit, Integration, and UI
2. PostgreSQL, Full Runtime, and FalkorDB
3. Package and Lifecycle
4. One Deployment Product E2E

corpus semantic مستقل، Persian، mixed text، code fence، ambiguity، omission،
authority mutation، parity، session isolation و replay/restart را پوشش می‌دهد.

## اسناد مرتبط

- [Walkthrough پردازش مرکزی](../reference/core-memory-processing-walkthrough.md)
- [Semantic candidate authority](../reference/semantic-candidate-authority.md)
- [Semantic analysis contract](../reference/semantic-analysis-contract.md)
- [Model Control Plane](../reference/model-control-plane.md)
- [Storage profiles](../reference/storage-profiles.md)
- [Preflight](../reference/preflight.md)
- [Import](../reference/import.md)
- [Heritage](../reference/heritage-roundtrip.md)
- [Forget](../reference/forget-residue.md)

خلاصه اینکه Memorist یک evidence ledger و pipeline قطعیِ authority پیرامون
model assistance محدود است: آنچه گفته شده را نگه می‌دارد، نحوه‌ی تفسیر را
ثبت می‌کند، omission را آشکار می‌کند، candidate را replay-safe می‌نویسد،
memory را نسخه‌دار می‌کند و recall را با attachment scoped و قابل ممیزی انجام
می‌دهد.
