# وایت‌پیپر Open WebUI Memorist Edition

**عنوان پیشنهادی محصول:** Open WebUI Memorist Edition  
**شعار محصولی:** حافظه‌ای محلی برای گفتگوهایی که فراموش نمی‌کنند.  
**شعار فنی:** حافظه رویدادمحور، شاهدبنیاد و fail-open برای میزکارهای لوکال LLM.  
**وضعیت سند:** Draft v0.1 — برای تکامل پیش از Public Beta  
**مخاطب:** توسعه‌دهندگان LLM، پژوهشگران حافظه عامل‌ها، کاربران power-user و maintainers پروژه  

---

## چکیده

مدل‌های زبانی بزرگ در ظاهر «به‌خاطر می‌آورند»، اما در عمل بیشتر آنچه به‌عنوان حافظه تجربه می‌شود، محتوایی است که همان لحظه در پنجره کانتکست قرار گرفته است. با خروج اطلاعات از کانتکست، مدل دوباره بی‌حافظه می‌شود؛ با افزایش کانتکست، هزینه و آشفتگی بالا می‌رود؛ و با اتکا به RAG ساده، حافظه اغلب به مجموعه‌ای از قطعات متن بی‌زمان، بی‌اعتبارسنجی و بی‌تبار تبدیل می‌شود. مسئله اصلی Memorist این است: چگونه می‌توان برای یک محیط گفتگوی لوکال، حافظه‌ای ساخت که نه فقط «retrieval» کند، بلکه سابقه، تغییرات زمانی، شواهد، اعتماد، فراموشی، import/export و کنترل کاربر را به‌عنوان اجزای درجه‌یک معماری در نظر بگیرد؟

Open WebUI Memorist Edition یک معماری local-first برای افزودن حافظه پایدار، قابل audit و شاهدبنیاد به Open WebUI است. Open WebUI نقش رابط مادر را حفظ می‌کند و Memorist به‌عنوان لایه حافظه محلی عمل می‌کند. طراحی بر چند اصل بنا شده است: SQLite به‌عنوان source of truth، event sourcing برای lineage، حافظه‌های کاندید قبل از تثبیت، گراف زمانی برای حقایق در حال تغییر، بلوک‌های حافظه فعال، retrieval چندسطحی، Memory Context Attachment جدا از متن کاربر، fail-open بودن مسیر چت، import/Heritage export، و فراموشی dependency-aware.

این سند مسئله، روش حل، الهام‌ها، گپ‌های پروژه‌های پیشین، معماری پیشنهادی، چرخه حافظه، مسیر فراخوانی حافظه، ملاحظات امنیتی و مسیر رسیدن به public beta را تشریح می‌کند.

---

## 1. مسئله: چرا «حافظه» در چت LLM هنوز حل نشده است؟

### 1.1 حافظه پارامتریک کافی نیست

دانش ذخیره‌شده در وزن‌های مدل، حافظه شخصی کاربر نیست. مدل ممکن است درباره جهان عمومی بداند، اما نمی‌داند کاربر در پروژه دیروز چه تصمیمی گرفت، کدام ترجیحش تغییر کرد، کدام دستور را لغو کرد، یا کدام داده باید فراموش شود. fine-tuning نیز برای حافظه شخصی روزمره، کند، پرهزینه و سخت‌قابل‌حذف است.

### 1.2 پنجره کانتکست حافظه نیست

افزایش context window کمک می‌کند، اما حافظه نیست. کانتکست طولانی گران‌تر است، مستعد تزریق دستورهای ناخواسته است، و الزاماً مدل را به انتخاب درست بین «اطلاعات فعلی»، «اطلاعات قدیمی»، «اطلاعات متناقض» و «اطلاعات نامطمئن» وادار نمی‌کند. هر چه کانتکست بیشتر شود، نیاز به انتخاب، فشرده‌سازی و اعتمادسنجی دقیق‌تر بیشتر می‌شود.

### 1.3 RAG ساده حافظه شخصی نیست

RAG کلاسیک با اتصال مدل به یک index بیرونی، راه حل مهمی برای grounding است، اما در شکل ساده خود چند گپ دارد:

- زمان و تغییرات را ضعیف مدل می‌کند؛
- شواهد و provenance را همیشه به سطح تصمیم حافظه نمی‌آورد؛
- conflict و supersession را جدی نمی‌گیرد؛
- تفاوت «گفته کاربر»، «استنباط مدل»، «نقل قول»، «دستور فعلی» و «حافظه قابل تزریق» را مخلوط می‌کند؛
- حذف و فراموشی مشتقات را دشوار می‌کند؛
- بیشتر برای اسناد طراحی شده، نه جریان زنده گفتگوی شخصی.

### 1.4 حافظه بدون فراموشی خطرناک است

سیستم حافظه‌ای که فقط ذخیره می‌کند، اما فراموشی، ابطال، residue check و export بعد از erasure ندارد، برای استفاده طولانی‌مدت قابل اعتماد نیست. حافظه شخصی باید بتواند چیزی را حذف، قدیمی، مشکوک، محرمانه یا scope-limited کند و بعد ثابت کند آن چیز از مسیرهای retrieval و attachment برنمی‌گردد.

### 1.5 حافظه در رابط‌های چت موجود باید non-invasive باشد

Open WebUI یک رابط قوی برای کار با مدل‌های مختلف است. Memorist نباید جای Open WebUI را بگیرد، هویت آن را بپوشاند، یا prompt کاربر را خام دست‌کاری کند. باید در مسیر extension رسمی و قابل فهم عمل کند، و اگر حافظه خراب شد، چت کاربر از کار نیفتد.

---

## 2. تز اصلی Memorist

تز معماری Memorist این است:

> حافظه LLM نباید یک vector DB کنار چت باشد؛ باید یک سیستم محلی، رویدادمحور، شاهدبنیاد، زمان‌آگاه و قابل‌فراموشی باشد که فقط بخش لازم، معتبر و scope-safe را به‌صورت attachment جداگانه وارد کانتکست کند.

این تز چند نتیجه مستقیم دارد:

1. **Raw chat حافظه نیست؛ evidence است.**  
   پیام‌ها منبع شواهدند، نه لزوماً memory fact.

2. **LLM extraction حقیقت نیست؛ hypothesis است.**  
   خروجی مدل فقط candidate می‌سازد و باید با evidence، authority، confidence و consolidation بررسی شود.

3. **حافظه باید lineage داشته باشد.**  
   هر حافظه باید قابل ردیابی به پیام، text unit، analysis، candidate و evidence باشد.

4. **حافظه باید زمان‌آگاه باشد.**  
   ترجیح کاربر ممکن است در گذشته درست بوده و امروز غلط باشد. حافظه باید هم تاریخ اعتبار و هم زمان مشاهده/ثبت را نگه دارد.

5. **حافظه باید قابل تزریق کنترل‌شده باشد.**  
   همه حافظه‌ها نباید وارد prompt شوند. attachment باید بودجه، scope، trust و security داشته باشد.

6. **حافظه باید قابل فراموشی باشد.**  
   فراموشی باید مشتقات را هم پوشش دهد: FTS، embeddings، graph، blocks، attachments، cache، export و object store.

---

## 3. مرور مختصر پیشینه و گپ‌ها

### 3.1 RAG و حافظه غیرپارامتریک

RAG نشان داد که اتصال مدل به حافظه غیرپارامتریک می‌تواند پاسخ‌ها را groundedتر کند. اما RAG کلاسیک بیشتر به retrieval اسناد می‌پردازد تا حافظه شخصی زمان‌مند. Memorist از RAG اصل «حافظه بیرونی قابل بازیابی» را می‌گیرد، اما آن را با lineage، temporal state، trust separation و erasure ترکیب می‌کند.

### 3.2 GraphRAG، LightRAG و گراف به‌عنوان لایه معنا

