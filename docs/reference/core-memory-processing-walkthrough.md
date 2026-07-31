# Walkthrough پردازش حافظه در موتور مرکزی

این سند یک turn واقعیِ معماری را از لحظه‌ی ورود پیام کاربر تا ثبت پاسخ
دستیار، استخراج حافظه، تثبیت نسخه‌ی حافظه و استفاده‌ی دوباره از آن دنبال
می‌کند. ترتیب زیر از مسیرهای اجرایی موجود در کد استخراج شده است؛ این سند
طرح آینده یا خلاصه‌ی مفهومیِ مستقل از implementation نیست.

نسخه‌ی مستندشده: `0.2.0-beta.3`، storage schema `27`، semantic candidate
contract `1.0`.

## مثال مکالمه

فرض می‌کنیم Memory On است و کاربر در یک session معتبر می‌نویسد:

> برای گزارش‌های WP02 از قالب RFC استفاده کن و پاسخ‌ها را فارسی بنویس.

مدل اصلی Open WebUI پاسخ می‌دهد:

> حتماً. گزارش‌های WP02 را با ساختار RFC و به زبان فارسی آماده می‌کنم.

Memorist مالک مدل اصلی یا پاسخ بالا نیست. مدل اصلی را Open WebUI اجرا می‌کند؛
Memorist پیش از آن context حافظه را آماده می‌کند و پس از آن پیام‌های کاربر و
دستیار را به‌عنوان evidence مستقل ثبت و پردازش می‌کند.

## نمای کلی دو چرخه

```text
چرخه‌ی recall، هم‌زمان با inlet و پیش از مدل اصلی

user prompt
  -> turn policy
  -> session resolution
  -> immutable user capture
  -> scoped retrieval
  -> bounded Memory Context Attachment
  -> Open WebUI main model

چرخه‌ی learning، پس از capture و در worker

captured message
  -> TextEnvelope + text units
  -> structural envelope + exact block/span metadata
  -> whole-message semantic candidate analysis v1
  -> Message semantics ledger
  -> optional Jakobson/route/gate compatibility annotations
  -> strict + evidence validation
  -> deterministic coverage plan
  -> proposal/candidate persistence
  -> consolidation
  -> memory version + rebuildable projections
```

چرخه‌ی اول فقط حافظه‌های موجود را برای همین درخواست بازیابی می‌کند. حافظه‌ی
جدید حاصل از prompt جاری، پس از اجرای worker و consolidation برای turnهای
بعدی قابل retrieval است؛ در همان preflight که پیش از پاسخ مدل اجرا می‌شود
نمی‌تواند به گذشته برگردد.

## توالی دقیق یک turn

### 1. Filter ورودی را پاک‌سازی و policy را حل می‌کند

`Filter.inlet` در
`open-webui-integration/memorist/filter/memorist_memory_filter.py` ابتدا هر
`memorist_context` قدیمی را از body حذف می‌کند، actor/workspace مورد اعتماد
host را می‌خواند و turn policy را از Core می‌گیرد.

- `private`: هیچ session، capture، retrieval یا attachment ساخته نمی‌شود.
- `no_recall`: capture مجاز است ولی retrieval و attachment اجرا نمی‌شوند.
- workflow کامل: capture و recall طبق policy ادامه پیدا می‌کنند.

Memory On/Off فقط metadata رابط کاربری نیست. Core دوباره actor، workspace،
session و turn contract را کنترل می‌کند؛ بنابراین body دستکاری‌شده نمی‌تواند
policy ضعیف‌تری تحمیل کند.

### 2. session و پیام کاربر به‌صورت idempotent ثبت می‌شوند

Filter ابتدا session را resolve می‌کند و سپس
`POST /memcore/openwebui/messages/capture` را با role=`user` صدا می‌زند.
مسیر `capture_message` در
`memorist-core/src/memcore/api/routes_openwebui.py`:

1. actor و workspace را با session تطبیق می‌دهد؛
2. policy را دوباره resolve می‌کند؛
3. محتوای اصلی را بدون الصاق memory context در `messages` و نسخه‌ی canonical
   آن ثبت می‌کند؛
