# تاریخی: برنامه اجرای چهار گام نهایی

این سند برای سابقه audit نگه داشته شده است. متن آن مربوط به قبل از پیاده‌سازی گام‌های 1 تا 4 بود. وضعیت فعلی در `README.md`، `docs/architecture.md`، `docs/model-control-plane.md` و `docs/prompt-pack.md` آمده است.

تا زمانی که هر چهار گام زیر پیاده‌سازی، تست و در release gate matrix ثبت نشده‌اند، Public Beta Readiness نباید شروع شود.

## Step 1 — Memory Intelligence Core / Sentence-Level Jakobson Pipeline

- هدف: تبدیل تحلیل جمله‌محور یاکوبسن به stage اصلی memory intelligence.
- محدوده: sentence segmentation قطعی، جدول sentence offset، prompt `memorist.jakobson_sentence_analysis`، schema شش‌کارکردی، signal routeها، hookهای extractor، lineage candidate و annotation outbox.
- خارج از محدوده: Full Mode PostgreSQL، بازنویسی runtime مدل، formalization کامل Prompt Pack v2 به‌جز تعریف حداقلی prompt یاکوبسن.
- فایل‌های احتمالی: `memcore/memory_worker/`، `memcore/models/`، `memcore/repositories/`، `memcore/api/routes_memory.py`، `docs/phase-2-memory-worker.md`.
- migrationهای احتمالی: `jakobson_analysis_runs`، `jakobson_sentence_annotations`، `memory_signal_routes` و ستون‌های lineage candidate.
- تست‌های لازم: offset جمله، اعتبارسنجی شش‌کارکردی، route selection، evidence lineage، سازگاری legacy `unit_analysis`.
- وابستگی: baseline فعلی SQLite Lite و Prompt Pack v1.
- rollback: مسیر text-unit فعلی تا پاس شدن gateهای Step 1 حفظ شود.
- gate پذیرش: همه gateهای فعلی به‌علاوه تست‌های sentence pipeline یاکوبسن.

## Step 2 — Full Mode Storage/Core Runtime: PostgreSQL + FalkorDB + Hot Scheduler

- هدف: اضافه‌کردن Full Mode واقعی با PostgreSQL canonical store و FalkorDB projection.
- محدوده: profile split برای lite/full، abstraction `CanonicalStore`، migrationهای PostgreSQL، outbox/jobs پایدار، hot scheduler، projection/retrieval روی FalkorDB، migration از Lite به Full و Full compose smoke.
- خارج از محدوده: حذف Lite mode، تضعیف baseline محلی SQLite، تغییر مالکیت UI/مدل اصلی Open WebUI.
- فایل‌های احتمالی: `memcore/storage/`، `memcore/repositories/`، `memcore/jobs/`، `docker-compose.full.yml`، `docs/deployment-guide.md`.
- migrationهای احتمالی: مجموعه migration PostgreSQL موازی با canonical schema.
- تست‌های لازم: contract تست canonical store، Full compose smoke، graph projection، graph forget residue، Lite-to-Full migration.
- وابستگی: schemaهای Step 1 باید پایدار شوند.
- rollback: Lite mode default باقی بماند و Full Mode پشت flag صریح باشد.
- gate پذیرش: gateهای Lite بدون regression و gateهای Full storage/projection/compose.

## Step 3 — Model Control Plane Runtime Integration

- هدف: تبدیل scaffold فعلی model control به کنترل runtime اجرای نقش‌های Memorist.
- محدوده: provider adapterها، role-to-profile resolution، timeout/fail-open برای preflight، lifecycle extraction، embedding re-index، usage/cost/privacy events و UI contract.
- خارج از محدوده: کنترل مدل چت اصلی Open WebUI، ذخیره secret خام، دورزدن privacy acknowledgement.
- فایل‌های احتمالی: `memcore/model_control/`، `memcore/memory_worker/`، `memcore/attachments/`، `open-webui-integration/memorist/ui/`.
- migrationهای احتمالی: capabilityهای runtime provider، lifecycle invocation و رویدادهای usage/cost غنی‌تر در صورت نیاز.
- تست‌های لازم: provider resolution، acknowledgement برای local/remote، timeout behavior، usage recording، embedding stale/re-index.
- وابستگی: نیازهای prompt در Step 1 و constraintهای runtime در Step 2.
- rollback: providerهای deterministic/local default باقی بمانند و نقش‌های remote فقط با acknowledgement فعال شوند.
- gate پذیرش: تست‌های فعلی model-control به‌علاوه تست‌های runtime invocation.

## Step 4 — Memory Worker Prompt Pack v2

- هدف: formalize کردن Prompt Pack v2 بعد از مشخص شدن schemaهای Step 1 و semantics runtime Step 3.
- محدوده: prompt رسمی یاکوبسن، extractorهای route-specific، consolidation، preflight، block compaction، import reconstruction، privacy sensitivity، role-to-prompt mapping، schema validation و prompt execution linkage.
- خارج از محدوده: معماری حافظه جدید خارج از schema Step 1، اجرای prompt بدون ایمنی Model Control.
- فایل‌های احتمالی: `memcore/memory_worker/prompts/`، `memcore/model_control/`، `memcore/validators/`، `docs/phase-2-memory-worker.md`.
- migrationهای احتمالی: metadata و execution linkage برای Prompt Pack v2 اگر جدول‌های v1 کافی نباشند.
- تست‌های لازم: کامل بودن registry v2، schema validation خروجی، injection resistance، evidence requirements و model role mapping.
- وابستگی: schemaهای Jakobson در Step 1 و runtime model control در Step 3.
- rollback: Prompt Pack v1 تا پاس شدن gateهای v2 به‌عنوان legacy baseline حفظ شود.
- gate پذیرش: gateهای فعلی prompt-pack به‌علاوه fixtureهای contract مخصوص v2.

## Beta Blockerها

- نبود sentence-level Jakobson pipeline در Step 1.
- نبود PostgreSQL canonical Full Mode و gateهای آن در Step 2.
- partial بودن runtime integration در Step 3.
- نبود Prompt Pack v2 در Step 4.
- شکست هر gate واقعی در `release/test_manifest.ijson`.
- dirty بودن source package یا شکست scan بسته RC.