GraphRAG و LightRAG نشان دادند که ساختار گرافی می‌تواند از retrieval تخت بهتر باشد، چون اطلاعات به entity، relation و community وصل می‌شود. اما Memorist به جای ساخت صرفاً یک knowledge graph از اسناد، با یک memory graph رویدادمحور و زمان‌آگاه سروکار دارد. در اینجا گراف projection است، نه source of truth. اگر گراف خراب شود، SQLite canonical state می‌تواند آن را دوباره بسازد.

### 3.3 Graphiti/Zep و temporal context graph

Graphiti ایده temporal context graph را جدی می‌کند: روابط در زمان زندگی می‌کنند و با تغییر facts، facts قدیمی invalid می‌شوند. Memorist از این ایده برای bi-temporal fact/memory handling الهام می‌گیرد، اما آن را local-first و قابل audit نگه می‌دارد. هدف ما فقط retrieval بهتر نیست؛ هدف ما دانستن این است که «در چه زمانی چه چیزی درست تلقی می‌شد و چرا».

### 3.4 Letta/MemGPT و memory blocks

Letta و MemGPT مسئله stateful agents و حافظه را به سطح agent runtime آوردند. ایده memory blocks برای کنترل بخش‌های فعال context بسیار مهم است. Memorist از این ایده برای Active Memory Blocks الهام می‌گیرد: UserProfileBlock، ProjectContextBlock، StylePolicyBlock، PromptRulesBlock، CurrentSessionStateBlock و SafetyPrivacyBlock. تفاوت اینجاست که blocks در Memorist truth نیستند؛ projectionهای نسخه‌دار از canonical memories هستند.

### 3.5 LangMem و prompt improvement

LangMem بر استخراج اطلاعات مهم از تعاملات، نگهداری long-term memory و بهبود رفتار عامل‌ها از طریق prompt refinement تأکید می‌کند. Memorist این ایده را به Prompt Memory و PromptPatch محدود و auditپذیر تبدیل می‌کند. هر بهبود prompt باید منبع، دلیل، scope و trust داشته باشد.

### 3.6 HippoRAG و associative retrieval

HippoRAG با الهام از نظریه indexing هیپوکامپ، retrieval را از بردار ساده به association و graph traversal می‌برد. Memorist از این جهت الهام می‌گیرد، اما در نسخه لوکال و budget-aware خود associative retrieval را optional و fallbackپذیر نگه می‌دارد. مسیر اصلی همچنان باید در Lite mode بدون گراف هم کار کند.

### 3.7 MemOS/MemoryOS و memory as a system resource

MemOS و MemoryOS حافظه را نه یک feature، بلکه یک resource مدیریتی می‌بینند: ذخیره، به‌روزرسانی، retrieval، استفاده، نسخه‌بندی و governance. Memorist همین نگاه را در مقیاس Open WebUI local پیاده می‌کند: memory storage، memory processing، memory attachment، import/export، erasure، recovery و diagnostics یک سیستم واحدند.

### 3.8 MIRIX و taxonomy حافظه

MIRIX حافظه را به انواع مختلف مانند core، episodic، semantic، procedural و resource تقسیم می‌کند. Memorist از این ایده برای taxonomy داخلی استفاده می‌کند، اما آن را برای کاربر لوکال و Open WebUI ساده‌تر می‌کند: Core/Profile، Episodic، Semantic، Procedural/Prompt، Resource/Document، Correction/Contradiction.

### 3.9 OpenMemory، Cognee و حافظه local/portable

پروژه‌هایی مثل OpenMemory و Cognee نشان می‌دهند که بازار به سمت memory layerهای local، graph-aware و قابل اتصال به ابزارها می‌رود. Memorist گپ خاصی را هدف می‌گیرد: یک نسخه محلی، Open WebUI-native، evidence-grounded و privacy/forget-aware که خاموشی یا خرابی آن چت اصلی را خراب نکند.

### 3.10 Jakobson و تحلیل کارکردی زبان

الهام از Jakobson در Memorist به این معنا نیست که سیستم یک نظریه زبان‌شناسی را کامل پیاده می‌کند. استفاده ما عملیاتی است: پیام‌ها فقط «اطلاعات factual» نیستند. بعضی پیام‌ها ترجیح، دستور، احساس، maintenance channel، تعریف اصطلاح، یا اصلاح رفتارند. نگاشت تقریبی کارکردهای referential، emotive، conative، phatic، metalingual و poetic به analysis stage کمک می‌کند حافظه‌سازی بی‌رویه کاهش یابد.

