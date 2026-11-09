# خطة المعالجة الهندسية الشاملة — Master Remediation Plan (v3.0 Peer-Reviewed)
**المستودع:** `wewe00220033/Gemini-3.8-Flash`  
**الفرع المستهدف:** `A-test`  
**الأساس الهندسي المرجعي:** `HEAD = f3709cc` (238 Commits)  
**التاريخ:** 2026-11-09  
**الحالة:** مُحدَّثة بعد المراجعة المشتركة (EGM Swarm + Independent Peer Review) — **جاهزة للتنفيذ فور إشارة المالك.**

---

## 1. الأهداف الحاكمة والمبادئ المعمارية (Architectural Invariants)

1. **ضمان الحالة النهائية (Zero Hanging Interim Messages):**  
   لا يُسمح تحت أي ظرف (مهلة، خطأ تليجرام 400/429، استثناء مفاجئ، أو نص > 3950 حرف) أن تظل رسالة الانتظار المؤقتة `⏳ جاري توليد النسخة الذهبية المسبوكة مع شلبي...` معلقة في شات المستخدم.
2. **وحدة منطق التسليم (Single Source of Truth):**  
   إزالة التكرار الرباعي لمنطق الإنقاذ والتعديل في `delivery.py` وتوحيده في محرك واحد `_replace_or_send()`.
3. **سلامة سجل المهام ومنع التكرار (`jobs.py` Idempotency Invariant):**  
   **الأصل `state == "sent"` و`TERMINAL = ("sent", "failed", "skipped")` و`find_recent_result()` تبقى كما هي دون مساس**؛ يتم تحديث النص الذهبي عبر الترتيب السليم أو حدث لا يكسر الحالة النهائية `"sent"`.
4. **نظافة الأرشيف والجلسات بالترتيب السليم (`archive.py` / `sessions.py`):**  
   عدم التعديل القسري لملفات append-only؛ بل **تأجيل كتابة `append_polished`** في وضع `synthesizer` لحين حسم نتيجة الخيط الخلفي (كتابة الذهبي عند النجاح، أو مسودة جيميني عند التعذر/المهلة/No-Op).
5. **حماية كوتة Flash-Lite (Shelby No-Op Guard):**  
   فحص توكيني حتمي (`raw.strip().split() == local.strip().split()`) لتخطي نداء السبك الثالث عند عودة النص الخام كما هو.
6. **عزل الاختبارات (Air-Gapped Test Suite):**  
   عزل مسار الإعدادات (`fresh_env`) ومنع أي كتابة في ملفات الإنتاج بالجذر (`settings.json`, `keys.json`, `glossary.json`).

---

## 2. تصحيحات المراجعة المتبادلة المعتمدة في v3.0 (Peer-Review Integration)

تمت مراجعة الخطة بواسطة المراجع المستقل وسرب EGM واعتماد التحسينات التالية في التصميم النهائي:

1. **حماية `jobs.py` و`find_recent_result()` دون تعديل:**  
   بدلاً من تغيير `TERMINAL` أو شرط `find_recent_result()`، يتم الحفاظ على `state == "sent"` سليماً عبر تسجيل حدث `"sent"` محدث (أو دمج الحقول دون تغيير `state` بعد بلوغ `TERMINAL`) مع حماية سباق الخيوط بقفل `result_lock`.
2. **حل جذري لأرشيف `ALL.md` والجلسات بالترتيب لا بالترقيع:**  
   في `delivery.py:425-428`، كان الكود يسجل مسودة جيميني في الأرشيف قبل بدء السبك، فيفشل تسجيل النسخة الذهبية لاحقاً عند السطر 465 بسبب حارس `_POL_MARK in block`. الحل النظيف: إذا كان `is_compare and cmp_mode == "synthesizer"` لا نكتب في الأرشيف عند السطر 426، بل يكتب خيط السبك النسخة النهائية المعتمدة مرة واحدة (الذهبية عند النجاح، أو مسودة جيميني في مسار البديل/الخطأ/No-Op).
3. **سلامة وسوم HTML عند التقسيم (`_html_chunks`):**  
   استخدام وسوم قصيرة وآمنة (`<i>...</i>`) في رسائل الإشعار والإغلاق بدلاً من `<b>` في نصوص قد تخضع للتقسيم، لضمان توازن الوسوم.
4. **معالجة المسودة الأولية الكبيرة (> 3,950 حرف):**  
   إذا تجاوزت `init_html` حد التعديل، تُرسل مسودة جيميني أولاً نظيفة دون تذييل الانتظار، ثم تُرسل رسالة انتظار مستقلة ومختصرة (`wait_mid`) لتجنب تعديل القطعة الخطأ من رسالة مقسمة.
5. **تنظيف التوثيق من أي مسارات بيئة محلية:**  
   تم تطهير مستندات `audit/` بالكامل من أي مسارات محلية خاصة بالجهاز.

---

## 3. حزم التنفيذ التفصيلية (P0 إلى P2)

### المرحلة P0: إصلاحات النواة الحرجة (Zero Hanging & Data Consistency)

