# 🐠 برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود (`Goldfish Auditor + Code Builder`) — جولة التحقق فوق `v2.10`

أنت الآن **مُدقّق «السمكة الذهبية 360°» المُزقّق حرفياً + مهندس برمجيات SRE & AppSec تنفيذي** على مستودع **`wewe00220033/Gemini-3.8-Flash`** (الفرع النشط: **`A-test`**).

مطلوب منك **الاثنان معاً في نفس الجولة — بلا اكتفاء بالكلام النظري ولا الاعتماد على نسخة مخبأة قديمة**:
1. **التحقق الحرفي والتمحيص الميكروسكوبي 360°:** فحص كل ملف، دالة، وسطراً بسطر في الكود الفعلي الأحدث على `origin/A-test` أمام الخطة المعتمدة **[`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md`](https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md)**؛ لتثبت بالدليل هل كل بنود **`v2.10`** صحيحة **100%** أم هناك فجوة، سباق، تضارب، أو تفصيلة جديدة فاتتنا.
2. **تقديم الكود والحل الفعلي (والتنفيذ إن طُلب):** أي ثغرة أو تعديل أو تحسين تراه صحاً، أو تنفيذ المرحلة `M1` — **لا توصفه نظرياً فقط، بل هات الكود الكامل الدقيق الخالي من الأخطاء النحوية والمطابق للبيئة + اختبارات `pytest` الكاملة (`Drop-in code / Unified Diff / pytest cases`)**.

---

## 📌 السياق الفعلي: ماذا فعلنا حتى الآن؟ *(اقرأه قبل أي حكم)*

1. **ما نُفِّذ في الكود الفعلي (`.py`) حتى الآن:**
   - سلسلة التزامات التعافي على `A-test`: [`f3709cc`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f3709cc) ← [`f5b4bee`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f5b4bee) ← [`b97cb2f`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/b97cb2f) ← وصولاً إلى الالتزام الكودي الأحدث **[`0dd3be5d`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c)** (`Waves 1-3`: web auth, delivery/archive ordering, TG 429/UTF-16, No-Op guard, float timeout, docs).
   - **وضع تجميد الكود المقصود:** أوقفنا تعديل جميع ملفات `.py` بعد `0dd3be5d` عمداً لحين إغلاق الخطة التنفيذية نهائياً وتدقيقها قبل إعطاء شارة `GO M1`. **بالتالي فإن عدم تنفيذ بنود `M1..M5` داخل `.py` الآن هو الحالة الطبيعية المقصودة، وليس خطأً في المستودع.**
2. **ما نُفِّذ في الوثائق والخطط المرفوعة على `origin/A-test`:**
   - [`f122f23`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f122f23): نشر خطة `v2.7` وبرومبت التدقيق.
   - [`85ecdf6`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/85ecdf6e4ec642eae1ec5cc3e4bec7d1116181c4) + [`26d2b17`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/26d2b17): ترقية الخطة إلى `v2.8` و`v2.9`.
   - **الترقية الحالية إلى `v2.10` (`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md`):** دمجت فحص Elephant SRE الميكروسكوبي ونتائج الجولة التالتة الحرفية:
     1. **إصلاح حلقة انهيار `Supervisor` والموت الذاتي (`killpg Suicide Trap`):** إضافة `EXIT_CONFIG = 2` في `supervisor.py` لمنع حلقة الـ 20 محاولة المهدِرة للوقت والمولدة للحوادث عند كود 2 (غياب التوكن مع `WEB_ENABLED=0`)، وضبط `start_new_session=True` في `spawn()` لتفادي انتحار المشرف عند قتل الشجرة.
     2. **عزل كوتا `gemini-3.5-flash-lite` (500 RPD) عن كوتا الصوت (45 RPD):** تصحيح `key_manager._model_blocked_until` بحيث لا يقفل `status == "exhausted"` نموذج السبك `flash-lite` عند نفاد كوتا الصوت.
     3. **إغلاق خريطة `A-10` الكاملة للتنقيح عند المصدر:** تنقيح أخطاء `/health` غير المصادق عليها في `web_service.py`، وقطع تسريب توكن البوت في استثناءات التنزيل (`_download_with_retry` و`voice_queue.py` قبل التسجيل في `jobs.ndjson`)، وأمر `getMe` في `observability.py:325`، و`incidents.py`، وجميع مواضع `STATE["last_error"]`.
     4. **تحصين الخيوط والنسخ الاحتياطي (`watchdog.py` و`backup.py`):** قفل تزامن لـ `watchdog.events` و`_l1_times`، وحماية `backup.py` ضد ملفات `keys.json.corrupt-*` و`.env.*` وقفل ضد التنافس وسقف لحجم فك الضغط (Zip-bomb defense).
     5. **تصحيح فجوة سطر 349 في `text_assist.py`:** تعديل شرط `is_effective_empty` ليشمل كلا المسودتين بجانب حذف أسطر 351-354 لتمكين `synthesize_ex` من السبك بالخام فقط عند غياب المسودتين.
     6. **إعادة توزيع نطاق المراحل بتخفيف `M1` ونقل تواقيع `Sender` إلى `M3`:** تركيز `M1` على الأمان وحوكمة الإقلاع وعزل النماذج وتجميد الويب، ونقل تحديث الـ 8 ملفات اختبار لموكات `Sender`/`reply_markup` إلى `M3` حيث تُطبق الأزرار فعلياً.