---

## 4. گپ‌هایی که Memorist سعی می‌کند پر کند

### گپ 1: حافظه بدون source of truth قابل audit

بسیاری از memory layerها روی index یا graph تأکید می‌کنند. Memorist source of truth را SQLite event/source store می‌داند و graph/FTS/embeddings را projection می‌گیرد. این باعث می‌شود rebuild، verification، erasure و Heritage export ممکن شود.

### گپ 2: مخلوط شدن memory و instruction

یکی از خطرهای بزرگ memory injection این است که memory content به instruction تبدیل شود. Memorist attachment را data می‌داند، نه directive. trusted directives فقط از policyهای تأییدشده، constraints فعلی و تنظیمات صریح می‌آیند.

### گپ 3: زمان و supersession

کاربر ممکن است بگوید «من از سبک رسمی خوشم نمی‌آید» و ماه بعد بگوید «برای این پروژه رسمی بنویس». سیستم باید بتواند current preference و historical preference را جدا کند. Memorist valid time، observed time، supersession و conflict را در consolidation نگه می‌دارد.

### گپ 4: import بدون heritage و dedupe قابل اعتماد

انتقال تاریخچه چت‌ها اگر بدون dry-run، source mapping، dedupe، adapter detection و cost warning باشد، می‌تواند حافظه را آلوده کند. Memorist import را به‌عنوان evidence ingestion می‌بیند، نه trust injection.

### گپ 5: فراموشی بدون residue check

حذف row اصلی کافی نیست. Memorist forget را dependency-aware طراحی می‌کند: canonical DB، FTS، embeddings، graph، blocks، attachments، hot cache، imports، exports و object store باید بررسی شوند.

### گپ 6: memory engine که چت را می‌شکند

یک memory layer نباید اگر خراب شد رابط اصلی را خراب کند. Memorist در مسیر Open WebUI fail-open است: اگر preflight timeout دهد یا core down باشد، user chat ادامه می‌یابد.

---

## 5. معماری کلان

معماری Memorist شامل لایه‌های زیر است:

1. **Open WebUI Integration Layer**  
   Filter inlet/outlet، payload parser، session resolver، fail-open client.

2. **Memorist Core API**  
   FastAPI service برای capture، preflight، diagnostics، import/export، privacy، jobs.

3. **SQLite Canonical Store**  
   source of truth شامل sessions، messages، events، memories، versions، evidence، imports، privacy requests.

4. **Write Actor / Write Gateway**  
   کنترل single-writer برای SQLite، priority، backpressure، idempotency و metrics.

5. **Memory Worker**  
   unitization، gating، linguistic/conceptual analysis، candidate extraction، consolidation.

6. **Derived Retrieval Layers**  
   FTS، optional embeddings، optional graph/FalkorDB، active blocks، hot cache.

7. **Attachment Builder**  
   تولید Memory Context Attachment با budget، scope، trust و provenance.

8. **Import/Heritage Layer**  
   staging، adapter detection، dry-run، dedupe، commit، export، verify، restore، compare.

9. **Privacy/Forget Layer**  
   dependency closure، quarantine، erasure/redaction، residue check، receipt.

10. **Reliability Layer**  
   consistency checker، recovery، backup، package scanner، release gates.

---

## 6. چرخه کامل حافظه

### 6.1 Capture

وقتی کاربر در Open WebUI پیام می‌دهد، Filter ورودی را می‌بیند. Memorist session را resolve می‌کند، پیام را بدون تغییر ذخیره می‌کند، و اگر امکان دارد preflight را اجرا می‌کند. پیام کاربر byte-for-byte حفظ می‌شود.

### 6.2 Unitization

پیام به text unitهای کوچک‌تر تقسیم می‌شود. این کار باید deterministic باشد تا evidence span قابل audit بماند.

