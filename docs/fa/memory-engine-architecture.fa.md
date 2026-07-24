# معماری موتور حافظه Memorist

این سند، توضیح معماری موتور حافظه در **Open WebUI Memorist Edition** است. تمرکز این README روی خود موتور حافظه است: فلسفه طراحی، لایه‌های پردازش، مدل داده، جریان‌های runtime، تکنیک‌های برنامه‌نویسی، سازوکارهای اعتماد/حریم خصوصی، تفاوت Lite و Full، و آنچه در این پروژه به‌عنوان ترکیب معماری متمایز ساخته شده است.

این سند جایگزین README اصلی پروژه نیست. README اصلی برای معرفی repo، نصب، تست و وضعیت release است. این فایل بهتر است با یکی از نام‌های زیر در پروژه قرار گیرد:

```text
docs/memory-engine-architecture.fa.md
README_MEMORY_ENGINE_ARCHITECTURE.fa.md
```

## وضعیت فعلی

وضعیت قابل ادعای فعلی:

```text
Version: 0.2.0-beta.3 development baseline
Schema version: 19
Lite Mode: beta-candidate
Full Mode: experimental preview, materially improved
Open WebUI integration: contract-tested; pinned container smoke pending/manual
Memory Intelligence Core: implemented baseline
Model Control Plane: implemented backend/runtime baseline
Prompt Pack v2: implemented contract baseline
```

Full Mode هنوز beta-supported نیست. تا وقتی همه gateهای خارجی PostgreSQL، FalkorDB، graph retrieval، graph forget/residue و docker-compose.full پاس نشده‌اند، Full Mode باید با همین عبارت معرفی شود:

```text
Full Mode: experimental preview; external certification incomplete.
```

## مسئله‌ای که موتور حافظه حل می‌کند

LLMها در جریان کار طولانی‌مدت مشکل حافظه دارند. کاربر هفته‌ها یا ماه‌ها درباره پروژه، سبک نوشتن، تصمیم‌های محصول، workflow تیم، ایمیل‌ها، research، importهای قدیمی و ترجیحات شخصی حرف می‌زند؛ اما مدل در جلسه بعد اغلب از صفر شروع می‌کند یا با یک retrieval سطحی و نامطمئن پاسخ می‌دهد.

راه‌حل ساده این است که همه چت‌ها ذخیره شوند و هنگام نیاز خلاصه یا embedding شوند. اما این کافی نیست. چت خام، حافظه نیست. در یک مکالمه ممکن است یک جمله دستور باشد، یک جمله توصیف واقعیت پروژه باشد، یک جمله تعریف اصطلاح باشد، یک جمله ناراحتی کاربر باشد، و یک جمله صرفاً phatic یا ادامه‌دار نگه داشتن تماس باشد. اگر همه این‌ها به شکل یک chunk مبهم ذخیره شوند، سیستم حافظه دقیق و قابل اعتماد نمی‌سازد.

Memorist تلاش می‌کند از مکالمه خام، حافظه‌ای بسازد که این ویژگی‌ها را هم‌زمان داشته باشد:

```text
دقیق
شاهدبنیاد
قابل ردگیری
قابل نسخه‌بندی
قابل بازیابی
قابل فراموشی
قابل کنترل در prompt
قابل audit
local-first
```

هدف این نیست که مدل «همه چیز را یادش بماند». هدف این است که سیستم بداند چه چیزی واقعاً باید حافظه شود، از کدام شاهد آمده، در چه scopeی معتبر است، چه زمانی اصلاح شده، آیا حساس است، و در لحظه پاسخ چگونه باید به مدل داده شود.

## اصل‌های فلسفی معماری

### ۱. پیام خام حافظه نیست؛ شاهد است

```text
Raw message is evidence, not memory.
```

وقتی کاربر می‌نویسد:

```text
تیم محصول نباید آیتمی را بدون اتصال به اپیک امتیازدار وارد جیرا کند.
```

این جمله در ابتدا فقط evidence است. هنوز معلوم نیست حافظه نهایی است یا نه. ممکن است policy پروژه باشد. ممکن است مربوط به یک تیم خاص باشد. ممکن است بعداً اصلاح شود. ممکن است فقط در یک context خاص معتبر باشد. بنابراین موتور ابتدا آن را در evidence ledger ثبت می‌کند و بعد پردازش می‌کند.

### ۲. جمله واحد ارتباطی است

```text
Sentence is the communication unit.
```

chunkهای بزرگ برای memory intelligence دقیق نیستند. یک paragraph ممکن است چند کار ارتباطی مختلف داشته باشد. موتور حافظه، پیام را به sentence unit تبدیل می‌کند تا هر جمله جداگانه تحلیل، route و evidence-linked شود.

### ۳. تحلیل یاکوبسنی parser مکالمه است

در code intelligence، parserهایی مثل Tree-sitter ساختار کد را پیدا می‌کنند. در Memorist، تحلیل جمله‌محور یاکوبسنی نقش parser ارتباطی مکالمه را دارد.

```text
Tree-sitter parses code structure.
Jakobson sentence analysis parses communication structure.
```

برای هر جمله، سیستم شش عامل ارتباطی را استخراج می‌کند:

```text
sender_addresser
receiver_addressee
message
context_referent
code
contact_channel
```

و function غالب/ثانویه جمله را مشخص می‌کند:

```text
referential
emotive
conative
phatic
metalingual
poetic
```

این تحلیل هنوز memory نیست. یک annotation است که مسیر استخراج حافظه را هدایت می‌کند.

### ۴. کاندید حافظه حقیقت نیست؛ تفسیر route‌شده است

```text
MemoryCandidate is a routed interpretation.
```

وقتی جمله‌ای conative است و receiver آن Product Team است، مسیر آن ممکن است `workflow_policy` یا `team_obligation` باشد. extractor مخصوص همان مسیر، یک candidate تولید می‌کند. این candidate هنوز حافظه نهایی نیست؛ باید trust، privacy، contradiction و consolidation را طی کند.