4. capture/session event و turn contract را ثبت می‌کند؛
5. jobهای idempotent پردازش را queue می‌کند.

در Lite نوشتن از `WriteGateway` و SQLite write actor عبور می‌کند. در Full همان
عملیات در transaction PostgreSQL انجام و jobهای `text_unitization` و
`memory_extraction` درج می‌شوند.

### 3. preflight فقط حافظه‌های قبلیِ مجاز را بازیابی می‌کند

بعد از capture پیام کاربر و قبل از مدل اصلی، Filter
`POST /memcore/preflight` را فراخوانی می‌کند. `PreflightService.run`:

1. budget را از mode، سقف context مدل، conversation اخیر و حاشیه‌ی completion
   محاسبه می‌کند؛
2. مدل preflight یک query understanding محدود شامل intent، topic، entity،
   process و stage/ordinal پیشنهاد می‌کند؛
3. کد محلی همان plan را با scope و policy اجباری روی memory versions و Message
   evidence و projectionهای مجاز اجرا می‌کند؛
4. scoring قطعی، reranking امن و selection/abstention را اجرا می‌کند؛
5. رخدادهای `retrieval_started`، `retrieval_completed` و نتیجه را ثبت می‌کند.

`RetrievalRunner` plan، queryها، candidateها، score trace و selection نهایی را
در ledger بازیابی نگه می‌دارد. projectionهایی مثل embedding یا FalkorDB
می‌توانند candidate تولید کنند، اما authority حافظه همچنان PostgreSQL/SQLite
و `memory_versions` است.

اگر حافظه‌ی کافی وجود نداشته باشد، budget صفر شود، timeout رخ دهد یا preflight
fail-open کند، مدل اصلی بدون attachment ادامه می‌دهد.

### 4. attachment به prompt کاربر چسبانده نمی‌شود

`AttachmentBuilder` آیتم‌های منتخب را در `memory_context_attachments` ثبت
می‌کند و renderer متن را با budget، provenance، escaping و کنترل
instruction-like content می‌سازد. Filter آن را به‌صورت یک پیام جداگانه درج
می‌کند:

```json
{
  "role": "system",
  "name": "memorist_context",
  "content": "<memory_context_attachment>...</memory_context_attachment>"
}
```

این role شکل transport برای سازگاری با API چت است، نه ارتقای authority:
metadata صریحاً `memorist_context_untrusted=true` دارد و محتوای attachment
داده‌ی غیرقابل‌اعتماد است. متن prompt کاربر دست‌نخورده باقی می‌ماند.

اگر attachment review فعال باشد، attachment تا approval ارسال نمی‌شود. پس از
delivery، lifecycle و delivery attribution ثبت می‌شوند تا بعداً مشخص باشد
کدام پاسخ دقیقاً کدام حافظه را دیده است.

### 5. مدل اصلی پاسخ را تولید می‌کند

Open WebUI body شامل پیام اصلی کاربر و، در صورت وجود، context جداگانه را به
مدل انتخاب‌شده‌ی chat می‌فرستد. نقش
`main_chat_observed` در Memorist فقط metadata مدل را برای budget و trace
مشاهده می‌کند. هیچ profile در Model Control Plane جای مدل اصلی را نمی‌گیرد.

در مثال، پاسخ فارسی مدل در همین مرحله ایجاد می‌شود. این پاسخ هنوز memory
نیست و حتی user-authoritative evidence هم نیست.

### 6. outlet پاسخ دستیار را ثبت و به درخواست پیوند می‌دهد

`Filter.outlet` متن پاسخ و شناسه‌ی provider را به
`POST /memcore/assistant-response/completed` می‌فرستد. این endpoint:

- content hash را برای replay/dedup محاسبه می‌کند؛
- policy و attachment delivery را دوباره بررسی می‌کند؛
- پیام role=`assistant` را با creator type=`memory_augmented_model` ثبت می‌کند؛
- در `assistant_response_links` آن را به input message، attachment و provider
  response پیوند می‌دهد؛
- job `memory_extraction` را queue می‌کند؛
- attachment را به حالت `used_for_response` می‌برد و attribution را ثبت
  می‌کند.