### 6.3 Gating

همه چیز نباید حافظه شود. پیام‌هایی که صرفاً سلام، noise، code bulk یا محتوای حساس هستند، ممکن است retain_raw_only یا reject شوند. پیام‌هایی که preference، decision، constraint، correction یا project fact دارند به analysis می‌روند.

### 6.4 Linguistic and conceptual analysis

اینجا از تحلیل کارکردی زبان، speech act، entity extraction، temporal expression، modality و memory signals استفاده می‌شود. این مرحله chain-of-thought ذخیره نمی‌کند؛ فقط خروجی structured و قابل validation می‌دهد.

### 6.5 Candidate extraction

از unitهای معتبر، memory candidate ساخته می‌شود. candidate هنوز حقیقت نیست. باید subject/predicate/object یا value، evidence span، confidence، authority و valid time داشته باشد.

### 6.6 Consolidation

candidate با حافظه‌های موجود مقایسه می‌شود و یکی از تصمیم‌ها گرفته می‌شود: ADD، REINFORCE، UPDATE، SUPERSEDE، CONTRADICT، RETRACT، REJECT یا MANUAL_REVIEW. اینجا زمان، conflict و scope اعمال می‌شود.

### 6.7 Projection

حافظه تثبیت‌شده به FTS، optional vector، optional graph، blocks و hot cache project می‌شود. Projectionها rebuildپذیرند.

### 6.8 Retrieval

در preflight، query planner تصمیم می‌گیرد چه scopeهایی مجازند، چه نوع حافظه‌ای لازم است، current یا historical بودن پرسش چیست، و budget چقدر است. سپس retrieval از exact/FTS/semantic/graph/recent/hot cache ترکیب می‌شود.

### 6.9 Attachment

نتیجه retrieval مستقیماً به prompt کاربر چسبانده نمی‌شود. Attachment جدا ساخته می‌شود که شامل بخش‌هایی مثل trusted directives، current context، relevant memories، conflicts، provenance و security labels است.

### 6.10 Delivery and feedback

سیستم ثبت می‌کند کدام حافظه retrieve، selected، rendered و injected شد. بعداً می‌توان feedback یا correction را به lineage وصل کرد.

---

## 7. حافظه چگونه فراخوانی می‌شود؟

فراخوانی حافظه در Memorist یک call ساده به vector DB نیست؛ یک فرایند چندمرحله‌ای است:

1. **تشخیص مدل مقصد و بودجه کانتکست**  
   چون مدل‌ها context window متفاوت دارند، attachment budget به صورت adaptive محاسبه می‌شود.

2. **تشخیص scope**  
   حافظه فقط از session/project/workspace مجاز می‌آید. cross-project leakage نباید رخ دهد.

3. **Query planning**  
   سیستم تشخیص می‌دهد پرسش درباره ترجیح کاربر است، تصمیم پروژه است، constraint فعلی است، history است یا نیاز به abstention دارد.

4. **Candidate generation**  
   از مسیرهای exact key، FTS، semantic، graph، recent session، active blocks و hot cache candidate گرفته می‌شود.

5. **Reranking**  
   بر اساس relevance، confidence، authority، recency، scope، evidence quality، conflict و sensitivity امتیازدهی می‌شود.

6. **Abstention**  
   اگر شواهد کافی نیست یا conflict unresolved است، سیستم نباید حافظه نامطمئن را با قطعیت تزریق کند.

7. **Rendering**  
   attachment با delimiter escaping، trust separation و provenance ساخته می‌شود.

8. **Injection**  
   Filter attachment را به‌صورت پیام جداگانه وارد context می‌کند، نه با تغییر متن کاربر.

---

## 8. Memory Context Attachment

Memory Context Attachment قلب interface بین memory و LLM است. ساختار پیشنهادی:

- metadata: attachment_uuid، mode، budget، model profile؛
- trusted_directives: فقط policyها و constraints تأییدشده؛
- current_context: hot cache و active project context؛
- relevant_memories: حافظه‌های منتخب با source و confidence؛
- historical_context: اگر query تاریخی باشد؛
- conflicts_and_uncertainty: contradiction و unresolved facts؛
- provenance: source message IDs، memory version IDs؛
- security: untrusted flags، delimiter escaping، sensitive exclusions.