#### [P0-01] المحرك الموحد `_replace_or_send()` + `_send_long(force_document=...)`
- **الملف:** `delivery.py`
- **التغييرات:**
  1. **تطوير `_send_long` (`delivery.py:638`):** إضافة معامل `force_document=False` لإجبار الإرسال كملف `.md` عندما تستدعيها `_replace_or_send()` لنصوص > 3,950 حرف متى ما كانت `sender.send_document` متاحة (سد الفجوة 3,950–12,000 حرف، BUG-02).
  2. **بناء `_replace_or_send()`:**
     - `text_len <= max_edit_len` (`3950`): تعديل في المكان عبر `sender.edit_text(mid, full_html, "HTML")`.
     - `text_len > max_edit_len` مع وجود `mid` و`edit_text`: تعديل `mid` بإشعار إغلاق نهائي قصير ومتوازن HTML (`<i>...</i>`) يوضّح إرسال الناتج في ملف أدناه (أو رسائل منفصلة إن غابت `send_document`)، ثم إرسال الناتج كاملاً عبر `_send_long(..., force_document=has_doc)`.
     - فشل التعديل لأي سبب أو `mid is None`: التوجيه الفوري لـ `_send_long` إلى `mid or anchor` **دون شرط `and mid is None`** (إزالة BUG-01 نهائياً).
  3. **فصل رسالة الانتظار للمسودة الأولية الكبيرة (`delivery.py:431-439`):** إذا كانت `len(init_html) > Config.TELEGRAM_MAX_MSG - 50` تُرسل المسودة أولاً نظيفة عبر `_send_long`، ثم تُرسل رسالة انتظار منفصلة صغيرة `wait_mid` ليكون التعديل في المكان موجهاً إليها دائماً.
  4. **تعميم المحرك وحراسة الخيط:** تطبيق `_replace_or_send()` على مسارات `_bg_synthesizer` (النجاح، `fallback_compare`، مهلة/خطأ)، `_bg_shelby_rescue`، ووضع `merged`، وتغليف `_bg_synthesizer` و`_bg_shelby_rescue` بحارس `try...except` يغلق `mid` دائماً برسالة واضحة.

#### [P0-02] الحفظ المتسق في `jobs.ndjson` والأرشيف بالترتيب السليم (BUG-04)
- **الملفات:** `delivery.py`, `telegram_bot.py`, `jobs.py`, `observability.py`
- **التصميم الآمن:**
  1. **الأرشيف والجلسات بالترتيب السليم (`delivery.py:425-428`):**  
     إذا كان `is_compare and cmp_mode == "synthesizer"` يتم تأجيل استدعاء `archive.append_polished(...)` و`sessions.append_polished(...)` لخيط `_bg_synthesizer`:
     - عند نجاح السبك: يُكتب `[نسخة ذهبية مسبوكة]\n{golden_text}` في `archive` و`golden_text` في `sessions`.
     - عند فشل السبك أو المهلة أو No-Op أو الاستثناء: يُكتب `polished` (مسودة جيميني) مرة واحدة فقط.
     *(تبقي `archive.py` و`sessions.py` 100% append-only دون أي ترقيع).*
  2. **سجل المهام `jobs.ndjson` دون كسر `find_recent_result`:**
     - **تبقى `TERMINAL` و`find_recent_result()` في `jobs.py` كما هي (`state == "sent"`).**
     - إضافة معامل `job_id=None` إلى `delivery.deliver()` وتمرير `job_id=job.job_id` من `telegram_bot.py:559`.
     - استخدام `result_lock = threading.Lock()` في `result`:
       - عند انتهاء السبك بالنسخة الذهبية، يحدّث `_bg_synthesizer` القاموس `result["polished"] = golden_text` تحت القفل، ويسجل الحدث في `jobs.ndjson` بحالة تحفظ بقاء المهمة كـ `"sent"` (مثل تسجيل حدث `"sent"` مكمّل بالـ `polished_text=golden_text[:2000]` و`synth_elapsed`, أو حدث داخلي `"synthesized"` في `load_state` يحدّث الحقول ويُضاف لـ `events` مع الإبقاء على `j["state"] = "sent"` إن كانت قد أُرسلت).
       - **ميزة دمج الحدث الداخلي `"synthesized"` مع إبقاء `state == "sent"` إذا وصلت بعد `"sent"`:**
         - `jobs.py` تظل تحافظ على `j["state"] == "sent"` في كل الأحوال.
         - `find_recent_result()` تعمل وتُعيد النص الذهبي المحفوظ دون أي تغيير في شرطها.
         - `pending_jobs()` لا تعيد تشغيل المهمة عند الـ Restart.
         - `observability.py` تعرض في `/trace` حدث `🏆 سبك ذهبي` بأوقاته الدقيقة.