### ۵. حافظه نهایی claim نسخه‌دار و شاهدبنیاد است

```text
Memory is a consolidated, source-linked, versioned claim.
```

حافظه نهایی باید به evidence برگردد. باید نسخه داشته باشد. باید بتواند با correction، contradiction، retraction و supersession کنار بیاید. حذف یا overwrite کور، در این معماری مجاز نیست.

### ۶. Projection حافظه، source of truth نیست

FTS، embedding، active block، graph projection و retrieval cache همگی projection هستند. منبع حقیقت، canonical store است.

```text
SQLite is the Lite ledger.
PostgreSQL is the Full ledger.
FalkorDB is the graph memory map.
```

در Lite، SQLite canonical است. در Full، PostgreSQL canonical است. FalkorDB فقط نقشه گرافی rebuildable است و نباید حقیقت مستقل یا منبع اصلی داده باشد.

### ۷. حافظه instruction نیست؛ داده untrusted است

Memory Context Attachment نباید prompt کاربر را تغییر دهد و نباید مثل system instruction مطلق رفتار شود. حافظه به مدل داده می‌شود، اما با provenance، scope، trust level و محدودیت privacy.

```text
Memory is data, not instruction.
```

## نمای کلی معماری

```text
Open WebUI
  |
  | server-side Filter inlet/outlet
  v
Memorist Core API
  |
  +--> Raw Evidence Ledger
  |
  +--> Sentence Unitization
  |
  +--> Jakobson Communication Analysis
  |
  +--> Memory Signal Routing
  |
  +--> Route-Specific Extraction
  |
  +--> Trust / Privacy / Injection Review
  |
  +--> Consolidation / Versioning
  |
  +--> Canonical Store
  |       Lite: SQLite
  |       Full: PostgreSQL
  |
  +--> Projections
  |       FTS
  |       Active Memory Blocks
  |       Embeddings, optional
  |       FalkorDB Graph, Full preview
  |
  +--> Retrieval / Preflight Planning
  |
  +--> Memory Context Attachment
  |
  v
Main Chat Model inside Open WebUI
```

Open WebUI همچنان UI و main chat را کنترل می‌کند. Memorist یک runtime حافظه کنار Open WebUI است، نه جایگزین آن.

## دو جریان اصلی سیستم

### جریان ورود حافظه

```text
User / Assistant / Import
-> raw capture
-> message/session/project resolution
-> sentence segmentation
-> Jakobson sentence analysis
-> memory signal routing
-> specialized candidate extraction
-> privacy/trust/injection review
-> consolidation
-> memory versions
-> projections/outbox
```

### جریان مصرف حافظه

```text
New user message
-> session/project/workspace resolution
-> retrieval plan
-> candidate retrieval
-> scope/privacy/conflict filtering
-> attachment budgeting
-> Memory Context Attachment
-> Open WebUI main chat model
-> assistant response
-> post-response capture
-> async memory worker jobs
```

اگر سیستم فقط جریان ورود داشته باشد، آرشیو است. اگر فقط جریان مصرف داشته باشد، RAG سطحی است. Memorist این دو مسیر را به هم وصل می‌کند: ساخت حافظه و مصرف کنترل‌شده حافظه.

## لایه ۱ — Open WebUI Filter Boundary

### نقش

Open WebUI parent UI است. Memorist از طریق Filter سرور-ساید به آن متصل می‌شود. Filter دو نقطه اصلی دارد:

```text
inlet / pre-send
outlet / post-response
```

در inlet، پیام کاربر capture می‌شود، session/project resolve می‌شود، retrieval/preflight انجام می‌شود، و Memory Context Attachment به شکل جدا وارد context می‌شود. در outlet، پاسخ assistant capture می‌شود و jobهای memory worker enqueue می‌شوند.

### اصل مهم

```text
User prompt remains byte-for-byte unchanged.
```

Memorist نباید متن user prompt را بازنویسی کند. اگر حافظه‌ای لازم است، به شکل attachment جدا و auditپذیر اضافه می‌شود.

### تکنیک‌های پیاده‌سازی

- boundary جدا برای Open WebUI integration
- shared client برای تماس با Memorist Core
- contract tests برای Filter/Function
- fail-open برای preflight
- capture key / idempotency برای جلوگیری از ثبت تکراری پیام
- حفظ distinction بین user message و assistant response

## لایه ۲ — Evidence Ledger

### نقش

هر ورودی ابتدا به‌عنوان شاهد خام ذخیره می‌شود:

```text
workspace
project
session
message
role
raw_text
timestamps
source
model metadata
import source
capture key
```

این لایه تفسیر نمی‌کند. فقط می‌گوید چه چیزی، کی، کجا و در چه زمینه‌ای وارد سیستم شده است.

### چرا مهم است؟

بدون evidence ledger، حافظه قابل اعتماد نیست. اگر حافظه‌ای ساخته شد ولی معلوم نبود از کدام جمله آمده، نمی‌توان آن را audit، اصلاح یا حذف کرد.

### تکنیک‌های پیاده‌سازی

- stable UUIDها
- content hash
- idempotent inserts
- transaction boundary روشن
- canonical migrations
- residue-aware delete/quarantine paths
- source mapping برای importها

## لایه ۳ — Sentence Unitization

### نقش

پیام خام به sentence unit تبدیل می‌شود:

```text
text_units
  unit_type = sentence
  message_uuid
  sentence_index
  text
  char_start
  char_end
  stable hash
```

### چرا sentence؟

چون memory signal در سطح جمله دقیق‌تر است. مثال:

```text
تیم محصول باید تسک‌ها را به اپیک وصل کند. این قانون برای باگ‌های فوری استثنا دارد.
```