اصل مهم: attachment به مدل کمک می‌کند context داشته باشد، اما خودش نباید system prompt را override کند.

---

## 9. امنیت و trust boundary

### 9.1 Prompt injection از حافظه

حافظه می‌تواند شامل عباراتی مثل «دستورهای قبلی را نادیده بگیر» باشد. اگر این متن به trusted directive تبدیل شود، حافظه تبدیل به مسیر حمله می‌شود. Memorist چنین متن‌هایی را untrusted data می‌داند و آن‌ها را escape و flag می‌کند.

### 9.2 Sensitive Information Disclosure

حافظه شخصی ممکن است secrets، tokenها، آدرس‌ها یا محتوای خصوصی داشته باشد. سیاست پیش‌فرض باید حداقلی باشد: logها sanitized، status بدون raw memory، package بدون secret، receipt بدون متن حذف‌شده.

### 9.3 Open WebUI Filter risk

Filterها کد server-side هستند. بنابراین integration باید source-controlled، local، fail-open و documented باشد. نصب Filter ناشناس یا remote، خارج از محدوده اعتماد پروژه است.

---

## 10. Local-first و انتخاب SQLite

Memorist برای کاربر لوکال طراحی شده است. SQLite انتخاب مناسبی است چون سبک، portable و قابل backup است. اما SQLite در WAL فقط یک writer هم‌زمان دارد. بنابراین Memorist از write actor، bounded batches، busy timeout، retry/backoff و import backpressure استفاده می‌کند. این design برای یک کاربر لوکال و import متوسط/سنگین مناسب است، اما برای سرویس چندکاربره عمومی کافی نیست.

---

## 11. Import و Heritage

### 11.1 Import

Import فقط «کپی پیام‌ها» نیست. مسیر درست:

archive → secure staging → adapter detection → parse → normalize → dry-run → dedupe → commit → optional reconstruction.

محتوای import شده historical evidence است، نه حافظه trusted. memory reconstruction باید opt-in و cost-aware باشد.

### 11.2 Heritage Export

Heritage package باید شامل manifest، checksums، I-JSONL data، reports و امکان verify/restore باشد. هدف فقط portability نیست؛ هدف auditability و حفظ lineage است.

### 11.3 Restore

Restore باید DB جدید را بسازد، canonical equivalence را مقایسه کند، derived indexes را rebuild کند و erasure ledger را رعایت کند تا داده فراموش‌شده resurrect نشود.

---

## 12. Forget و residue

فراموشی در Memorist دو مرحله‌ای است:

preview → confirm → quarantine → execute → invalidate derived artifacts → residue check → receipt.

Quarantine باید سریع باشد تا target دیگر وارد retrieval و attachment نشود. Cleanup عمیق ممکن است زمان‌بر باشد، اما status باید visible و resumable باشد.

Residue check باید حداقل این مسیرها را ببیند:

- canonical SQLite؛
- FTS؛
- embeddings؛
- graph projection؛
- blocks؛
- attachments؛
- hot cache؛
- object store؛
- import staging؛
- future Heritage export؛
- logs در حد امکان.

رسید erasure نباید raw erased content داشته باشد.

---

## 13. روش ارزیابی

ارزیابی Memorist باید چند دسته داشته باشد:

1. **Unit tests** برای validatorها، repositories، budget، sanitizer؛
2. **Integration tests** برای Open WebUI filter contract؛
3. **Daily smoke** برای مسیر واقعی capture/preflight/diagnostics؛
4. **Heavy import smoke** با profileهای ci-small، small-heavy، local-heavy؛
5. **Heritage roundtrip** با golden DB غنی؛
6. **Forget residue** چندلایه؛
7. **Consistency checker** روی DB post-import؛
8. **Recovery tests** برای import/forget/export interrupted؛
9. **Security tests** برای prompt injection و leakage؛
10. **Open WebUI container smoke** برای رسیدن به beta عمومی.