ارسال تکراری همان completion، پیام یا job دوم نمی‌سازد. regeneration نیز
هویت و state مستقل و transaction-safe دارد.

## پردازش worker برای هر پیام

پیام کاربر و پیام دستیار هر کدام job مستقل دارند. هر دو از یک orchestration
service مشترک عبور می‌کنند؛ تفاوت Lite و Full در adapter و persistence است،
نه در تصمیم semantic.

### 7. snapshot، TextEnvelope و text unitها

`MemoryJobWorkerService` ابتدا `prepare_message` و سپس `process_message` را
اجرا می‌کند. snapshot شامل content hash، processing identity، profile
fingerprint و contract hash است. تغییر authority میان provider call و commit
باعث رد نتیجه می‌شود.

`TextEnvelope` متن raw را بدون تغییر نگه می‌دارد و material token/spanها،
dependency hintها و hash را می‌سازد. unitizer سپس `text_units` با offset دقیق
می‌سازد. Persian، mixed-direction text و code fence براساس offset متن raw
اعتبارسنجی می‌شوند؛ downstream مجاز نیست evidence را از متن normalize‌شده
بازسازی کند.

### 8. whole-message semantics و annotationهای سازگاری

فراخوانی authoritative روی کل پیام با
`memorist.semantic_candidate_analysis` انجام می‌شود. heading، list، table،
code fence، fragment و متن فارسی/انگلیسی conventional-sentence gate نیستند.
اجرای remote قبل از I/O در `processing_provider_attempts` reserve و بعد از
پاسخ finalize می‌شود؛ timeout و failure latency واقعی دارند و فقط یک repair
ساختاری مجاز است.

مدل در یک نتیجه‌ی ساختاری پیشنهاد می‌کند:

1. intent، primary/secondary topic و summary سه‌بخشی؛
2. چند Message category و حداکثر پنج concept tag؛
3. entity، process، stage/ordinal، epistemic/temporal status؛
4. semantic unitهای exact-span با memory kind و lifecycle proposal.

Jakobson v3، route و gate قدیمی هنوز اجرا و persist می‌شوند تا audit و replay
تاریخی شکسته نشود، اما authority semantic نیستند و `discard`،
`retain_raw_only` یا `manual_review` معمولی دیگر فراخوانی کل‌پیام را veto
نمی‌کند. فقط Memory Off، source/scope نامعتبر، deleted/redacted/quarantined و
privacy/secret ceiling قبل از provider fail-closed هستند.

### 9. context محدود semantic از history canonical ساخته می‌شود

`BoundedContextResolver` فقط unitهای قبلی را از همان user، session،
workspace و project می‌پذیرد. baseline دو unit است و فقط در صورت dependency
hint غیر authoritative تا شش unit افزایش می‌یابد.

موارد زیر حذف می‌شوند:

- پیام جاری یا turnهای آینده؛
- system/tool role؛
- hidden، deleted یا redacted content؛
- نسخه یا span نامعتبر؛
- sensitive context؛
- هر رکورد cross-session/cross-user/cross-workspace/project.

متن assistant با ceiling=`assistant_claim` وارد می‌شود. در مثال، پاسخ
«حتماً...» صرفاً lineage دستیار است؛ تنها reference یکتای resolved همراه با
relation صریح `ratifies` یا `corrects` در پیام جاریِ بعدی کاربر می‌تواند
authority آن را ارتقا دهد.

### 10. یک semantic call، validation و coverage کامل

`SemanticCandidatePlanningService` orchestration مشترک است و prompt را یک بار
روی کل پیام اجرا می‌کند:

```text
parse
-> strict Pydantic contract
-> semantic binding validation
-> exact WP01 evidence validation
-> at most one repair
-> contract-valid abstention fallback
```

مدل semantic message metadata و unit، proposition، reference/relation،
durability، polarity، memory kind و lifecycle status پیشنهاد می‌کند. privacy،
scope، provenance، UUID، lifecycle validation و persistence متعلق به کد محلی
هستند. «دستور را اجرا نکن» به معنی «دستور را تحلیل نکن» نیست.