جمله اول policy است. جمله دوم exception/update است. اگر این دو با هم chunk شوند، سیستم ممکن است policy را بدون exception به مدل بدهد.

### تکنیک‌های پیاده‌سازی

- deterministic sentence segmentation
- نگهداری offset برای evidence span
- hash پایدار برای replay/idempotency
- language-aware ولی local-safe defaults
- عدم وابستگی به LLM برای segmentation پایه

## لایه ۴ — Jakobson Communication Analysis

### نقش

این لایه برای هر جمله، ساختار ارتباطی را ثبت می‌کند.

خروجی مفهومی:

```json
{
  "sentence": "...",
  "six_factors": {
    "sender_addresser": "...",
    "receiver_addressee": "...",
    "message": "...",
    "context_referent": "...",
    "code": "...",
    "contact_channel": "..."
  },
  "dominant_function": "conative",
  "secondary_functions": ["referential"],
  "function_reason": "...",
  "confidence": "high"
}
```

### چرا Jakobson؟

چون حافظه انسانی فقط fact نیست. مکالمه شامل دستور، ابراز، تعریف، تأکید، سبک، رابطه، درخواست، اصلاح، مخالفت و تماس است. مدل‌های حافظه ساده معمولاً این‌ها را در یک متن خلاصه می‌کنند. Memorist از ابتدا structure ارتباطی می‌سازد.

### mapping function به حافظه

| Function | معنا در حافظه | مسیرهای محتمل |
|---|---|---|
| conative | دستور/درخواست/وظیفه | prompt_instruction, workflow_policy, team_obligation |
| referential | گزارش واقعیت/فرایند | project_context, process_fact, jira_configuration |
| metalingual | تعریف واژه/قاعده بیان | terminology_rule, naming_rule, style_policy |
| emotive | ترجیح/نارضایتی/موضع | user_preference, emotional_stance, quality_feedback |
| poetic | فرم/سبک/شعار/بیان | branding_style, rhetorical_pattern |
| phatic | حفظ تماس/گفت‌وگوی سبک | ignore یا interaction preference در صورت تکرار |

### تکنیک‌های پیاده‌سازی

- Prompt Pack v2
- schema-bound I-JSON output
- validator برای function/confidence/schema
- ذخیره در `jakobson_analysis_runs`
- ذخیره هر جمله در `jakobson_sentence_annotations`
- lineage به `text_units`
- رد خروجی invalid
- عدم تبدیل مستقیم annotation به memory

## لایه ۵ — Memory Signal Routing

### نقش

Routing تصمیم می‌گیرد هر annotation به کدام extractor برود.

مثال:

```text
dominant_function = conative
receiver = AI
=> prompt_instruction / task_constraint

dominant_function = conative
receiver = Product Team
=> workflow_policy / team_obligation

dominant_function = metalingual
=> terminology_rule / naming_rule

dominant_function = emotive
=> user_preference / emotional_stance
```

### چرا routing جداست؟

اگر یک extractor عمومی همه چیز را پردازش کند، حافظه بی‌دقت می‌شود. routing باعث می‌شود جمله دستوری، جمله factual، جمله سبک‌شناختی و جمله احساسی با prompt و schema مناسب خود پردازش شوند.

### تکنیک‌های پیاده‌سازی

- deterministic route rules
- optional LLM routing assist
- `memory_signal_routes`
- priority و confidence
- route UUID برای evidence lineage
- manual_review برای ambiguity یا sensitivity
- جلوگیری از routeهای cross-scope

## لایه ۶ — Route-Specific Candidate Extraction

### نقش

هر route به extractor مخصوص می‌رود:

```text
conative_instruction_extractor
referential_context_extractor
metalingual_policy_extractor
emotive_preference_extractor
poetic_style_extractor
```

خروجی، MemoryCandidate است؛ نه memory نهایی.

### مثال conative

جمله:

```text
تیم محصول باید هیچ آیتمی را بدون اتصال به اپیک امتیازدار وارد نکند.
```

کاندید:

```json
{
  "candidate_type": "team_obligation",
  "subject": "Product Team",
  "predicate": "must_not_add",
  "object": "item_without_scored_epic",
  "scope": "project",
  "obligation_strength": "mandatory",
  "evidence": [
    {
      "message_uuid": "...",
      "unit_uuid": "...",
      "annotation_uuid": "...",
      "route_uuid": "...",
      "quote": "..."
    }
  ]
}
```

### تکنیک‌های پیاده‌سازی

- promptهای تخصصی
- schema validation
- evidence-required outputs
- rejection reasons
- confidence و importance جدا
- candidate_evidence
- audit link به prompt_execution_runs
- عدم قبول candidate بدون quote/span/source

## لایه ۷ — Trust, Privacy, Injection Review

### نقش

قبل از consolidation، candidate باید از فیلترهای اعتماد و حریم خصوصی عبور کند.

سؤال‌های اصلی:

```text
آیا این واقعاً حرف کاربر است؟
آیا assistant فقط حدس زده؟
آیا متن import شده و historical_untrusted است؟
آیا prompt injection داخل متن وجود دارد؟
آیا محتوا حساس است؟
آیا scope آن project است یا global؟
آیا باید never_auto_attach شود؟
```

### مثال assistant speculation

Assistant می‌گوید:

```text
احتمالاً شما پاسخ‌های کوتاه‌تر را ترجیح می‌دهید.
```

این نباید user memory شود.

اما کاربر می‌گوید:

```text
از این به بعد جواب‌ها را کوتاه‌تر بده.
```

این می‌تواند preference/prompt_instruction شود.

### تکنیک‌های پیاده‌سازی

- privacy_sensitivity prompt role
- deterministic sensitivity checks
- remote provider privacy acknowledgement
- prompt injection fixture tests
- imported content trust downgrade
- sensitive memory retrieval restrictions
- no raw secrets in model profiles
- redaction در diagnostics
- non-content-bearing forget receipts

## لایه ۸ — Consolidation and Memory Versioning

