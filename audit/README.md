# 📚 فهرس خطط التدقيق والتنفيذ (`audit/`)

> **المستودع:** `wewe00220033/Gemini-3.8-Flash` · **الفرع النشط:** `A-test`

| الملف | الغرض والحالة |
|---|---|
| **[`TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md)** | ⭐ **الخطة التنفيذية الهندسية النشطة والمعتمدة (`v2.9`)** — مُحدَّثة فوق `0dd3be5d`/`26d2b17` بدمج تصويبات الجولة التانية (`_BARE_BEARER_RE`, `p_dir.rstrip("/\\") >= 6`, `incidents.on_job_result`, `preflight web off`, حذف `text_assist.py#L351-354`, قتل شجرة `Supervisor._terminate()`, حظر `symlink` في `backup.build_zip()`) + إغلاق 5 ألغام انحدار/تسمية (`conftest` حماية 5 اختبارات `generate`, ترتيب `web_error` قبل `web_port is None` في `preflight`, عقد `test_main_applies_persisted_overrides_before_start_queue`, اسم `Config._ALL_MODELS` و`_VOID_TAGS`, وفصل `jobs.live_pending_jobs()` وتشغيل الاسترداد قبل `start_queue(resume=True)`، مع حسم سلامة `key_groups.json`). |
| **[`PROMPT_AUDITOR_360_AND_CODE.md`](./PROMPT_AUDITOR_360_AND_CODE.md)** | ⭐ **برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود** (مُحدَّث لمرجع **`v2.9`** مع روابط GitHub المباشرة لكل التزام وملف). |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md) | *(تاريخي — مُستبدَلة بـ `v2.9`)* نسخة التدقيق المعتمدة السابقة. |
| [`TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md`](./TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md) | *(تاريخي — مُستبدَلة بـ `v2.9`)* نسخة تدقيق سابقة. |
| [`GOLDFISH_AUDIT_PROMPT_READONLY.md`](./GOLDFISH_AUDIT_PROMPT_READONLY.md) | نسخة مرجعية لأمر التكليف الفاحص `READ-ONLY 100%` الذي أُنتج به تدقيق `A-01..A-18`. |
| [`MASTER_REMEDIATION_PLAN.md`](./MASTER_REMEDIATION_PLAN.md) | *(تاريخي — منجز)* خطة `v3.0` الخاصة بمرحلة `f3709cc` والتي نُفِّذت موجاتها في الالتزام `0dd3be5d`. |
| [`INDEPENDENT_360_AUDIT_REPORT.md`](./INDEPENDENT_360_AUDIT_REPORT.md) | *(تاريخي)* تقرير التدقيق المستقل السابق المؤرّخ عند `f3709cc`. |