`CoveragePlanner` برای هر unit پذیرفته‌شده دقیقاً یک item و برای material
پوشش‌داده‌نشده یک `unsupported/uncovered_material` item می‌سازد. reference
مبهم `unresolved_reference` باقی می‌ماند؛ planner مجاز نیست referent را حدس
بزند. فقط disposition=`durable_candidate` دقیقاً یک `CandidateProposal`
می‌سازد.

برای prompt مثال، اگر contract معتبر، route/gate مجاز و privacy/provenance
کامل باشند، دستور durable کاربر می‌تواند proposal بسازد. پاسخ مشابه دستیار
خودبه‌خود به user fact تبدیل نمی‌شود و معمولاً زیر ceiling دستیار باقی
می‌ماند.

### 11. identity و persistence قابل replay

`proposal_id` با UUIDv5 از material canonical ساخته می‌شود و همان
`candidate_uuid` است. timestamp، provider metadata، execution UUID و ID
انتخابی مدل در identity نیستند.

قبل از candidate:

1. snapshot و authority دوباره revalidate می‌شوند؛
2. coverage run/items به‌شکل idempotent persist می‌شوند؛
3. proposal reservation ثبت می‌شود؛
4. candidate و exact evidence و link در یک transaction ایجاد می‌شوند.

جدول‌های `semantic_coverage_runs`، `semantic_coverage_items` و
`semantic_candidate_links` audit metadata بدون raw evidence نگه می‌دارند.
محتوا فقط در ledgerهای canonical پیام/evidence است. replay با همان payload
existing state را برمی‌گرداند؛ hash متفاوت deterministic identity conflict
است. بنابراین restart یا تحویل دوباره‌ی job candidate تکراری نمی‌سازد.

### 11.1. Message به‌عنوان node canonical قابل بازیابی

هر خروجی semantic معتبر در `message_semantic_analyses` به `messages`، آخرین
`message_versions`، processing run و prompt/stage execution وصل می‌شود. محتوای
خام دوباره در audit کپی نمی‌شود. داده‌های normalized در این جداول هستند:

```text
message_semantic_categories    multi-select base category
canonical_concepts             concept canonical identity
concept_aliases                Persian/English/acronym aliases
message_concept_tags           at most five tags plus confidence/span
message_semantic_units         exact span, kind, epistemic/lifecycle state
message_entity_references      scoped entity links
message_process_references     process + stage label/ordinal
semantic_job_outcomes          semantic meaning of generic job success
model_retrieval_plans          audited Lite/Full preflight model plan
```

نمونه‌ی artifact:

```json
{
  "one_line_summary": "project proposal > DPQTP > multipath post-quantum transport",
  "message_categories": ["ProjectArtifact", "Hypothesis", "Question"],
  "epistemic_status": "proposed",
  "concept_tags": ["DPQTP", "post-quantum transport", "multipath routing"],
  "semantic_outcome": "succeeded_with_candidates_only"
}
```

Job canonical برای compatibility همچنان `succeeded` می‌شود، ولی diagnostics
میان `succeeded_with_memory`، `succeeded_with_candidates_only`،
`succeeded_no_candidate`، `succeeded_with_abstention`،
`succeeded_with_partial_semantics` و `succeeded_with_failed_open_stage` فرق
می‌گذارد.

### 12. candidate stages و consolidation

candidateهای مجاز از policyهای privacy و high-confidence عبور می‌کنند.
`MemoryConsolidator.consolidate_candidate` evidence را الزامی می‌داند،
canonical key و scope را از session می‌سازد و یکی از تصمیم‌های reject،
manual-review، noop، create، reinforce، supersede یا contradict را ثبت می‌کند.

- create: `memories` و نخستین `memory_versions` را می‌سازد؛
- reinforce: evidence جدید را بدون بازنویسی تاریخ متصل می‌کند؛
- supersede: نسخه‌ی قبلی را می‌بندد و نسخه‌ی جدید می‌سازد؛
- contradict/manual-review: تعارض را پنهان نمی‌کند.

در Lite، SQLite ledger canonical است و FTS/embedding/graph runnerهای مجاز از
رکورد canonical تغذیه می‌شوند. در Full، PostgreSQL canonical است و
`embedding_outbox` و `graph_projection_outbox` projectionهای rebuildable را
به‌روزرسانی می‌کنند؛ FalkorDB هرگز source of truth نیست.