### نقش

Candidateها با memoryهای قبلی تطبیق داده می‌شوند و عملیات consolidation انتخاب می‌شود:

```text
ADD
REINFORCE
UPDATE
SUPERSEDE
CONTRADICT
RETRACT
NOOP
REJECT
MANUAL_REVIEW
```

### چرا versioning؟

حافظه انسانی تغییر می‌کند. کاربر ممکن است قاعده‌ای را اصلاح کند، preference را عوض کند، یا exception بگذارد. اگر حافظه overwrite شود، تاریخچه و evidence از بین می‌رود.

### مثال

اول:

```text
همه تسک‌ها باید به اپیک امتیازدار وصل شوند.
```

بعد:

```text
برای باگ‌های فوری استثنا بگذار.
```

حافظه نباید اولی را حذف کند. باید version یا exception بسازد.

### تکنیک‌های پیاده‌سازی

- memory_versions
- current version pointer
- supersedes/contradicts/corrects/retracts relations
- temporal validity
- scope-aware consolidation
- evidence preservation
- manual review در confidence پایین

## لایه ۹ — Canonical Storage

### Lite

Lite از SQLite استفاده می‌کند:

```text
SQLite canonical ledger
WAL
foreign keys
local object store
FTS
SQLite write actor
bounded retry
safe backup
maintenance commands
```

Lite برای local daily use طراحی شده و مسیر beta-candidate است.

### Full

Full از PostgreSQL استفاده می‌کند:

```text
PostgreSQL canonical ledger
PostgreSQL migrations
durable jobs/outbox
FOR UPDATE SKIP LOCKED
hot scheduler runnable references
FalkorDB projection
SQLite-to-PostgreSQL migration
```

Full هنوز experimental preview است و نیاز به external certification دارد.

### اصل مهم

Full نباید silently به SQLite fallback کند. اگر `MEMORIST_RUNTIME_PROFILE=full` است، باید `MEMORIST_CANONICAL_STORE=postgres` و `MEMORIST_POSTGRES_DSN` معتبر داشته باشد.

## لایه ۱۰ — Projection Layer

Canonical memory برای storage کافی است، ولی برای retrieval سریع و قابل استفاده باید projection ساخته شود.

Projectionها:

```text
FTS
active memory blocks
embedding records
retrieval cache
graph projection
attachment-ready summaries
```

### Active Memory Blocks

Active blockها source of truth نیستند. derived view هستند. از memory versionهای فعلی ساخته می‌شوند و source UUID نگه می‌دارند.

### Embeddings

Embedding اختیاری است. وقتی embedding model تغییر می‌کند، recordهای قدیمی stale می‌شوند و re-index لازم است.

### FalkorDB Graph

در Full preview، FalkorDB گراف memory را از PostgreSQL projection می‌کند. این گراف می‌تواند شامل nodeهای زیر باشد:

```text
Workspace
Project
Session
Message
TextUnit
JakobsonAnnotation
CommunicativeFunction
Addressee
ContextReferent
CodeRegister
MemorySignalRoute
MemoryCandidate
Memory
MemoryVersion
Evidence
ModelProfile
PromptExecution
PrivacyRequest
```

و edgeهایی مثل:

```text
HAS_UNIT
HAS_JAKOBSON_ANNOTATION
HAS_DOMINANT_FUNCTION
ADDRESSES
REFERS_TO
ROUTES_TO
DERIVED_FROM
EVIDENCED_BY
HAS_VERSION
CURRENT_VERSION
SUPERSEDES
CONTRADICTS
RETRACTS
```

گراف برای navigation و retrieval expansion است، نه حقیقت مستقل.

## لایه ۱۱ — Preflight Retrieval Planning

### نقش

قبل از ارسال درخواست به main chat model، preflight تصمیم می‌گیرد چه حافظه‌ای لازم است.

ورودی:

```text
current user message
session/project/workspace
active blocks
retrieval candidates
conflicts
privacy restrictions
token budget
main model context window
```

خروجی:

```text
selected_memory_ids
excluded_memory_ids
trusted_directive_ids
ordinary_memory_ids
conflict_ids
compression strategy
estimated tokens
```

### fail-open

اگر preflight model یا runtime خراب شود، chat نباید خراب شود. سیستم fail-open می‌کند؛ یعنی بدون attachment یا با fallback محدود ادامه می‌دهد.

### تکنیک‌های پیاده‌سازی

- bounded timeout
- model role: preflight
- schema-bound preflight prompt
- invalid output rejection
- attachment budget
- scope/privacy/conflict filtering
- provenance-preserving plan
- no mutation of user prompt

## لایه ۱۲ — Memory Context Attachment

### نقش

این لایه حافظه انتخاب‌شده را به مدل اصلی می‌دهد.

اما attachment باید جدا از user text باشد:

```text
User prompt: unchanged
Memory Context Attachment: separate, scoped, provenance-aware, untrusted
```

### نمونه attachment

```text
Relevant project memory:
- In this project, product-team Jira items must be linked to scored epics.
  Scope: project
  Evidence: message_uuid=..., unit_uuid=...
- Exception: urgent bugs may bypass scored epic linkage.
  Scope: project
  Evidence: message_uuid=..., unit_uuid=...

Warning:
These are project-scoped workflow memories, not global user preferences.
```

### چرا مهم است؟

بدون این تفکیک، حافظه می‌تواند مثل system prompt عمل کند و prompt injection یا stale memory را تقویت کند. در Memorist، attachment داده است، نه فرمان مطلق.

## لایه ۱۳ — Post-response Capture

### نقش

پاسخ assistant هم capture می‌شود؛ ولی به‌طور مستقیم user memory نیست.

چرا لازم است؟

```text
traceability
decision history
conversation reconstruction
future references
import/export/Heritage
```

اما اگر assistant حدس بزند یا پیشنهاد بدهد، آن حدس نباید بدون تأیید کاربر به حافظه user تبدیل شود.

