# 📚 فهرس خطط التدقيق والتنفيذ (`audit/`)

> **المستودع:** `wewe00220033/Gemini-3.8-Flash` · **الفرع النشط:** `A-test`

| الملف | الغرض والحالة |
|---|---|
| **[`TELEGRAM_SYNTH_MASTER_PLAN_v2.12.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.12.md)** | ⭐ **الخطة التنفيذية الهندسية النشطة والمعتمدة (`v2.12`)** — مُعتمدة نهائياً بالإجماع التام لسرب وكلاء EGM الثمانية وتوقيع «الفيل SRE» و«السمكة الذهبية»: استيراد `logkit` المركزي في `observability.py:19`، مواءمة `test_runtime_shutdown.py:168-187` لمساري الويب، تثبيت شرط خروج المشرف الحاسم في `main.py:425` (`sys.exit(2)` عند غياب التوكن مع `WEB_ENABLED=0`)، عزل كوتا `flash-lite` مع استمرار حظر `invalid` للكل، تحصين `backup.py` ببادئات الأسرار وسقف 512MB، قفل التزامن لـ `watchdog`، وتضمين حزمة الاختبارات الرسمية الـ 9 لـ `M1` (`test_m1_v211.py`). |
| **[`PROMPT_AUDITOR_360_AND_CODE.md`](./PROMPT_AUDITOR_360_AND_CODE.md)** | ⭐ **برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود** (مُحدَّث لمرجع **`v2.12`** مع روابط GitHub المباشرة لكل التزام وملف). |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md) | *(تاريخي — مُستبدَلة بـ `v2.12`)* نسخة التدقيق المعتمدة السابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md) | *(تاريخي — مُستبدَلة بـ `v2.12`)* نسخة تدقيق سابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md) | *(تاريخي — مُستبدَلة بـ `v2.12`)* نسخة تدقيق سابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md) | *(تاريخي — مُستبدَلة بـ `v2.12`)* نسخة تدقيق سابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md) | *(تاريخي — مُستبدَلة بـ `v2.12`)* نسخة تدقيق سابقة. |
| [`GOLDFISH_AUDIT_PROMPT_READONLY.md`](./GOLDFISH_AUDIT_PROMPT_READONLY.md) | نسخة مرجعية لأمر التكليف الفاحص `READ-ONLY 100%` الذي أُنتج به تدقيق `A-01..A-18`. |
| [`MASTER_REMEDIATION_PLAN.md`](./MASTER_REMEDIATION_PLAN.md) | *(تاريخي — منجز)* خطة `v3.0` الخاصة بمرحلة `f3709cc` والتي نُفِّذت موجاتها في الالتزام `0dd3be5d`. |
| [`INDEPENDENT_360_AUDIT_REPORT.md`](./INDEPENDENT_360_AUDIT_REPORT.md) | *(تاريخي)* تقرير التدقيق المستقل السابق المؤرّخ عند `f3709cc`. |
