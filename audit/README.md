# 📚 فهرس خطط التدقيق والتنفيذ (`audit/`)

> **المستودع:** `wewe00220033/Gemini-3.8-Flash` · **الفرع النشط:** `A-test`

| الملف | الغرض والحالة |
|---|---|
| **[`TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md)** | ⭐ **الخطة التنفيذية الهندسية النشطة والمعتمدة (`v2.10`)** — مُحدَّثة فوق `0dd3be5d`/`26d2b17` بدمج نتائج تدقيق «الفيل SRE» والجولة التالتة الخارجية: إصلاح حلقة انهيار `Supervisor` والموت الذاتي (`EXIT_CONFIG=2` و`start_new_session=True`)، عزل كوتا `gemini-3.5-flash-lite` عن استنفاد موديلات الصوت، إغلاق خريطة `A-10` الكاملة للتنقيح عند المصدر، قفل تزامن لـ `watchdog` و`backup`، تصحيح فجوة سطر 349 في `text_assist.py` لتمكين السبك بالخام، وإعادة جدولة تواقيع `Sender` من `M1` إلى `M3`. |
| **[`PROMPT_AUDITOR_360_AND_CODE.md`](./PROMPT_AUDITOR_360_AND_CODE.md)** | ⭐ **برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود** (مُحدَّث لمرجع **`v2.10`** مع روابط GitHub المباشرة لكل التزام وملف). |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md) | *(تاريخي — مُستبدَلة بـ `v2.10`)* نسخة التدقيق المعتمدة السابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md) | *(تاريخي — مُستبدَلة بـ `v2.10`)* نسخة تدقيق سابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md) | *(تاريخي — مُستبدَلة بـ `v2.10`)* نسخة تدقيق سابقة. |
| [`GOLDFISH_AUDIT_PROMPT_READONLY.md`](./GOLDFISH_AUDIT_PROMPT_READONLY.md) | نسخة مرجعية لأمر التكليف الفاحص `READ-ONLY 100%` الذي أُنتج به تدقيق `A-01..A-18`. |
| [`MASTER_REMEDIATION_PLAN.md`](./MASTER_REMEDIATION_PLAN.md) | *(تاريخي — منجز)* خطة `v3.0` الخاصة بمرحلة `f3709cc` والتي نُفِّذت موجاتها في الالتزام `0dd3be5d`. |
| [`INDEPENDENT_360_AUDIT_REPORT.md`](./INDEPENDENT_360_AUDIT_REPORT.md) | *(تاريخي)* تقرير التدقيق المستقل السابق المؤرّخ عند `f3709cc`. |