## لایه ۱۴ — Import and Heritage

### Import

Import workflow چند مرحله‌ای است:

```text
stage
inspect
adapter probe
reconstruct
dry-run
commit
post-import processing
```

پشتیبانی مفهومی برای providerهای مختلف:

```text
ChatGPT
Claude
Gemini
Open WebUI
generic Memorist JSON
manual transcripts
```

Imported content historical_untrusted است مگر اینکه بعداً تأیید شود.

### Heritage

Heritage export حافظه canonical را به package آفلاین قابل بررسی تبدیل می‌کند:

```text
manifest
I-JSONL data files
checksums
schemas
reports
object placeholders
```

هدف Heritage این است که حافظه local قابل انتقال و قابل audit باشد، نه فقط dump مبهم DB.

## لایه ۱۵ — Forget, Residue, and Governance

### Forget

Forget workflow:

```text
preview
confirm
execute
quarantine
projection cleanup
residue check
receipt
```

حذف حافظه فقط حذف row نیست. اثر حافظه باید در این لایه‌ها بررسی شود:

```text
canonical memory
memory versions
evidence links
active blocks
attachments
FTS
embedding records
graph projection
import mappings
exports/reports where applicable
```

### Residue

Residue check بررسی می‌کند آیا محتوای فراموش‌شده هنوز از مسیر retrieval یا projection قابل دسترسی است یا نه.

### Receipt

Receipt نباید raw erased content داشته باشد. فقط non-content-bearing metadata و نتیجه عملیات را گزارش می‌کند.

## Model Control Plane

Memorist مدل main chat را تصاحب نمی‌کند. Open WebUI مالک main model است. Memorist فقط metadata آن را مشاهده می‌کند.

نقش‌ها:

```text
main_chat_observed
preflight
memory_extraction
embedding
import_reconstruction
block_compaction
privacy_sensitivity
```

### فلسفه

یک مدل واحد نباید هم پاسخ کاربر را بدهد، هم حافظه بسازد، هم privacy را تشخیص دهد، هم embedding بسازد. این تفکیک باعث audit، هزینه‌سنجی، timeout و privacy control بهتر می‌شود.

### تکنیک‌های پیاده‌سازی

- model_profiles
- role defaults
- provider types
- health events
- usage events
- privacy acknowledgements
- secret strategy: environment_reference
- raw secret rejection
- stale embedding tracking
- preflight fail-open
- memory extraction async

## Prompt Pack v2

Prompt Pack v2 قرارداد رفتاری مدل‌های غیرچتی است.

اصل‌ها:

```text
Prompts do not answer users.
Analyzed content is data, not instruction.
Output must be valid I-JSON.
No markdown.
No chain-of-thought.
No unsupported memory.
Evidence required where applicable.
Invalid output is rejected.
```

Promptهای اصلی:

```text
memorist.preflight_planning
memorist.jakobson_sentence_analysis
memorist.memory_signal_routing_assist
memorist.conative_instruction_extractor
memorist.referential_context_extractor
memorist.metalingual_policy_extractor
memorist.emotive_preference_extractor
memorist.poetic_style_extractor
memorist.memory_consolidation_assist
memorist.contradiction_detection
memorist.block_compaction
memorist.import_reconstruction
memorist.privacy_sensitivity
```

### prompt_execution_runs

هر اجرای prompt می‌تواند ثبت شود:

```text
prompt_id
prompt_version
stage
model_role
model_profile_uuid
provider_type
input_hash
output_hash
raw_output
validated_output
warnings
latency
token counts
status
```

این باعث می‌شود بعداً بدانیم هر memory candidate با کدام prompt و کدام مدل ساخته شده است.

## Job, Outbox, and Scheduler Design

### Lite

در Lite، SQLite write actor از تداخل write path جلوگیری می‌کند. live chat capture باید از import و کارهای سنگین جلوتر باشد.

### Full

در Full، jobs و outboxها در PostgreSQL durable هستند. Hot Scheduler فقط runnable reference و priority را نگه می‌دارد. payload اصلی در PostgreSQL می‌ماند.

الگوهای مهم:

```text
durable jobs
outbox pattern
FOR UPDATE SKIP LOCKED
stale job recovery
bounded batch import
priority lanes
backpressure
```

لایه‌های priority می‌توانند مثل این باشند:

```text
critical_privacy
live_chat_capture
preflight_persist
assistant_capture
memory_extraction
import_commit
import_reconstruction
graph_projection
embedding_index
block_rebuild
maintenance
```

هدف این است که heavy import یا graph projection باعث کندی live chat نشود.

## Error Handling and Fail-Open Philosophy

Memorist باید کمک کند، نه اینکه chat را گروگان بگیرد.

مسیرهای fail-open:

```text
preflight unavailable
model timeout
invalid preflight output
graph backend down
embedding unavailable
memory worker backlog
```

در این حالت‌ها، Open WebUI chat ادامه پیدا می‌کند و diagnostics مشکل را گزارش می‌دهد.

مسیرهایی که نباید fail-open خام داشته باشند:

```text
privacy erasure
secret storage
Full mode canonical store mismatch
database migration corruption
forget residue failure
```

## Security Architecture

موتور حافظه فرض می‌کند حافظه و import می‌توانند آلوده باشند.

تهدیدهای اصلی:

```text
prompt injection inside imported chats
delimiter attacks
scope expansion
tool-call attempts
secret exfiltration instructions
stale memory directives
assistant speculation becoming memory
sensitive memory auto-attachment
forgotten content residue
```

کنترل‌ها:

```text
analyzed text is data
prompt output validation
I-JSON only
schema validators
privacy sensitivity routing
remote provider acknowledgement
secret redaction
no raw secret persistence
forget residue checks
package forbidden-file scans
source tree scans
```

## Testing Strategy