3. **⚠️ تنبيه تقني لنسخ GitHub والكود:**
   - إذا لاحظت تأخر cache صفحة `commits/A-test` على GitHub فاقرأ الملفات مباشرة من `raw.githubusercontent.com/wewe00220033/Gemini-3.8-Flash/A-test/...`.
   - **تحذير تنسيق الكود:** تأكد أن الـ Diff/الكود يحافظ حرفياً على الشرطة السفلية `_` وعلامات `**` و`*` والترقيم (مثل `[A-Za-z0-9_\-]` و`**kwargs` و`_VOID_TAGS` و`_TG_RE`) دون إسقاطها عند التوليد أو النسخ!

---

## 🔗 روابط GitHub المباشرة الإجبارية (افتح الجديد والقديم والمقارنات والملفات بنفسك)

### 1) الفرع والتاريخ والالتزامات والـ Diff الكامل:
- **الفرع النشط (`A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/tree/A-test
- **سجل الالتزامات (`Commits`):** https://github.com/wewe00220033/Gemini-3.8-Flash/commits/A-test
- **⭐ الخطة النشطة المعتمدة (`v2.10`):** https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md
- **نسخ تاريخية (`v2.8` / `v2.9`):**
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md
- **⭐ التزام الكود الفعلي الرئيسي (`0dd3be5d` — Waves 1-3):** https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c
- **🔍 المقارنة الشاملة من نقطتك القديمة حتى أحدث نسخة على الفرع (`1083332...A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/1083332...A-test
- **🔍 مقارنة ترقية الخطة (`26d2b17...A-test` — من `v2.9` إلى `v2.10`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/26d2b17...A-test

### 2) ملفات أساسية (إجباري فتحها وقراءتها قبل الحكم):
- `telegram_bot.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/telegram_bot.py
- `archive.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/archive.py
- `sessions.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/sessions.py
- `panel.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/panel.py
- `assistant.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/assistant.py
- `preflight.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/preflight.py
- `backup.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/backup.py
- `voice_queue.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/voice_queue.py
- `watchdog.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/watchdog.py
- `supervisor.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/supervisor.py
- `observability.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/observability.py
- `tools/pack_deploy.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tools/pack_deploy.py
- `Antigravity/local_provider_template.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/Antigravity/local_provider_template.py

### 3) الوحدات المحورية:
- `config.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/config.py
- `text_assist.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/text_assist.py
- `delivery.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/delivery.py
- `prompts.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/prompts.py
- `jobs.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/jobs.py
- `logkit.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/logkit.py
- `main.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/main.py
- `web_service.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/web_service.py
- `settings.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/settings.py
- `key_manager.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/key_manager.py
- `netsession.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/netsession.py
- `test_360_exhaustive.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/test_360_exhaustive.py
- `.github/workflows/ci.yml`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/.github/workflows/ci.yml
- `audit/README.md`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/README.md

### 4) ملفات الاختبارات الأساسية:
- `tests/conftest.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/conftest.py
- `tests/test_hardening_360.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_hardening_360.py
- `tests/test_dual_polish.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_dual_polish.py
- `tests/test_synthesizer_p0.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_synthesizer_p0.py
- `tests/test_wave2_resilience.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_wave2_resilience.py
- `tests/test_wave3_resilience.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_wave3_resilience.py
- `tests/test_pipeline_phase3.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_pipeline_phase3.py
- `tests/test_sessions_phase4.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_sessions_phase4.py
- `tests/test_phase4_polish.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_phase4_polish.py
- `tests/test_web_api.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_web_api.py
- `tests/test_telegram_bot.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_telegram_bot.py
- `tests/test_jobs.py`: https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/tests/test_jobs.py

---

## ⚙️ الخطوة 0 (إلزامية قبل أي حكم أو كود): خط الأساس النظيف

- **إذا كانت لديك بيئة تنفيذ (`Terminal`):** نفّذ على الفرع **`A-test`** الحديث:
  1. `git status --short --branch` + `git rev-parse HEAD` + `git log -n 5 --oneline`.
  2. شغّل **`pyflakes`** على جميع ملفات `*.py`, `tests/*.py`, `tools/*.py` و**`python -m py_compile`**.
  3. شغّل في بيئة معزولة: `python -m pytest -q` + `python test_360_exhaustive.py` + `python tools/pack_deploy.py --list`.
- **إذا كانت بيئتك تصفح GitHub بلا Terminal:** صرّح بذلك بوضوح — وافتح الروابط المباشرة واذكر في جدولك عنوان URL ورقم السطور التي قرأتها فعلاً من نسخة `A-test` الحالية.

---

## 🔍 دستور الفحص «المزقّق» حرفياً (Zero-Blindspot Audit)

1. **ممنوع القفز أو التخمين (`Evidence-or-Silence`):** لا تحكم على دالة إلا بعد قراءتها كاملة. كل ادعاء يجب أن يحمل `[المسار:الدالة:أرقام الأسطر]` + مخرج أمر أو مقتطف فعلي من أحدث نسخة. وما لم تفحصه بعد فتح الروابط اكتب أمامه `UNVERIFIED`.
2. **احذر أخطاء برمجية شائعة:**
   - ❌ **Regex التوكنات (`logkit.py`):** احفظ `(?<!\d)` واجعل طول الجزء الثاني `{30,}`، واحفظ `_` داخل `_BEARER_RE` و`_BARE_BEARER_RE`، واستعمل `len(p_dir.rstrip("/\\")) >= 6`.
   - ❌ **المشرف `supervisor.py`:** لا تنسَ `EXIT_CONFIG = 2` و`start_new_session=True` لتفادي انتحار المشرف بـ `killpg`.
   - ❌ **مدير المفاتيح `key_manager.py`:** لا تدع `status == "exhausted"` يحجب `gemini-3.5-flash-lite` عند نفاد كوتا الصوت.
   - ❌ **اختبارات `conftest.py`:** ممنوع استبدال `text_assist.generate` بـ lambda عالمياً (يحمي 5 اختبارات قائمة).
   - ❌ **ترتيب `preflight.py`:** فحص `if web_error:` أولاً بدقة ثم `elif web_port is None:`.

---

## 💻 المطلوب العملي الحاسم (الاثنين معاً: التدقيق + الكود/التنفيذ)

### الحالة أ — إذا كنت في بيئة مراجعة/شات:
1. **أصدر حكمك الصريح** على خطة **`v2.10`** بنداً ببند (بعد فتح أحدث نسخة ومقارنة `26d2b17...A-test`).
2. **لكل فجوة أو تحسين جديد تراه، ولكل تعديل تحتاجه `M1` كاملة:**
   - قدّم **الكود الفعلي الكامل والدقيق الجاهز للتطبيق (`Drop-in code / Unified Diff`)** + **اختبارات `pytest` الكاملة**.

### الحالة ب — إذا كانت بيئتك فيها صلاحية الكود والاختبار (`Code Builder`):
1. افتح فرعاً مرحلياً معزولاً متفرعاً من `A-test` (`feat/synth-v2-m1`).
2. نفّذ التعديلات والاختبارات بترتيب `M1` أولاً مع معايير الجودة (`pyflakes = 0`, `pytest`, `test_360_exhaustive.py`).
3. سلّم الكود الفعلي + الـ Diff + `Gate Report` كاملاً.

---

## 🔴 خطوط حمراء غير قابلة للتراجع
1. ممنوع إضافة أي مفتاح لتعطيل `shelby1` عندما يكون `LOCAL_POLISH_CMD` مهيأً (المرحلة 1 دائماً بالتوازي).
2. السبّاك الثالث هو `shelby2` (`<= 180ث` ضمن ميزانية `420ث`) واحتياطيه فقط `flash-lite`؛ **ممنوع `gemini-3.6-flash` مطلقاً**.
3. لا أزرار `.md` على النتيجة المسبوكة القصيرة (`<= 4000` UTF-16)؛ الزر الأزرق يظهر **حصراً على `chunk_last` للنتيجة المسبوكة `> 4000` UTF-16**، مع **صفر ملفات `.md` تلقائية**.
4. بقاء `off` / `merged` / `split` وعتبة الخام `12000` حرفاً و`prepare_for_web` **بلا أي تغيير وظيفي**، وتعطيل الويب افتراضياً (`WEB_ENABLED=0`).
5. ممنوع إدخال أي أسرار أو `.local/` أو `keys.json` في Git أو `pack_deploy.py`.