نکته: LLM-as-judge نباید ستون اصلی validation باشد. invariants باید deterministic باشند.

---

## 14. تمایز معماری Memorist

Memorist نه فقط RAG است، نه فقط memory blocks، نه فقط graph memory، نه فقط import/export. تمایز آن در ترکیب این اصول است:

- local-first؛
- Open WebUI-native؛
- SQLite source of truth؛
- event-sourced lineage؛
- evidence-grounded memory consolidation؛
- temporal/supersession-aware memory؛
- active blocks as projections, not truth؛
- fail-open attachment injection؛
- import as evidence, not trust؛
- Heritage portability؛
- dependency-aware forgetting؛
- beta-grade test gates.

---

## 15. محدودیت‌ها

نسخه beta نباید ادعا کند:

- production-ready است؛
- همه نسخه‌های Open WebUI را پشتیبانی می‌کند؛
- prompt injection را کامل حذف می‌کند؛
- physical deletion را روی همه filesystem/SSDها تضمین می‌کند؛
- برای multi-user hosted service آماده است؛
- object-store payload را کامل در Heritage پوشش می‌دهد اگر هنوز implement نشده؛
- graph mode کاملاً پایدار است اگر FalkorDB smoke کامل نشده.

---

## 16. مسیر اولیه تا Public Beta (تاریخی)

این بخش roadmap اولیه‌ی whitepaper است و وضعیت runtime جاری را بیان نمی‌کند.
نسخه، schema و مسیر پیاده‌سازی فعلی در
[معماری موتور حافظه](memory-engine-architecture.fa.md) و
[walkthrough مرکزی](../reference/core-memory-processing-walkthrough.md) ثبت
شده‌اند.

1. clean source package؛
2. GitHub-ready README؛
3. architecture README انگلیسی/فارسی؛
4. SECURITY، CONTRIBUTING، issue templates؛
5. script-based beta_check؛
6. Open WebUI container smoke against pinned v0.9.6؛
7. source package scan؛
8. release package scan؛
9. beta readiness report؛
10. tag `v0.2.0-beta.1` با محدودیت‌های صادقانه.

---

## 17. نتیجه‌گیری

حافظه در LLMها دیگر یک feature جانبی نیست؛ یک لایه زیرساختی است. اما حافظه‌ای که فقط ذخیره می‌کند، فقط retrieve می‌کند، یا فقط prompt را بزرگ‌تر می‌کند، برای استفاده بلندمدت کافی نیست. Memorist سعی می‌کند حافظه را به شکل یک سیستم محلی، قابل audit، زمان‌آگاه، شاهدبنیاد، قابل export و قابل فراموشی تعریف کند.

اگر Open WebUI میزکار لوکال مدل‌ها باشد، Memorist می‌تواند لایه حافظه‌ای باشد که گفتگوها را از «جلسات جدا و فراموش‌شونده» به «روندهای پیوسته، قابل بازبینی و قابل کنترل» تبدیل کند.

---

## منابع منتخب و الهام‌ها

[S1] Open WebUI documentation — Filter Functions and extensibility lifecycle.  
[S2] Open WebUI documentation — Docker install/update and persistent volume model.  
[S3] Lewis et al., Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks, 2020.  
[S4] Microsoft GraphRAG documentation and paper, 2024.  
[S5] LightRAG paper and project, 2024.  
[S6] HippoRAG paper, 2024; HippoRAG 2, 2025.  
[S7] Zep/Graphiti temporal knowledge graph paper and project, 2025.  
[S8] Letta/MemGPT documentation and agent memory materials.  
[S9] LangMem documentation and SDK materials.  
[S10] Mem0 / OpenMemory materials.  
[S11] Cognee memory platform and knowledge graph materials.  
[S12] MemOS / MemoryOS papers, 2025.  
[S13] MIRIX multi-agent memory system paper, 2025.  
[S14] Roman Jakobson, linguistic functions of language.  
[S15] OWASP Top 10 for LLM Applications / GenAI risks.  
[S16] SQLite WAL and Online Backup documentation.