تست‌ها فقط unit test نیستند؛ چند سطح دارند:

```text
core tests
Open WebUI contract tests
Model Control tests
Prompt Pack tests
Jakobson pipeline tests
security tests
daily smoke
heavy import smoke
Heritage roundtrip
forget residue
consistency check
recovery tests
source package scan
RC package schema
version consistency
Full Mode external gates
```

Full Mode external gates شامل:

```text
PostgreSQL canonical smoke
PostgreSQL job/outbox concurrency
scheduler live-chat preemption
import under live traffic
FalkorDB projection
FalkorDB rebuild
graph retrieval
graph down fallback
graph forget/residue
SQLite-to-PostgreSQL migration
full compose smoke
```

تا وقتی این‌ها external pass نشده‌اند، Full Mode experimental preview می‌ماند.

## تکنیک‌های کدنویسی و طراحی نرم‌افزار

### ۱. Separation of concerns

کد به چند boundary تقسیم می‌شود:

```text
core API
storage
memory worker
prompt registry
model control
retrieval
import
heritage
Open WebUI integration
release tooling
```

هر boundary وظیفه مشخص دارد. این باعث می‌شود حافظه، prompt، storage و UI با هم مخلوط نشوند.

### ۲. Repository / Store abstraction

Lite و Full storage با abstraction جدا می‌شوند. هدف این است که SQLite و PostgreSQL رفتار canonical مشابه داشته باشند، اما با قابلیت‌های متفاوت.

### ۳. Migration-first design

تغییرات داده با migration رسمی انجام می‌شوند. schema version بخشی از release posture است. هر package باید version/schema سازگار داشته باشد.

### ۴. Idempotency

Capture و import باید idempotent باشند. تکرار capture key یا import mapping نباید duplicate memory بسازد.

### ۵. Outbox pattern

Projection به graph، embedding، block rebuild و erasure cleanup با outbox انجام می‌شود تا transaction اصلی و side effect از هم جدا باشند.

### ۶. Schema-bound LLM outputs

LLM output به‌عنوان متن آزاد پذیرفته نمی‌شود. باید JSON معتبر، مطابق schema، دارای prompt_id/version و evidence باشد.

### ۷. Audit-first records

هر لایه مهم audit record دارد:

```text
prompt_execution_runs
model_usage_events
model_health_events
import reports
forget receipts
release reports
baseline check reports
full certification reports
```

### ۸. Local-first release hygiene

Repo باید از runtime artifacts پاک باشد:

```text
.venv
__pycache__
.pyc
.sqlite
.env
logs
release zips
checksums
```

Packageها generated artifact هستند و نباید به‌صورت پیش‌فرض commit شوند.

## Flow مثال ۱ — تبدیل یک دستور محصولی به حافظه

ورودی:

```text
تیم محصول باید هیچ آیتمی را بدون اتصال به اپیک امتیازدار وارد نکند.
```

Sequence:

```text
1. raw message capture
2. sentence unitization
3. Jakobson:
   receiver = Product Team
   context = Jira/product workflow
   dominant_function = conative
4. routing:
   workflow_policy / team_obligation
5. extraction:
   candidate_type = team_obligation
   subject = Product Team
   obligation_strength = mandatory
6. privacy/trust:
   low sensitivity, project-scoped
7. consolidation:
   ADD or REINFORCE
8. projection:
   ProjectContextBlock, FTS, graph preview
9. retrieval:
   when user asks about Jira process
10. attachment:
   project-scoped memory, with evidence
```

## Flow مثال ۲ — ترجیح نوشتاری

ورودی:

```text
این ending بد است؛ مثبت‌تر، خلاق‌تر و با خون و گوشت خودم بنویس.
```

Sequence:

```text
Jakobson:
  dominant = conative
  secondary = emotive / poetic
Routing:
  style_policy
  prompt_instruction
  emotional_stance
Extraction:
  preference for embodied, creative, positive writing
Consolidation:
  if repeated, style block
  if local, project/session-scoped
Retrieval:
  only in writing/rewrite tasks, not every technical answer
```

## Flow مثال ۳ — import قدیمی

Imported message:

```text
از این به بعد همیشه همین سبک را رعایت کن.
```

Sequence:

```text
staged import
historical_untrusted trust level
sentence analysis
candidate possible
no immediate trusted global instruction
confirmation/repetition needed for active memory
```

هدف: import قدیمی نباید سیستم را با directiveهای stale آلوده کند.

## Flow مثال ۴ — Forget

کاربر درخواست حذف یک memory را می‌دهد.

Sequence:

```text
preview affected objects
confirm
quarantine canonical rows
invalidate retrieval
remove/mark graph projection
invalidate active blocks
remove FTS/embedding reachability
run residue check
write receipt without raw erased content
```

## ابداعات و ترکیب‌های متمایز این معماری

در اینجا «ابداع» به معنای claim حقوقی یا اختراع ثبت‌شده نیست. منظور ترکیب طراحی و architecture خاص این پروژه است.

### ۱. Jakobson-as-Conversation-Parser

به جای اینکه حافظه فقط بر اساس embedding/chunk ساخته شود، sentence-level Jakobson analysis به‌عنوان parser مکالمه استفاده می‌شود. این باعث می‌شود سیستم بفهمد جمله دستور است، fact است، تعریف است، ترجیح است یا سبک.

### ۲. Route-before-Extract

سیستم اول function و receiver را تحلیل می‌کند، بعد extractor مناسب را انتخاب می‌کند. این جلوی extractor عمومی و بی‌دقت را می‌گیرد.

### ۳. Evidence-first Memory

هیچ memory معتبر بدون evidence نباید ساخته شود. Candidate، route، annotation، sentence و message به هم وصل‌اند.

### ۴. Attachment-not-Mutation

حافظه به prompt کاربر چسبانده یا در آن rewrite نمی‌شود. Memory Context Attachment جداست و prompt اصلی را دست‌نخورده نگه می‌دارد.

