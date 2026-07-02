# Architecture README — Open WebUI Memorist Edition

**Product slogan:** A local memory layer for conversations that remember.  
**Technical slogan:** Event-sourced, evidence-grounded, fail-open memory for local LLM workbenches.

**شعار محصولی:** حافظه‌ای محلی برای گفتگوهایی که فراموش نمی‌کنند.  
**شعار فنی:** حافظه رویدادمحور، شاهدبنیاد و fail-open برای میزکارهای لوکال LLM.

---

## English

### 1. Architectural philosophy

Open WebUI Memorist Edition treats memory as a local operating layer for LLM work, not as a loose note-taking feature. The design starts from one constraint: a local user should own the memory, inspect it, export it, and erase it without depending on a remote Memorist server.

The core idea is:

```text
Conversation events are evidence.
Evidence produces memory candidates.
Candidates become memories only after consolidation.
Memories are retrieved through scoped, budget-aware retrieval.
Retrieved memory is attached as data, not as command.
```

This produces a memory system that is auditable, reversible, and safer than simply summarizing old chats into a hidden prompt.

### 2. Main runtime architecture

```text
Open WebUI
  └─ Memorist Filter
       ├─ inlet: capture user message, run preflight, attach memory context
       └─ outlet: capture assistant response

Memorist Core
  ├─ FastAPI routes
  ├─ SQLite source of truth
  ├─ priority write actor / write gateway
  ├─ memory worker pipeline
  ├─ retrieval and attachment builder
  ├─ import / Heritage export / restore
  ├─ privacy and forget workflow
  └─ diagnostics, consistency and recovery checks

Optional projections
  ├─ FTS lexical index
  ├─ embeddings / vector store
  └─ FalkorDB graph projection
```

SQLite is the canonical source. All other stores are projections and must be rebuildable or invalidatable.

### 3. Memory lifecycle

```text
message
→ message_version
→ session_event
→ text_unit
→ analysis
→ memory_candidate
→ evidence link
→ consolidation decision
→ memory
→ memory_version
→ retrieval candidate
→ Memory Context Attachment
→ delivery/usage attribution
```

The system deliberately separates raw evidence, model analysis, candidate claims, consolidated memories, and delivered context. This avoids treating a model-generated sentence as truth too early.

### 4. How memory is recalled

When the user sends a message through Open WebUI:

1. The Filter receives the inbound body.
2. The payload parser extracts user ID, chat/session ID, selected model and message content.
3. The session resolver maps temporary and stable Open WebUI chat IDs to a Memorist session.
4. The user message is captured without modification.
5. Memorist calculates an attachment budget based on the selected model and context window.
6. Retrieval plans are scoped by workspace/project/session and current query intent.
7. Candidate memory is fetched from active blocks, recent session state, FTS, semantic/vector index and optional graph projection.
8. Reranking prefers current, scoped, high-confidence, evidence-backed memories.
9. Conflicts and stale memories are flagged rather than silently flattened.
10. The attachment builder renders selected memory as a separate Memory Context Attachment.
11. The Open WebUI Filter inserts this as separate context while preserving the original user message.
12. If Memorist fails or times out, chat fails open without memory.

### 5. Why attachment is separate from user text

Memory can contain prompt-like or malicious text. Therefore, retrieved memory is never trusted as instruction by default. The attachment is rendered with clear boundaries, escaped delimiters and metadata marking it as memory data. Trusted directives are a separate category and must be promoted through policy, not inferred from ordinary memory.

### 6. Inspirations and how they were adapted

#### Open WebUI

Open WebUI provides the parent local chat UI and extensibility through Filters/Functions. Memorist uses the Filter lifecycle for preflight context attachment and response capture while preserving Open WebUI as the visible parent interface.

#### Event sourcing

The event-sourced design is used to preserve auditability. Instead of overwriting memory, the system stores events, message versions, memory versions and evidence links. This makes correction, rollback, export, restore and forget workflows more reliable.

#### Graphiti-like temporal memory

The architecture borrows the idea of temporally valid facts: memory can have valid time, transaction time, supersession and contradiction. This prevents “latest summary wins” from destroying historical context.

#### Letta-like memory blocks

Active Memory Blocks are compact working projections such as UserProfileBlock, ProjectContextBlock, StylePolicyBlock, PromptRulesBlock, CurrentSessionStateBlock and SafetyPrivacyBlock. They are not the source of truth; they are rebuilt from canonical memory.

#### LangMem-style hot/warm/cold paths

Memorist separates low-latency hot-path work from slower background processing. Chat capture and preflight remain fast; analysis, consolidation, graph projection and import reconstruction run in lower-priority jobs.

#### Cognee / GraphRAG / LightRAG inspiration

The system uses both low-level evidence retrieval and higher-level summarized/clustered context. The goal is to retrieve exact facts when needed and compact project/user context when token budget is tight.

#### HippoRAG-style associative retrieval

Optional graph/associative retrieval supports spreading from focal entities to related memories. It is an enhancement, not a dependency for Lite mode.

#### MemX-like abstention

If memory evidence is weak, stale, conflicting or out of scope, Memorist may abstain rather than inject irrelevant or dangerous context.

