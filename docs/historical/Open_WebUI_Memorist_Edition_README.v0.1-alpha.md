# Historical Open WebUI Memorist Edition README — v0.1 alpha

This document is retained only as historical context. It does not describe the
current `v0.2.0-beta.1` development baseline. Use the root `README.md`,
`GITHUB_BASELINE.md`, `HANDOFF.md`, and `KNOWN_LIMITATIONS.md` for current
status.

## Original v0.1 alpha README

# Open WebUI Memorist Edition

A local memory layer for conversations that remember.  
Event-sourced, evidence-grounded, fail-open memory for local LLM workbenches.

حافظه‌ای محلی برای گفتگوهایی که فراموش نمی‌کنند.  
 حافظه رویدادمحور، شاهدبنیاد و fail-open برای میزکارهای لوکال LLM.

> Status: `v0.1.0-alpha.1 local alpha RC`  
> This is an alpha release candidate for local testing, not a stable public release.

---

## English

### What is this?

Open WebUI Memorist Edition is a local-first memory edition for Open WebUI. It adds a Memorist Core sidecar and an Open WebUI Filter integration that can capture local chat events, build evidence-grounded memories, retrieve scoped context, and inject a Memory Context Attachment as separate context before a model request.

Open WebUI remains the parent interface. Memorist is an add-on memory layer, not a replacement, not a cloud service, and not a rebrand of Open WebUI.

### What problem does it solve?

LLM chats forget. Projects, preferences, corrections, constraints, decisions and previous reasoning context disappear unless manually repeated. Memorist gives a local Open WebUI setup a structured memory layer that can remember, retrieve, audit, and forget.

### Core principles

- Local-first by default.
- SQLite is the source of truth.
- Graph, FTS, embeddings, active blocks and attachments are derived projections.
- Memory is evidence-grounded, not blindly accepted model output.
- User text must remain unchanged.
- Memory is inserted as separate context, not concatenated into the user prompt.
- Imported content is untrusted by default.
- Chat must fail open if Memorist is unavailable.
- Forget workflows must invalidate derived artifacts, not only delete one row.

### Current alpha capabilities

- FastAPI-based Memorist Core sidecar.
- SQLite schema v10 with migrations.
- Open WebUI Filter contract tests.
- Session alias handling for temporary and stable Open WebUI chat IDs.
- Adaptive Memory Context Attachment budget.
- Priority SQLite writer/write gateway for hot paths.
- Daily diagnostics endpoint and smoke test.
- CI-small heavy import smoke.
- Rich Heritage export/verify/restore/compare smoke.
- Multi-layer forget residue smoke.
- Consistency and recovery checks.
- Clean RC package scanner and schema regression test.

### Current limitations

- This is still alpha.
- Open WebUI compatibility is pinned and partly fixture/manual validated; broader version matrix is not certified yet.
- The real Open WebUI container smoke should be run before broader public beta distribution.
- Heritage currently preserves object-store references; full object payload export remains an alpha limitation unless implemented.
- Graph/FalkorDB projection is optional and not required for Lite mode.
- Prompt injection cannot be eliminated; the system limits blast radius through trust separation, escaping, policy gates and fail-open behavior.
- Physical erasure depends on SQLite, filesystem, WAL/checkpoint, backups and storage media behavior.

### Recommended name

Use:

**Open WebUI Memorist Edition**

This preserves the upstream parent identity while making the added memory layer clear.

Avoid:

- `Open UI Memorist Edition` — too close but not exact; it loses the Open WebUI parent name.
- `Memorist WebUI` — implies a separate product and weakens attribution.

### Quick start, local alpha

```bash
# From a clean checkout
cd memorist-openwebui
python -m uv sync --all-extras --dev
python -m uv run ruff check .
python -m uv run mypy src/memcore
python -m uv run pytest -q
```

Build the RC package:

```bash
python installer/scripts/assemble_rc.py
python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.1.0.zip
python release/tests/rc_package_schema.py
python scripts/run_release_report.py --manifest release/test_manifest.ijson --external-gates-passed
```

Run alpha gates:

```bash
python release/tests/daily_use_smoke.py
python release/tests/heavy_import_smoke.py --mode ci-small
python release/tests/heritage_roundtrip.py
python release/tests/forget_residue.py
python release/tests/consistency_check.py
python release/tests/recovery_tests.py
```

Run real Open WebUI compatibility smoke before public beta:

```bash
python release/tests/openwebui_container_smoke.py --run-containers
```

### Data locations

Memorist stores local data in configured local paths, normally including:

- SQLite database
- object store
- import staging
- export folder
- logs or diagnostics reports
- optional graph/vector projection stores

Do not commit local runtime data. `.env`, local databases, imports, exports, caches and logs must be excluded from source and release packages.

### Security model

Memorist assumes memory can be hostile. Imported transcripts, retrieved memory and document chunks are treated as data, not instructions. Memory Context Attachments must be separated from user text and must never become trusted directives unless explicitly promoted through policy.

### Public beta checklist

Before public beta, the project should have:

- Automated real Open WebUI container smoke for at least one pinned Open WebUI image.
- Clean source package without `.git`, `.venv`, caches, local DBs or logs.
- Clean RC ZIP with matching schema and checksum.
- GitHub-ready README, architecture README, security policy and known limitations.
- Clear install path for Lite mode.
- Clear uninstall/reset and backup/restore docs.
- Public issue template and bug report template.
- Versioned release notes.
- Honest alpha/beta labels.

