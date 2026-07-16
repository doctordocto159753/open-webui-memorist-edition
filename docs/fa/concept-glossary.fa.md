# واژه‌نامه مفهومی

این واژه‌نامه خط مبنای فعلی را قبل از چهار گام نهایی تثبیت می‌کند.

- `Raw Message`: پیام خام کاربر، دستیار، سیستم یا ابزار که از Open WebUI یا import دریافت می‌شود و به‌عنوان evidence حفظ می‌گردد.
- `Text Unit`: واحد قطعه‌بندی فعلی همراه offset. در baseline فعلی می‌تواند جمله، بخش پاراگراف یا utterance فشرده باشد.
- `Sentence Unit`: واحد جمله‌ای درجه‌اول Step 1. offset دقیق دارد و با Text Unitهای broad/legacy یکی فرض نمی‌شود.
- `Jakobson Annotation`: annotation جمله‌ای Step 1 با شش عامل یاکوبسن روی Sentence Unit. این اکنون pipeline معنایی اصلی baseline است.
- `Memory Signal Route`: تصمیم routing پیاده‌سازی‌شده Step 1 از annotation یاکوبسن به مسیر extractor تخصصی.
- `Memory Candidate`: حافظه پیشنهادی دارای evidence که هنوز canonical memory نشده است.
- `Memory`: هویت canonical حافظه محلی.
- `Memory Version`: نسخه زمانی/قابل audit از Memory. اصلاح و به‌روزرسانی history را بازنویسی نمی‌کند و نسخه جدید می‌سازد.
- `Memory Context Attachment`: زمینه حافظه محدود و untrusted که در preflight جدا از prompt کاربر به Open WebUI داده می‌شود.
- `Canonical Store`: لایه ذخیره‌سازی مرجع. در baseline فعلی SQLite Lite است؛ PostgreSQL canonical برای Step 2 برنامه‌ریزی شده است.
- `Projection`: نمای مشتق‌شده مانند index یا graph که از canonical state ساخته می‌شود و source of truth نیست.
- `Lite Mode`: baseline محلی پشتیبانی‌شده فعلی با SQLite و مسیرهای local object.
- `Full Mode`: هدف Step 2 شامل PostgreSQL canonical store و FalkorDB projection/runtime profile. Full compose فعلی experimental است و beta-supported محسوب نمی‌شود.
- `Model Control Plane`: سطح role/profile/default/usage/privacy که مدل چت اصلی Open WebUI را از نقش‌های حافظه Memorist جدا می‌کند. baseline فعلی به‌عنوان backend/runtime baseline پیاده‌سازی شده و UI polish و orchestration گسترده‌تر در گام‌های بعدی سخت‌سازی می‌شود.
- `Prompt Pack`: مجموعه versioned از system promptها، schema ورودی/خروجی، validatorها، قواعد evidence، قواعد rejection، mapping نقش مدل و timeout برای nodeهای غیرچت Memory Worker. Prompt Pack v2 baseline فعلی است.
- `Prompt Execution`: اجرای قابل audit محلی یک prompt روی داده ورودی همراه metadata مربوط به prompt/model/provider، لینک scope، hash ورودی/خروجی، خروجی خام/validateشده، هشدار، خطای sanitizeشده، latency و token count.
- `Hot Scheduler`: scheduler runtime برنامه‌ریزی‌شده Step 2 برای jobs/outbox داغ. در baseline فعلی به‌عنوان runtime کامل Full Mode پیاده‌سازی نشده است.

## برچسب‌های Legacy

- `memorist.unit_analysis` فقط به‌عنوان summary مشتق‌شده legacy حفظ شده است. prompt اصلی اکنون `memorist.jakobson_sentence_analysis` نسخه 2.0 است.
- smoke scriptهای placeholder فقط marker مستندسازی هستند و release evidence محسوب نمی‌شوند.
- smoke testهای manual-only نیازمند اقدام اپراتور هستند و به‌عنوان gate خودکار beta شمرده نمی‌شوند.
