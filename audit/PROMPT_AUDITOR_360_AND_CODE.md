# 🐠 برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود (`Goldfish Auditor + Code Builder`) — جولة التحقق فوق `v2.11`

أنت الآن **مُدقّق «السمكة الذهبية 360°» المُزقّق حرفياً + مهندس برمجيات SRE & AppSec تنفيذي** على مستودع **`wewe00220033/Gemini-3.8-Flash`** (الفرع النشط: **`A-test`**).

مطلوب منك **الاثنان معاً في نفس الجولة — بلا اكتفاء بالكلام النظري ولا الاعتماد على نسخة مخبأة قديمة**:
1. **التحقق الحرفي والتمحيص الميكروسكوبي 360°:** فحص كل ملف، دالة، وسطراً بسطر في الكود الفعلي الأحدث على `origin/A-test` أمام الخطة المعتمدة **[`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md`](https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md)**؛ لتثبت بالدليل هل كل بنود **`v2.11`** صحيحة **100%** أم هناك فجوة، سباق، تضارب، أو تفصيلة جديدة فاتتنا.
2. **تقديم الكود والحل الفعلي (والتنفيذ إن طُلب):** أي ثغرة أو تعديل أو تحسين تراه صحاً، أو تنفيذ المرحلة `M1` — **لا توصفه نظرياً فقط، بل هات الكود الكامل الدقيق الخالي من الأخطاء النحوية والمطابق للبيئة + اختبارات `pytest` الكاملة (`Drop-in code / Unified Diff / pytest cases`)**.

---

## 📌 السياق الفعلي: ماذا فعلنا حتى الآن؟ *(اقرأه قبل أي حكم)*

1. **ما نُفِّذ في الكود الفعلي (`.py`) حتى الآن:**
   - سلسلة التزامات التعافي على `A-test`: [`f3709cc`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f3709cc) ← [`f5b4bee`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f5b4bee) ← [`b97cb2f`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/b97cb2f) ← وصولاً إلى الالتزام الكودي الأحدث **[`0dd3be5d`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c)** (`Waves 1-3`: web auth, delivery/archive ordering, TG 429/UTF-16, No-Op guard, float timeout, docs).
   - **وضع تجميد الكود المقصود:** أوقفنا تعديل جميع ملفات `.py` بعد `0dd3be5d` عمداً لحين إغلاق الخطة التنفيذية نهائياً وتدقيقها قبل إعطاء شارة `GO M1`. **بالتالي فإن عدم تنفيذ بنود `M1..M5` داخل `.py` الآن هو الحالة الطبيعية المقصودة، وليس خطأً في المستودع.**
2. **ما نُفِّذ في الوثائق والخطط المرفوعة على `origin/A-test`:**
   - [`f122f23`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f122f23): نشر خطة `v2.7` وبرومبت التدقيق.
   - [`85ecdf6`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/85ecdf6e4ec642eae1ec5cc3e4bec7d1116181c4) + [`26d2b17`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/26d2b17): ترقية الخطة إلى `v2.8` و`v2.9`.
   - [`366d7f2`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/366d7f2): ترقية الخطة إلى `v2.10`.
   - **الترقية الحالية إلى `v2.11` (`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md`):** دمجت تدقيق الجولة الرابعة الحرفي وتحسينات كود M1 المعتمدة:
     1. **حوكمة `Supervisor`:** خروج فوري بكود `2` عند `EXIT_CONFIG` مع تدوين `self.write_reason("config_error", code, ...)` في `restart_reason.json` ليراها المالك في `/status`. وضبط `start_new_session=True` / `CREATENEWPROCESSGROUP` في `spawn()` تمهيداً معمارياً لقتل الشجرة في M2.
     2. **عزل كوتا `flash-lite` (500 RPD) مع استمرار حجب `invalid` للكل:** قفل `status == "exhausted"` لا يقفل `flash-lite` عند نفاد كوتا الصوت، بينما `status == "invalid"` يحجب كل الموديلات بلا استثناء.
     3. **تنقيح أمني مركزي في `observability.check()`:** تنقيح استثناءات نداء `_check(fn)` بنقطة مركزية واحدة `return WARN, logkit.redact(...)` تغطي الفحوصات الـ 12 وتحجب توكن `getMe`.
     4. **تحصين `backup.py` بالبادئات والسقف:** إضافة `SECRETPREFIXES = (".keys-", "keys.json.", "keys.txt.", ".env.")`، واستثناء `.env.example`، وسقف فك الضغط التراكمي `RESTORE_MAX_BYTES = 512MB` في `restore_test()`، وتخطي symlinks في `build_zip()`.
     5. **قفل التزامن في `watchdog.py`:** حماية `events` و`_l1_times` بـ `self._lock = threading.Lock()` أثناء القراءة والتعديل.
     6. **اعتماد حزمة اختبارات M1 الرسمية:** تضمين `tests/test_m1_v210.py` (8 اختبارات).
