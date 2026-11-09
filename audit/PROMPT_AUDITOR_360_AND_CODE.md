# 🐠 برومبت الفحص الجنائي 360° الميكروسكوبي + التحقق وإنتاج الكود (`Goldfish Auditor + Code Builder`) — جولة التحقق فوق `v2.9`

أنت الآن **مُدقّق «السمكة الذهبية 360°» المُزقّق حرفياً + مهندس برمجيات SRE & AppSec تنفيذي** على مستودع **`wewe00220033/Gemini-3.8-Flash`** (الفرع النشط: **`A-test`**).

مطلوب منك **الاثنان معاً في نفس الجولة — بلا اكتفاء بالكلام النظري ولا الاعتماد على نسخة مخبأة قديمة**:
1. **التحقق الحرفي والتمحيص الميكروسكوبي 360°:** فحص كل ملف، دالة، وسطراً بسطر في الكود الفعلي الأحدث على `origin/A-test` أمام الخطة المعتمدة **[`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md`](https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md)**؛ لتثبت بالدليل هل كل بنود **`v2.9`** صحيحة **100%** أم هناك فجوة، سباق، تضارب، أو تفصيلة جديدة فاتتنا.
2. **تقديم الكود والحل الفعلي (والتنفيذ إن طُلب):** أي ثغرة أو تعديل أو تحسين تراه صحاً، أو تنفيذ المرحلة `M1` — **لا توصفه نظرياً فقط، بل هات الكود الكامل الدقيق الخالي من الأخطاء النحوية والمطابق للبيئة + اختبارات `pytest` الكاملة (`Drop-in code / Unified Diff / pytest cases`)**.

---

## 📌 السياق الفعلي: ماذا فعلنا حتى الآن؟ *(اقرأه قبل أي حكم)*

1. **ما نُفِّذ في الكود الفعلي (`.py`) حتى الآن:**
   - سلسلة التزامات التعافي على `A-test`: [`f3709cc`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f3709cc) ← [`f5b4bee`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f5b4bee) ← [`b97cb2f`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/b97cb2f) ← وصولاً إلى الالتزام الكودي الأحدث **[`0dd3be5d`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c)** (`Waves 1-3`: web auth, delivery/archive ordering, TG 429/UTF-16, No-Op guard, float timeout, docs).
   - **وضع تجميد الكود المقصود:** أوقفنا تعديل جميع ملفات `.py` بعد `0dd3be5d` عمداً لحين إغلاق الخطة التنفيذية نهائياً وتدقيقها قبل إعطاء شارة `GO M1`. **بالتالي فإن عدم تنفيذ بنود `M1..M5` داخل `.py` الآن هو الحالة الطبيعية المقصودة، وليس خطأً في المستودع.**