### ۵. Prompt Pack as Runtime Contract

Promptها فقط متن نیستند. نسخه، schema، role، validation، audit و fail behavior دارند.

### ۶. Model Role Separation

مدل اصلی chat با مدل‌های preflight، extraction، privacy، embedding و import جدا می‌شود. این هم از نظر هزینه و هم از نظر privacy و audit مهم است.

### ۷. Canonical vs Projection Discipline

SQLite/PostgreSQL source of truth هستند. Graph/embedding/blockها projection هستند. این مانع از source-of-truth drift می‌شود.

### ۸. Forget as Cross-Projection Erasure

Forget فقط حذف row نیست؛ باید retrieval، block، graph، embedding و receipts را هم در نظر بگیرد.

### ۹. Heritage as Portable Memory Evidence

حافظه فقط در DB محلی زندانی نیست. Heritage آن را با manifest، checksum و I-JSONL قابل انتقال و audit می‌کند.

### ۱۰. Full Certification Discipline

Full Mode با unit test certified نمی‌شود. باید external gates داشته باشد. اگر Docker یا DSN نیست، نتیجه honest skip است، نه pass.

## مقایسه با حافظه ساده/RAG ساده

| موضوع | RAG ساده | Memorist |
|---|---|---|
| واحد پردازش | chunk | sentence unit |
| معناشناسی | embedding similarity | communication-aware routing |
| evidence | گاهی مبهم | mandatory lineage |
| حافظه | summary یا vector | versioned claim |
| correction | overwrite/append | update/supersede/contradict/retract |
| prompt use | context dump | bounded attachment |
| privacy | معمولاً سطحی | sensitivity, trust, forget residue |
| graph | optional visualization | projection from canonical memory topology |
| import | ingest متن | staged, dry-run, historical_untrusted |
| audit | محدود | prompt/model/storage/release audit |

## محدودیت‌های فعلی

این معماری هنوز کامل نیست. محدودیت‌های فعلی:

```text
Full Mode external certification incomplete
Open WebUI pinned container smoke pending/manual
semantic quality of Prompt Pack v2 needs real-world evaluation
Jakobson routing needs larger multilingual fixtures
Full graph retrieval needs external evidence
Full graph forget/residue needs real FalkorDB pass
UI for Model Control needs polish
operator UX for memory review still needs work
```

## فرمان‌های مهم

Baseline:

```bash
python scripts/baseline_check.py
```

Lite smoke:

```bash
make smoke-daily
make smoke-import-heavy-ci
make heritage-roundtrip
make forget-residue
```

Prompt/Memory tests:

```bash
cd memorist-core
python -m uv run pytest -q tests/test_memory_worker_prompt_pack.py tests/test_jakobson_pipeline.py
python -m uv run pytest -q tests/test_model_control_plane.py
```

Full certification:

```bash
python scripts/full_mode_check.py
```

Clean:

```bash
python scripts/clean_artifacts.py --apply
python scripts/clean_artifacts.py --check
python scripts/scan_source_tree.py
```

## جمع‌بندی

Memorist یک سیستم ذخیره چت نیست. یک موتور حافظه local-first است که تلاش می‌کند از مکالمه خام، حافظه‌ای بسازد که قابل اعتماد، نسخه‌دار، شاهدبنیاد، قابل بازیابی و قابل فراموشی باشد.

هسته معماری این است:

```text
پیام خام شاهد است.
جمله واحد ارتباطی است.
تحلیل یاکوبسنی parser مکالمه است.
route تصمیم پردازش است.
candidate تفسیر شاهدبنیاد است.
memory claim تثبیت‌شده و نسخه‌دار است.
projection نقشه مصرف حافظه است.
attachment مصرف کنترل‌شده حافظه در لحظه چت است.
```

این معماری می‌خواهد کاری کند که مدل در پروژه‌های طولانی از صفر شروع نکند، اما هم‌زمان هر چیزی را هم کورکورانه به حافظه فعال تبدیل نکند. حافظه باید کمک کند، نه اینکه prompt را آلوده کند؛ باید زمینه بدهد، نه اینکه جای حقیقت بنشیند؛ باید قابل استفاده باشد، نه فقط آرشیو؛ و باید قابل فراموشی باشد، نه انباشت بی‌پایان.






# موخره: پروژه‌های الهام‌بخش، منابع و جایگاه Memorist

Memorist در خلأ شکل نگرفته است. این پروژه در نسبت با Open WebUI، در مقایسه با سیستم‌های جدید حافظه و context برای agentها، و بر اساس یک نیاز مشخص طراحی شد: ساخت یک موتور حافظه local-first و شاهدبنیاد برای کار طولانی‌مدت انسان با LLM.

این پروژه، مگر در مواردی که مجوزها یا maintainers آن پروژه‌ها صراحتاً گفته باشند، وابسته، تأییدشده یا مشتق رسمی از پروژه‌های زیر نیست. نام آن‌ها در اینجا به‌عنوان نقطه الهام، مرجع مقایسه، سیستم مجاور یا contrast مهم ذکر می‌شود.

## Open WebUI