#### OpenMemory / portability layer

Heritage export/restore provides a portable local memory package with manifests, checksums and I-JSONL records. This supports user ownership and migration.

#### MemoryOS-style layers

The architecture separates working memory, session memory, project/topic memory, long-term memory and prompt/policy memory.

#### MIRIX-like memory taxonomy

Memory types may include core/profile, episodic, semantic, procedural, resource, prompt/policy, correction and contradiction memory. This helps retrieval and forgetting behave differently for different memory kinds.

#### Jakobson-inspired linguistic analysis

Jakobson-style communication functions are used as annotation signals, not as memory themselves. They help distinguish instruction, preference, emotion, metalinguistic clarification, factual claim and social context.

### 7. Import architecture

Import is treated as historical evidence, not trusted current memory. The flow is:

```text
secure staging
→ adapter detection
→ parse provider export
→ normalize conversation tree
→ dry-run
→ deduplicate
→ commit raw evidence
→ optional reconstruction
→ memory worker processing
```

Heavy import uses priority queues and backpressure so live chat remains responsive.

### 8. Heritage architecture

Heritage export is designed for portability and verification:

```text
manifest.ijson
checksums.sha256
data/*.ijsonl
objects/
reports/
```

Restore into a clean database must preserve canonical UUIDs and rebuild derived indexes rather than trusting exported projections.

### 9. Forget architecture

Forget is not just deletion. It is a dependency workflow:

```text
preview
→ confirm
→ quarantine
→ erase/redact canonical records
→ invalidate derived artifacts
→ run residue check
→ produce receipt without raw erased content
```

It checks canonical tables, FTS, embeddings, blocks, attachments, hot cache, import records and future Heritage export. Graph-specific checks are skipped with reason when graph is disabled.

### 10. Public beta architecture requirements

Before public beta, the project should add:

- fully automated Open WebUI container smoke for the pinned target version;
- source package cleanup;
- GitHub-ready README and architecture README;
- security policy and issue templates;
- release CI or script-equivalent CI;
- broader Open WebUI version compatibility matrix;
- stronger Heritage object-store payload export or explicit limitation;
- first-run documentation for non-developer local testers.

---

## فارسی

### ۱. فلسفه معماری

Open WebUI Memorist Edition حافظه را یک لایه عملیاتی محلی برای کار با LLM می‌داند، نه یک قابلیت ساده note-taking. نقطه شروع طراحی این است که کاربر محلی باید مالک حافظه باشد، بتواند آن را inspect کند، export کند و erase کند، بدون وابستگی به یک سرور ابری Memorist.

ایده اصلی:

```text
رویدادهای مکالمه شاهد هستند.
شاهدها memory candidate می‌سازند.
candidateها فقط بعد از consolidation حافظه می‌شوند.
حافظه با scope و token budget مناسب فراخوانی می‌شود.
حافظه فراخوانی‌شده به‌عنوان داده وارد context می‌شود، نه دستور.
```

این طراحی باعث می‌شود حافظه قابل audit، قابل rollback، قابل export و قابل forget باشد.

### ۲. معماری runtime

```text
Open WebUI
  └─ Memorist Filter
       ├─ inlet: ثبت پیام کاربر، preflight، اتصال context حافظه
       └─ outlet: ثبت پاسخ assistant

Memorist Core
  ├─ FastAPI routes
  ├─ SQLite source of truth
  ├─ priority write actor / write gateway
  ├─ memory worker pipeline
  ├─ retrieval و attachment builder
  ├─ import / Heritage export / restore
  ├─ privacy و forget workflow
  └─ diagnostics، consistency و recovery checks

projectionهای اختیاری
  ├─ FTS lexical index
  ├─ embedding/vector store
  └─ FalkorDB graph projection
```

SQLite منبع حقیقت است. بقیه storeها projection هستند و باید قابل rebuild یا invalidate باشند.

### ۳. چرخه عمر حافظه

```text
message
→ message_version
→ session_event
→ text_unit
→ analysis
→ memory_candidate
→ evidence link
→ consolidation decision
→ memory
→ memory_version
→ retrieval candidate
→ Memory Context Attachment
→ delivery/usage attribution
```

این تفکیک اجازه نمی‌دهد خروجی مدل بلافاصله به‌عنوان حقیقت وارد حافظه شود.

### ۴. حافظه چگونه فراخوانی می‌شود؟

وقتی کاربر در Open WebUI پیام می‌فرستد:

1. Filter بدنه پیام را در inlet دریافت می‌کند.
2. payload parser اطلاعات کاربر، chat/session ID، مدل انتخاب‌شده و متن پیام را استخراج می‌کند.
3. session resolver آیدی‌های موقت و پایدار Open WebUI را به session محلی Memorist وصل می‌کند.
4. پیام کاربر بدون تغییر ثبت می‌شود.
5. Memorist براساس مدل انتخاب‌شده و context window بودجه attachment را محاسبه می‌کند.
6. retrieval با scope پروژه/نشست/workspace و intent فعلی برنامه‌ریزی می‌شود.
7. حافظه‌های کاندید از active blocks، وضعیت نشست، FTS، vector index و graph اختیاری بازیابی می‌شوند.
8. reranking حافظه‌های current، scoped، high-confidence و evidence-backed را ترجیح می‌دهد.
9. حافظه‌های متعارض یا قدیمی برچسب می‌خورند، نه اینکه بی‌صدا merge شوند.
10. attachment builder حافظه منتخب را به‌صورت Memory Context Attachment جداگانه render می‌کند.
11. Filter آن را جدا از متن کاربر وارد context می‌کند.
12. اگر Memorist fail یا timeout شود، چت بدون حافظه ادامه پیدا می‌کند.