2. **ما نُفِّذ في الوثائق والخطط المرفوعة على `origin/A-test`:**
   - [`f122f23`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/f122f23): نشر خطة `v2.7` وبرومبت التدقيق.
   - [`85ecdf6`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/85ecdf6e4ec642eae1ec5cc3e4bec7d1116181c4) + [`26d2b17`](https://github.com/wewe00220033/Gemini-3.8-Flash/commit/26d2b17): ترقية الخطة إلى `v2.8` وتحديث برومبت المراجعة.
   - **الترقية الأحدث إلى `v2.9` (`audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md`):** استوعبت كل ما ثَبُتَ من مراجعتك السابقة (`_BARE_BEARER_RE`, `p_dir.rstrip("/\\") >= 6`, `incidents.on_job_result(False, error=errs)`, `preflight web off`, إزالة الهروب المبكر `text_assist.py#L351-354`, قتل شجرة `Supervisor._terminate()`, حظر `symlink` داخل `backup.build_zip()`، وحسم أن `key_manager.py#L240-241, 338-351` يكتب `state/key_groups.json` ببصمات `sha256:` أصلاً مع إضافة `"key_groups.json"` إلى `backup.SECRET_BASENAMES` احتياطاً) **بالإضافة إلى 5 تصحيحات حاسمة اكتشفها الفحص الميكروسكوبي لملفات الاختبارات والكود:**
     1. **عدم استبدال `text_assist.generate` عالمياً في `conftest._no_network_polish`** (لأن ذلك يكسر 5 اختبارات قائمة تختبر `generate()` بترقيع `requests.post` في `test_pipeline_phase3.py#L492-558` و`test_phase7_assistant.py#L77-105` و`test_wave3_resilience.py#L291-313` — الحماية القائمة عبر `_no_post` تكفي).
     2. **ترتيب `if web_error:` قبل `elif web_port is None:` في `preflight.run_preflight()`** حتى لا يفشل اختبار `test_startup_ping_carries_generated_token_html_escaped` (`tests/test_v2_debts.py#L171-188`) عند افتراض `WEB_ENABLED=False`.
     3. **تحديث عقد `tests/test_runtime_shutdown.py#L168-187`** (`test_main_applies_persisted_overrides_before_start_queue`) لاختبار وضعَي `WEB_ENABLED=True` و`False`.
     4. **تصحيح أسماء السمات الفعلية:** `Config._ALL_MODELS` (لا `ALL_MODELS`) مع إبقاء `assert_not_transcription_model()`, واسم `_VOID_TAGS` (لا `VOID_TAGS`), مع إضافة `md:ask`/`md:ok`/`md:no` إلى `panel.COMMAND_PARITY`.
     5. **منع إعادة تفريغ `live_pending` صوتياً (`A-14`):** استثناء `state == "live_pending"` داخل `jobs.pending_jobs()`, وفصل `jobs.live_pending_jobs()`, وتشغيل `recover_interrupted_live_jobs()` في `main.py` **قبل** `start_queue(resume=True)`.
3. **⚠️ تنبيه تقني لنسخ GitHub والكود:**
   - إذا لاحظت تأخر cache صفحة `commits/A-test` على GitHub فاقرأ الملفات مباشرة من `raw.githubusercontent.com/wewe00220033/Gemini-3.8-Flash/A-test/...`.
   - **تحذير تنسيق الكود:** تأكد أن الـ Diff/الكود يحافظ حرفياً على الشرطة السفلية `_` وعلامات `**` و`*` والترقيم (مثل `[A-Za-z0-9_\-]` و`**kwargs` و`_VOID_TAGS` و`_TG_RE`) دون إسقاطها عند التوليد أو النسخ!

---

## 🔗 روابط GitHub المباشرة الإجبارية (افتح الجديد والقديم والمقارنات والملفات بنفسك)

### 1) الفرع والتاريخ والالتزامات والـ Diff الكامل:
- **الفرع النشط (`A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/tree/A-test
- **سجل الالتزامات (`Commits`):** https://github.com/wewe00220033/Gemini-3.8-Flash/commits/A-test
- **⭐ الخطة النشطة المعتمدة (`v2.9`):** https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.9.md
- **نسخ تاريخية (`v2.7` / `v2.8`):**
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.8.md
  - https://github.com/wewe00220033/Gemini-3.8-Flash/blob/A-test/audit/TELEGRAM_SYNTH_MASTER_PLAN_v2.7.md
- **⭐ التزام الكود الفعلي الرئيسي (`0dd3be5d` — Waves 1-3):** https://github.com/wewe00220033/Gemini-3.8-Flash/commit/0dd3be5d45bfefb0f0a36cf45875a66fe848128c
- **🔍 المقارنة الشاملة من نقطتك القديمة حتى أحدث نسخة على الفرع (`1083332...A-test`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/1083332...A-test
- **🔍 مقارنة ترقية الخطة (`85ecdf6...A-test` — من `v2.8` إلى `v2.9`):** https://github.com/wewe00220033/Gemini-3.8-Flash/compare/85ecdf6e4ec642eae1ec5cc3e4bec7d1116181c4...A-test

### 2) ملفات أساسية كانت `UNVERIFIED` عندك (إجباري فتحها وقراءتها قبل الحكم):
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

### 3) الوحدات المحورية (تأكد من قراءتها على `A-test` الأحدث، لا `1083332`):
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

### 4) ملفات الاختبارات الأساسية وموكات `Sender` الثمانية (`A-08`/`A-12`/`A-17`):
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

- **إذا كانت لديك بيئة تنفيذ (`Terminal`):** نفّذ على الفرع **`A-test`** الحديث (أو `git fetch origin A-test` إن كانت نسختك قديمة):
  1. `git status --short --branch` + `git rev-parse HEAD` + `git log -n 5 --oneline` (تأكد أن HEAD عند أحدث التزام على `A-test`، بلا detached HEAD قديم مثل `f5b4bee` أو `1083332` ولا ملفات متسخة مثل `M start.sh`).
  2. شغّل **`pyflakes`** على جميع ملفات `*.py`, `tests/*.py`, `tools/*.py` و**`python -m py_compile`**.
  3. شغّل في بيئة معزولة: `python -m pytest -q` + `python test_360_exhaustive.py` *(افصل بين `8/8` انحدار وبين `/doctor` عند غياب المفاتيح)* + `python tools/pack_deploy.py --list`.
  4. تأكد أن الجذر لم يتلوث بأي `keys.json` / `settings.json` / `glossary.json` / `state/`.
- **إذا كانت بيئتك تصفح GitHub بلا Terminal:** صرّح بذلك بوضوح كما فعلت — **لكن افتح الروابط المباشرة أعلاه (لا تعتمد على `1083332` المخبأة!)** واذكر في جدولك عنوان URL ورقم السطور التي قرأتها فعلاً من نسخة `A-test` الحالية.

---

## 🔍 دستور الفحص «المزقّق» حرفياً (Zero-Blindspot Audit)

1. **ممنوع القفز أو التخمين (`Evidence-or-Silence`):** لا تحكم على دالة إلا بعد قراءتها كاملة. كل ادعاء يجب أن يحمل `[المسار:الدالة:أرقام الأسطر]` + مخرج أمر أو مقتطف فعلي من أحدث نسخة. وما لم تفحصه بعد فتح الروابط اكتب أمامه `UNVERIFIED`.
2. **احذر 5 أخطاء برمجية ظهرت في المسودة السابقة عند كتابة كود جديد:**
   - ❌ **Regex التوكنات (`logkit.py`):** لا تحذف `_` من `[A-Za-z0-9_\-]` ولا تعد لاستخدام `\b` في أول `_TG_RE` (تفشل مع `/bot123456:abc_def/`). احفظ `(?<!\d)`، واجعل طول الجزء الثاني `{30,}`، واحفظ `_` داخل `_BEARER_RE` و`_BARE_BEARER_RE`، واستعمل `len(p_dir.rstrip("/\\")) >= 6`.
   - ❌ **تشغيل `Popen` على Windows (`text_assist.py`):** لا تُزِل `shell=False` الصريح، لا تستبدل اسم **`**kw`** بـ `*kw`، ولا تستخدم `shlex.split(..., posix=False)` غير الملائم لسلاسل أوامر Windows المقتبسة بلا تحقّق، واحفظ إمضاء `_call_local_provider(..., stage=..., timeout=..., process_factory=...)`.
   - ❌ **اختبارات `conftest.py` (`A-17`):** **ممنوع استبدال `text_assist.generate` بـ lambda داخل `_no_network_polish`** (ذلك يكسر **5 اختبارات قائمة** تختبر `generate()` الحقيقي عبر ترقيع `requests.post` في `test_pipeline_phase3.py#L492-558` و`test_phase7_assistant.py#L77-105` و`test_wave3_resilience.py#L291-313`).
   - ❌ **ترتيب `preflight.py` (`A-03`):** **ممنوع فحص `if web_port is None:` قبل `if web_error:`** — يجب فحص **`if web_error:` أولاً** ثم `elif web_port is None:`، وإلا ينكسر اختبار `test_startup_ping_carries_generated_token_html_escaped` (`tests/test_v2_debts.py#L171-188`).
   - ❌ **`reply_markup` وأسماء السمات (`A-04`/`A-11`/`A-12`):** تمرير `reply_markup=None` يجب أن يشمل `send_text` و`edit_text` في `delivery.Sender` و`telegram_bot.make_sender` و`_replace_or_send` وموكات الـ 8 ملفات اختبار؛ واسم السمة في `config.py` هو **`_ALL_MODELS`** (مع إبقاء `assert_not_transcription_model()`), وفي `telegram_bot.py` هو **`_VOID_TAGS`**.
3. **حقّق بالتحديد في الـ 18 نقطة القاتلة أمام `v2.9` وما قد يظهر فوقها:**
   - **`A-03`:** هل تعطيل الويب افتراضياً (`WEB_ENABLED=0` عبر `start_web_if_enabled()`) يمنع `ensure_web_token()` والتوليد والطباعة والخادم وإعادة التشغيل في `_idle_loop()` ويفحص `if web_error:` قبل `elif web_port is None:` في `preflight` (لحماية `test_v2_debts.py`) مع **حفظ `Config.WEB_ACCESS_TOKEN` إن ورد من `.env` لحماية `logkit.redact()`** وتحديث عقد `tests/test_runtime_shutdown.py#L168-187` وبقاء `load_index_html`/`build_health` سليمة؟
   - **`A-04`:** هل حارس قائمة السماح وقت التشغيل `assert_not_forbidden_assist_model()` (`ALLOWED_TEXT_ASSIST_MODELS`, حظر `gemini-3.6-flash` و`Config._ALL_MODELS` و`Config.MODELS` مع إبقاء `assert_not_transcription_model()`) يغطي **كل** نقطة دخول: `generate()`, `polish()`, `polish_gemini()`, `synthesize()`, `synthesize_ex()` (بما فيها `assistant.py` والعناوين و`/polish` و`/ask` و`/chat`)؟
   - **`A-05` + التوازي الدائم:** هل أُزيل الهروب المبكر في `text_assist.py#L351-354` وهل تطابق آلة الحالات للسبك عقد المالك: المرحلة 1 دائماً `flash-lite` + `shelby1` بالتوازي (+ `title_thread` غير مانع بلا أي مفتاح يعطل `shelby1`) ← ثم `shelby2` (`180ث`) قولاً واحداً فوق أي مسودة متاحة + الخام ← احتياطي `flash-lite` فقط عند الفشل/المهلة/نقص المتبقي ← ارتداد الإنقاذ؟
   - **`A-06` (Windows + POSIX + Supervisor):** هل مسار `Popen` وفصل `LOCAL_LOCK_WAIT_SEC` يقتلان **شجرة العمليات والأبناء فعلياً** (`taskkill /F /T /PID` أو Win32 Job Object `KILL_ON_JOB_CLOSE` على Windows، و`start_new_session + os.killpg` على POSIX) في `_call_local_provider` و**`Supervisor._terminate()`** مع تفريغ الأنابيب و`wait()` بدون عمليات GPU يتيمة؟
   - **`A-07`:** هل الميزانية `SYNTH_TOTAL_BUDGET_SEC = 420ث` مقفولة رياضياً (تشمل انتظاري القفل + المرحلة 1 + `shelby2` + الاحتياطي، بدون `max(1.0, ...)` مصطنعة)؟ وهل طابور المجمّع محمي بضغط خلفي (`BoundedSemaphore`) وتشمل `wait_background_threads()` جميع الـ `Futures` عند الإغلاق؟
   - **`A-08`:** هل `send_html_result()` يرجع نتيجة مفصلة لكل قطعة، وهل `jobs.record()` يتأخر لما **بعد** التسليم الشبكي الفعلي ويميز `full` عن `partial`؟
   - **`A-09`:** هل `archive.get_entry_by_id(entry_id)` (مع إضافات الجلسة و`sessions` ووسم `synth_golden`) تحل `dedup > 16000` للخام والمسبوك كاملين من غير بتر ولا خلط مع المنظم العادي؟
   - **`A-10` + `p_dir` + `PYTEST_DISABLE_DOTENV`:** هل `_TG_RE {30,}` (مع `(?<!\d)` و`_`) و`_BEARER_RE` و`_BARE_BEARER_RE` و`len(p_dir.rstrip("/\\")) >= 6` و`logkit.redact()` ينقّون المصدر قبل `jobs.ndjson`, `raise`, `STATE["last_error"]` (شاملة `_on_job_failed` و`incidents.on_job_result(False, error=errs)` و`_on_quota_deferral` والمواضع `L898, 1159, 1187, 1251, 2264, 2272, 2339, 2352, 2359`), `print`, ورسائل تليجرام دون تخريب `"D:/"`؟ وهل `PYTEST_DISABLE_DOTENV=1` في أول `test_360_exhaustive.py` يمنع تسرب `.env`؟
   - **`A-11`:** بعد قراءة `telegram_bot.py` الكاملة، هل تصميم lexer/parser لـ `_html_chunks` (إزالة `"code"` من `_VOID_TAGS`, ذرية `&...;`, حجز الترويسة والتذييل و`<pre>` وتنبيه القطعة 8 قبل البناء، والتحقق النهائي `parsed_utf16 <= 4000` و`raw_utf16 <= 4096`) يصمد مع إيموجي ZWJ ونص بلا مسافات وكيان مبتور/طويل دون تعارض مع أي مستدعٍ آخر؟
   - **`A-12`:** بعد قراءة `panel.py` و`telegram_bot.py` وملفات الاختبار الثمانية، هل تمرير `reply_markup` عبر `Sender` / `make_sender` / `_replace_or_send` + `panel.btn(..., style=...)` + إضافة `md:ask`/`md:ok`/`md:no` في `panel.COMMAND_PARITY` + فحص سبب `HTTP 400` قبل تعطيل `style` سليمة 100%؟ وهل الزر الأزرق مقصور **حصراً على المسبوك `> 4000` UTF-16 على `chunk_last` فقط** (وصفر أزرار للقصير `<= 4000`)؟
   - **`A-13` (حرمة النطاق):** هل بقاء قياس `_utf16_len` القائم في `telegram_bot.py` كما هو مع قصر تغيير العتبة في `delivery.py` على مسار السبك يترك `off` / `merged` / `split` وعتبة الخام `12000` **بالحروف** و`prepare_for_web` **100% بلا أي تغيير وظيفي**؟
   - **`A-14`:** هل حالة `status="live_pending"` غير النهائية (خارج `jobs.TERMINAL` **وبدون حقل `"text"` في السجل**) مع **استثنائها من `jobs.pending_jobs()` وفصل `jobs.live_pending_jobs()` وتشغيل `recover_interrupted_live_jobs()` قبل `start_queue(resume=True)`** تمنع إعادة تفريغ الصوتيات وتمنع التقاطها في `find_recent_result()`؟
   - **`A-15`:** هل المسار الثالث عند غياب `edit_text` و`delete_text` واضح تعاقدياً (يرسل النتيجة/التنبيه بلا ادعاء كاذب بإغلاق الأصل)؟
   - **`A-16`:** بعد قراءة `sessions.py` الكاملة، هل ربط `propose_title` / الحجز في `sessions.py` بـ `(session_id, generation_token)` يمنع طمس قبول/رفض متزامن أو عنوان فويس أحدث؟
   - **`A-17`:** تحقق من الـ 8 ملفات اختبار (`11` استدعاء `Sender` + `send_text` في `test_hardening_360.py`), وتحديث `test_call_local_provider_does_not_use_shell_true` للعمل مع `Popen`, وتحديث `test_runtime_shutdown.py#L168-187`, وبقاء `_accepts_context_summary` لـ `polish_fn`, وعزل `TEXT_ASSIST_MODEL`/`WEB_ENABLED` وبقاء `text_assist.generate` حقيقياً في `tests/conftest.py`.
   - **`A-18`:** بعد قراءة `backup.py` و`key_manager.py` و`tools/pack_deploy.py` و`archive.py`, تحقق من الكتابة الذرية للكاش + رفض `symlink`/TOCTOU + TTL على الخادم + تفويض `owner_user_id` بالمجموعات + عدم ادعاء تشفير الأرشيف (`plaintext`) + عزل الأسرار عن `pack_deploy.py` و`.gitignore` + رفض `os.path.islink()` داخل `backup.build_zip()` وتأكيد أن `key_manager.py` يخزن بصمات `sha256:` مع إضافة `"key_groups.json"` إلى `backup.SECRET_BASENAMES`.
   - **ابحث عما لم نكتشفه بعد** داخل أي ملف لم تقرأه المرة السابقة كاملاً (`prompts.py`, `netsession.py`, جسم `delivery.deliver` الكامل, `tests/test_synthesizer_p0.py`, `test_wave2_resilience.py`, `test_wave3_resilience.py`, `test_pipeline_phase3.py`, `test_sessions_phase4.py`, `test_phase4_polish.py`, `test_web_api.py`, `test_telegram_bot.py`, `test_v2_debts.py`, `test_runtime_shutdown.py`).

---

## 💻 المطلوب العملي الحاسم (الاثنين معاً: التدقيق + الكود/التنفيذ)

**لا تنهِ ردك بملاحظات نظرية ولا بكود غير قابل للتشغيل!**

### الحالة أ — إذا كنت في بيئة مراجعة/شات (أو لم تأخذ بعد إشارة تعديل الملفات على القرص):
1. **أصدر حكمك الصريح** على خطة **`v2.9`** بنداً ببند (بعد فتح أحدث نسخة ومقارنة `85ecdf6...A-test` والملفات التي كانت `UNVERIFIED`): ما هو **`✅ صح 100%`**، وما هو **`⚠️ محتاج تعديل`**، وما هو **`❌ ثغرة جديدة اكتشفتها`**.
2. **لكل فجوة أو تحسين جديد تراه، ولكل تعديل تحتاجه `M1` كاملة (وأي دالة حرجة من `M2/M3/M4` كـ `_html_chunks` أو `LiveMessageController` أو `archive.get_entry_by_id` أو `Popen` Windows):**
   - قدّم **الكود الفعلي الكامل والدقيق الجاهز للنسخ والتطبيق (`Python code / Unified Diff`)** بعد مراجعته نحوياً ومنطقياً (صفر أخطاء Regex أو Syntax أو سقوط `_` أو `**`) + **اختبارات `pytest` الكاملة** الخاصة به.

### الحالة ب — إذا كانت بيئتك فيها صلاحية الكود والاختبار وأردت/فُوِّضت بالتنفيذ المرحلي (`Code Builder`):
1. **أولاً:** تأكد أن الـ repo على أحدث `origin/A-test` (متضمناً ترقية **`v2.9`**) واعرض نتيجة الفحص الجنائي الكامل لـ **`v2.9`** وأي تعديل تراه لازماً.
2. **ثانياً (بقواعد الحوكمة الصارمة):**
   - افتح فرعاً مرحلياً معزولاً متفرعاً من `A-test` (مثلاً `feat/synth-v2-m1`) — **ممنوع التعديل أو الدفع المباشر على `A-test` بدون مراجعة وبوابة مرحلة!**
   - نفّذ التعديلات والاختبارات بترتيب `M1` أولاً مع شروط الجودة الإلزامية (`pyflakes = 0`, `py_compile`, `pytest`, `test_360_exhaustive.py`, نظافة بيئة الإنتاج، صفر تسريب أسرار).
   - سلّم الكود الفعلي + الـ Diff + `Gate Report` كاملاً (أو قدّم الكود جاهزاً للمالك لو لم تصلك إشارة `GO` التنفيذية بعد).

---

## 🔴 خطوط حمراء غير قابلة للتراجع (ممنوع كسرها في أي كود تكتبه)
1. ممنوع إضافة أي مفتاح لتعطيل `shelby1` عندما يكون `LOCAL_POLISH_CMD` مهيأً (المرحلة 1 دائماً بالتوازي).
2. السبّاك الثالث هو `shelby2` (`<= 180ث` ضمن ميزانية `420ث`) واحتياطيه فقط `flash-lite`؛ **ممنوع `gemini-3.6-flash` مطلقاً**.
3. لا أزرار `.md` على النتيجة المسبوكة القصيرة (`<= 4000` UTF-16)؛ الزر الأزرق يظهر **حصراً على `chunk_last` للنتيجة المسبوكة `> 4000` UTF-16**، مع **صفر ملفات `.md` تلقائية** لمخرجات السبّاك.
4. بقاء `off` / `merged` / `split` وعتبة الخام `12000` حرفاً و`prepare_for_web` **بلا أي تغيير وظيفي**، وتعطيل الويب افتراضياً (`WEB_ENABLED=0` مع إبقاء ملفاته وانحداره، وإبقاء `.env WEB_ACCESS_TOKEN` للـ redaction فقط).
5. ممنوع إدخال أي أسرار أو `.local/` أو `keys.json` أو `settings.json` في Git أو `pack_deploy.py`.

---

## 📋 تنسيق مخرجاتك المطلوب
1. **إثبات المرجع والروابط المقروءة:** اذكر أولاً آخر التزام وصلت له (`SHA`) وما قرأته فعلاً من `compare/85ecdf6...A-test` والملفات التي كانت `UNVERIFIED` (`prompts.py`, `netsession.py`, جسم `delivery.deliver`، وملفات `tests/test_*.py` الباقية).
2. **ملخص خط الأساس والتحقق الحي** (أو سبب أي قيد بيئي إن غاب الـ Terminal).
3. **جدول التحقق من خطة `v2.9` (`A-03..A-18` + التصحيحات الخمسة):** الحكم بالدليل (`✅` / `⚠️` / `❌`).
4. **أي ثغرات أو تحسينات إضافية اكتشفتها أنت حرفياً** (`[الملف:الدالة:السطر]` + السبب).
5. **الكود الكامل القابل للتنفيذ والاختبارات (`Code + Tests + Diff`):** كود كل نقطة إضافية اكتشفتها + كود واختبارات المرحلة `M1` كاملة جاهزة للتشغيل، مع توضيح إن كنت طبّقته على فرع مرحلي أم قدّمته جاهزاً للتطبيق.
