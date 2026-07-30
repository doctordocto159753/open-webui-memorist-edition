# Walkthrough پردازش حافظه در موتور مرکزی

این سند یک turn واقعیِ معماری را از لحظه‌ی ورود پیام کاربر تا ثبت پاسخ
دستیار، استخراج حافظه، تثبیت نسخه‌ی حافظه و استفاده‌ی دوباره از آن دنبال
می‌کند. ترتیب زیر از مسیرهای اجرایی موجود در کد استخراج شده است؛ این سند
طرح آینده یا خلاصه‌ی مفهومیِ مستقل از implementation نیست.

نسخه‌ی مستندشده: `0.2.0-beta.3`، storage schema `25`، semantic candidate
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
  -> Jakobson v3
  -> persisted route
  -> persisted gate
  -> bounded semantic context
  -> semantic candidate analysis v1
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
2. یک retrieval plan محدود به session/project/workspace می‌سازد؛
3. candidateها را از منابع canonical و projectionهای مجاز تولید می‌کند؛
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

### 8. Jakobson v3، route و gate

prompt `memorist.jakobson_sentence_analysis` نسخه‌ی `3.0` برای unitها اجرا
می‌شود. اجرای remote قبل از I/O در `processing_provider_attempts` reserve و
بعد از پاسخ finalize می‌شود. نتیجه‌ی معتبر در
`prompt_execution_runs`/`processing_stage_runs` و annotationهای Jakobson ثبت
می‌شود.

پس از آن deterministic code:

1. annotationها را به routeهای canonical نگاشت می‌کند؛
2. route و status را persist می‌کند؛
3. gate را محاسبه و persist می‌کند؛
4. analysisهای legacy را فقط به‌عنوان داده‌ی کمکی نگه می‌دارد.

Gate همیشه قبل از semantic candidate و candidate persistence است.
`discard` و `retain_raw_only` provider semantic را صدا نمی‌زنند و candidate
نمی‌سازند. `manual_review` می‌تواند پوشش audit داشته باشد ولی candidate
خودکار ندارد.

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

`SemanticCandidatePlanningService` تنها orchestration مشترک است. برای gateهای
مجاز prompt `memorist.semantic_candidate_analysis` نسخه‌ی `1.0` را یک بار روی
کل پیام اجرا می‌کند:

```text
parse
-> strict Pydantic contract
-> semantic binding validation
-> exact WP01 evidence validation
-> at most one repair
-> contract-valid abstention fallback
```

مدل فقط semantic unit، proposition، reference/relation، durability، polarity
و epistemic status پیشنهاد می‌کند. route، gate، privacy، provenance،
disposition، UUID و persistence متعلق به deterministic code هستند.

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

## Lite و Full کجا متفاوت‌اند؟

| مرحله | Lite | Full |
| --- | --- | --- |
| canonical ledger | SQLite + serialized write actor | PostgreSQL transactions |
| worker adapter | `MemoryWorkerPipeline` | `PostgresMemoryWorkerPipeline` |
| semantic orchestration | `SemanticCandidatePlanningService` مشترک | همان service |
| coverage/candidate replay | SQLite repository | PostgreSQL repository/locks |
| graph | اختیاری/غیر canonical | FalkorDB از outbox، غیر canonical |
| semantic decision | یکسان | یکسان |

## invariantsی که این sequence حفظ می‌کند

- Memory Off پیش از capture و recall متوقف می‌کند.
- gate پیش از هر candidate است و adapter پیش از persistence آن را دوباره
  می‌خواند.
- context بین session، user، workspace یا project نشت نمی‌کند.
- assistant content بدون ratification صریح user authority نمی‌گیرد.
- ambiguity حل‌نشده، حل‌نشده باقی می‌ماند.
- raw prompt با attachment بازنویسی نمی‌شود.
- provider output بدون strict/evidence validation به state راه ندارد.
- candidate و evidence به exact raw span متصل‌اند.
- restart و replay candidate تکراری نمی‌سازند.
- PostgreSQL/SQLite authority هستند؛ embedding و FalkorDB projection هستند.

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