## بازیابی در turn بعدی

اگر کاربر بعداً بپرسد:

> گزارش WP02 را آماده کن.

inlet جدید ابتدا آن پیام را capture می‌کند. preflight این بار memory version
تثبیت‌شده‌ی ترجیح/قید را در scope مجاز پیدا می‌کند، score و select می‌کند و
آن را در attachment جداگانه به مدل می‌دهد. UI با «Memory used» منابعی را که
واقعاً delivery شده‌اند از audit rows نمایش می‌دهد؛ UI از متن model یا
metadata مرورگر برای ادعای delivery استفاده نمی‌کند.

### مثال SCF مرحله‌ی سوم

برای query زیر:

> در خصوص مرحله سوم SCF چه تدابیری باید اندیشید؟

preflight model می‌تواند plan زیر را پیشنهاد دهد:

```json
{
  "intent": "problem-solving/mitigation",
  "primary_topic": "scf",
  "process_label": "supply continuity framework",
  "stage_ordinal": 3,
  "relation_expansion_hints": ["need", "constraint", "decision", "problem"]
}
```

`MessageEvidenceRetriever` عبارت‌ها را تفسیر نمی‌کند؛ alias canonical `SCF`
را به concept/process node و ordinal `3` را به `ProcessStage` ثبت‌شده تطبیق
می‌دهد، scope user/workspace/project را fail-closed اعمال می‌کند و messageهای
مرتبط را rank می‌کند. تست `tests/test_message_first_retrieval.py` ثابت می‌کند
که evidence انگلیسی «stabilization phase C» بدون وجود عبارت فارسی query و
بدون canonical MemoryClaim در attachment قرار می‌گیرد. در Full همان topology
با edges `MESSAGE_IN_SESSION`، `MESSAGE_IN_PROJECT`, `HAS_MESSAGE_TYPE`,
`HAS_INTENT`, `HAS_PRIMARY_TOPIC`, `HAS_SECONDARY_TOPIC`, `HAS_CONCEPT_TAG`,
`MENTIONS_ENTITY`, `REFERS_TO_PROCESS`, `REFERS_TO_STAGE`, `DERIVED_CLAIM` و
`EVIDENCES` در FalkorDB projection می‌شود؛ graph-down به canonical query برمی‌گردد.

### سه نمونه‌ی authority

Explicit memory: عبارت «این را به‌عنوان تصمیم پایدار به خاطر بسپار» باید
`explicit_memory_request=true`، category=`Decision` و durability بالا پیشنهاد
کند، اما privacy/scope/write validation همچنان محلی است.

Implicit artifact: proposal بدون «remember» به‌صورت
`ProjectArtifact`/`Hypothesis` و epistemic=`proposed` قابل بازیابی است؛ به حقیقت
عینی تبدیل نمی‌شود.

Instruction/preference: «برای معماری فنی و مرحله‌ای جواب بده» توسط مدل
پردازشی اجرا نمی‌شود، اما می‌تواند `Instruction` و `Preference` scoped باشد.

Assistant ratification: assistant message با `assistant_claim` ذخیره و قابل
retrieval است. فقط پیام user بعدی با relation صریح `ratifies`/`corrects` می‌تواند
claim مشخص را با lineage دستیار promote کند.

## Lite و Full کجا متفاوت‌اند؟

| مرحله | Lite | Full |
| --- | --- | --- |
| canonical ledger | SQLite + serialized write actor | PostgreSQL transactions |
| worker adapter | `MemoryWorkerPipeline` | `PostgresMemoryWorkerPipeline` |
| semantic orchestration | `SemanticCandidatePlanningService` مشترک | همان service |
| coverage/candidate replay | SQLite repository | PostgreSQL repository/locks |
| graph | canonical relation query روی SQLite؛ graph اختیاری | FalkorDB از outbox، غیر canonical |
| semantic decision | یکسان | یکسان |

## invariantsی که این sequence حفظ می‌کند

- Memory Off پیش از capture و recall متوقف می‌کند.
- مدل قبل از legacy promotion annotation پیام عادی را می‌بیند؛ privacy/scope
  پیش از هر provider و persistence دوباره اعتبارسنجی می‌شوند.
