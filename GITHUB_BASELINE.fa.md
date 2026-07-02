# خط مبنای توسعه GitHub

وضعیت: **Conditional GO برای audit مستقل و ادامه توسعه روی GitHub**.

- نسخه: `0.2.0-beta.1`
- نسخه schema: `18`
- برچسب پیشنهادی: `v0.2.0-beta.1 development baseline`
- Lite Mode: مسیر محلی beta-candidate
- Full Mode: preview آزمایشی
- Open WebUI integration: با contract/fixture تست شده؛ smoke واقعی container هنوز manual/pending است

## پیاده‌سازی‌شده

- runtime محلی SQLite Lite و hardening مسیر write actor
- Memory Intelligence Core با تحلیل جمله‌ای Jakobson و signal routing
- Model Control Plane به‌عنوان baseline backend/runtime
- Prompt Pack v2 با schema، validator، role mapping و audit اجرای prompt
- hardening import، Heritage، forget residue، consistency و recovery
- تست contract برای Open WebUI Filter/Function

## آزمایشی

- مسیر Full Mode با PostgreSQL/FalkorDB
- graph projection و graph diagnostics
- smoke خارجی Full compose/runtime

## هنوز certify نشده

- Public Beta readiness
- پشتیبانی production برای Full Mode
- smoke واقعی container برای Open WebUI
- ارزیابی semantic طولانی‌مدت برای Prompt Pack v2

## دستورهای اصلی

```bash
python scripts/clean_artifacts.py --check
python scripts/baseline_check.py
make check
make source-package
make assemble-rc
make rc-schema-test
make version-consistency
```

اگر `make` روی Windows نصب نیست، دستورهای معادل Python/uv داخل `Makefile` را مستقیم اجرا کنید.
