# معماری Open WebUI Memorist Edition

Memorist کنار Open WebUI اجرا می‌شود. Open WebUI رابط چت و مدل اصلی را نگه می‌دارد؛ Memorist ذخیره محلی، بازیابی، استخراج حافظه، کنترل مدل‌های حافظه، حریم خصوصی، import/export و بسته‌بندی release را مدیریت می‌کند.

## مسیر مدل‌ها

```text
مدل چت اصلی در Open WebUI
  -> main_chat_observed فقط مشاهده می‌شود
پیام کاربر
  -> preflight محلی/محدود
  -> Memory Context Attachment جدا از متن کاربر
پاسخ دستیار
  -> job پس‌زمینه memory_extraction
  -> تحلیل جمله‌ای Jakobson
  -> candidate و evidence
متن حافظه/کوئری
  -> embedding مستقل
  -> re-index در صورت تغییر default
```

هیچ نقش حافظه‌ای به‌طور پنهان از مدل چت اصلی استفاده نمی‌کند. preflight خطا را fail-open می‌کند. استخراج حافظه مسیر چت را block نمی‌کند. embedding از extraction جدا است.

## حریم خصوصی و هزینه

Profileها cost، latency، quality و privacy دارند. provider راه‌دور قبل از default شدن acknowledgement می‌خواهد. secret خام ذخیره نمی‌شود و فقط نام متغیر محیطی مجاز است. diagnostics برای usage، health و هزینه نقش‌ها در APIهای Model Control Plane قرار دارد.

## Prompt Pack v2

Prompt Pack v2 مرز بین «انتخاب مدل» و «رفتار مدل» را روشن می‌کند. Model Control Plane تعیین می‌کند هر نقش از کدام مدل استفاده کند، اما Prompt Pack تعیین می‌کند آن مدل با چه `prompt_id`، چه `prompt_version`، چه schema ورودی/خروجی، چه قواعد evidence، و چه مسیر audit اجرا شود.

Promptهای حافظه پاسخ کاربر را تولید نمی‌کنند، چت نمی‌کنند، و متن مکالمه یا import را به‌عنوان دستور اجرا نمی‌کنند. متن تحلیل‌شده فقط داده است. خروجی همه Promptهای غیرچت باید I-JSON معتبر با envelope استاندارد `schema_version`، `prompt_id`، `prompt_version`، `status`، `warnings` و `items` باشد.

`memorist.jakobson_sentence_analysis` نسخه 2.0 لنز معنایی اصلی است. extractorهای تخصصی conative، referential، metalingual، emotive و poetic فقط candidateهای evidence-grounded می‌سازند و هر evidence باید `annotation_uuid` و `route_uuid` داشته باشد. اجرای Promptها در جدول `prompt_execution_runs` با hash ورودی/خروجی، نقش مدل، provider، profile، وضعیت، هشدار و خطای sanitizeشده audit می‌شود. هیچ Prompt غیرچتی به‌صورت پنهانی از مدل Main Chat استفاده نمی‌کند.
