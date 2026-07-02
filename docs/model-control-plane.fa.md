# صفحه کنترل مدل‌ها

Memorist مدل چت اصلی را با مدل‌های حافظه یکی نمی‌کند. مدل چت اصلی در Open WebUI انتخاب می‌شود و Memorist فقط متادیتای آن را مشاهده می‌کند.

نقش‌های اصلی:

- `main_chat_observed`: مدل چت اصلی، فقط مشاهده‌شده.
- `preflight`: قبل از درخواست اصلی اجرا می‌شود، محدود است و خطاهایش fail-open هستند.
- `memory_extraction`: بعد از پاسخ دستیار و به‌صورت job پس‌زمینه اجرا می‌شود.
- `embedding`: مستقل از چت و استخراج است و با تغییر مدل، رکوردهای embedding قبلی stale می‌شوند.

پیش‌فرض بتا امن است: preflight و extraction قطعی/محلی هستند و embedding در Lite غیرفعال است. اگر provider راه‌دور تنظیم شود، قبل از default شدن باید privacy acknowledgement ثبت شود.