[Open WebUI](https://github.com/open-webui/open-webui) رابط مادر و هدف اصلی integration این نسخه است. جهت‌گیری local-first، extensible و self-hosted آن باعث شد Memorist به‌جای تبدیل شدن به یک محصول چت مستقل، به شکل یک runtime حافظه همراه طراحی شود.

مرز معماری Memorist عمداً چنین تعریف شده است:

```text
Open WebUI مالک رابط چت و تجربه مدل اصلی است.
Memorist مالک capture، پردازش، retrieval و attachment حافظه محلی است.
```

این جداسازی اجازه می‌دهد Open WebUI همان workbench کاربر باقی بماند و موتور حافظه مستقل از آن تکامل پیدا کند.

## Model Context Protocol

[Model Context Protocol](https://github.com/modelcontextprotocol) از نظر معماری، این ایده را تقویت کرد که context، toolها و memory باید از طریق interfaceهای صریح، قابل audit و قابل کنترل ارائه شوند، نه از طریق prompt stuffing پنهان. Memorist فعلاً حول integration با Open WebUI ساخته شده است، اما معماری آن برای یک سطح tool-first یا MCP-facing در آینده آماده است.

این مسیر مخصوصاً برای future external tool surface مهم است:

```text
memorist_search_memory
memorist_get_project_context
memorist_trace_decision
memorist_explain_attachment
memorist_forget_memory
memorist_get_memory_graph
```

## Codebase-Memory MCP

[Codebase-Memory MCP](https://github.com/DeusData/codebase-memory-mcp) مرجع مقایسه‌ای مهمی برای intelligence محلی و graph-native بود. دامنه آن متفاوت است: codebase را برای agentهای کدنویسی به یک گراف ساختاری پایدار تبدیل می‌کند. Memorist روی مکالمه، حافظه پروژه، ترجیح‌های کاربر، قواعد workflow، historyهای importشده و attachment کنترل‌شده به prompt تمرکز دارد.

اما درس معماری آن مهم است:

```text
Codebase-Memory ساختار کد را شایسته گراف پایدار می‌داند.
Memorist ساختار مکالمه را شایسته توپولوژی حافظه پایدار می‌داند.
```

از این جهت، Codebase-Memory نقش لایه Jakobson در Memorist را شفاف‌تر کرد. اگر Tree-sitter می‌تواند ساختار کد را برای agentهای کدنویسی parse کند، تحلیل جمله‌محور یاکوبسنی می‌تواند ساختار ارتباطی مکالمه را برای کار طولانی‌مدت انسان و LLM parse کند.

## Letta / MemGPT

[Letta](https://github.com/letta-ai/letta)، که پیش‌تر با نام MemGPT شناخته می‌شد، یکی از مراجع مهم در طراحی agentهای stateful و حافظه‌محور است. این پروژه اهمیت نگاه به حافظه به‌عنوان یک substrate عملیاتی را روشن می‌کند، نه صرفاً یک feature تزئینی برای chat history.

تفاوت Memorist در مرز محصولی آن است. Memorist در اصل یک پلتفرم agent خودمختار نیست. Memorist یک runtime حافظه محلی برای chat workbenchهاست و نقش‌ها را صریحاً از هم جدا می‌کند:

```text
main chat
preflight planning
memory extraction
embedding
privacy sensitivity
import reconstruction
block compaction
```

این جداسازی نقش‌ها برای audit و privacy در Memorist مرکزی است.

## Zep و Graphiti

[Zep](https://github.com/getzep/zep) و [Graphiti](https://github.com/getzep/graphiti) مرجع‌های مهمی برای حافظه temporal و graph-oriented agentها هستند. آن‌ها نشان می‌دهند که حافظه بلندمدت فقط مسئله vector search نیست؛ روابط زمانی، اتصال entityها و contextهای در حال تغییر اهمیت دارند.

Memorist از یک discipline مشابه اما متمایز استفاده می‌کند:

```text
حافظه canonical در SQLite یا PostgreSQL زندگی می‌کند.
graph memory یک projection است.
FalkorDB نقشه rebuildable حافظه است، نه source of truth.
```

این تمایز برای forget/residue و rebuild در Memorist حیاتی است.

## Mem0

[Mem0](https://github.com/mem0ai/mem0) مرجع مهمی برای memory layerهای production-oriented در agentهای هوش مصنوعی است. تمرکز آن بر به‌خاطر سپردن ترجیحات کاربر، سازگاری در طول زمان و کاهش context تکراری با مسئله Memorist هم‌پوشانی دارد.

تمایز Memorist در pipeline ارتباط‌محور آن است. Memorist حافظه را فقط با salience detection عمومی استخراج نمی‌کند. ابتدا جمله را از نظر کارکرد ارتباطی تحلیل می‌کند، memory signal را بر اساس function و receiver route می‌کند، و سپس candidateهای شاهدبنیاد می‌سازد.

## Cognee

[Cognee](https://github.com/topoteretes/cognee) یک پلتفرم open-source مجاور برای memory agentهاست که ingestion، ساخت knowledge graph و حافظه پایدار را ترکیب می‌کند. این پروژه بخشی از حرکت کلی به سمت سیستم‌های context graph و graph-augmented memory است.

تمرکز محدودتر Memorist، تبدیل evidence مکالمه به حافظه scoped، نسخه‌دار و قابل audit برای کار با Open WebUI است. در Memorist، گراف یکی از projectionهاست، نه کل سیستم حافظه.

## Memorist چه چیزی اضافه می‌کند

پروژه‌های بالا میدان مقایسه و الهام را شکل دادند، اما معماری Memorist آن‌ها را با فلسفه حافظه خاص خودش ترکیب می‌کند:

```text
پیام خام شاهد است.
جمله واحد ارتباطی است.
تحلیل یاکوبسنی parser مکالمه است.
Memory signal routing قبل از extraction می‌آید.
Candidateها تفسیرند، نه حقیقت.
Memoryها claimهای نسخه‌دار و شاهدبنیادند.
Graph، embedding، FTS و blockها projection هستند.
Attachment مصرف کنترل‌شده حافظه است، نه mutation پرامپت.
Forget باید residue را در projectionهای مختلف بررسی کند.
```

نتیجه این نیست که Memorist از همه این سیستم‌ها «بهتر» است. ادعای دقیق‌تر این است: Memorist یک موتور حافظه local-first برای کار انسان و LLM را بررسی و پیاده‌سازی می‌کند؛ جایی که معنای ارتباطی مکالمه، شاهد، scope، privacy، correction و مصرف کنترل‌شده حافظه در prompt، نگرانی‌های درجه اول معماری هستند.