3. **⚠️ تنبيه تقني لنسخ GitHub والكود:**
   - إذا لاحظت تأخر cache صفحة `commits/A-test` على GitHub فاقرأ الملفات مباشرة من `raw.githubusercontent.com/wewe00220033/Gemini-3.8-Flash/A-test/...`.
   - **تحذير تنسيق الكود:** تأكد أن الـ Diff/الكود يحافظ حرفياً على الشرطة السفلية `_` وعلامات `**` و`*` والترقيم (مثل `[A-Za-z0-9_\-]` و`**kwargs` و`_VOID_TAGS` و`_TG_RE`) دون إسقاطها عند التوليد أو النسخ!

---

## 🔗 روابط GitHub المباشرة الإجبارية (افتح الجديد والقديم والمقارنات والملفات بنفسك)

### 1) الفرع والتاريخ والالتزامات والـ Diff الكامل:
- **الفرع النشط (`A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/tree/A-test
- **سجل الالتزامات (`Commits`):** https://github.com/wewe00220033/Gemini-3.8-Flash/commits/A-test
- **⭐ الخطة النشطة المعتمدة (`v2.11`):** https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.11.md
- **نسخ تاريخية (`v2.8` / `v2.9` / `v2.10`):**
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.10.md
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md
- **⭐ التزام الكود الفعلي الرئيسي (`0dd3be5d` — Waves 1-3):** https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c
- **🔍 المقارنة الشاملة من نقطتك القديمة حتى أحدث نسخة على الفرع (`1083332...A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/1083332...A-test
- **🔍 مقارنة ترقية الخطة (`366d7f2...A-test` — من `v2.10` إلى `v2.11`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/366d7f2...A-test

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
   - ❌ **المشرف `supervisor.py`:** لا تنسَ `EXIT_CONFIG = 2` و`write_reason` قبل الخروج، وضبط `start_new_session=True` تمهيداً لـ M2.
   - ❌ **مدير المفاتيح `key_manager.py`:** لا تدع `status == "exhausted"` يحجب `gemini-3.5-flash-lite`، مع إبقاء `invalid` حاجباً للكل.
   - ❌ **اختبارات `conftest.py`:** ممنوع استبدال `text_assist.generate` بـ lambda عالمياً (يحمي 5 اختبارات قائمة).
   - ❌ **ترتيب `preflight.py`:** فحص `if web_error:` أولاً بدقة ثم `elif web_port is None:`.

---

## 💻 المطلوب العملي الحاسم (الاثنين معاً: التدقيق + الكود/التنفيذ)

### الحالة أ — إذا كنت في بيئة مراجعة/شات:
1. **أصدر حكمك الصريح** على خطة **`v2.11`** بنداً ببند (بعد فتح أحدث نسخة ومقارنة `366d7f2...A-test`).
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