- context بین session، user، workspace یا project نشت نمی‌کند.
- assistant content بدون ratification صریح user authority نمی‌گیرد.
- ambiguity حل‌نشده، حل‌نشده باقی می‌ماند.
- raw prompt با attachment بازنویسی نمی‌شود.
- provider output بدون strict/evidence validation به state راه ندارد.
- candidate و evidence به exact raw span متصل‌اند.
- restart و replay candidate تکراری نمی‌سازند.
- PostgreSQL/SQLite authority هستند؛ embedding و FalkorDB projection هستند.

## timeout، token و failure contract

- `MEMORIST_PREFLIGHT_TIMEOUT_MS=60000` به call واقعی provider می‌رسد و timeout
  همچنان chat را fail-open نگه می‌دارد.
- processing role override، سپس
  `MEMORIST_PROCESSING_NODE_DEFAULT_TIMEOUT_MS=120000` و سپس built-in default
  اعمال می‌شود؛ default فعال `8000` باقی نمانده است.
- input/context settings برابر 100000 token هستند و helper کمینه‌سازی
  certified/provider/role limit وجود دارد؛ اتصال این budget به همه‌ی provider
  requestها هنوز کامل نیست و output budgets صرفاً جداگانه پیکربندی می‌شوند.
- attempt success، parse/schema failure، repair، timeout و transport failure
  elapsed latency واقعی ثبت می‌کنند.
- audit record هر prompt execution پیش از redaction اعتبارسنجی می‌شود. redaction
  فقط مقدارهای رشته‌ای/ساختاری با نام secret-مانند را حذف می‌کند و مقدارهای عددی
  قراردادی مانند `items[].estimated_tokens`، `max_input_tokens` و `output_tokens`
  را دست‌نخورده نگه می‌دارد، بنابراین replay همان contract را برآورده می‌کند.

## محدودیت‌های صادقانه‌ی این نسخه

این pass مدل semantic پیام، Message graph، SCF retrieval و evidence fallback را
در Lite و Full production-integrated کرده است. Full همان query understanding
مدل را audit می‌کند، `model_retrieval_plans` را می‌نویسد و Message Evidence را
با provenance نوع `message_semantics` مصرف می‌کند. consolidation موجود هنوز برای همه‌ی عملیات
`ADD/REINFORCE/UPDATE/SUPERSEDE/CONTRADICT/RETRACT` یک مقایسه‌ی remote مدل‌محور
جداگانه اجرا نمی‌کند؛ policy/version history فعلی حفظ شده است. provider quota
scheduler مشترک endpoint+credential+model نیز هنوز به‌طور کامل بین processها
توزیع‌شده نیست. بنابراین این دو مورد قابلیت پیاده‌شده ادعا نمی‌شوند.

## مسیرهای کد مرجع

- Open WebUI inlet/outlet:
  `open-webui-integration/memorist/filter/memorist_memory_filter.py`
- capture و Full job enqueue:
  `memorist-core/src/memcore/api/routes_openwebui.py`
- preflight و assistant completion:
  `memorist-core/src/memcore/api/routes_retrieval.py`
- retrieval:
  `memorist-core/src/memcore/preflight.py` و
  `memorist-core/src/memcore/retrieval/runner.py`
- Lite/Full worker:
  `memorist-core/src/memcore/memory_worker/pipeline.py` و
  `memorist-core/src/memcore/memory_worker/postgres/pipeline.py`
- shared semantic orchestration:
  `memorist-core/src/memcore/memory_worker/semantic/orchestration.py`
- bounded context:
  `memorist-core/src/memcore/memory_worker/semantic/bounded_context.py`
- coverage/identity:
  `memorist-core/src/memcore/memory_worker/semantic/coverage/`
- consolidation:
  `memorist-core/src/memcore/memory_worker/consolidation/consolidator.py`

قرارداد فیلدها، authority و identity در
[Semantic candidate authority](semantic-candidate-authority.md) و شکل کلی
ماشین در [The Memory Machine](../MEMORY_MACHINE.md) نگه‌داری می‌شود.