#### [P0-03] كاشف Shelby No-Op التوكيني وتوفير الكوتة (BUG-05)
- **الملفات:** `text_assist.py`, `delivery.py`
- **التنفيذ:**
  - إضافة دالة `is_shelby_noop(raw_text: str, local_text: str) -> bool` في `text_assist.py`:
    ```python
    def is_shelby_noop(raw_text: str, local_text: str) -> bool:
        if not local_text or not raw_text:
            return False
        return raw_text.strip().split() == local_text.strip().split()
    ```
  - إضافة عداد `local_noop` في `text_assist._state` المحمي بـ `_lock`.
  - في `delivery.py` داخل `_bg_synthesizer`: عند كشف No-Op، تُسجّل رسالة تحذير واضحة، يُزاد عداد `local_noop`، يُتخطّى نداء `text_assist.synthesize()` تماماً لتوفير الكوتة، وتُحفظ مسودة جيميني في الأرشيف والجلسة وتُغلق رسالة الانتظار عبر `_replace_or_send()`.

---

### المرحلة P1: الاختبارات ومزامنة الوثائق

#### [P1-01] عزل بيئة الاختبارات (BUG-06)
- تمرير `fresh_env` في كافة دوال `tests/test_dual_polish.py` عبر `pytestmark = pytest.mark.usefixtures("fresh_env")` حتى لا تُلمس ملفات `settings.json` / `keys.json` / `glossary.json` الحقيقية.
- إضافة اختبار `test_suite_does_not_create_settings_json` للتحقق الفعلي من عدم الكتابة على الجذر.

#### [P1-02] اختبارات الإثبات الحدودية وتحديث التوكيدات (QA-01 / BUG-07)
- **تحديث `tests/test_dual_polish.py:488` (`test_deliver_synthesizer_oversized_fallback`):**
  لأن `huge_golden` (> 3950 حرف) يغلق رسالة الانتظار المؤقتة بإشعار قصير ثم يرسل ملفاً (بدلاً من ترك `⏳` معلقة)، يتم التحقق من:
  - تعديل رسالة الانتظار بإشعار الإغلاق الصريح (`len(fs.edited_texts) == 1` ووجود `"ملف"` في نص الإشعار، وليس النص الضخم نفسه).
  - إرسال المستند فعلياً (`len(fs.docs) == 1`).
- **اختبارات الإثبات الجديدة (T-01 إلى T-06):**
  1. فشل `edit_text` عند مهلة شلبي → وصول رسالة البديل وعدم تعليق الشريط.
  2. الحدود الطولية: `3,900` حرف (تعديل في المكان) · `4,500` حرف (إغلاق + ملف `.md`) · `13,000` حرف (إغلاق + ملف `.md`).
  3. تقسيم مسودة جيميني الأولى (> 3,950 حرف) → إرسال رسالة انتظار مستقلة وتعديلها هي دون مساس بقطع المسودة.
  4. التحقق من حفظ النسخة الذهبية في `jobs.ndjson` وبقاء `state == "sent"` وعمل `find_recent_result()` وتحديث `archive` و`sessions` بالنسخة الذهبية لا المسودة الأولى.
  5. كاشف Shelby No-Op → تخطي `synthesize` وإغلاق الحالة بمسودة جيميني.

#### [P1-03] مزامنة الوثائق والافتراضات (DOC-01..DOC-05)
- تحديث `README.md` (`LOCAL_POLISH_TIMEOUT=180.0`، `COMPARE_MODE=synthesizer`، 534+ اختباراً).
- تحديث `.env.example` ليطابق القيم الفعلية (`180.0` و`synthesizer`).
- تصحيح أسماء المتغيرات في `Antigravity/local_provider_template.py` (`LOCAL_POLISH_CMD` و`LOCAL_POLISH_TIMEOUT`).

---

### المرحلة P2: استقرار العمليات (SRE & Security)
- تتبع خيوط السبك وإغلاقها بمهلة في `main.py:shutdown()`.
- إظهار إحصائيات السبك و`local_noop` في `/status` و`/doctor`.
- إضافة `rate_limit` لـ `/api/add_key` و`settimeout(30)` في `web_service.py`.

---

## 4. سجل قرارات المالك المعتمدة (D-1 إلى D-8)

- **D-1 (الوضع الافتراضي):** `COMPARE_MODE = "synthesizer"` مع كاشف No-Op التوكيني لتوفير الكوتة عند غياب المنظم المحلي الحقيقي.
- **D-2 (مهلة شلبي):** `LOCAL_POLISH_TIMEOUT = 180.0` ثانية كما هي حالياً، مع توثيقها رسمياً ومزامنتها في `README.md` و`.env.example`.
- **D-3 (سجل المهام والأرشيف):** تسجيل النسخة الذهبية في `jobs.ndjson` (مع إبقاء `state == "sent"`) وفي `ALL.md` بالترتيب الصحيح.
- **D-4 (نصوص > 3,950 حرف):** إغلاق رسالة الانتظار بإشعار وإرسال ملف `.md` (`force_document=True`).
- **D-5 .. D-8:** `README.md` و`.env.example` هما المرجع، Python 3.12/3.13، إبقاء `/api/add_key` مع rate-limit، وطلب التوكن للمنافذ غير المحلية.