### License and attribution

Open WebUI remains the parent project. Preserve upstream attribution and license notices. Memorist Edition should clearly state what is original to Memorist and what belongs to upstream Open WebUI or third-party dependencies.

---

## فارسی

### این پروژه چیست؟

Open WebUI Memorist Edition یک نسخه حافظه‌دار و local-first برای Open WebUI است. این نسخه یک سرویس جانبی به نام Memorist Core و یک Filter برای Open WebUI اضافه می‌کند تا مکالمات محلی را ثبت کند، حافظه‌های شاهدبنیاد بسازد، حافظه مرتبط را بازیابی کند و قبل از درخواست مدل، یک Memory Context Attachment را به‌صورت جداگانه وارد کانتکست کند.

Open WebUI همچنان رابط اصلی است. Memorist فقط لایه حافظه است؛ نه جایگزین Open WebUI، نه سرویس ابری، و نه ری‌برند Open WebUI.

### چه مشکلی را حل می‌کند؟

چت‌های LLM حافظه پایدار ندارند. ترجیحات، تصمیم‌ها، اصلاحات، محدودیت‌های پروژه و زمینه‌های قبلی از بین می‌روند مگر اینکه کاربر هر بار آن‌ها را تکرار کند. Memorist برای Open WebUI یک لایه حافظه محلی می‌سازد که می‌تواند به‌یاد بسپارد، بازیابی کند، حسابرسی کند و فراموش کند.

### اصول اصلی

- local-first به‌صورت پیش‌فرض.
- SQLite منبع حقیقت است.
- Graph، FTS، embeddings، active blocks و attachments خروجی مشتق‌شده هستند.
- حافظه باید شاهدبنیاد باشد، نه خروجی خام و پذیرفته‌شده مدل.
- متن کاربر نباید تغییر کند.
- حافظه باید جدا از prompt کاربر وارد شود.
- محتوای import شده به‌صورت پیش‌فرض untrusted است.
- اگر Memorist قطع شد، چت Open WebUI باید fail-open ادامه پیدا کند.
- فراموشی باید artifactهای مشتق‌شده را هم invalidate کند، نه فقط یک row را حذف کند.

### قابلیت‌های فعلی alpha

- Memorist Core با FastAPI.
- SQLite schema v10 و migrationها.
- تست contract برای Open WebUI Filter.
- مدیریت alias نشست برای chat ID موقت و پایدار Open WebUI.
- بودجه adaptive برای Memory Context Attachment.
- priority SQLite writer/write gateway برای مسیرهای حساس.
- diagnostics endpoint و daily smoke.
- heavy import smoke سبک برای CI.
- Heritage export/verify/restore/compare با fixture غنی.
- forget residue چندلایه.
- consistency و recovery checks.
- اسکن package و تست schema داخل RC ZIP.

### محدودیت‌های فعلی

- این نسخه هنوز alpha است.
- سازگاری Open WebUI پین شده ولی هنوز ماتریس کامل نسخه‌ها ندارد.
- قبل از بتای عمومی باید Open WebUI container smoke واقعی اجرا شود.
- Heritage فعلاً referenceهای object store را حفظ می‌کند؛ export کامل payloadها هنوز محدودیت alpha است مگر اینکه پیاده‌سازی شود.
- Graph/FalkorDB اختیاری است و برای Lite mode لازم نیست.
- Prompt injection حذف‌شدنی نیست؛ فقط می‌توان اثر آن را با trust separation، escaping، policy gate و fail-open محدود کرد.
- حذف فیزیکی به SQLite، filesystem، WAL/checkpoint، backupها و نوع storage وابسته است.

### نام پیشنهادی

نام پیشنهادی:

**Open WebUI Memorist Edition**

این نام هم هویت Open WebUI را حفظ می‌کند، هم روشن می‌کند که Memorist یک لایه افزوده حافظه است.

### اجرای سریع برای alpha محلی

```bash
cd memorist-openwebui
python -m uv sync --all-extras --dev
python -m uv run ruff check .
python -m uv run mypy src/memcore
python -m uv run pytest -q
```

ساخت package:

```bash
python installer/scripts/assemble_rc.py
python -m release.scan_forbidden_files release/rc/memorist-openwebui-0.1.0.zip
python release/tests/rc_package_schema.py
python scripts/run_release_report.py --manifest release/test_manifest.ijson --external-gates-passed
```

### چک‌لیست بتای عمومی

قبل از بتای عمومی باید این‌ها آماده باشند:

- smoke واقعی Open WebUI container برای حداقل یک image پین‌شده.
- source package تمیز بدون `.git`, `.venv`, cacheها، دیتابیس‌های محلی و logها.
- RC ZIP تمیز با schema و checksum درست.
- README گیت‌هابی، README معماری، security policy و known limitations.
- مسیر نصب روشن برای Lite mode.
- راهنمای uninstall/reset و backup/restore.
- قالب issue و bug report.
- release notes نسخه‌دار.
- برچسب‌گذاری صادقانه alpha/beta.

### نسبت با Open WebUI

Open WebUI پروژه والد است. Memorist Edition باید attribution، license notice و نام Open WebUI را حفظ کند. هر چیزی که متعلق به Memorist است باید جداگانه توضیح داده شود.