### ۵. چرا attachment از متن کاربر جداست؟

حافظه ممکن است شامل متن مخرب یا شبیه prompt باشد. بنابراین حافظه بازیابی‌شده به‌صورت پیش‌فرض instruction نیست. attachment با delimiter، escaping و metadata مشخص render می‌شود تا «داده حافظه» باقی بماند. trusted directiveها مسیر جداگانه دارند و نباید از حافظه معمولی حدس زده شوند.

### ۶. الهام‌ها و نحوه استفاده در معماری ما

#### Open WebUI

Open WebUI رابط محلی والد است. Memorist از Filter lifecycle برای preflight و capture استفاده می‌کند و هویت Open WebUI را حفظ می‌کند.

#### Event sourcing

برای auditability استفاده شده است. به‌جای overwrite کردن حافظه، event، message version، memory version و evidence link نگه داشته می‌شود.

#### Graphiti-like temporal memory

از ایده factهای temporal الهام گرفته شده است: حافظه می‌تواند valid time، transaction time، supersession و contradiction داشته باشد. این جلوی نابودشدن تاریخچه با یک summary جدید را می‌گیرد.

#### Letta-like memory blocks

Active Memory Blocks مثل UserProfileBlock، ProjectContextBlock، StylePolicyBlock، PromptRulesBlock، CurrentSessionStateBlock و SafetyPrivacyBlock projectionهای compact هستند، نه source of truth.

#### LangMem-style hot/warm/cold paths

کارهای سریع chat capture و preflight از تحلیل و consolidation و graph projection و import reconstruction جدا شده‌اند.

#### Cognee / GraphRAG / LightRAG

از ترکیب retrieval دقیق مبتنی بر evidence و retrieval سطح بالاتر مبتنی بر context/project/profile الهام گرفته شده است.

#### HippoRAG-style associative retrieval

graph/associative retrieval اختیاری می‌تواند از entityهای محوری به memoryهای مرتبط spread کند. Lite mode به آن وابسته نیست.

#### MemX-like abstention

اگر حافظه ضعیف، قدیمی، متعارض یا خارج از scope باشد، سیستم بهتر است abstain کند تا context غلط تزریق کند.

#### OpenMemory / portability

Heritage export/restore برای مالکیت و جابه‌جایی حافظه محلی طراحی شده است.

#### MemoryOS-style layers

حافظه به working، session، project/topic، long-term و prompt/policy تقسیم می‌شود.

#### MIRIX-like taxonomy

انواع حافظه شامل core/profile، episodic، semantic، procedural، resource، prompt/policy، correction و contradiction هستند.

#### تحلیل زبانی الهام‌گرفته از Jakobson

کارکردهای ارتباطی Jakobson به‌عنوان annotation signal استفاده می‌شوند، نه خود حافظه. هدف تفکیک instruction، preference، emotion، clarification، factual claim و social context است.

### ۷. معماری import

Import به‌عنوان شاهد تاریخی وارد می‌شود، نه حافظه trusted فعلی:

```text
secure staging
→ adapter detection
→ parse provider export
→ normalize conversation tree
→ dry-run
→ deduplicate
→ commit raw evidence
→ optional reconstruction
→ memory worker processing
```

import سنگین با priority queue و backpressure کنترل می‌شود تا live chat کند نشود.

### ۸. معماری Heritage

Heritage برای portability و verification طراحی شده است:

```text
manifest.ijson
checksums.sha256
data/*.ijsonl
objects/
reports/
```

در restore، canonical UUIDها حفظ می‌شوند و derived indexes دوباره ساخته می‌شوند.

### ۹. معماری Forget

Forget فقط deletion نیست:

```text
preview
→ confirm
→ quarantine
→ erase/redact canonical records
→ invalidate derived artifacts
→ residue check
→ receipt بدون محتوای خام حذف‌شده
```

این مسیر canonical tables، FTS، embeddings، blocks، attachments، hot cache، import records و exportهای آینده را بررسی می‌کند.

### ۱۰. الزامات معماری برای public beta

قبل از بتای عمومی باید اضافه شود:

- Open WebUI container smoke کاملاً خودکار برای نسخه پین‌شده؛
- پاک‌سازی source package؛
- README و architecture README گیت‌هابی؛
- security policy و issue templates؛
- CI یا معادل script-based؛
- ماتریس سازگاری Open WebUI؛
- export کامل‌تر object-store در Heritage یا limitation صریح؛
- راهنمای first-run برای testerهای غیرتوسعه‌دهنده.
