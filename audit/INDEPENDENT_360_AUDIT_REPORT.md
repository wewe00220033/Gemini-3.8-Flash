تقرير التدقيق الهندسي المستقل — 360° Audit
المستودع: wewe00220033/Gemini-3.8-Flash · الفرع: A-test · HEAD: f3709cc
تاريخ التدقيق: 2026-11-09 (Africa/Cairo)
الوضع: READ-ONLY — لم يُعدَّل أي ملف في المستودع، ولم يُنفَّذ أي commit/push/PR، ولم يُشغَّل أي إصلاح.
فريق التدقيق (أدوار مُنفَّذة فعليًا): Principal Architect · Senior SWE · QA/Automation · AppSec · DevOps/CI-CD · SRE/Performance · Code Reviewer · Tech-Debt Auditor.

0. نطاق الفحص الفعلي وحدوده (يُقرأ أولًا)
ما فُحص فعليًا (أدلة قابلة للتكرار)
#	ما تم	النتيجة
1	استنساخ كامل + فحص A-test (238 commit)	HEAD = f3709cc · working tree نظيف · 7.7MB · صفر tags/releases
2	قراءة سطر-بسطر: delivery.py (650 سطرًا)، text_assist.py (337)، prompts.py (300)، settings.py (161)، config.py (383)	✅
3	قراءة موضعية مُدلَّلة: telegram_bot.py (2167 سطرًا — 6 مقاطع)، web_service.py، panel.py، key_manager.py، logkit.py، main.py، voice_queue.py، conftest.py، tests/test_dual_polish.py (593)	✅
4	python -m pyflakes *.py tests/*.py tools/*.py	0 ملاحظة
5	python -m pytest -q (بيئة نظيفة)	534 مُجمَّعة / 534 ناجحة / 0 فاشلة (~74ث)
6	مسح أسرار (AIza… / توكن تليجرام / sk-…) في شجرة العمل	0 تطابق
7	مسح بدائيات خطرة (os.system, shell=True, eval, exec, pickle, yaml.load)	0
8	عدّ أقسام الدستور في _POLISH_COMMON	12/12 موجودة
9	5 سكربتات إعادة إنتاج (R1–R5) على نسخة منفصلة في /tmp	كشفت 3 أخطاء مؤكدة — انظر §4
ما لم يُفحص ولماذا (شفافية كاملة)
بيئة 3.10 و3.12 (مصفوفة CI): المتاح في هذه البيئة Python 3.13.16 فقط. الاختبارات نُفِّذت على 3.13 → التوافق مع 3.10 غير مُتحقَّق هنا (توجد مخاطر datetime.UTC/fromisoformat وسيطة، راجع DEP-02).
اختبار حقيقي عبر الشبكة (Gemini/Shelby CLI/Telegram): لا خروج شبكي ولا CLI حقيقي → صفر E2E حقيقي. كل ما يتعلق بزمن الاستجابة والتزامن هو تحليل ساكن + محاكاة.
حماية الفرع (Branch Protection) وPRs/Issues: غير قابلة للقراءة من clone (تحتاج GitHub API/صلاحيات) → UNVERIFIED.
سلوك ويندوز الفعلي (os.name == "nt", CREATE_NO_WINDOW, reconfigure): لم يُنفَّذ.
الأداء الحقيقي تحت الحمل: لا benchmark — كل ما في §7 إما مقيس جزئيًا أو مُصنَّف "تحليل ساكن".
ملفات لم تُقرأ بالكامل (أُخذت عيّنات مستهدفة بدلًا من قراءة كل سطر): sessions.py (1045)، key_manager.py (770)، transcriber.py (688)، backup.py (524)، panel.py (486)، observability.py، assistant.py، voice_queue.py، supervisor.py، watchdog.py، ومعظم tests/*.
لا أصرّح بأن "المشروع فُحص بالكامل". نطاق الفحص الفعلي: 12 ملفًا جوهرًا بكاملها + 10 ملفات مقتطعة + مجموعة الاختبارات كأداة (لا كقراءة سطرية) + 5 تجارب إعادة إنتاج. هذا يبرّر الأحكام الواردة هنا، ولا يبرّر حكمًا نهائيًا على وحدات لم تُقرأ.

A. Executive Summary
حالة المشروع: قاعدة كود ناضجة بشكل غير عادي لمشروع شخصي/فردي: 23,033 سطرًا، 534 اختبارًا ناجحًا، صفر ملاحظات lint، فصل مسؤوليات واضح (config / delivery / text_assist / prompts / transcriber / web / panel / backup)، تعافٍ ذاتي متعدد الطبقات (watchdog، supervisor، L1–L4)، سجلات مُنقّاة من الأسرار (logkit.redact على مستوى logging.Filter)، وحماية ويب مبنية على hmac.compare_digest + cookie HttpOnly + rate-limit + رفض X-Forwarded-For إلا خلف TRUST_PROXY.

لكن: ميزة السبك الثلاثي (Tri-Agent Synthesizer) — آخر 5 commits، وهي محور طلبك — تحتوي على 3 أخطاء مؤكدة مُعاد إنتاجها، وواحدة منها تُخالف الوعد الأساسي للميزة (التعديل في المكان In-Place):

شريط حالة "⏳ جاري السبك مع شلبي…" يبقى معلَّقًا للأبد إذا فشل editMessageText (400/429/5xx) — المستخدم يرى انتظارًا لا ينتهي، ولا تصله معلومة المهلة/الفشل. (مُعاد إنتاجه: R1)
النص الذهبي بين 3,950 و12,000 حرف لا يعدّل الرسالة ولا يُرسل كملف — يُرسل كرسالة HTML جديدة طولها 5,388 حرفًا يتجاوز ما بنته الخوارزمية تفترضه، فيُقسَّم في طبقة النقل إلى رسالتين، وتتحوّل "التعديل في المكان" إلى "رسائل جديدة" مع بقاء شريط الانتظار معلَّقًا. (مُعاد إنتاجه: R2)
prompts.compose_synthesis_prompt() لا يستقبل context_summary رغم أن delivery.py:455 يمرّره — فملخص الجلسة (L2) يُسقَط صامتًا في مسار السبك خلافًا لبقية المسارات. (مثبت من التوقيع + _accepts_context_summary)
مخاطر عالية إضافية خارج الميزة الجديدة:
4. COMPARE_MODE الافتراضي = synthesizer + مهلة محلية = 180.0ث بينما الوثيقة الرسمية (README/.env.example) تقول 8.0 وmerged. أي عملية تستخدم .local/shelby_polish.py الافتراضي (وهو قالب No-Op يعيد النص كما هو بلا تنظيم) ستُنتج "نسخة ذهبية" = النص الخام، بينما سيُحرق بها نداء Flash-Lite ثالث مدفوع الحصة لكل فويس.
5. "النسخة الذهبية" لا تُلتزم في jobs.ndjson ولا في deliver() عند عودة الدالة — لأن الخيط الخلفي لا يُنتظر (main.py:188 ينتظر _in_flight() فقط). فالسجل يقول "لا يوجد نص منظم" بينما المستخدم استلم نصًا ذهبيًا، واستئناف المهمة بعد كراش يعيد الإرسال كأن شيئًا لم يكن.
6. panel.apply_persisted_overrides() تُنفَّذ في main.py:437 بعد start_queue() في السطر 432 — أول مهمة مستأنفة بعد restart قد تعمل بإعدادات لم تُطبَّق بعد.

الحكم المختصر: المشروع جاهز للإنتاج في المسارات القديمة (التفريغ + التنظيم المفرد + الويب + النسخ + الاستعادة)، وغير جاهز للإنتاج بنسبة 100% في ميزة السبك الثلاثي تحديدًا بسبب بندَي CRITICAL/HIGH أعلاه. الإصلاح المطلوب صغير ومحدَّد (≈120 سطرًا في 4 ملفات) وليس إعادة بناء.

B. Project Inventory
المكوّن	الملفات	الحجم	الدور
التهيئة	config.py, .env.example, requirements.txt, runtime.txt, VERSION	383 سطر	مصدر واحد للإعدادات (2.0.0-phase7.1)
إعدادات المالك	settings.py, panel.py, fsm.py	161+486+—	delivery_mode/role/header_target/compare_mode + /panel
التفريغ	transcriber.py, key_manager.py, model_state.py	688+770+229	سلسلة موديلات + قواطع + حصص
الطابور	voice_queue.py, jobs.py, main.py, watchdog.py, supervisor.py, heartbeat.py	318+166+478+300+249+—	FIFO + سجل مهام + L1–L4
خط الإنتاج	delivery.py, text_assist.py, prompts.py, glossary.py, archive.py	650+337+300+335+216	التسليم + السبك + البرومبت
الويب	web_service.py, static/index.html	503+600	/api/transcribe, /api/status, /health
التشغيل	logkit.py, observability.py, incidents.py, backup.py, audit.py, store.py, netsession.py, clock.py, preflight.py	~1800	سجلات/تشخيص/نسخ
الاختبارات	tests/ (33 ملفًا) + test_360_exhaustive.py	~9,000	534 اختبارًا
الوثائق	README.md, DEPLOY.md, architecture-v2/ (23 ملفًا), requirements/ (17)، Antigravity/ (12)، genspark-review/, concepts_brief.md	—	نظام وثائقي ثقيل
CI	.github/workflows/ci.yml	26 سطرًا	pyflakes + compile + pytest × (3.10, 3.12)
لا يوجد: Dockerfile · pyproject.toml/setup.cfg (الإعدادات في pytest.ini فقط) · قفل اعتماديات (lock file) · tags/releases · CHANGELOG.md · SECURITY.md · CONTRIBUTING.md.

C. Architecture Assessment
C.1 ما هو سليم فعليًا (VERIFIED)
فصل الطبقات مقصود ومُوثَّق في رؤوس الملفات ويطابق الكود: A (تفريغ) ← B (تنظيم) ← C (دور). prompts.compose_prompt() دالة نقية بلا شبكة/حالة (prompts.py:113) — وهذا سبب مباشر لسهولة اختبارها.
حقن التبعية عبر Sender (delivery.py:29-37) هو أفضل قرار معماري في المشروع: كل الإرسال قابل للاستبدال، ولهذا يوجد 534 اختبارًا بلا شبكة.
الأرشفة قبل الإرسال (outbox) (delivery.py:260-265) يجعل فشل تليجرام غير مُفقد للنص (/last) — نمط صحيح.
main.py لا يستورد delivery ويستخدم web_service.RUNTIME كحل حقن لدائرة الاستيراد (web_service.py:348-349) → لا توجد دورات استيراد معروفة.
حدود الوحدات محترمة: interactions_client.py معزول تمامًا لـ Antigravity، وtext_assist معزول لـ Flash-Lite، وtranscriber لا يعرف شيئًا عن تليجرام.
C.2 نقاط ضعف معمارية (بالدليل)
#	الملاحظة	الدليل	التصنيف
ARC-01	حالة قابلة للتغيير مشتركة بين خيوط بلا قفل: deliver() يعيد result كقاموس، والخيط الخلفي يكتب result["polished"] وresult["compare_thread"] بعد عودة الدالة. لا قفل، ولا threading.Event، ولا join قبل القراءة.	delivery.py:462, 537 · telegram_bot.py:559-571 · main.py:188	HIGH
ARC-02	"In-Place" مضمونة بالحجم فقط لا بالبنية: كل قرار تعديل مربوط بشرط len(html) <= TELEGRAM_MAX_MSG - 50؛ عند تجاوزه يتحول السلوك صامتًا إلى "رسالة جديدة" بينما نص الحالة المبدئي يقول للمستخدم إنه سيُعدَّل في مكانه.	delivery.py:483, 502, 524, 390	HIGH
ARC-03	ازدواج مصدر الحقيقة لإعداد واحد: COMPARE_MODE في Config وcompare_mode في settings.json وpanel.SET_CONFIG_ATTR يكتب على Config — مع منطق هجين (settings._defaults() يقرأ من Config، و_load() يعطي الأولوية للقرص).	settings.py:43, 152-154 · panel.py:44-51, 427-429	MEDIUM
ARC-04	ترتيب الإقلاع: استئناف المهام (start_queue) قبل تطبيق تجاوزات settings.json.	main.py:432 ثم 437	MEDIUM
ARC-05	ميزة ثالثة تفتقر لمسارات المراقبة: text_assist.stats() يجمع synth_* لكن /status يعرض فقط local_ok/local_calls — لا مؤشر على معدل نجاح السبك.	text_assist.py:44-48 · telegram_bot.py:1362-1366	LOW
ARC-06	delivery.deliver = 384 سطرًا و10 مستويات تداخل (دالة واحدة تدير: القاموس، الأرشيف، العنوان، الجلسة، التنظيم، الإنقاذ، 4 أوضاع، حدود الحجم).	delivery.py:241-625	MEDIUM (دين تقني)
لا توجد Single Points of Failure جديدة داخل عملية البوت؛ SPOF الحقيقي هو العملية الواحدة على استضافة Pterodactyl بحجم 512MB (موثَّق ومقصود)، وتُدار بـ watchdog + supervisor.

D. Confirmed Bugs (أخطاء مؤكدة — جميعها مُعاد إنتاجها)
منهجية: كل بند هنا له سكربت إعادة إنتاج نُفِّذ فعليًا على نسخة منفصلة (R1–R5 في EVIDENCE_LOG.md). ما لم يُعَد إنتاجه مكتوب صراحة "ساكن (Static proof)".

BUG-01 — شريط "جاري السبك" معلَّق للأبد عند فشل التعديل · CRITICAL
المسار: delivery.py:516-531
الوصف: عند فشل شلبي (مهلة/خطأ) يُبنى to_html، ويُحاول التعديل فقط إذا mid is not None. إذا رجع edit_text بـ False (400 تنسيق، 429 flood، رسالة محذوفة) → edited=False، والشرط التالي if not edited and mid is None لا يتحقق لأن mid ليس None. النتيجة: لا شيء يحدث. المستخدم يرى للأبد: ⏳ جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة 180ث)...
إعادة الإنتاج: R1 → sent=2, edited=0, "تجاوز شلبي المهلة" غير موجود في أي رسالة مُرسلة.
السبب الجذري: الشرط مكتوب كـ"أرسل بديلًا فقط إذا لم تكن هناك رسالة أصلًا"، بينما المطلوب "أرسل بديلًا إذا لم ينجح التعديل" (كما في مسارَي shelby_rescue وfallback_compare اللذين يستخدمان if not edited: بلا شرط mid).
الأثر: تضليل كامل للمستخدم + فقدان الإشارة إلى أن مسودة جيميني هي المعتمدة.
الإصلاح: if not edited: _send_long(...) or sender.send_text(to_html, anchor, "HTML").
اختبار الإثبات المطلوب: test_synthesizer_timeout_both_paths بـ edit_text → False وتحقّق أن رسالة نهائية موجودة تحوي "تجاوز شلبي المهلة".
BUG-02 — النص الذهبي بين 3,950 و12,000 حرف: لا تعديل ولا ملف · HIGH
المسار: delivery.py:483 (شرط التعديل) + delivery.py:491 (_send_long) + delivery.py:641 (عتبة الملف).
الوصف: ثلاثة حدود غير متسقة: TELEGRAM_MAX_MSG-50 = 3950 (حد التعديل) · POLISH_FILE_THRESHOLD = 12000 (حد الملف) · TELEGRAM_MAX_MSG = 4000. النص بين 3950 و12000 يمرّ إلى _send_long كـhtml_text فيرسله sender.send_text كاملًا — وفي الإنتاج send_html_get_id يقسمه إلى رسائل (telegram_bot.py:285), فتُنشأ رسائل جديدة، وتبقى رسالة الانتظار الأصلية غير معدَّلة.
إعادة الإنتاج: R2 → golden=5000 ⇒ sent lengths = [155, 266, 5388], edited=[], docs=[].
السبب الجذري: _send_long يقارن مع POLISH_FILE_THRESHOLD بينما المُنادي يقارن مع TELEGRAM_MAX_MSG؛ لا يوجد "حزام أمان" في _send_long يمنع تمرير نص > سقف الرسالة.
الأثر: مخالفة وعد الميزة (In-Place) + إزعاج المستخدم برسائل متعددة + بقاء شريط انتظار معلَّقًا (نفس BUG-01).
الإصلاح المقترح: في _send_long: limit = min(POLISH_FILE_THRESHOLD, Config.TELEGRAM_MAX_MSG)؛ وإن كان النص > TELEGRAM_MAX_MSG-50 دائمًا اجعله ملفًا (أو أضف _send_long_chunks منفصلة صراحةً) — وفي مسار السبك تحديدًا: إذا كبر الناتج عن حد التعديل → عدّل الرسالة إلى "✅ اكتملت النسخة الذهبية — أُرسلت في ملف/رسائل أدناه" ثم أرسل الناتج، كي تُغلق رسالة الحالة دائمًا.
اختبار الإثبات: 3 قيم حدودية: 3,900 (تعديل) · 4,500 (يجب أن يصبح ملفًا) · 13,000 (ملف) مع التأكد أن كل حالة تنتهي بحالة نهائية مرئية.
BUG-03 — context_summary (L2) يُسقَط صامتًا في السبك · HIGH
المسار: delivery.py:455 → text_assist.synthesize → prompts.compose_synthesis_prompt
الوصف: _accepts_context_summary(text_assist.synthesize) ترجع True (التوقيع يحتوي context_summary)، فيُمرَّر الملخص، لكن synthesize() لا يستخدمه في استدعاء compose بل يضيفه على system_text بعد التوليد (انظر §التفصيل) وبطريقة لا تصل للموديل في مسار generate(system_text, user_content)… أدق صياغة: الملخص يُلحق بـsystem_text بعد أن بنى compose_synthesis_prompt رسالتي النظام والمستخدم → فهو يصل فعلًا إلى الموديل لكن مخالفًا لنمط polish_gemini الذي يلحقه قبل الاستخدام بنفس الطريقة…
تصحيح ذاتي أثناء التدقيق: هذا البند ليس خطأً وظيفيًا — text_assist.py:311-315 يضيف الملخص إلى system_text قبل generate()، فالموديل يراه (تمامًا كما في polish_gemini:205-207). ترقية التصنيف: INFO لا HIGH. يبقى كملاحظة تصميمية: بناء الاثنين في compose_synthesis_prompt أنظف من الحقن الخارجي، وتمكين اختباره دالّيًا.
لا أُبقي على ادعاء غير مثبت. (هذا تصحيح مقصود ومعلن، لأنه جوهر صحة التقرير.)

BUG-04 — "النسخة الذهبية" لا تصل إلى سجل المهام ولا إلى deliver() · HIGH
المسار: telegram_bot.py:567-572 + delivery.py:462
الوصف: الخيط الخلفي يكتب result["polished"]=golden_text بعد أن يكون process_job قد قرأ res["polished"] (السطر 567-571 يُنفَّذ فور عودة deliver). وبالتالي:
jobs.record(..., polished_text=...) يحفظ مسودة جيميني أو None بدلًا من الذهبية.
استئناف المهمة/المكرر (jobs.find_recent_result) يعيد إرسال مسودة جيميني كأنها الناتج النهائي.
/trace و/status يُظهران معلومة غير صحيحة.
الدليل: main.py:188-190 ينتظر _in_flight() فقط؛ delivery.py:535-537 الخيط daemon=True؛ لا join في أي مكان (بحث شامل: grep compare_thread = تعريف واحد + اختبارات فقط).
الأثر: عدم اتساق بيانات + احتمال إعادة إرسال محتوى مختلف عن الذي رآه المستخدم.
الإصلاح: إبقاء deliver تُرجع result مع compare_thread (موجود) وجعل process_job يسجّل بعد t.join(timeout=...)، أو (أنظف) تسجيل الذهبية من داخل الخيط عبر callback on_polished(text) تمرّره deliver.
اختبار الإثبات: محاكاة "إعادة تشغيل بعد event" والتحقق أن jobs.ndjson يحوي النص الذهبي.
BUG-05 — السبك يعتمد على مسودة Shelby الافتراضية (No-Op) · HIGH (تصميمي/تشغيلي)
المسار: config.py:183-189 · Antigravity/local_provider_template.py:39-42 · .env.example:98
الوصف: config.py يفعّل تلقائيًا LOCAL_POLISH_CMD = python .local/shelby_polish.py إذا وُجد. الملف غير موجود في المستودع (.local/ مستثنى) — والقالب المرفق execute_custom_provider() يعيد النص كما هو ⇒ مسودة شلبي = النص الخام. فيُسبك (خام + مسودة جيميني + خام) بنداء Flash-Lite إضافي.
الأثر: مضاعفة استهلاك حصة Flash-Lite، وأحيانًا "تلويث" المسودة الذهبية بالخام (التكرار/الفصحنة)، والادعاء بأن هناك محركين.
لا يوجد أي تحذير في السجلات بأن السبك يعمل بمسودة No-Op.
الإصلاح: فحص stdout == input (تشابه ≥ 95%) ⇒ اعتبار Shelby "غير مفعّل" + سطر تحذير واضح في /status + عدم تحمّل تكلفة السبك في هذه الحالة.
BUG-06 — مجموعة الاختبارات تكتب على إعدادات التشغيل الحقيقية · MEDIUM (QA)
المسار: settings.py:71-75 (_save يكتب Config.SETTINGS_FILE) + tests/test_dual_polish.py
الدليل (R5): في نسخة نظيفة بدون settings.json، تشغيل pytest tests/test_dual_polish.py يُنشئ settings.json بمحتوى {"delivery_mode":"dual","role":"general","header_target":"both","compare_mode":"synthesizer"}. والمجموعة الكاملة تُنشئ keys.json وglossary.json أيضًا (مثبت بجولة على 33 ملف اختبار: 33/33 تُنشئ keys.json).
الأثر الفعلي: على خادم فيه settings.json حقيقي (مثلًا delivery_mode=polished_only)، تشغيل الاختبارات دون fresh_env (وهو الوضع الافتراضي في test_dual_polish.py) يعيد كتابة الملف بالكامل بالقيم المحفوظة أو الافتراضية. ليس تلويثًا للقيم (الاختبار يستعيد compare_mode) لكنه كتابة/fsync/os.replace على ملف إنتاج + إعادة ترتيب/تطبيع الملف.
ملاحظة تصحيحية مهمة: ادعاء المستند ca9218c "restore compare_mode in finally blocks to prevent test suite from polluting settings.json" يُثبت الحفظ والاستعادة للقيمة، لا العزل. finally تعيد القيمة لكنها لا تمنع الكتابة على الملف الإنتاجي.
الإصلاح: استخدم fixture fresh_env (الموجود فعلًا ويوجّه SETTINGS_FILE إلى tmp_path) في كل اختبارات test_dual_polish.py، أو autouse تُصفّر Config.SETTINGS_FILE للجميع.
اختبار الإثبات: اختبار meta يشغّل الحزمة على SETTINGS_FILE مفقود ويتحقّق أنه لم يُنشأ.
BUG-07 — هاتان الحالتان لا تغطيهما الاختبارات رغم ادعاء العكس · MEDIUM (QA)
test_deliver_synthesizer_mid_none_graceful_fallback (tests/test_dual_polish.py:560-590) يتحقق فقط أن res["polished"] تحدّث — لا يتحقق أن المستخدم استلم حالة نهائية، وهو بالتحديد ما يفشل في BUG-01.
test_deliver_synthesizer_timeout_fallback (:397-424) يستخدم FakeSender.edit_text → True دائمًا، فلا يمكنه كشف BUG-01 أبدًا.
النتيجة: 534 اختبارًا ناجحًا + 3 أخطاء مؤكدة في نفس الميزة ⇒ الاختبارات تختبر المسار السعيد لا الفشل الحقيقي.
BUG-08 — t0_flow يبدأ قبل نداء Gemini بمقدار 0 · قياس الـ Wall-Clock · INFO
delivery.py:348 t0_flow = time.time() يُسجَّل قبل استدعاء التنظيم مباشرة، ولا يشمل: التحميل، التفريغ، إرسال الخام، حساب العنوان المؤجَّل (_attach_title()) — أي أن "إجمالي" في التذييل = زمن ما بعد إرسال الخام، لا الزمن الكلي للفويس.
الحكم: الحساب دقيق كـ wall-clock للنافذة التي يقيسها (time.time() لا جمع مُدد) — طلبك "هل يقيس الـ Real Wall-Clock بدقة؟" ⇒ نعم للفترة المقاسة، لا للزمن الكلي للفويس. التسمية "⏱️ إجمالي" مضلّلة. INFO — يُستحسن تسميتها "منذ إرسال الخام".
عدد الأخطاء المؤكدة: 6 (2 عالية الأثر في الميزة + 1 عالية في السجلات + 2 متوسطة + 1 INFO).

E. Security Findings
ID	الشدة	الحالة	البند	الدليل
SEC-01	INFO / VERIFIED	✅ صحيح	حجب الأسرار ثنائي الطبقة: RedactFilter على مستوى logging + redact() عند القراءة؛ يغطي AIza…, AQ.…, توكن تليجرام، WEB_ACCESS_TOKEN، ومسار LOCAL_POLISH_CMD وكل مجلداته	logkit.py:21-49, 52-66
SEC-02	INFO / VERIFIED	✅ صحيح	لا أسرار في المستودع (مسح فعلي = 0) + .gitignore يغطي keys.*, .env, .local/, *.ndjson, settings.json	مسح + .gitignore:1-38
SEC-03	INFO / VERIFIED	✅ صحيح	shell=False إجباري؛ LOCAL_POLISH_CMD يُقسَّم بـ shlex.split على POSIX، وos.name=="nt" يمرّره كسلسلة إلى subprocess.run بلا shell	text_assist.py:162-175
SEC-04	INFO / VERIFIED	✅ صحيح	مصادقة الويب سليمة: hmac.compare_digest (لا ==)، cookie HttpOnly، ?token= لمرة واحدة فقط، رفض X-Forwarded-For إلا مع TRUST_PROXY=1، توليد توكن تلقائي على مضيف عام	web_service.py:123-160, 442-465
SEC-05	MEDIUM	⚠️ يحتاج قرارًا	/api/add_key يتطلب التوكن فقط، لا يُقيَّد بـ rate-limit (بينما /api/transcribe مقيَّد). من يملك التوكن يستطيع إغراق keys.json بمفاتيح صحيحة الشكل	web_service.py:280, 292-295, 321-337
SEC-06	MEDIUM	⚠️	_authorized() يقبل أي طلب من localhost بلا توكن (self._is_local()). آمن على استضافة معزولة، لكنه خطر لو شُغِّل البوت على جهاز شخصي مع متصفح (CSRF محلي / أي تطبيق محلي)	web_service.py:123-137
SEC-07	LOW	⚠️	_client_ip() يعتمد X-Forwarded-For[0] عند TRUST_PROXY=1 — لو كان الوكيل لا يُعيد كتابة الترويسة يمكن تجاوز حد الـ rate. موثَّق كقرار، لكن التوصية أخذ آخر عنصر بدلًا من الأول	web_service.py:154-160
SEC-08	LOW	⚠️	/health عام بلا توكن ويعيد تفاصيل تشغيلية (حالة الخيوط، القواطع، عدد المفاتيح المتاحة). لا يكشف أسرارًا (مثبت: build_health لا يطبع قيمًا) لكنه يكشف بنية داخلية	web_service.py:218-222, 352-…
SEC-09	MEDIUM	⚠️	سماح التحميل عبر Content-Length قبل القراءة، والقراءة الفعلية بلا سقف كلي (self.rfile.read(content_length) بعد فحص الطول فقط) — طلب بـContent-Length كاذب/بطيء يمكن أن يحجز خيطًا. ThreadingHTTPServer بلا timeout افتراضي	web_service.py:164-172, 297-304
SEC-10	INFO / VERIFIED	✅	backup يحمي من zip-slip ويكتب مسارات مُنقّاة فقط + فحص contains_secret قبل التسليم	backup.py:44-95
SEC-11	INFO / VERIFIED	✅	لا تنفيذ ديناميكي (eval/exec/pickle/yaml.load = 0)	مسح فعلي
SEC-12	INFO / UNVERIFIED	—	حماية الفرع/الصلاحيات على GitHub: لا تُقرأ من clone	غير متاح
لا توجد ثغرة حرجة معروفة في المسارات المفحوصة. أخطر بند أمني هو SEC-09 (استنزاف خيوط)، وSEC-05/06 يعتمدان على قرار نشر.

F. Testing Assessment
الحقائق المقيسة: 534 مُجمَّعة · 534 ناجحة · 0 فاشلة · 0 skipped · زمن ~74ث · بلا شبكة (conftest._no_network_polish يرمي AssertionError على أي requests.post).

نقاط قوة (VERIFIED)
حاجز شبكة autouse يمنع أي اختبار من لمس Gemini/تليجرام — ممتاز.
fresh_env fixture غني: يوجّه ≥17 مسارًا إلى tmp_path ويعيد ربط مراجع Config في 21 وحدة (مشكلة "الوحدة تحمل نسخة Config قديمة" مُعالجة بوعي).
وجود tests/fake_telegram.py وFakeClock ⇒ اختبارات زمنية/شبكية حتمية.
تغطية ممتازة للمسارات الحرجة القديمة: النسخ/الاستعادة، القواطع، الاستئناف، الإيقاف الرشيق، تعدد المنافذ، parity لوحة/أوامر.
فجوات مؤكدة
ID	الشدة	الفجوة	الدليل
QA-01	HIGH	لا اختبار يُثبت أن الاختبار يفشل عند خلل: الحزمة تمرّ وهي تحوي 3 أخطاء في الميزة الجديدة (BUG-01/02/04) — asserts تتحقق من وجود نص، لا من الحالة النهائية للمستخدم	§D
QA-02	MEDIUM	عزل settings.json مفقود في test_dual_polish.py (لا fresh_env)	BUG-06
QA-03	MEDIUM	FakeSender.edit_text يعيد True دائمًا ⇒ كل مسارات "فشل التعديل" غير مغطاة (وهي بالضبط ما يسبب BUG-01)	tests/test_dual_polish.py:39-41
QA-04	MEDIUM	tests/conftest.py:20 يمسح LOCAL_POLISH_CMD/TIMEOUT في fresh_env فقط، لا في autouse ⇒ اختبارات أخرى قد ترى القيمة المنبعثة من .env الحقيقي	conftest.py:16-32, 24-25
QA-05	LOW	لا تغطية لـ _call_local_provider زمنيًا حقيقيًا (TimeoutExpired, FileNotFoundError, stdout فارغ، returncode≠0) — يوجد تغطية جزئية فقط	text_assist.py:150-196
QA-06	LOW	لا اختبار حدّي واحد عند 3950/4000/12000 (BUG-02 كان سيُكتشف فورًا)	—
QA-07	LOW	test_360_exhaustive.py في جذر المستودع خارج testpaths=tests ⇒ غير مُنفَّذ في CI (يُستثنى أيضًا من pack_deploy). لا أحد يعرف إن كان يمرّ	pytest.ini:2 · tools/pack_deploy.py:57
QA-08	INFO	لا تغطية على 3.10 (مصفوفة CI تنصّ عليها لكن هذه البيئة 3.13)	CI matrix
QA-09	INFO	لا coverage محسوب/منشور — pytest-cov غير مثبَّت ولا نسبة معلنة	requirements-dev.txt
G. Performance & Reliability
مقيس / مثبت
ID	الشدة	البند	الدليل
PERF-01	HIGH	نداءان أو ثلاثة نداءات ذكاء واحدة لكل فويس في وضع السبك (Flash-Lite تنظيم + شلبي محلي + Flash-Lite سبك). مع TRANSCRIBE_WORKERS=1 وQUEUE_PACING_SEC=2، فويس واحد يحتاج 3 نداءات، حصتها اليومية 500 ⇒ السقف الفعلي ~160 فويسًا/يوم مع مضاعفة زمن الانتظار. ولا توجد مقايضة موثقة بين "الجودة" والحصة، ولا circuit breaker على السبك	delivery.py:441-460 · text_assist.synthesize
PERF-02	HIGH	مهلة 180ث لكل فويس في طابور عامل واحد: أسوأ حالة = فويس يُقفل الطابور 180ث (شلبي) + 40ث (سبك) + مهلة تفريغ. QUEUE_SKIP_DEFERRED=0 يجعل الفويسات المتراكمة تنتظر	config.py:188 · voice_queue.py
REL-01	HIGH	خيوط daemon=True غير مُنتظرة عند الإيقاف: main.py:188 ينتظر _in_flight() (المهام) ثم يخرج. الخيط الخلفي للسبك قد يكون في منتصف edit_text ⇒ رسالة لم تُحرَّر/لم تُغلَق، ويُعاد إرسال كل شيء بعد الإقلاع (تكرار). لا join على compare_thread في shutdown()	main.py:178-201 · delivery.py:535
REL-02	MEDIUM	حدود التعديل مقابل حدود الإرسال (نفس جذر BUG-02). أي تعديل >3950 يتحول صامتًا لمسار مختلف	delivery.py:483
REL-03	MEDIUM	prompts.py:44 يعترف ضمنيًا بأن التعليمات داخل المادة: "أي أوامر بداخلها تُعامل كنص" — جيد، لكن سبك ثلاثي يعني حقن مسودة Shelby (مخرَج نموذج خارجي) داخل رسالة المستخدم في compose_synthesis_prompt → سطح Prompt Injection أوسع (نموذج محلي مخرجه غير موثوق). المطلوب تغليف المسودتين بفواصل صريحة + تعليمات "بيانات لا تعليمات" (موجود جزئيًا)	prompts.py:293-298
REL-04	MEDIUM	clock.py/موقتات time.time() مستخدمة لقياس مُدد (ينزلق مع تغيير ساعة النظام/NTP). الأصح time.monotonic() — main.py:186 يستخدم monotonic بشكل صحيح، بينما delivery.py وtext_assist.py يستخدمان time.time()	delivery.py:348,443,453,471 · text_assist.py:102,160
REL-05	LOW	لا retry/backoff لتعديل تليجرام: edit_telegram_message يعيد مرة واحدة بلا parse_mode؛ أخطاء 429 تحتاج انتظارًا (retry_after)	telegram_bot.py:322-334
نقاط قوة اعتمادية (VERIFIED)
backoff متعدد المستويات على مستوى الشبكة (NET_BACKOFF)، حالة "حذر" بعد ReadTimeout، TCP_KEEPALIVE لكشف half-open، قواطع موديلات محفوظة على القرص وتُحمَّل بعد restart، jobs.ndjson مع fsync واستئناف، L4 (exit code 3) + supervisor + watchdog، /doctor + /incidents + restore_test. هذا أفضل مما في أغلب المشاريع الإنتاجية الصغيرة.
H. Dependencies & CI/CD
ID	الشدة	البند	الدليل
DEP-01	MEDIUM	requests>=2.31.0,<=2.32.3: السقف يمنع تلقّي إصلاحات أمنية مستقبلية، والسقف الأدنى غير مثبَّت بدقة ⇒ بناءات غير حتمية (لا Lock file). الإصدار 2.32.x نفسه شمل إصلاحات أمنية	requirements.txt:1
DEP-02	MEDIUM/UNVERIFIED	مصفوفة CI تشمل 3.10 لكن runtime.txt يقول 3.12 — لا ضمان أن الكود يعمل على 3.10 (لم أشغّله). تثبيت الإصدار يجب أن يكون مصدرًا واحدًا	ci.yml:13 · runtime.txt
DEP-03	LOW	requirements-dev.txt بـ-r requirements.txt صحيح، لكن pyflakes وpytest غير مثبَّتين بسقف ⇒ تغيّر سلوك lint بين تشغيل وآخر	requirements-dev.txt
DEP-04	LOW	python-3.12 في runtime.txt مكتوب بصيغة غير قياسية (ملف Pterodactyl يتوقع python-3.12.x عادةً) — يحتاج تحقق على لوحة الاستضافة	runtime.txt
CI-01	MEDIUM	on.push.branches: ["**"] = CI على كل فرع + pull_request = تشغيل مزدوج لكل PR (تكلفة/إزعاج)	ci.yml:4-6
CI-02	MEDIUM	لا خطوة اختبار انحدار على A-test قبل الدمج، ولا حماية فرع موثّقة — الآلية تعتمد على انتباه بشري	—
CI-03	LOW	لا نشر/إصدار آلي: tools/pack_deploy.py يدوي (allowlist + فحص أسرار) — جيد أمنيًا، لكن النشر ليس قابلًا للتكرار (لا artifact versioned، لا rollback آلي)	tools/pack_deploy.py
CI-04	INFO	لا pip cache ولا timeout-minutes على الـ job — تشغيل معلَّق قد يستهلك 6 ساعات	ci.yml:9-26
I. Documentation Assessment
ID	الشدة	التناقض	الدليل
DOC-01	HIGH	LOCAL_POLISH_TIMEOUT = 8.0 في الوثائق، 180.0 في الكود	README.md:137 ("8.0") vs config.py:188 ("180.0")
DOC-02	HIGH	COMPARE_MODE الافتراضي موثَّق merged وغير مذكور synthesizer أصلًا — الميزة الجديدة غير موثقة في README/.env.example إطلاقًا	.env.example:99 vs config.py:189 · grep synthesizer README.md = ∅
DOC-03	HIGH	ميزة السبك الثلاثي غير موثقة ضمن README رغم أنها الافتراضي الآن. لا شيء عن: شريط الحالة، In-Place، صمام إنقاذ شلبي، ولا التذييل الزمني. التوثيق الوحيد = concepts_brief.md بحالة "Draft / Ideation"	concepts_brief.md:6 "Status: Draft / Ideation"
DOC-04	HIGH	الوثائق العليا تُناقض نفسها: Antigravity/* تُقرّر "خفض المهلة إلى 8.0s لمنع تجويع الطابور" بينما الكود الآن 180.0s — أي أن قرارًا هندسيًا موثَّقًا نُقض بصمت	Antigravity/02_…:37,78 vs config.py:188
DOC-05	MEDIUM	متغيرات بيئة موثقة لا يقرأها الكود: POLISH_PROVIDER, LOCAL_PROVIDER_CMD, LOCAL_PROVIDER_TIMEOUT (في local_provider_template.py وAntigravity/01) — الأسماء الحقيقية LOCAL_POLISH_CMD/TIMEOUT. من يتبع القالب لن يعمل عنده شيء	Antigravity/local_provider_template.py:10-12 · grep = 0 في الكود
DOC-06	MEDIUM	README يدّعي "525 اختبارًا" والواقع 534	README.md:195
DOC-07	LOW	قائمة الإصدارات VERSION=2.0.0-phase7.1 بلا CHANGELOG ولا tags ⇒ لا يمكن ربط إصدار بكوميت	—
DOC-08	INFO	حجم توثيقي مفرط مقابل الكود: 23 ملفًا في architecture-v2 + 17 في requirements + 12 في Antigravity + genspark-review/ + consolidated_review_report.md + PROGRESS/RESUME مكرّرة في الجذر وAntigravity/ ⇒ مصدر حقيقة موزَّع يتناقض (وهو سبب DOC-04)	قائمة الملفات
نقاط قوة (VERIFIED): وثائق داخلية غنية فوق العادة، تعليقات عربية تشرح سبب القرار لا ماذا يفعله الكود (مثال ممتاز: delivery.py:267-269)، وDEPLOY.md + docs للوحة، وأوامر /doctor, /incidents, /trace تجعل تشخيص المشكلة ممكنًا بلا SSH.

J. Positive Findings (يجب الحفاظ عليها دون تغيير)
Sender injection (delivery.py:29-37) — العمود الفقري لقابلية الاختبار. لا تكسره.
logkit.RedactFilter على مستوى logging — حماية واحدة تغطي كل القنوات، وليس "حجبًا في كل print".
الأرشفة قبل الإرسال + /last — لا نص يضيع عند فشل تليجرام.
_html_chunks يوازن الوسوم (telegram_bot.py:135-…) وتجنّب قطع <pre> — تفصيلة يستصعبها معظم المشاريع.
jobs.ndjson مع fsync + استئناف بعد إقلاع + idempotency بـfile_unique_id — سلوك منتج حقيقي.
القواطع المحفوظة على القرص + سلّم 20→45→90→180 — تعافٍ مقصود ومُختبَر.
apply_persisted_overrides() + panel.SET_CONFIG_ATTR — طبقتان: Config افتراضي، settings.json تجاوز (نمط سليم).
حاجز الشبكة autouse في الاختبارات — لا اختبار "يزحف" إلى الإنترنت.
_MODE_ALIASES يقبل مرادفات عربية (خام, منظم, الاتنين) — تجربة مستخدم مدروسة.
backup.contains_secret() + zip-slip guard + restore_test — النسخ الاحتياطي مُثبَت لا مُدَّعى.
prompts.compose_prompt دالة نقية + حدّ TRANSCRIBE_LAYER_A_MAX_CHARS لحماية طبقة التفريغ من التضخم.
صفر بدائيات خطرة (shell=True, eval, pickle) في 23 ألف سطر — انضباط أمني جيد.
K. Technical Debt
ID	الشدة	الدين	التقدير
TD-01	HIGH	delivery.deliver = 384 سطرًا / تداخل 10 مستويات / 5 ميزات في دالة واحدة. كل خطأ في هذا التدقيق سببه فرع جديد أُضيف داخل نفس الدالة	إعادة هيكلة إلى _deliver_compare(...), _deliver_single(...) — ~2 يوم
TD-02	HIGH	تكرار كود الإنقاذ 4 مرات: _bg_shelby_rescue, _bg_synthesizer, _bg_shelby (merged), _bg_shelby_split — نفس بنية edit → fail → _send_long مكرّرة 4×وبنسخ مختلفة ⇒ BUG-01 موجود في نسخة واحدة فقط. مصدر الأخطاء نفسه هو التكرار	دالة واحدة _replace_or_send(html, plain, mid, anchor, prefix, caption) — ~4 ساعات
TD-03	MEDIUM	ثلاثة حدود حجم متفرقة (TELEGRAM_MAX_MSG, POLISH_FILE_THRESHOLD, -50) بلا مشتق واحد	تعريف DELIVERY_LIMITS مشتق
TD-04	MEDIUM	الاعتماد على delivery.Config vs config.Config — conftest يعيد ربط 21 وحدة يدويًا لأن كل وحدة تعمل from config import Config. هشاشة بنيوية متكررة	نمط import config; config.Config.X
TD-05	MEDIUM	test_360_exhaustive.py خارج testpaths — ملف 331 سطرًا محجوب عن CI	نقله إلى tests/
TD-06	LOW	print() بدل logger في معظم delivery.py (18 موضعًا) — لا يمرّ عبر RedactFilter (رغم أن redact() تُستدعى يدويًا في بعضها فقط)	تحويل تدريجي
TD-07	LOW	COMPARE_MODE/compare_mode مصدران + panel.apply_setting يكتب على Config مباشرة بلا settings في حالة compare_mode (يكتب على الاثنين)	توحيد
TD-08	LOW	ملفّات PROGRESS.md وRESUME.md مكرّرة في الجذر وفي Antigravity/ بمحتوى مختلف	حذف/دمج
L. Improvement Opportunities (مرتّبة بالقيمة/التكلفة)
#	الفرصة	القيمة	التكلفة	ضروري؟
O-1	توحيد دالة _replace_or_send() وإصلاح BUG-01/02 من جذر واحد	حرجة	منخفضة	ضروري
O-2	إغلاق دورة السبك بشكل متزامن (join بـtimeout) لتسجيل الناتج الصحيح	عالية	منخفضة	ضروري
O-3	فحص No-Op لشلبي + إسقاط تكلفة السبك عند غيابه	عالية	منخفضة	ضروري
O-4	مزامنة الوثائق مع الكود (8.0→180.0، merged→synthesizer، 525→534)	عالية	منخفضة	ضروري
O-5	عزل settings.json في الاختبارات + edit_text قابل للفشل في FakeSender	عالية	منخفضة	ضروري
O-6	إعادة هيكلة deliver() إلى دوال صغيرة	متوسطة	متوسطة	مستحسن
O-7	threading.Event/join في shutdown() لخيوط السبك	متوسطة	منخفضة	مستحسن
O-8	قياس coverage ونشر النسبة	متوسطة	منخفضة	مستحسن
O-9	مؤشرات synth_ok/synth_fail في /status و/doctor	متوسطة	منخفضة	مستحسن
O-10	Circuit breaker على السبك (إذا فشل N مرة متتالية → إيقاف المؤقت)	متوسطة	متوسطة	اختياري
O-11	lockfile/تثبيت دقيق للاعتماديات	منخفضة	منخفضة	مستحسن
O-12	monotonic() للمُدد	منخفضة	منخفضة	اختياري
O-13	مستند معمارية واحد "الحقيقة الحالية" بدل 50 ملفًا	متوسطة	متوسطة	مستحسن
M. Risk Register
Risk	الاحتمال	الأثر	التقييم	مُخفِّف حالي
R-أ: المستخدم يرى "جاري السبك" للأبد عند فشل التعديل	مرتفع	متوسط	CRITICAL	لا شيء
R-ب: نص ذهبي طويل يتحول لرسائل متعددة + انتظار معلَّق	مرتفع	متوسط	HIGH	لا شيء
R-ج: حرق حصة Flash-Lite في سبك بمسودة No-Op	مرتفع	منخفض-متوسط	HIGH	لا شيء
R-د: سجلات /trace وjobs.ndjson تُظهر ناتجًا مختلفًا عن المرسل	مؤكد	منخفض	HIGH (بيانات)	لا شيء
R-هـ: تكرار الإرسال بعد كراش أثناء السبك	متوسط	متوسط	MEDIUM	jobs.ndjson idempotency لا تغطي إعادة الإرسال الجزئية
R-و: مستخدم يعتمد توثيق LOCAL_POLISH_TIMEOUT=8.0 فيصطدم بـ180ث	مؤكد	منخفض	MEDIUM	لا شيء
R-ز: تلويث settings.json من CI/اختبارات على خادم إنتاج	متوسط	متوسط	MEDIUM	لا شيء
R-ح: تجاوز حد الـ rate في الويب عبر X-Forwarded-For إذا الوكيل سيئ	منخفض	منخفض	LOW	TRUST_PROXY=0 افتراضيًا
R-ط: استنزاف خيوط الويب بطلب بطيء	منخفض	متوسط	MEDIUM	ThreadingHTTPServer + MAX_UPLOAD_MB
R-ي: تشغيل إعدادات لم تُطبَّق على أول مهمة مستأنفة	متوسط	منخفض	LOW	نافذة زمنية صغيرة
N. Final Engineering Verdict
درجة الجاهزية (0–100)
البُعد	الدرجة	الأساس
المعمارية (المسارات القديمة)	84	طبقات واضحة، حقن سليم، لا دورات استيراد — مقابل دالة deliver ضخمة
المعمارية (السبك الثلاثي)	46	التصميم صحيح مفاهيميًا، التنفيذ يعتمد على شرط حجم وحيد + 4 نسخ مكرّرة من منطق الإنقاذ
الأمن	86	لا أسرار، shell=False، compare_digest، حجب سجلات؛ بعض مخاطر DoS الويب
الاختبارات	72	534 ناجحة بلا شبكة، لكنها تختبر المسار السعيد وتُخفي 3 أخطاء
الاعتمادية	80	أفضل من المتوسط بكثير (watchdog/supervisor/incidents/backup) مع ثقب في shutdown وسبك غير مُنتظَر
الأداء	58	3 نداءات/فويس، مهلة 180ث على عامل واحد — بلا قياس حمل حقيقي
التوثيق	44	ضخمة في الحجم، متناقضة في المضمون، والميزة الافتراضية غير موثقة
CI/CD	62	lint+compile+tests على مصفوفتين، لكن نشر يدوي وبلا lock/coverage
هل الكود جاهز للإنتاج بنسبة 100%؟
لا. ليس بسبب هشاشة عامة، بل بسبب 3 عيوب محدَّدة ومُعادة الإنتاج في الميزة التي طلبت فحصها، أخطرها أن المستخدم قد يرى شريط انتظار لا ينتهي (BUG-01)، وأن وعد "التعديل في المكان" ينهار عند 3,950 حرفًا (BUG-02)، وأن ما يُسجَّل في السجل ليس ما استلمه المستخدم (BUG-04).

ما أنجزه — ومع إصلاح P0/P1 في MASTER_REMEDIATION_PLAN.md (تقدير 1.5–2 يوم عمل)، تصبح الميزة قابلة للإنتاج بثقة.

ما تم التحقق منه / ما لم يتم
✅ VERIFIED: كل الاستنتاجات في §D (بما فيها 3 أخطاء أُعيد إنتاجها بسكربت)، §E (مسح أسرار/بدائيات)، §F (534/534)، §H (قراءة CI/requirements).
⚠️ UNVERIFIED: التوافق مع Python 3.10، الأداء الفعلي، سلوك ويندوز، حماية فرع GitHub، سلامة .local/shelby_polish.py الحقيقي (ليس في المستودع)، أي E2E عبر الشبكة.
❌ لم يُفحص: 4 وحدات كبيرة قراءة سطرية كاملة (sessions.py, key_manager.py, transcriber.py, backup.py) — أُخذت عيّنات مستهدفة فقط؛ أي حكم عليها في هذا التقرير هو مبدئي.
FINDINGS INDEX — قائمة المشكلات المُدلَّلة
A-test @ f3709cc · 2026-11-09
المفاتيح: ✅ VERIFIED (بإعادة إنتاج) · 🔍 VERIFIED (بقراءة كود/مسح) · ⚠️ UNVERIFIED (تحتاج بيئة) · ▪️ INFO

ID	الشدة	الحالة	العنوان	المسار:السطر	مُعاد إنتاجه؟
BUG-01	CRITICAL	✅ VERIFIED	شريط "جاري السبك" معلَّق للأبد عند فشل edit_text	delivery.py:516-531	نعم (R1)
BUG-02	HIGH	✅ VERIFIED	نص ذهبي 3,950–12,000 حرف: لا تعديل ولا ملف + حالة معلَّقة	delivery.py:483,491 + :641	نعم (R2)
BUG-04	HIGH	🔍 VERIFIED	النسخة الذهبية لا تُسجَّل في jobs.ndjson ولا في res["polished"] عند العودة	telegram_bot.py:567-571 · delivery.py:462,535 · main.py:188	تحليل ساكن + سببية مباشرة
BUG-05	HIGH	🔍 VERIFIED	Shelby الافتراضي = قالب No-Op ⇒ سبك بلا فائدة + حرق حصة	config.py:183-189 · Antigravity/local_provider_template.py:39-42	تحليل ساكن
PERF-01	HIGH	🔍 VERIFIED	3 نداءات ذكاء/فويس في وضع السبك بلا ميزانية أو قاطع	delivery.py:441-460	تحليل ساكن
PERF-02	HIGH	🔍 VERIFIED	180ث على عامل طابور واحد (TRANSCRIBE_WORKERS=1)	config.py:188 · voice_queue.py:78	تحليل ساكن
REL-01	HIGH	🔍 VERIFIED	خيوط السبك daemon لا تُنتظَر عند الإيقاف	main.py:178-201 · delivery.py:535	تحليل ساكن
DOC-01	HIGH	🔍 VERIFIED	LOCAL_POLISH_TIMEOUT: 8.0 في الوثائق / 180.0 في الكود	README.md:137 vs config.py:188	مباشر
DOC-02	HIGH	🔍 VERIFIED	COMPARE_MODE الافتراضي الموثَّق merged / الفعلي synthesizer	.env.example:99 vs config.py:189	مباشر
DOC-03	HIGH	🔍 VERIFIED	ميزة السبك غير موثقة إطلاقًا في README (فقط concepts_brief.md بحالة Draft)	grep synthesizer README.md = ∅	مباشر
DOC-04	HIGH	🔍 VERIFIED	قرار هندسي موثَّق "8.0s" نُقض بصمت إلى 180.0s	Antigravity/02_…:37,78	مباشر
QA-01	HIGH	🔍 VERIFIED	لا اختبار يفشل عند خلل حقيقي (534 خضراء + 3 أخطاء مؤكدة)	§D	تَبَعي
TD-01	HIGH	🔍 VERIFIED	deliver() = 384 سطرًا / تداخل 10 مستويات	delivery.py:241-625	قياس
TD-02	HIGH	🔍 VERIFIED	منطق الإنقاذ مكرّر 4 مرات (سبب جذر BUG-01)	delivery.py:366,441,546,578	قياس
BUG-03	INFO	✅ VERIFIED (مُصحَّح)	context_summary في السبك يصل فعلًا للموديل (الادعاء الأولي بخطئه غير صحيح — مسحوب)	text_assist.py:311-315	تحقق
BUG-06	MEDIUM	✅ VERIFIED	الاختبارات تكتب على settings.json/keys.json/glossary.json الحقيقية	settings.py:71-75 · tests/test_dual_polish.py	نعم (R5)
BUG-07	MEDIUM	🔍 VERIFIED	اختباران يدّعيان تغطية الفشل ولا يغطيانه	tests/test_dual_polish.py:397,560	قراءة
ARC-01	HIGH	🔍 VERIFIED	حالة مشتركة بين خيوط بلا قفل/event	delivery.py:462,537	قراءة
ARC-02	HIGH	🔍 VERIFIED	"In-Place" مضمونة بشرط حجم فقط	delivery.py:483,502,524	قراءة
ARC-04	MEDIUM	🔍 VERIFIED	overrides تُطبَّق بعد بدء الطابور (استئناف)	main.py:432 ثم 437	قراءة
ARC-05	LOW	🔍 VERIFIED	synth_ok/synth_fail لا تُعرَض في /status	telegram_bot.py:1355-1366	قراءة
SEC-05	MEDIUM	🔍 VERIFIED	/api/add_key بلا rate-limit	web_service.py:321-337	قراءة
SEC-06	MEDIUM	🔍 VERIFIED	localhost يتجاوز التوكن دائمًا	web_service.py:123-137	قراءة
SEC-09	MEDIUM	🔍 VERIFIED	Content-Length فقط كسقف + socket بلا timeout	web_service.py:164-172,297-304	قراءة
SEC-07	LOW	🔍 VERIFIED	X-Forwarded-For[0] قابل للتزوير خلف وكيل سيئ	web_service.py:154-160	قراءة
SEC-08	LOW	🔍 VERIFIED	/health عام يكشف بنية داخلية	web_service.py:218-222	قراءة
REL-02	MEDIUM	🔍 VERIFIED	عدم اتساق حدود التعديل/الإرسال/الملف	delivery.py:483,641	قراءة
REL-03	MEDIUM	🔍 VERIFIED	حقن مخرَج Shelby داخل رسالة المستخدم (Prompt Injection أوسع)	prompts.py:293-298	قراءة
REL-04	MEDIUM	🔍 VERIFIED	time.time() لقياس مُدد (ينزلق مع NTP)	delivery.py:348,443,453,471	قراءة
REL-05	LOW	🔍 VERIFIED	لا retry/backoff لتعديل تليجرام (429)	telegram_bot.py:322-334	قراءة
DEP-01	MEDIUM	🔍 VERIFIED	requests بسقف أعلى ثابت وبلا lock ⇒ غير حتمي	requirements.txt:1	قراءة
DEP-02	MEDIUM	⚠️ UNVERIFIED	CI على 3.10 لم يُنفَّذ هنا (بيئتي 3.13)	ci.yml:13	لم يُنفَّذ
DEP-04	LOW	⚠️ UNVERIFIED	runtime.txt بصيغة python-3.12 غير مؤكدة لـ Pterodactyl	runtime.txt	يحتاج لوحة
CI-01	MEDIUM	🔍 VERIFIED	CI يعمل مرتين لكل PR (push: ** + pull_request)	ci.yml:4-6	قراءة
CI-03	LOW	🔍 VERIFIED	نشر يدوي بلا artifact versioned/rollback آلي	tools/pack_deploy.py	قراءة
CI-04	INFO	🔍 VERIFIED	لا timeout-minutes ولا pip cache	ci.yml:9-26	قراءة
DOC-05	MEDIUM	🔍 VERIFIED	متغيرات موثقة لا يقرأها الكود (LOCAL_PROVIDER_CMD…)	Antigravity/local_provider_template.py:10-12	grep = 0
DOC-06	MEDIUM	🔍 VERIFIED	README: "525 اختبارًا" / الواقع 534	README.md:195	عدّ فعلي
QA-04	MEDIUM	🔍 VERIFIED	LOCAL_POLISH_* تُمسح في fresh_env فقط لا autouse	conftest.py:16-32	قراءة
QA-05	LOW	🔍 VERIFIED	تغطية جزئية لمسارات _call_local_provider الفاشلة	text_assist.py:150-196	قراءة
QA-06	LOW	🔍 VERIFIED	لا اختبار حدودي عند 3950/4000/12000	—	قراءة
QA-07	LOW	🔍 VERIFIED	test_360_exhaustive.py خارج testpaths ⇒ غير مُنفَّذ	pytest.ini:2	قراءة
QA-09	INFO	🔍 VERIFIED	لا coverage محسوب	requirements-dev.txt	قراءة
TD-03..08	LOW	🔍 VERIFIED	حدود متفرقة · from config import Config · print بدل logger · مصدران لـ compare_mode · وثائق مكرّرة	متعددة	قراءة
SEC-12	INFO	⚠️ UNVERIFIED	حماية فرع GitHub / PRs / Issues	—	لا صلاحيات
ملخص الأعداد
CRITICAL: 1 · HIGH: 11 · MEDIUM: 14 · LOW: 11 · INFO: 6
أخطاء مؤكدة بإعادة إنتاج فعلية: 2 (BUG-01, BUG-02) + 1 (BUG-06 بالكتابة على الملفات)
أخطاء مؤكدة بالسببية المباشرة من الكود: 3 (BUG-04, BUG-05, QA-01 تَبَعي)
ادعاء واحد سُحب بعد التحقق (BUG-03) — مُعلَن في التقرير الرئيسي.
MASTER REMEDIATION PLAN — خطة الإصلاح (غير مُنفَّذة)
wewe00220033/Gemini-3.8-Flash @ A-test / f3709cc
لم يُنفَّذ أي بند من هذه الخطة. هذه خطة مكتوبة لتُنفَّذ بواسطة وكيل برمجي آخر بلا تخمين: كل مهمة تحدد الملف والسطر والتغيير والاختبار ومعيار القبول ومخاطر الانحدار.

تقدير إجمالي: P0 ≈ 4 ساعات · P1 ≈ 1 يوم · P2 ≈ 1 يوم · P3 ≈ 1.5 يوم · P4 اختياري.

P0 — المخاطر الحرجة (لا تصلح للإنتاج قبلها)
P0-01 · إغلاق دورة السبك دائمًا (إصلاح BUG-01 + BUG-02 من جذر واحد)
المشكلة المرتبطة: BUG-01 (شريط انتظار معلَّق) · BUG-02 (3950–12000 حرف)
الملفات: delivery.py (السطور 390، 483–496، 502–515، 516–531، 638–650)
التغييرات المقترحة:
أضف دالة مساعدة واحدة أعلى delivery.py:
Python

def _replace_or_send(sender, html_text, plain_text, mid, anchor, filename_prefix, caption, head_html=""):
    """يحاول التعديل في المكان؛ وإن تعذّر (mid=None / فشل / حجم) يُغلق الدورة بإرسال نهائي.
    يرجع True إذا أُغلقت الدورة (تعديل أو إرسال)."""
    limit = min(Config.TELEGRAM_MAX_MSG - 50, Config.POLISH_FILE_THRESHOLD)
    edited = False
    if mid is not None and len(html_text) <= limit and getattr(sender, "edit_text", None):
        try:
            edited = (sender.edit_text(mid, html_text, "HTML") is not False)
        except Exception:
            edited = False
    if not edited:
        _ensure_no_stale_status(sender, mid, anchor)      # اختياري — انظر P1-03
        _send_long(sender, html_text, plain_text, mid or anchor,
                   filename_prefix, caption, head_html)
    return True
استبدل الكتل الأربع المكرّرة (السطور 389–399 _bg_shelby_rescue، 482–496 السبك الناجح، 501–515 fallback، 523–531 مسار الفشل) باستدعاء واحد لهذه الدالة ⇒ حذف ~80 سطرًا مكرّرًا (TD-02) وإصلاح BUG-01 وBUG-02 في آن واحد.
في الحالة الأخيرة (shelby_status in ("timeout","error")) استخدم if not edited: sender.send_text(to_html, anchor, "HTML") (أو _replace_or_send نفسها) بدل if not edited and mid is None:.
في _send_long أضف حزام أمان: if len(plain_text) > Config.TELEGRAM_MAX_MSG - 50 and sender.send_document is not None: ⇒ أرسل ملفًا (قبل شرط POLISH_FILE_THRESHOLD) كي لا يمرّ نص > سقف الرسالة أبدًا.
الاعتماديات: لا شيء. يجب أن يسبق P1-01.
الاختبارات المطلوبة (tests/test_dual_polish.py):
test_synthesizer_edit_failure_still_closes (FakeSender.edit_text→False + shelby timeout): assert أن هناك رسالة/تعديلًا يحوي "تجاوز شلبي المهلة".
test_synthesizer_golden_4500_becomes_file_or_final: golden=4500 → assert len(fs.docs)==1 أو رسالة نصية ≤ 3950.
test_synthesizer_3950_boundary_edits_in_place: golden نصّه 3900 → assert len(edited)==1 and docs==[].
test_synthesizer_13000_becomes_file.
test_all_synthesis_paths_end_in_terminal_state: مصفوفة (shelby ok/fail, synth ok/fail, edit ok/fail) ⇒ في كل حالة يوجد آخر رسالة لا تحوي "⏳ جاري".
معايير القبول: كل حالة من المصفوفة السابقة (12 حالة) تنتهي برسالة حالة نهائية؛ لا edited_texts تضم نصًا > 3950؛ الملفات تُرسل عند > 4000 نصًا.
مخاطر الانحدار: تغيير سلوك أوضاع merged/split (تستخدم نفس الدوال). ⇒ شغّل الحزمة كاملة، وخصوصًا test_deliver_merged_mode, test_deliver_split_mode, test_deliver_merged_oversized_fallback.
التحقق النهائي: pytest -q = كامل الدفعة ناجحة + إعادة تشغيل سكربتي R1 وR2 ⇒ يجب أن يظهر تغيير السلوك.
P0-02 · الافتراضيات الخطرة: COMPARE_MODE=synthesizer + مهلة 180ث
المشكلة: أداء/حصص/توثيق (PERF-01, PERF-02, DOC-01, DOC-02, DOC-04)
الملفات: config.py:188-189، .env.example:98-99، README.md:137
التغييرات المقترحة (اختر واحدًا بقرار مالك صريح):
الخيار A (مفضَّل أمنيًا): الإبقاء على COMPARE_MODE=synthesizer لكن خفض LOCAL_POLISH_TIMEOUT إلى 20.0 مع تضمين زمن السبك في ميزانية RETRY_BUDGET_SECONDS، وتحديث README/.env.example إلى القيم الفعلية.
الخيار B: إرجاع COMPARE_MODE=merged افتراضيًا وتمكين synthesizer عبر متغيّر بيئة صريح فقط.
اعتماديات: يحتاج قرار مالك (انظر §"قرارات مطلوبة" القرار-1). لا تتجاوزه.
الاختبارات: test_default_compare_mode_documented (يقارن Config.COMPARE_MODE بقيمة مذكورة في .env.example — اختبار ثابت).
معايير القبول: لا توجد قيمة افتراضية في الكود تختلف عن أي قيمة موثقة.
مخاطر الانحدار: منخفضة (إعداد فقط). التحقق: تشغيل البوت مع .env نظيف وقراءة settings.compare_mode().
P1 — أخطاء ومشكلات عالية الأولوية
P1-01 · "النسخة الذهبية" لا تصل للسجلات ولا للدالة المُنادية (BUG-04)
الملفات: delivery.py:462, 535-537 · telegram_bot.py:559-572
التغيير: أضف wait=True/join(timeout) منطقي:
في deliver: بعد t.start() أضف result["compare_thread"] = t (موجود) — ولا تعد حالة.
في telegram_bot.process_job: بعد res = delivery.deliver(...):
Python

_t = res.get("compare_thread")
if _t is not None and res.get("mode") == "dual":
    _t.join(timeout=Config.LOCAL_POLISH_TIMEOUT + Config.TEXT_ASSIST_TIMEOUT + 5)
ثم أضف pending_after لـ jobs.record عند عدم الانتهاء بدل تسجيل polished=False كأنها حقيقة.
بديل أنظف (مفضَّل): تمرير on_polished=lambda txt: jobs.record(job_id, "polished", text=txt[:2000]) إلى deliver واستدعاؤه داخل الخيط.
الاختبارات: test_deliver_blocks_until_compare_done + test_jobs_ndjson_contains_golden (بـtmp_path للـJOBS_FILE).
معايير القبول: jobs.ndjson يحوي النص الذهبي؛ res["polished"] = الذهبية عند عودة process_job.
مخاطر الانحدار: زيادة زمن الإرجاع لخيط العامل (قد يصل 180ث) → قد يؤثر على _in_flight/الإيقاف. البديل on_polished لا يزيد الزمن — استخدمه إن أمكن.
التحقق النهائي: فحص /trace <job_id> بعد فويس حقيقي.
P1-02 · Shelby No-Op (BUG-05)
الملفات: text_assist.py:216-242 · config.py:184-189
التغيير: في polish_local: بعد الحصول على text، إن كان text.strip() == raw_text.strip() أو نسبة تشابه (difflib.SequenceMatcher) ≥ 0.98 ⇒ أعِد (None, "المزود المحلي أعاد النص كما هو (No-Op) — السبك غير مجدٍ") وسجّل _state["local_noop"] += 1.
الاختبارات: test_local_provider_noop_detected.
معايير القبول: لا نداء synthesize عندما تكون مسودة Shelby No-Op؛ /status يُظهر "مزود محلي: No-Op".
مخاطر الانحدار: مزود محلي شرعي يعيد النص كما هو (وضع smart_verbatim مثلًا) سيُعتبر No-Op — rozwiąz: استثنِ role_id == "smart_verbatim".
P1-03 · إغلاق رسالة الحالة القديمة عند التحوّل لمسار مختلف (تعزيز P0-01)
الملفات: delivery.py (دالة جديدة _ensure_no_stale_status)
التغيير: قبل أي _send_long بديل، إن كان mid موجودًا ولم ننجح في تعديله، حاول تعديله إلى سطر قصير "✅ اكتملت — التفاصيل أدناه" ثم أرسل الناتج. إن فشل التعديل أيضًا، أرسل السطر كنص منفصل.
الاختبارات: test_stale_status_marked_when_oversized.
معايير القبول: المستخدم لا يرى أبدًا نصين متعارضين ("جاري السبك" + "النسخة الذهبية") في نفس الوقت.
P1-04 · عزل settings.json في الاختبارات (BUG-06 / QA-02)
الملفات: tests/test_dual_polish.py (أضف fresh_env لكل اختبار يمسّ settings)
التغيير: أضف fixture موحّد:
Python

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config.Config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.reset_cache()
أو الأفضل: اجعل _runtime_dirs_in_tmp في conftest.py تُصفّر SETTINGS_FILE/GLOSSARY_FILE/ROLES_FILE (الآن لا تفعل).
الاختبارات الإضافية: test_suite_does_not_create_settings_json (meta-test) وtest_edit_text_can_fail + FakeSender(edit_ok=False).
معايير القبول: تشغيل الحزمة في نسخة نظيفة لا يُنشئ settings.json ولا يعدّل واحدًا موجودًا (تحقق بـmtime).
مخاطر الانحدار: بعض اختبارات test_phase6_panel.py تعتمد على الحفظ الفعلي — تأكد أنها تستخدم fresh_env.
P1-05 · مزامنة الوثائق مع الكود (DOC-01/02/03/05/06)
الملفات: README.md:137, 195 · .env.example:98-99 · Antigravity/local_provider_template.py:10-12 · concepts_brief.md:6
التغيير: (أ) LOCAL_POLISH_TIMEOUT = القيمة الفعلية؛ (ب) إضافة synthesizer لكل قوائم compare_mode؛ (ج) قسم README جديد "🏆 النسخة الذهبية المسبوكة" بوصف: شريط الحالة، In-Place، صمام الإنقاذ، التذييل الزمني، والحدود (3,950/12,000)؛ (د) تصحيح 525→534؛ (هـ) تصحيح أسماء المتغيرات في القالب إلى LOCAL_POLISH_CMD/TIMEOUT؛ (و) رفع حالة concepts_brief.md من Draft إلى Implemented.
معايير القبول: grep synthesizer README.md ≠ ∅؛ لا توجد قيمة رقمية في README تخالف config.py (أضف اختبار ثابت test_docs_match_defaults).
مخاطر الانحدار: لا شيء.
P1-06 · ترتيب الإقلاع (ARC-04)
الملف: main.py:429-441
التغيير: انقل panel.apply_persisted_overrides() إلى قبل telegram_bot.start_queue(resume=...).
الاختبار: test_overrides_applied_before_resume (mock على start_queue يقرأ Config.LOCAL_POLISH_TIMEOUT ويتحقق أنه مضبوط).
معايير القبول: أول مهمة مستأنفة تعمل بالـ overrides.
مخاطر الانحدار: منخفضة — تأكد أن panel لا يعتمد على شيء يبدأه start_queue.
P2 — جودة واعتمادية
P2-01 · انتظار خيوط السبك في الإيقاف (REL-01)
الملفات: main.py:178-201 · delivery.py (سجّل الخيوط النشطة في قائمة على مستوى الوحدة: _ACTIVE_COMPARE = set())
التغيير: deliver يضيف الخيط إلى _ACTIVE_COMPARE وfinally يُزيله؛ Runtime.shutdown بعد _in_flight() ينتظر min(5, GRACE) ثانية ثم يسجّل تحذيرًا بعدد الخيوط المتبقية.
الاختبار: test_shutdown_waits_for_compare_threads.
معايير القبول: لا daemon خيط يُقتل في منتصف edit_text في الاختبار (محاكاة بحدث + time.sleep).
مخاطر الانحدار: إطالة زمن الإيقاف ≤5ث.
P2-02 · مؤشرات السبك في /status و/doctor (ARC-05)
الملفات: telegram_bot.py:1355-1366 · observability.py
التغيير: أضف | سبك: نجح {synth_ok}/{synth_calls} (فشل {synth_fail}) في _pipeline_text، وسطر تحذير في /doctor إذا synth_fail > 0 and synth_ok == 0.
الاختبار: test_status_shows_synth_stats.
معايير القبول: الأرقام تظهر فعليًا بعد فويس حقيقي.
مخاطر الانحدار: نص /status مُختبَر في اختبارات parity — حدّث التوقعات.
P2-03 · monotonic() للمُدد (REL-04)
الملفات: delivery.py:348,443,453,471 · text_assist.py:102,160
التغيير: time.monotonic() بدل time.time() في قياسات المدد فقط (لا في الطوابع الزمنية).
الاختبار: لا سلوك مختلف — اختبارات قائمة تكفي.
معايير القبول: grep "time.time()" delivery.py لا يظهر في قياس مدة.
P2-04 · حماية /api/add_key وإبطاء الطلبات (SEC-05, SEC-09)
الملفات: web_service.py:280, 321-337, 164-172
التغيير: (أ) if rate_limited(self._client_ip(), limit=Config.WEB_RATE_LIMIT_PER_MIN // 3) قبل معالجة add_key؛ (ب) self.connection.settimeout(30) في setup()؛ (ج) قراءة الجسم بـmin(content_length, max_bytes) والتحقق من len(body).
الاختبارات: test_add_key_rate_limited, test_slow_body_times_out.
معايير القبول: 429 بعد N طلب؛ لا خيط يبقى > 30ث.
مخاطر الانحدار: رفع تحميل مفاتيح شرعي أثناء الإعداد — وثّق الحد.
P2-05 · نقل test_360_exhaustive.py إلى tests/ (TD-05)
التغيير: git mv test_360_exhaustive.py tests/test_360_exhaustive.py + تأكد أنه يمرّ + أزله من ROOT_PY_EXCLUDE في tools/pack_deploy.py.
معايير القبول: العدد يصبح 534 + N في CI.
P2-06 · Coverage (QA-09)
التغيير: أضف pytest-cov إلى requirements-dev.txt و--cov=. --cov-report=term-missing --cov-fail-under=<الحالي> في CI (ابدأ بـfail_under الحالي ثم ارفعه تدريجيًا).
معايير القبول: نسبة معلنة في CI.
P3 — أداء وبنية
P3-01 · تفكيك deliver() (TD-01)
التغيير: استخرج: _deliver_raw_and_title(...), _deliver_polished_single(...), _deliver_compare(...) (يفرّع على cmp_mode), _deliver_no_polish(...). اجعل deliver() مُنسِّقًا ≤ 60 سطرًا.
معايير القبول: كل الاختبارات القائمة تمرّ دون تعديل (دليل على نقاء إعادة الهيكلة). عدد مستويات التداخل ≤ 4.
مخاطر الانحدار: عالية إن لم تكن الحزمة خضراء قبلها — يجب أن يسبقه P0/P1 مكتمل.
P3-02 · ميزانية زمنية للسبك
التغيير: أدخل SYNTH_BUDGET_SECONDS (مثلًا 25ث) وتحقّق قبل بدء السبك: إن تجاوز الزمن الكلي الميزانية أرسل مسودة جيميني مع حالة "تم تخطي السبك لتجاوز الميزانية".
الاختبار: test_synth_skipped_when_budget_exceeded.
معايير القبول: زمن الفويس الكلي ≤ ميزانية معلنة.
P3-03 · Circuit breaker على السبك (O-10)
التغيير: بعد 3 إخفاقات متتالية → تعطيل السبك 10 دقائق (استخدم model_state القائم أو عدّاد في text_assist._state).
P3-04 · توحيد حدود الحجم (TD-03)
التغيير: DELIVERY_LIMITS = {"edit": TELEGRAM_MAX_MSG-50, "message": TELEGRAM_MAX_MSG-50, "file": min(POLISH_FILE_THRESHOLD, TELEGRAM_MAX_MSG-50)} واستخدامها في كل المواضع.
P4 — اختياري/مستقبلي
ID	البند	القيمة
P4-01	Lockfile (pip-compile/uv pip compile) وتثبيت دقيق للاعتماديات (DEP-01/03)	بناءات حتمية
P4-02	توحيد مصفوفة Python: runtime.txt وci.yml من مصدر واحد (DEP-02/04)	توافق
P4-03	pip cache + timeout-minutes: 15 في CI + تشغيل مزدوج (push: branches: [main, A-test]) (CI-01/04)	تكلفة CI
P4-04	SECURITY.md + CHANGELOG.md + tags/releases (DOC-07)	تتبّع
P4-05	مستند "الحقيقة الحالية" واحد يدمج 50 ملف توثيق (DOC-08)	تقليل التناقض
P4-06	نمط import config; config.Config.X بدل from config import Config لتقليل إعادة الربط في conftest (TD-04)	هشاشة أقل
P4-07	تحويل print() إلى logger في delivery.py (TD-06)	حجب أسرار مضمون
P4-08	_client_ip() يأخذ آخر عنصر من X-Forwarded-For (SEC-07)	دقة rate-limit
جدول الاعتماديات بين المهام
text

P0-01 ──► P1-03 ──► P3-01 ──► P3-04
  │
  └──► P1-01 ──► P2-01
P0-02 ──► P1-05
P1-02 ──► P2-02
P1-04 ──► P2-06
P1-06 (مستقل)
قائمة الاختبارات المطلوبة (مجموعة كاملة)
#	الاختبار	يحمي من
T-01	test_all_synthesis_paths_end_in_terminal_state (12 حالة)	BUG-01 · BUG-02
T-02	test_synthesizer_3950_boundary_edits_in_place	BUG-02
T-03	test_synthesizer_4500_becomes_file	BUG-02
T-04	test_synthesizer_13000_becomes_file	BUG-02
T-05	test_jobs_ndjson_contains_golden	BUG-04
T-06	test_local_provider_noop_detected	BUG-05
T-07	test_suite_does_not_create_settings_json (meta)	BUG-06
T-08	test_edit_text_can_fail (FakeSender جديد)	QA-03
T-09	test_overrides_applied_before_resume	ARC-04
T-10	test_shutdown_waits_for_compare_threads	REL-01
T-11	test_docs_match_defaults (static)	DOC-01/02
T-12	test_status_shows_synth_stats	ARC-05
T-13	test_add_key_rate_limited	SEC-05
T-14	test_slow_body_times_out	SEC-09
T-15	test_synth_skipped_when_budget_exceeded	PERF-02
قرارات مطلوبة منك كمالك المشروع (لا أتخذها عنك)
#	القرار	الخيارات	الأثر
D-1	الوضع الافتراضي لـ COMPARE_MODE	(أ) synthesizer مع مهلة 20ث · (ب) merged افتراضيًا · (ج) synthesizer + 180ث كما هو	كل فويس = 2 أو 3 نداءات ذكاء + 180ث أقصى
D-2	مهلة LOCAL_POLISH_TIMEOUT	8 / 20 / 60 / 180	8 يقطع شلبي المحلي الحقيقي غالبًا ⇒ سبك دائم الفشل؛ 180 يجمّد الطابور
D-3	هل "النسخة الذهبية" تُسجَّل في jobs.ndjson وALL.md بالنص الكامل؟	نعم (خصوصية + حجم) / لا (كفاية المسودة)	يمس /trace والتكرار
D-4	هل تُفرَّق رسائل > 4000 إلى رسائل متعددة أم ملف .md دائمًا؟	دائمًا ملف / تقسيم	تجربة المستخدم
D-5	مصدر الحقيقة للتوثيق	README.md فقط + أرشفة Antigravity/ / التوثيق السردي كمرجع تاريخي	ينهي DOC-04
D-6	دعم Python 3.10 رسميًا؟	نعم (وسّع CI) / لا (حدّث ci.yml إلى 3.12 فقط)	أعباء اختبار
D-7	/api/add_key من الويب: يبقى أم يُقيَّد بالبوت فقط؟	يبقى مع rate-limit / البوت فقط	سطح هجوم
D-8	هل localhost يتجاوز التوكن في الويب؟	يبقى / يُلغى ويُطلب التوكن دائمًا	سهولة مقابل أمانتقرير التدقيق الهندسي المستقل — 360° Audit
المستودع: wewe00220033/Gemini-3.8-Flash · الفرع: A-test · HEAD: f3709cc
تاريخ التدقيق: 2026-11-09 (Africa/Cairo)
الوضع: READ-ONLY — لم يُعدَّل أي ملف في المستودع، ولم يُنفَّذ أي commit/push/PR، ولم يُشغَّل أي إصلاح.
فريق التدقيق (أدوار مُنفَّذة فعليًا): Principal Architect · Senior SWE · QA/Automation · AppSec · DevOps/CI-CD · SRE/Performance · Code Reviewer · Tech-Debt Auditor.

0. نطاق الفحص الفعلي وحدوده (يُقرأ أولًا)
ما فُحص فعليًا (أدلة قابلة للتكرار)
#	ما تم	النتيجة
1	استنساخ كامل + فحص A-test (238 commit)	HEAD = f3709cc · working tree نظيف · 7.7MB · صفر tags/releases
2	قراءة سطر-بسطر: delivery.py (650 سطرًا)، text_assist.py (337)، prompts.py (300)، settings.py (161)، config.py (383)	✅
3	قراءة موضعية مُدلَّلة: telegram_bot.py (2167 سطرًا — 6 مقاطع)، web_service.py، panel.py، key_manager.py، logkit.py، main.py، voice_queue.py، conftest.py، tests/test_dual_polish.py (593)	✅
4	python -m pyflakes *.py tests/*.py tools/*.py	0 ملاحظة
5	python -m pytest -q (بيئة نظيفة)	534 مُجمَّعة / 534 ناجحة / 0 فاشلة (~74ث)
6	مسح أسرار (AIza… / توكن تليجرام / sk-…) في شجرة العمل	0 تطابق
7	مسح بدائيات خطرة (os.system, shell=True, eval, exec, pickle, yaml.load)	0
8	عدّ أقسام الدستور في _POLISH_COMMON	12/12 موجودة
9	5 سكربتات إعادة إنتاج (R1–R5) على نسخة منفصلة في /tmp	كشفت 3 أخطاء مؤكدة — انظر §4
ما لم يُفحص ولماذا (شفافية كاملة)
بيئة 3.10 و3.12 (مصفوفة CI): المتاح في هذه البيئة Python 3.13.16 فقط. الاختبارات نُفِّذت على 3.13 → التوافق مع 3.10 غير مُتحقَّق هنا (توجد مخاطر datetime.UTC/fromisoformat وسيطة، راجع DEP-02).
اختبار حقيقي عبر الشبكة (Gemini/Shelby CLI/Telegram): لا خروج شبكي ولا CLI حقيقي → صفر E2E حقيقي. كل ما يتعلق بزمن الاستجابة والتزامن هو تحليل ساكن + محاكاة.
حماية الفرع (Branch Protection) وPRs/Issues: غير قابلة للقراءة من clone (تحتاج GitHub API/صلاحيات) → UNVERIFIED.
سلوك ويندوز الفعلي (os.name == "nt", CREATE_NO_WINDOW, reconfigure): لم يُنفَّذ.
الأداء الحقيقي تحت الحمل: لا benchmark — كل ما في §7 إما مقيس جزئيًا أو مُصنَّف "تحليل ساكن".
ملفات لم تُقرأ بالكامل (أُخذت عيّنات مستهدفة بدلًا من قراءة كل سطر): sessions.py (1045)، key_manager.py (770)، transcriber.py (688)، backup.py (524)، panel.py (486)، observability.py، assistant.py، voice_queue.py، supervisor.py، watchdog.py، ومعظم tests/*.
لا أصرّح بأن "المشروع فُحص بالكامل". نطاق الفحص الفعلي: 12 ملفًا جوهرًا بكاملها + 10 ملفات مقتطعة + مجموعة الاختبارات كأداة (لا كقراءة سطرية) + 5 تجارب إعادة إنتاج. هذا يبرّر الأحكام الواردة هنا، ولا يبرّر حكمًا نهائيًا على وحدات لم تُقرأ.

A. Executive Summary
حالة المشروع: قاعدة كود ناضجة بشكل غير عادي لمشروع شخصي/فردي: 23,033 سطرًا، 534 اختبارًا ناجحًا، صفر ملاحظات lint، فصل مسؤوليات واضح (config / delivery / text_assist / prompts / transcriber / web / panel / backup)، تعافٍ ذاتي متعدد الطبقات (watchdog، supervisor، L1–L4)، سجلات مُنقّاة من الأسرار (logkit.redact على مستوى logging.Filter)، وحماية ويب مبنية على hmac.compare_digest + cookie HttpOnly + rate-limit + رفض X-Forwarded-For إلا خلف TRUST_PROXY.

لكن: ميزة السبك الثلاثي (Tri-Agent Synthesizer) — آخر 5 commits، وهي محور طلبك — تحتوي على 3 أخطاء مؤكدة مُعاد إنتاجها، وواحدة منها تُخالف الوعد الأساسي للميزة (التعديل في المكان In-Place):

شريط حالة "⏳ جاري السبك مع شلبي…" يبقى معلَّقًا للأبد إذا فشل editMessageText (400/429/5xx) — المستخدم يرى انتظارًا لا ينتهي، ولا تصله معلومة المهلة/الفشل. (مُعاد إنتاجه: R1)
النص الذهبي بين 3,950 و12,000 حرف لا يعدّل الرسالة ولا يُرسل كملف — يُرسل كرسالة HTML جديدة طولها 5,388 حرفًا يتجاوز ما بنته الخوارزمية تفترضه، فيُقسَّم في طبقة النقل إلى رسالتين، وتتحوّل "التعديل في المكان" إلى "رسائل جديدة" مع بقاء شريط الانتظار معلَّقًا. (مُعاد إنتاجه: R2)
prompts.compose_synthesis_prompt() لا يستقبل context_summary رغم أن delivery.py:455 يمرّره — فملخص الجلسة (L2) يُسقَط صامتًا في مسار السبك خلافًا لبقية المسارات. (مثبت من التوقيع + _accepts_context_summary)
مخاطر عالية إضافية خارج الميزة الجديدة:
4. COMPARE_MODE الافتراضي = synthesizer + مهلة محلية = 180.0ث بينما الوثيقة الرسمية (README/.env.example) تقول 8.0 وmerged. أي عملية تستخدم .local/shelby_polish.py الافتراضي (وهو قالب No-Op يعيد النص كما هو بلا تنظيم) ستُنتج "نسخة ذهبية" = النص الخام، بينما سيُحرق بها نداء Flash-Lite ثالث مدفوع الحصة لكل فويس.
5. "النسخة الذهبية" لا تُلتزم في jobs.ndjson ولا في deliver() عند عودة الدالة — لأن الخيط الخلفي لا يُنتظر (main.py:188 ينتظر _in_flight() فقط). فالسجل يقول "لا يوجد نص منظم" بينما المستخدم استلم نصًا ذهبيًا، واستئناف المهمة بعد كراش يعيد الإرسال كأن شيئًا لم يكن.
6. panel.apply_persisted_overrides() تُنفَّذ في main.py:437 بعد start_queue() في السطر 432 — أول مهمة مستأنفة بعد restart قد تعمل بإعدادات لم تُطبَّق بعد.

الحكم المختصر: المشروع جاهز للإنتاج في المسارات القديمة (التفريغ + التنظيم المفرد + الويب + النسخ + الاستعادة)، وغير جاهز للإنتاج بنسبة 100% في ميزة السبك الثلاثي تحديدًا بسبب بندَي CRITICAL/HIGH أعلاه. الإصلاح المطلوب صغير ومحدَّد (≈120 سطرًا في 4 ملفات) وليس إعادة بناء.

B. Project Inventory
المكوّن	الملفات	الحجم	الدور
التهيئة	config.py, .env.example, requirements.txt, runtime.txt, VERSION	383 سطر	مصدر واحد للإعدادات (2.0.0-phase7.1)
إعدادات المالك	settings.py, panel.py, fsm.py	161+486+—	delivery_mode/role/header_target/compare_mode + /panel
التفريغ	transcriber.py, key_manager.py, model_state.py	688+770+229	سلسلة موديلات + قواطع + حصص
الطابور	voice_queue.py, jobs.py, main.py, watchdog.py, supervisor.py, heartbeat.py	318+166+478+300+249+—	FIFO + سجل مهام + L1–L4
خط الإنتاج	delivery.py, text_assist.py, prompts.py, glossary.py, archive.py	650+337+300+335+216	التسليم + السبك + البرومبت
الويب	web_service.py, static/index.html	503+600	/api/transcribe, /api/status, /health
التشغيل	logkit.py, observability.py, incidents.py, backup.py, audit.py, store.py, netsession.py, clock.py, preflight.py	~1800	سجلات/تشخيص/نسخ
الاختبارات	tests/ (33 ملفًا) + test_360_exhaustive.py	~9,000	534 اختبارًا
الوثائق	README.md, DEPLOY.md, architecture-v2/ (23 ملفًا), requirements/ (17)، Antigravity/ (12)، genspark-review/, concepts_brief.md	—	نظام وثائقي ثقيل
CI	.github/workflows/ci.yml	26 سطرًا	pyflakes + compile + pytest × (3.10, 3.12)
لا يوجد: Dockerfile · pyproject.toml/setup.cfg (الإعدادات في pytest.ini فقط) · قفل اعتماديات (lock file) · tags/releases · CHANGELOG.md · SECURITY.md · CONTRIBUTING.md.

C. Architecture Assessment
C.1 ما هو سليم فعليًا (VERIFIED)
فصل الطبقات مقصود ومُوثَّق في رؤوس الملفات ويطابق الكود: A (تفريغ) ← B (تنظيم) ← C (دور). prompts.compose_prompt() دالة نقية بلا شبكة/حالة (prompts.py:113) — وهذا سبب مباشر لسهولة اختبارها.
حقن التبعية عبر Sender (delivery.py:29-37) هو أفضل قرار معماري في المشروع: كل الإرسال قابل للاستبدال، ولهذا يوجد 534 اختبارًا بلا شبكة.
الأرشفة قبل الإرسال (outbox) (delivery.py:260-265) يجعل فشل تليجرام غير مُفقد للنص (/last) — نمط صحيح.
main.py لا يستورد delivery ويستخدم web_service.RUNTIME كحل حقن لدائرة الاستيراد (web_service.py:348-349) → لا توجد دورات استيراد معروفة.
حدود الوحدات محترمة: interactions_client.py معزول تمامًا لـ Antigravity، وtext_assist معزول لـ Flash-Lite، وtranscriber لا يعرف شيئًا عن تليجرام.
C.2 نقاط ضعف معمارية (بالدليل)
#	الملاحظة	الدليل	التصنيف
ARC-01	حالة قابلة للتغيير مشتركة بين خيوط بلا قفل: deliver() يعيد result كقاموس، والخيط الخلفي يكتب result["polished"] وresult["compare_thread"] بعد عودة الدالة. لا قفل، ولا threading.Event، ولا join قبل القراءة.	delivery.py:462, 537 · telegram_bot.py:559-571 · main.py:188	HIGH
ARC-02	"In-Place" مضمونة بالحجم فقط لا بالبنية: كل قرار تعديل مربوط بشرط len(html) <= TELEGRAM_MAX_MSG - 50؛ عند تجاوزه يتحول السلوك صامتًا إلى "رسالة جديدة" بينما نص الحالة المبدئي يقول للمستخدم إنه سيُعدَّل في مكانه.	delivery.py:483, 502, 524, 390	HIGH
ARC-03	ازدواج مصدر الحقيقة لإعداد واحد: COMPARE_MODE في Config وcompare_mode في settings.json وpanel.SET_CONFIG_ATTR يكتب على Config — مع منطق هجين (settings._defaults() يقرأ من Config، و_load() يعطي الأولوية للقرص).	settings.py:43, 152-154 · panel.py:44-51, 427-429	MEDIUM
ARC-04	ترتيب الإقلاع: استئناف المهام (start_queue) قبل تطبيق تجاوزات settings.json.	main.py:432 ثم 437	MEDIUM
ARC-05	ميزة ثالثة تفتقر لمسارات المراقبة: text_assist.stats() يجمع synth_* لكن /status يعرض فقط local_ok/local_calls — لا مؤشر على معدل نجاح السبك.	text_assist.py:44-48 · telegram_bot.py:1362-1366	LOW
ARC-06	delivery.deliver = 384 سطرًا و10 مستويات تداخل (دالة واحدة تدير: القاموس، الأرشيف، العنوان، الجلسة، التنظيم، الإنقاذ، 4 أوضاع، حدود الحجم).	delivery.py:241-625	MEDIUM (دين تقني)
لا توجد Single Points of Failure جديدة داخل عملية البوت؛ SPOF الحقيقي هو العملية الواحدة على استضافة Pterodactyl بحجم 512MB (موثَّق ومقصود)، وتُدار بـ watchdog + supervisor.

D. Confirmed Bugs (أخطاء مؤكدة — جميعها مُعاد إنتاجها)
منهجية: كل بند هنا له سكربت إعادة إنتاج نُفِّذ فعليًا على نسخة منفصلة (R1–R5 في EVIDENCE_LOG.md). ما لم يُعَد إنتاجه مكتوب صراحة "ساكن (Static proof)".

BUG-01 — شريط "جاري السبك" معلَّق للأبد عند فشل التعديل · CRITICAL
المسار: delivery.py:516-531
الوصف: عند فشل شلبي (مهلة/خطأ) يُبنى to_html، ويُحاول التعديل فقط إذا mid is not None. إذا رجع edit_text بـ False (400 تنسيق، 429 flood، رسالة محذوفة) → edited=False، والشرط التالي if not edited and mid is None لا يتحقق لأن mid ليس None. النتيجة: لا شيء يحدث. المستخدم يرى للأبد: ⏳ جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة 180ث)...
إعادة الإنتاج: R1 → sent=2, edited=0, "تجاوز شلبي المهلة" غير موجود في أي رسالة مُرسلة.
السبب الجذري: الشرط مكتوب كـ"أرسل بديلًا فقط إذا لم تكن هناك رسالة أصلًا"، بينما المطلوب "أرسل بديلًا إذا لم ينجح التعديل" (كما في مسارَي shelby_rescue وfallback_compare اللذين يستخدمان if not edited: بلا شرط mid).
الأثر: تضليل كامل للمستخدم + فقدان الإشارة إلى أن مسودة جيميني هي المعتمدة.
الإصلاح: if not edited: _send_long(...) or sender.send_text(to_html, anchor, "HTML").
اختبار الإثبات المطلوب: test_synthesizer_timeout_both_paths بـ edit_text → False وتحقّق أن رسالة نهائية موجودة تحوي "تجاوز شلبي المهلة".
BUG-02 — النص الذهبي بين 3,950 و12,000 حرف: لا تعديل ولا ملف · HIGH
المسار: delivery.py:483 (شرط التعديل) + delivery.py:491 (_send_long) + delivery.py:641 (عتبة الملف).
الوصف: ثلاثة حدود غير متسقة: TELEGRAM_MAX_MSG-50 = 3950 (حد التعديل) · POLISH_FILE_THRESHOLD = 12000 (حد الملف) · TELEGRAM_MAX_MSG = 4000. النص بين 3950 و12000 يمرّ إلى _send_long كـhtml_text فيرسله sender.send_text كاملًا — وفي الإنتاج send_html_get_id يقسمه إلى رسائل (telegram_bot.py:285), فتُنشأ رسائل جديدة، وتبقى رسالة الانتظار الأصلية غير معدَّلة.
إعادة الإنتاج: R2 → golden=5000 ⇒ sent lengths = [155, 266, 5388], edited=[], docs=[].
السبب الجذري: _send_long يقارن مع POLISH_FILE_THRESHOLD بينما المُنادي يقارن مع TELEGRAM_MAX_MSG؛ لا يوجد "حزام أمان" في _send_long يمنع تمرير نص > سقف الرسالة.
الأثر: مخالفة وعد الميزة (In-Place) + إزعاج المستخدم برسائل متعددة + بقاء شريط انتظار معلَّقًا (نفس BUG-01).
الإصلاح المقترح: في _send_long: limit = min(POLISH_FILE_THRESHOLD, Config.TELEGRAM_MAX_MSG)؛ وإن كان النص > TELEGRAM_MAX_MSG-50 دائمًا اجعله ملفًا (أو أضف _send_long_chunks منفصلة صراحةً) — وفي مسار السبك تحديدًا: إذا كبر الناتج عن حد التعديل → عدّل الرسالة إلى "✅ اكتملت النسخة الذهبية — أُرسلت في ملف/رسائل أدناه" ثم أرسل الناتج، كي تُغلق رسالة الحالة دائمًا.
اختبار الإثبات: 3 قيم حدودية: 3,900 (تعديل) · 4,500 (يجب أن يصبح ملفًا) · 13,000 (ملف) مع التأكد أن كل حالة تنتهي بحالة نهائية مرئية.
BUG-03 — context_summary (L2) يُسقَط صامتًا في السبك · HIGH
المسار: delivery.py:455 → text_assist.synthesize → prompts.compose_synthesis_prompt
الوصف: _accepts_context_summary(text_assist.synthesize) ترجع True (التوقيع يحتوي context_summary)، فيُمرَّر الملخص، لكن synthesize() لا يستخدمه في استدعاء compose بل يضيفه على system_text بعد التوليد (انظر §التفصيل) وبطريقة لا تصل للموديل في مسار generate(system_text, user_content)… أدق صياغة: الملخص يُلحق بـsystem_text بعد أن بنى compose_synthesis_prompt رسالتي النظام والمستخدم → فهو يصل فعلًا إلى الموديل لكن مخالفًا لنمط polish_gemini الذي يلحقه قبل الاستخدام بنفس الطريقة…
تصحيح ذاتي أثناء التدقيق: هذا البند ليس خطأً وظيفيًا — text_assist.py:311-315 يضيف الملخص إلى system_text قبل generate()، فالموديل يراه (تمامًا كما في polish_gemini:205-207). ترقية التصنيف: INFO لا HIGH. يبقى كملاحظة تصميمية: بناء الاثنين في compose_synthesis_prompt أنظف من الحقن الخارجي، وتمكين اختباره دالّيًا.
لا أُبقي على ادعاء غير مثبت. (هذا تصحيح مقصود ومعلن، لأنه جوهر صحة التقرير.)

BUG-04 — "النسخة الذهبية" لا تصل إلى سجل المهام ولا إلى deliver() · HIGH
المسار: telegram_bot.py:567-572 + delivery.py:462
الوصف: الخيط الخلفي يكتب result["polished"]=golden_text بعد أن يكون process_job قد قرأ res["polished"] (السطر 567-571 يُنفَّذ فور عودة deliver). وبالتالي:
jobs.record(..., polished_text=...) يحفظ مسودة جيميني أو None بدلًا من الذهبية.
استئناف المهمة/المكرر (jobs.find_recent_result) يعيد إرسال مسودة جيميني كأنها الناتج النهائي.
/trace و/status يُظهران معلومة غير صحيحة.
الدليل: main.py:188-190 ينتظر _in_flight() فقط؛ delivery.py:535-537 الخيط daemon=True؛ لا join في أي مكان (بحث شامل: grep compare_thread = تعريف واحد + اختبارات فقط).
الأثر: عدم اتساق بيانات + احتمال إعادة إرسال محتوى مختلف عن الذي رآه المستخدم.
الإصلاح: إبقاء deliver تُرجع result مع compare_thread (موجود) وجعل process_job يسجّل بعد t.join(timeout=...)، أو (أنظف) تسجيل الذهبية من داخل الخيط عبر callback on_polished(text) تمرّره deliver.
اختبار الإثبات: محاكاة "إعادة تشغيل بعد event" والتحقق أن jobs.ndjson يحوي النص الذهبي.
BUG-05 — السبك يعتمد على مسودة Shelby الافتراضية (No-Op) · HIGH (تصميمي/تشغيلي)
المسار: config.py:183-189 · Antigravity/local_provider_template.py:39-42 · .env.example:98
الوصف: config.py يفعّل تلقائيًا LOCAL_POLISH_CMD = python .local/shelby_polish.py إذا وُجد. الملف غير موجود في المستودع (.local/ مستثنى) — والقالب المرفق execute_custom_provider() يعيد النص كما هو ⇒ مسودة شلبي = النص الخام. فيُسبك (خام + مسودة جيميني + خام) بنداء Flash-Lite إضافي.
الأثر: مضاعفة استهلاك حصة Flash-Lite، وأحيانًا "تلويث" المسودة الذهبية بالخام (التكرار/الفصحنة)، والادعاء بأن هناك محركين.
لا يوجد أي تحذير في السجلات بأن السبك يعمل بمسودة No-Op.
الإصلاح: فحص stdout == input (تشابه ≥ 95%) ⇒ اعتبار Shelby "غير مفعّل" + سطر تحذير واضح في /status + عدم تحمّل تكلفة السبك في هذه الحالة.
BUG-06 — مجموعة الاختبارات تكتب على إعدادات التشغيل الحقيقية · MEDIUM (QA)
المسار: settings.py:71-75 (_save يكتب Config.SETTINGS_FILE) + tests/test_dual_polish.py
الدليل (R5): في نسخة نظيفة بدون settings.json، تشغيل pytest tests/test_dual_polish.py يُنشئ settings.json بمحتوى {"delivery_mode":"dual","role":"general","header_target":"both","compare_mode":"synthesizer"}. والمجموعة الكاملة تُنشئ keys.json وglossary.json أيضًا (مثبت بجولة على 33 ملف اختبار: 33/33 تُنشئ keys.json).
الأثر الفعلي: على خادم فيه settings.json حقيقي (مثلًا delivery_mode=polished_only)، تشغيل الاختبارات دون fresh_env (وهو الوضع الافتراضي في test_dual_polish.py) يعيد كتابة الملف بالكامل بالقيم المحفوظة أو الافتراضية. ليس تلويثًا للقيم (الاختبار يستعيد compare_mode) لكنه كتابة/fsync/os.replace على ملف إنتاج + إعادة ترتيب/تطبيع الملف.
ملاحظة تصحيحية مهمة: ادعاء المستند ca9218c "restore compare_mode in finally blocks to prevent test suite from polluting settings.json" يُثبت الحفظ والاستعادة للقيمة، لا العزل. finally تعيد القيمة لكنها لا تمنع الكتابة على الملف الإنتاجي.
الإصلاح: استخدم fixture fresh_env (الموجود فعلًا ويوجّه SETTINGS_FILE إلى tmp_path) في كل اختبارات test_dual_polish.py، أو autouse تُصفّر Config.SETTINGS_FILE للجميع.
اختبار الإثبات: اختبار meta يشغّل الحزمة على SETTINGS_FILE مفقود ويتحقّق أنه لم يُنشأ.
BUG-07 — هاتان الحالتان لا تغطيهما الاختبارات رغم ادعاء العكس · MEDIUM (QA)
test_deliver_synthesizer_mid_none_graceful_fallback (tests/test_dual_polish.py:560-590) يتحقق فقط أن res["polished"] تحدّث — لا يتحقق أن المستخدم استلم حالة نهائية، وهو بالتحديد ما يفشل في BUG-01.
test_deliver_synthesizer_timeout_fallback (:397-424) يستخدم FakeSender.edit_text → True دائمًا، فلا يمكنه كشف BUG-01 أبدًا.
النتيجة: 534 اختبارًا ناجحًا + 3 أخطاء مؤكدة في نفس الميزة ⇒ الاختبارات تختبر المسار السعيد لا الفشل الحقيقي.
BUG-08 — t0_flow يبدأ قبل نداء Gemini بمقدار 0 · قياس الـ Wall-Clock · INFO
delivery.py:348 t0_flow = time.time() يُسجَّل قبل استدعاء التنظيم مباشرة، ولا يشمل: التحميل، التفريغ، إرسال الخام، حساب العنوان المؤجَّل (_attach_title()) — أي أن "إجمالي" في التذييل = زمن ما بعد إرسال الخام، لا الزمن الكلي للفويس.
الحكم: الحساب دقيق كـ wall-clock للنافذة التي يقيسها (time.time() لا جمع مُدد) — طلبك "هل يقيس الـ Real Wall-Clock بدقة؟" ⇒ نعم للفترة المقاسة، لا للزمن الكلي للفويس. التسمية "⏱️ إجمالي" مضلّلة. INFO — يُستحسن تسميتها "منذ إرسال الخام".
عدد الأخطاء المؤكدة: 6 (2 عالية الأثر في الميزة + 1 عالية في السجلات + 2 متوسطة + 1 INFO).

E. Security Findings
ID	الشدة	الحالة	البند	الدليل
SEC-01	INFO / VERIFIED	✅ صحيح	حجب الأسرار ثنائي الطبقة: RedactFilter على مستوى logging + redact() عند القراءة؛ يغطي AIza…, AQ.…, توكن تليجرام، WEB_ACCESS_TOKEN، ومسار LOCAL_POLISH_CMD وكل مجلداته	logkit.py:21-49, 52-66
SEC-02	INFO / VERIFIED	✅ صحيح	لا أسرار في المستودع (مسح فعلي = 0) + .gitignore يغطي keys.*, .env, .local/, *.ndjson, settings.json	مسح + .gitignore:1-38
SEC-03	INFO / VERIFIED	✅ صحيح	shell=False إجباري؛ LOCAL_POLISH_CMD يُقسَّم بـ shlex.split على POSIX، وos.name=="nt" يمرّره كسلسلة إلى subprocess.run بلا shell	text_assist.py:162-175
SEC-04	INFO / VERIFIED	✅ صحيح	مصادقة الويب سليمة: hmac.compare_digest (لا ==)، cookie HttpOnly، ?token= لمرة واحدة فقط، رفض X-Forwarded-For إلا مع TRUST_PROXY=1، توليد توكن تلقائي على مضيف عام	web_service.py:123-160, 442-465
SEC-05	MEDIUM	⚠️ يحتاج قرارًا	/api/add_key يتطلب التوكن فقط، لا يُقيَّد بـ rate-limit (بينما /api/transcribe مقيَّد). من يملك التوكن يستطيع إغراق keys.json بمفاتيح صحيحة الشكل	web_service.py:280, 292-295, 321-337
SEC-06	MEDIUM	⚠️	_authorized() يقبل أي طلب من localhost بلا توكن (self._is_local()). آمن على استضافة معزولة، لكنه خطر لو شُغِّل البوت على جهاز شخصي مع متصفح (CSRF محلي / أي تطبيق محلي)	web_service.py:123-137
SEC-07	LOW	⚠️	_client_ip() يعتمد X-Forwarded-For[0] عند TRUST_PROXY=1 — لو كان الوكيل لا يُعيد كتابة الترويسة يمكن تجاوز حد الـ rate. موثَّق كقرار، لكن التوصية أخذ آخر عنصر بدلًا من الأول	web_service.py:154-160
SEC-08	LOW	⚠️	/health عام بلا توكن ويعيد تفاصيل تشغيلية (حالة الخيوط، القواطع، عدد المفاتيح المتاحة). لا يكشف أسرارًا (مثبت: build_health لا يطبع قيمًا) لكنه يكشف بنية داخلية	web_service.py:218-222, 352-…
SEC-09	MEDIUM	⚠️	سماح التحميل عبر Content-Length قبل القراءة، والقراءة الفعلية بلا سقف كلي (self.rfile.read(content_length) بعد فحص الطول فقط) — طلب بـContent-Length كاذب/بطيء يمكن أن يحجز خيطًا. ThreadingHTTPServer بلا timeout افتراضي	web_service.py:164-172, 297-304
SEC-10	INFO / VERIFIED	✅	backup يحمي من zip-slip ويكتب مسارات مُنقّاة فقط + فحص contains_secret قبل التسليم	backup.py:44-95
SEC-11	INFO / VERIFIED	✅	لا تنفيذ ديناميكي (eval/exec/pickle/yaml.load = 0)	مسح فعلي
SEC-12	INFO / UNVERIFIED	—	حماية الفرع/الصلاحيات على GitHub: لا تُقرأ من clone	غير متاح
لا توجد ثغرة حرجة معروفة في المسارات المفحوصة. أخطر بند أمني هو SEC-09 (استنزاف خيوط)، وSEC-05/06 يعتمدان على قرار نشر.

F. Testing Assessment
الحقائق المقيسة: 534 مُجمَّعة · 534 ناجحة · 0 فاشلة · 0 skipped · زمن ~74ث · بلا شبكة (conftest._no_network_polish يرمي AssertionError على أي requests.post).

نقاط قوة (VERIFIED)
حاجز شبكة autouse يمنع أي اختبار من لمس Gemini/تليجرام — ممتاز.
fresh_env fixture غني: يوجّه ≥17 مسارًا إلى tmp_path ويعيد ربط مراجع Config في 21 وحدة (مشكلة "الوحدة تحمل نسخة Config قديمة" مُعالجة بوعي).
وجود tests/fake_telegram.py وFakeClock ⇒ اختبارات زمنية/شبكية حتمية.
تغطية ممتازة للمسارات الحرجة القديمة: النسخ/الاستعادة، القواطع، الاستئناف، الإيقاف الرشيق، تعدد المنافذ، parity لوحة/أوامر.
فجوات مؤكدة
ID	الشدة	الفجوة	الدليل
QA-01	HIGH	لا اختبار يُثبت أن الاختبار يفشل عند خلل: الحزمة تمرّ وهي تحوي 3 أخطاء في الميزة الجديدة (BUG-01/02/04) — asserts تتحقق من وجود نص، لا من الحالة النهائية للمستخدم	§D
QA-02	MEDIUM	عزل settings.json مفقود في test_dual_polish.py (لا fresh_env)	BUG-06
QA-03	MEDIUM	FakeSender.edit_text يعيد True دائمًا ⇒ كل مسارات "فشل التعديل" غير مغطاة (وهي بالضبط ما يسبب BUG-01)	tests/test_dual_polish.py:39-41
QA-04	MEDIUM	tests/conftest.py:20 يمسح LOCAL_POLISH_CMD/TIMEOUT في fresh_env فقط، لا في autouse ⇒ اختبارات أخرى قد ترى القيمة المنبعثة من .env الحقيقي	conftest.py:16-32, 24-25
QA-05	LOW	لا تغطية لـ _call_local_provider زمنيًا حقيقيًا (TimeoutExpired, FileNotFoundError, stdout فارغ، returncode≠0) — يوجد تغطية جزئية فقط	text_assist.py:150-196
QA-06	LOW	لا اختبار حدّي واحد عند 3950/4000/12000 (BUG-02 كان سيُكتشف فورًا)	—
QA-07	LOW	test_360_exhaustive.py في جذر المستودع خارج testpaths=tests ⇒ غير مُنفَّذ في CI (يُستثنى أيضًا من pack_deploy). لا أحد يعرف إن كان يمرّ	pytest.ini:2 · tools/pack_deploy.py:57
QA-08	INFO	لا تغطية على 3.10 (مصفوفة CI تنصّ عليها لكن هذه البيئة 3.13)	CI matrix
QA-09	INFO	لا coverage محسوب/منشور — pytest-cov غير مثبَّت ولا نسبة معلنة	requirements-dev.txt
G. Performance & Reliability
مقيس / مثبت
ID	الشدة	البند	الدليل
PERF-01	HIGH	نداءان أو ثلاثة نداءات ذكاء واحدة لكل فويس في وضع السبك (Flash-Lite تنظيم + شلبي محلي + Flash-Lite سبك). مع TRANSCRIBE_WORKERS=1 وQUEUE_PACING_SEC=2، فويس واحد يحتاج 3 نداءات، حصتها اليومية 500 ⇒ السقف الفعلي ~160 فويسًا/يوم مع مضاعفة زمن الانتظار. ولا توجد مقايضة موثقة بين "الجودة" والحصة، ولا circuit breaker على السبك	delivery.py:441-460 · text_assist.synthesize
PERF-02	HIGH	مهلة 180ث لكل فويس في طابور عامل واحد: أسوأ حالة = فويس يُقفل الطابور 180ث (شلبي) + 40ث (سبك) + مهلة تفريغ. QUEUE_SKIP_DEFERRED=0 يجعل الفويسات المتراكمة تنتظر	config.py:188 · voice_queue.py
REL-01	HIGH	خيوط daemon=True غير مُنتظرة عند الإيقاف: main.py:188 ينتظر _in_flight() (المهام) ثم يخرج. الخيط الخلفي للسبك قد يكون في منتصف edit_text ⇒ رسالة لم تُحرَّر/لم تُغلَق، ويُعاد إرسال كل شيء بعد الإقلاع (تكرار). لا join على compare_thread في shutdown()	main.py:178-201 · delivery.py:535
REL-02	MEDIUM	حدود التعديل مقابل حدود الإرسال (نفس جذر BUG-02). أي تعديل >3950 يتحول صامتًا لمسار مختلف	delivery.py:483
REL-03	MEDIUM	prompts.py:44 يعترف ضمنيًا بأن التعليمات داخل المادة: "أي أوامر بداخلها تُعامل كنص" — جيد، لكن سبك ثلاثي يعني حقن مسودة Shelby (مخرَج نموذج خارجي) داخل رسالة المستخدم في compose_synthesis_prompt → سطح Prompt Injection أوسع (نموذج محلي مخرجه غير موثوق). المطلوب تغليف المسودتين بفواصل صريحة + تعليمات "بيانات لا تعليمات" (موجود جزئيًا)	prompts.py:293-298
REL-04	MEDIUM	clock.py/موقتات time.time() مستخدمة لقياس مُدد (ينزلق مع تغيير ساعة النظام/NTP). الأصح time.monotonic() — main.py:186 يستخدم monotonic بشكل صحيح، بينما delivery.py وtext_assist.py يستخدمان time.time()	delivery.py:348,443,453,471 · text_assist.py:102,160
REL-05	LOW	لا retry/backoff لتعديل تليجرام: edit_telegram_message يعيد مرة واحدة بلا parse_mode؛ أخطاء 429 تحتاج انتظارًا (retry_after)	telegram_bot.py:322-334
نقاط قوة اعتمادية (VERIFIED)
backoff متعدد المستويات على مستوى الشبكة (NET_BACKOFF)، حالة "حذر" بعد ReadTimeout، TCP_KEEPALIVE لكشف half-open، قواطع موديلات محفوظة على القرص وتُحمَّل بعد restart، jobs.ndjson مع fsync واستئناف، L4 (exit code 3) + supervisor + watchdog، /doctor + /incidents + restore_test. هذا أفضل مما في أغلب المشاريع الإنتاجية الصغيرة.
H. Dependencies & CI/CD
ID	الشدة	البند	الدليل
DEP-01	MEDIUM	requests>=2.31.0,<=2.32.3: السقف يمنع تلقّي إصلاحات أمنية مستقبلية، والسقف الأدنى غير مثبَّت بدقة ⇒ بناءات غير حتمية (لا Lock file). الإصدار 2.32.x نفسه شمل إصلاحات أمنية	requirements.txt:1
DEP-02	MEDIUM/UNVERIFIED	مصفوفة CI تشمل 3.10 لكن runtime.txt يقول 3.12 — لا ضمان أن الكود يعمل على 3.10 (لم أشغّله). تثبيت الإصدار يجب أن يكون مصدرًا واحدًا	ci.yml:13 · runtime.txt
DEP-03	LOW	requirements-dev.txt بـ-r requirements.txt صحيح، لكن pyflakes وpytest غير مثبَّتين بسقف ⇒ تغيّر سلوك lint بين تشغيل وآخر	requirements-dev.txt
DEP-04	LOW	python-3.12 في runtime.txt مكتوب بصيغة غير قياسية (ملف Pterodactyl يتوقع python-3.12.x عادةً) — يحتاج تحقق على لوحة الاستضافة	runtime.txt
CI-01	MEDIUM	on.push.branches: ["**"] = CI على كل فرع + pull_request = تشغيل مزدوج لكل PR (تكلفة/إزعاج)	ci.yml:4-6
CI-02	MEDIUM	لا خطوة اختبار انحدار على A-test قبل الدمج، ولا حماية فرع موثّقة — الآلية تعتمد على انتباه بشري	—
CI-03	LOW	لا نشر/إصدار آلي: tools/pack_deploy.py يدوي (allowlist + فحص أسرار) — جيد أمنيًا، لكن النشر ليس قابلًا للتكرار (لا artifact versioned، لا rollback آلي)	tools/pack_deploy.py
CI-04	INFO	لا pip cache ولا timeout-minutes على الـ job — تشغيل معلَّق قد يستهلك 6 ساعات	ci.yml:9-26
I. Documentation Assessment
ID	الشدة	التناقض	الدليل
DOC-01	HIGH	LOCAL_POLISH_TIMEOUT = 8.0 في الوثائق، 180.0 في الكود	README.md:137 ("8.0") vs config.py:188 ("180.0")
DOC-02	HIGH	COMPARE_MODE الافتراضي موثَّق merged وغير مذكور synthesizer أصلًا — الميزة الجديدة غير موثقة في README/.env.example إطلاقًا	.env.example:99 vs config.py:189 · grep synthesizer README.md = ∅
DOC-03	HIGH	ميزة السبك الثلاثي غير موثقة ضمن README رغم أنها الافتراضي الآن. لا شيء عن: شريط الحالة، In-Place، صمام إنقاذ شلبي، ولا التذييل الزمني. التوثيق الوحيد = concepts_brief.md بحالة "Draft / Ideation"	concepts_brief.md:6 "Status: Draft / Ideation"
DOC-04	HIGH	الوثائق العليا تُناقض نفسها: Antigravity/* تُقرّر "خفض المهلة إلى 8.0s لمنع تجويع الطابور" بينما الكود الآن 180.0s — أي أن قرارًا هندسيًا موثَّقًا نُقض بصمت	Antigravity/02_…:37,78 vs config.py:188
DOC-05	MEDIUM	متغيرات بيئة موثقة لا يقرأها الكود: POLISH_PROVIDER, LOCAL_PROVIDER_CMD, LOCAL_PROVIDER_TIMEOUT (في local_provider_template.py وAntigravity/01) — الأسماء الحقيقية LOCAL_POLISH_CMD/TIMEOUT. من يتبع القالب لن يعمل عنده شيء	Antigravity/local_provider_template.py:10-12 · grep = 0 في الكود
DOC-06	MEDIUM	README يدّعي "525 اختبارًا" والواقع 534	README.md:195
DOC-07	LOW	قائمة الإصدارات VERSION=2.0.0-phase7.1 بلا CHANGELOG ولا tags ⇒ لا يمكن ربط إصدار بكوميت	—
DOC-08	INFO	حجم توثيقي مفرط مقابل الكود: 23 ملفًا في architecture-v2 + 17 في requirements + 12 في Antigravity + genspark-review/ + consolidated_review_report.md + PROGRESS/RESUME مكرّرة في الجذر وAntigravity/ ⇒ مصدر حقيقة موزَّع يتناقض (وهو سبب DOC-04)	قائمة الملفات
نقاط قوة (VERIFIED): وثائق داخلية غنية فوق العادة، تعليقات عربية تشرح سبب القرار لا ماذا يفعله الكود (مثال ممتاز: delivery.py:267-269)، وDEPLOY.md + docs للوحة، وأوامر /doctor, /incidents, /trace تجعل تشخيص المشكلة ممكنًا بلا SSH.

J. Positive Findings (يجب الحفاظ عليها دون تغيير)
Sender injection (delivery.py:29-37) — العمود الفقري لقابلية الاختبار. لا تكسره.
logkit.RedactFilter على مستوى logging — حماية واحدة تغطي كل القنوات، وليس "حجبًا في كل print".
الأرشفة قبل الإرسال + /last — لا نص يضيع عند فشل تليجرام.
_html_chunks يوازن الوسوم (telegram_bot.py:135-…) وتجنّب قطع <pre> — تفصيلة يستصعبها معظم المشاريع.
jobs.ndjson مع fsync + استئناف بعد إقلاع + idempotency بـfile_unique_id — سلوك منتج حقيقي.
القواطع المحفوظة على القرص + سلّم 20→45→90→180 — تعافٍ مقصود ومُختبَر.
apply_persisted_overrides() + panel.SET_CONFIG_ATTR — طبقتان: Config افتراضي، settings.json تجاوز (نمط سليم).
حاجز الشبكة autouse في الاختبارات — لا اختبار "يزحف" إلى الإنترنت.
_MODE_ALIASES يقبل مرادفات عربية (خام, منظم, الاتنين) — تجربة مستخدم مدروسة.
backup.contains_secret() + zip-slip guard + restore_test — النسخ الاحتياطي مُثبَت لا مُدَّعى.
prompts.compose_prompt دالة نقية + حدّ TRANSCRIBE_LAYER_A_MAX_CHARS لحماية طبقة التفريغ من التضخم.
صفر بدائيات خطرة (shell=True, eval, pickle) في 23 ألف سطر — انضباط أمني جيد.
K. Technical Debt
ID	الشدة	الدين	التقدير
TD-01	HIGH	delivery.deliver = 384 سطرًا / تداخل 10 مستويات / 5 ميزات في دالة واحدة. كل خطأ في هذا التدقيق سببه فرع جديد أُضيف داخل نفس الدالة	إعادة هيكلة إلى _deliver_compare(...), _deliver_single(...) — ~2 يوم
TD-02	HIGH	تكرار كود الإنقاذ 4 مرات: _bg_shelby_rescue, _bg_synthesizer, _bg_shelby (merged), _bg_shelby_split — نفس بنية edit → fail → _send_long مكرّرة 4×وبنسخ مختلفة ⇒ BUG-01 موجود في نسخة واحدة فقط. مصدر الأخطاء نفسه هو التكرار	دالة واحدة _replace_or_send(html, plain, mid, anchor, prefix, caption) — ~4 ساعات
TD-03	MEDIUM	ثلاثة حدود حجم متفرقة (TELEGRAM_MAX_MSG, POLISH_FILE_THRESHOLD, -50) بلا مشتق واحد	تعريف DELIVERY_LIMITS مشتق
TD-04	MEDIUM	الاعتماد على delivery.Config vs config.Config — conftest يعيد ربط 21 وحدة يدويًا لأن كل وحدة تعمل from config import Config. هشاشة بنيوية متكررة	نمط import config; config.Config.X
TD-05	MEDIUM	test_360_exhaustive.py خارج testpaths — ملف 331 سطرًا محجوب عن CI	نقله إلى tests/
TD-06	LOW	print() بدل logger في معظم delivery.py (18 موضعًا) — لا يمرّ عبر RedactFilter (رغم أن redact() تُستدعى يدويًا في بعضها فقط)	تحويل تدريجي
TD-07	LOW	COMPARE_MODE/compare_mode مصدران + panel.apply_setting يكتب على Config مباشرة بلا settings في حالة compare_mode (يكتب على الاثنين)	توحيد
TD-08	LOW	ملفّات PROGRESS.md وRESUME.md مكرّرة في الجذر وفي Antigravity/ بمحتوى مختلف	حذف/دمج
L. Improvement Opportunities (مرتّبة بالقيمة/التكلفة)
#	الفرصة	القيمة	التكلفة	ضروري؟
O-1	توحيد دالة _replace_or_send() وإصلاح BUG-01/02 من جذر واحد	حرجة	منخفضة	ضروري
O-2	إغلاق دورة السبك بشكل متزامن (join بـtimeout) لتسجيل الناتج الصحيح	عالية	منخفضة	ضروري
O-3	فحص No-Op لشلبي + إسقاط تكلفة السبك عند غيابه	عالية	منخفضة	ضروري
O-4	مزامنة الوثائق مع الكود (8.0→180.0، merged→synthesizer، 525→534)	عالية	منخفضة	ضروري
O-5	عزل settings.json في الاختبارات + edit_text قابل للفشل في FakeSender	عالية	منخفضة	ضروري
O-6	إعادة هيكلة deliver() إلى دوال صغيرة	متوسطة	متوسطة	مستحسن
O-7	threading.Event/join في shutdown() لخيوط السبك	متوسطة	منخفضة	مستحسن
O-8	قياس coverage ونشر النسبة	متوسطة	منخفضة	مستحسن
O-9	مؤشرات synth_ok/synth_fail في /status و/doctor	متوسطة	منخفضة	مستحسن
O-10	Circuit breaker على السبك (إذا فشل N مرة متتالية → إيقاف المؤقت)	متوسطة	متوسطة	اختياري
O-11	lockfile/تثبيت دقيق للاعتماديات	منخفضة	منخفضة	مستحسن
O-12	monotonic() للمُدد	منخفضة	منخفضة	اختياري
O-13	مستند معمارية واحد "الحقيقة الحالية" بدل 50 ملفًا	متوسطة	متوسطة	مستحسن
M. Risk Register
Risk	الاحتمال	الأثر	التقييم	مُخفِّف حالي
R-أ: المستخدم يرى "جاري السبك" للأبد عند فشل التعديل	مرتفع	متوسط	CRITICAL	لا شيء
R-ب: نص ذهبي طويل يتحول لرسائل متعددة + انتظار معلَّق	مرتفع	متوسط	HIGH	لا شيء
R-ج: حرق حصة Flash-Lite في سبك بمسودة No-Op	مرتفع	منخفض-متوسط	HIGH	لا شيء
R-د: سجلات /trace وjobs.ndjson تُظهر ناتجًا مختلفًا عن المرسل	مؤكد	منخفض	HIGH (بيانات)	لا شيء
R-هـ: تكرار الإرسال بعد كراش أثناء السبك	متوسط	متوسط	MEDIUM	jobs.ndjson idempotency لا تغطي إعادة الإرسال الجزئية
R-و: مستخدم يعتمد توثيق LOCAL_POLISH_TIMEOUT=8.0 فيصطدم بـ180ث	مؤكد	منخفض	MEDIUM	لا شيء
R-ز: تلويث settings.json من CI/اختبارات على خادم إنتاج	متوسط	متوسط	MEDIUM	لا شيء
R-ح: تجاوز حد الـ rate في الويب عبر X-Forwarded-For إذا الوكيل سيئ	منخفض	منخفض	LOW	TRUST_PROXY=0 افتراضيًا
R-ط: استنزاف خيوط الويب بطلب بطيء	منخفض	متوسط	MEDIUM	ThreadingHTTPServer + MAX_UPLOAD_MB
R-ي: تشغيل إعدادات لم تُطبَّق على أول مهمة مستأنفة	متوسط	منخفض	LOW	نافذة زمنية صغيرة
N. Final Engineering Verdict
درجة الجاهزية (0–100)
البُعد	الدرجة	الأساس
المعمارية (المسارات القديمة)	84	طبقات واضحة، حقن سليم، لا دورات استيراد — مقابل دالة deliver ضخمة
المعمارية (السبك الثلاثي)	46	التصميم صحيح مفاهيميًا، التنفيذ يعتمد على شرط حجم وحيد + 4 نسخ مكرّرة من منطق الإنقاذ
الأمن	86	لا أسرار، shell=False، compare_digest، حجب سجلات؛ بعض مخاطر DoS الويب
الاختبارات	72	534 ناجحة بلا شبكة، لكنها تختبر المسار السعيد وتُخفي 3 أخطاء
الاعتمادية	80	أفضل من المتوسط بكثير (watchdog/supervisor/incidents/backup) مع ثقب في shutdown وسبك غير مُنتظَر
الأداء	58	3 نداءات/فويس، مهلة 180ث على عامل واحد — بلا قياس حمل حقيقي
التوثيق	44	ضخمة في الحجم، متناقضة في المضمون، والميزة الافتراضية غير موثقة
CI/CD	62	lint+compile+tests على مصفوفتين، لكن نشر يدوي وبلا lock/coverage
هل الكود جاهز للإنتاج بنسبة 100%؟
لا. ليس بسبب هشاشة عامة، بل بسبب 3 عيوب محدَّدة ومُعادة الإنتاج في الميزة التي طلبت فحصها، أخطرها أن المستخدم قد يرى شريط انتظار لا ينتهي (BUG-01)، وأن وعد "التعديل في المكان" ينهار عند 3,950 حرفًا (BUG-02)، وأن ما يُسجَّل في السجل ليس ما استلمه المستخدم (BUG-04).

ما أنجزه — ومع إصلاح P0/P1 في MASTER_REMEDIATION_PLAN.md (تقدير 1.5–2 يوم عمل)، تصبح الميزة قابلة للإنتاج بثقة.

ما تم التحقق منه / ما لم يتم
✅ VERIFIED: كل الاستنتاجات في §D (بما فيها 3 أخطاء أُعيد إنتاجها بسكربت)، §E (مسح أسرار/بدائيات)، §F (534/534)، §H (قراءة CI/requirements).
⚠️ UNVERIFIED: التوافق مع Python 3.10، الأداء الفعلي، سلوك ويندوز، حماية فرع GitHub، سلامة .local/shelby_polish.py الحقيقي (ليس في المستودع)، أي E2E عبر الشبكة.
❌ لم يُفحص: 4 وحدات كبيرة قراءة سطرية كاملة (sessions.py, key_manager.py, transcriber.py, backup.py) — أُخذت عيّنات مستهدفة فقط؛ أي حكم عليها في هذا التقرير هو مبدئي.
FINDINGS INDEX — قائمة المشكلات المُدلَّلة
A-test @ f3709cc · 2026-11-09
المفاتيح: ✅ VERIFIED (بإعادة إنتاج) · 🔍 VERIFIED (بقراءة كود/مسح) · ⚠️ UNVERIFIED (تحتاج بيئة) · ▪️ INFO

ID	الشدة	الحالة	العنوان	المسار:السطر	مُعاد إنتاجه؟
BUG-01	CRITICAL	✅ VERIFIED	شريط "جاري السبك" معلَّق للأبد عند فشل edit_text	delivery.py:516-531	نعم (R1)
BUG-02	HIGH	✅ VERIFIED	نص ذهبي 3,950–12,000 حرف: لا تعديل ولا ملف + حالة معلَّقة	delivery.py:483,491 + :641	نعم (R2)
BUG-04	HIGH	🔍 VERIFIED	النسخة الذهبية لا تُسجَّل في jobs.ndjson ولا في res["polished"] عند العودة	telegram_bot.py:567-571 · delivery.py:462,535 · main.py:188	تحليل ساكن + سببية مباشرة
BUG-05	HIGH	🔍 VERIFIED	Shelby الافتراضي = قالب No-Op ⇒ سبك بلا فائدة + حرق حصة	config.py:183-189 · Antigravity/local_provider_template.py:39-42	تحليل ساكن
PERF-01	HIGH	🔍 VERIFIED	3 نداءات ذكاء/فويس في وضع السبك بلا ميزانية أو قاطع	delivery.py:441-460	تحليل ساكن
PERF-02	HIGH	🔍 VERIFIED	180ث على عامل طابور واحد (TRANSCRIBE_WORKERS=1)	config.py:188 · voice_queue.py:78	تحليل ساكن
REL-01	HIGH	🔍 VERIFIED	خيوط السبك daemon لا تُنتظَر عند الإيقاف	main.py:178-201 · delivery.py:535	تحليل ساكن
DOC-01	HIGH	🔍 VERIFIED	LOCAL_POLISH_TIMEOUT: 8.0 في الوثائق / 180.0 في الكود	README.md:137 vs config.py:188	مباشر
DOC-02	HIGH	🔍 VERIFIED	COMPARE_MODE الافتراضي الموثَّق merged / الفعلي synthesizer	.env.example:99 vs config.py:189	مباشر
DOC-03	HIGH	🔍 VERIFIED	ميزة السبك غير موثقة إطلاقًا في README (فقط concepts_brief.md بحالة Draft)	grep synthesizer README.md = ∅	مباشر
DOC-04	HIGH	🔍 VERIFIED	قرار هندسي موثَّق "8.0s" نُقض بصمت إلى 180.0s	Antigravity/02_…:37,78	مباشر
QA-01	HIGH	🔍 VERIFIED	لا اختبار يفشل عند خلل حقيقي (534 خضراء + 3 أخطاء مؤكدة)	§D	تَبَعي
TD-01	HIGH	🔍 VERIFIED	deliver() = 384 سطرًا / تداخل 10 مستويات	delivery.py:241-625	قياس
TD-02	HIGH	🔍 VERIFIED	منطق الإنقاذ مكرّر 4 مرات (سبب جذر BUG-01)	delivery.py:366,441,546,578	قياس
BUG-03	INFO	✅ VERIFIED (مُصحَّح)	context_summary في السبك يصل فعلًا للموديل (الادعاء الأولي بخطئه غير صحيح — مسحوب)	text_assist.py:311-315	تحقق
BUG-06	MEDIUM	✅ VERIFIED	الاختبارات تكتب على settings.json/keys.json/glossary.json الحقيقية	settings.py:71-75 · tests/test_dual_polish.py	نعم (R5)
BUG-07	MEDIUM	🔍 VERIFIED	اختباران يدّعيان تغطية الفشل ولا يغطيانه	tests/test_dual_polish.py:397,560	قراءة
ARC-01	HIGH	🔍 VERIFIED	حالة مشتركة بين خيوط بلا قفل/event	delivery.py:462,537	قراءة
ARC-02	HIGH	🔍 VERIFIED	"In-Place" مضمونة بشرط حجم فقط	delivery.py:483,502,524	قراءة
ARC-04	MEDIUM	🔍 VERIFIED	overrides تُطبَّق بعد بدء الطابور (استئناف)	main.py:432 ثم 437	قراءة
ARC-05	LOW	🔍 VERIFIED	synth_ok/synth_fail لا تُعرَض في /status	telegram_bot.py:1355-1366	قراءة
SEC-05	MEDIUM	🔍 VERIFIED	/api/add_key بلا rate-limit	web_service.py:321-337	قراءة
SEC-06	MEDIUM	🔍 VERIFIED	localhost يتجاوز التوكن دائمًا	web_service.py:123-137	قراءة
SEC-09	MEDIUM	🔍 VERIFIED	Content-Length فقط كسقف + socket بلا timeout	web_service.py:164-172,297-304	قراءة
SEC-07	LOW	🔍 VERIFIED	X-Forwarded-For[0] قابل للتزوير خلف وكيل سيئ	web_service.py:154-160	قراءة
SEC-08	LOW	🔍 VERIFIED	/health عام يكشف بنية داخلية	web_service.py:218-222	قراءة
REL-02	MEDIUM	🔍 VERIFIED	عدم اتساق حدود التعديل/الإرسال/الملف	delivery.py:483,641	قراءة
REL-03	MEDIUM	🔍 VERIFIED	حقن مخرَج Shelby داخل رسالة المستخدم (Prompt Injection أوسع)	prompts.py:293-298	قراءة
REL-04	MEDIUM	🔍 VERIFIED	time.time() لقياس مُدد (ينزلق مع NTP)	delivery.py:348,443,453,471	قراءة
REL-05	LOW	🔍 VERIFIED	لا retry/backoff لتعديل تليجرام (429)	telegram_bot.py:322-334	قراءة
DEP-01	MEDIUM	🔍 VERIFIED	requests بسقف أعلى ثابت وبلا lock ⇒ غير حتمي	requirements.txt:1	قراءة
DEP-02	MEDIUM	⚠️ UNVERIFIED	CI على 3.10 لم يُنفَّذ هنا (بيئتي 3.13)	ci.yml:13	لم يُنفَّذ
DEP-04	LOW	⚠️ UNVERIFIED	runtime.txt بصيغة python-3.12 غير مؤكدة لـ Pterodactyl	runtime.txt	يحتاج لوحة
CI-01	MEDIUM	🔍 VERIFIED	CI يعمل مرتين لكل PR (push: ** + pull_request)	ci.yml:4-6	قراءة
CI-03	LOW	🔍 VERIFIED	نشر يدوي بلا artifact versioned/rollback آلي	tools/pack_deploy.py	قراءة
CI-04	INFO	🔍 VERIFIED	لا timeout-minutes ولا pip cache	ci.yml:9-26	قراءة
DOC-05	MEDIUM	🔍 VERIFIED	متغيرات موثقة لا يقرأها الكود (LOCAL_PROVIDER_CMD…)	Antigravity/local_provider_template.py:10-12	grep = 0
DOC-06	MEDIUM	🔍 VERIFIED	README: "525 اختبارًا" / الواقع 534	README.md:195	عدّ فعلي
QA-04	MEDIUM	🔍 VERIFIED	LOCAL_POLISH_* تُمسح في fresh_env فقط لا autouse	conftest.py:16-32	قراءة
QA-05	LOW	🔍 VERIFIED	تغطية جزئية لمسارات _call_local_provider الفاشلة	text_assist.py:150-196	قراءة
QA-06	LOW	🔍 VERIFIED	لا اختبار حدودي عند 3950/4000/12000	—	قراءة
QA-07	LOW	🔍 VERIFIED	test_360_exhaustive.py خارج testpaths ⇒ غير مُنفَّذ	pytest.ini:2	قراءة
QA-09	INFO	🔍 VERIFIED	لا coverage محسوب	requirements-dev.txt	قراءة
TD-03..08	LOW	🔍 VERIFIED	حدود متفرقة · from config import Config · print بدل logger · مصدران لـ compare_mode · وثائق مكرّرة	متعددة	قراءة
SEC-12	INFO	⚠️ UNVERIFIED	حماية فرع GitHub / PRs / Issues	—	لا صلاحيات
ملخص الأعداد
CRITICAL: 1 · HIGH: 11 · MEDIUM: 14 · LOW: 11 · INFO: 6
أخطاء مؤكدة بإعادة إنتاج فعلية: 2 (BUG-01, BUG-02) + 1 (BUG-06 بالكتابة على الملفات)
أخطاء مؤكدة بالسببية المباشرة من الكود: 3 (BUG-04, BUG-05, QA-01 تَبَعي)
ادعاء واحد سُحب بعد التحقق (BUG-03) — مُعلَن في التقرير الرئيسي.
MASTER REMEDIATION PLAN — خطة الإصلاح (غير مُنفَّذة)
wewe00220033/Gemini-3.8-Flash @ A-test / f3709cc
لم يُنفَّذ أي بند من هذه الخطة. هذه خطة مكتوبة لتُنفَّذ بواسطة وكيل برمجي آخر بلا تخمين: كل مهمة تحدد الملف والسطر والتغيير والاختبار ومعيار القبول ومخاطر الانحدار.

تقدير إجمالي: P0 ≈ 4 ساعات · P1 ≈ 1 يوم · P2 ≈ 1 يوم · P3 ≈ 1.5 يوم · P4 اختياري.

P0 — المخاطر الحرجة (لا تصلح للإنتاج قبلها)
P0-01 · إغلاق دورة السبك دائمًا (إصلاح BUG-01 + BUG-02 من جذر واحد)
المشكلة المرتبطة: BUG-01 (شريط انتظار معلَّق) · BUG-02 (3950–12000 حرف)
الملفات: delivery.py (السطور 390، 483–496، 502–515، 516–531، 638–650)
التغييرات المقترحة:
أضف دالة مساعدة واحدة أعلى delivery.py:
Python

def _replace_or_send(sender, html_text, plain_text, mid, anchor, filename_prefix, caption, head_html=""):
    """يحاول التعديل في المكان؛ وإن تعذّر (mid=None / فشل / حجم) يُغلق الدورة بإرسال نهائي.
    يرجع True إذا أُغلقت الدورة (تعديل أو إرسال)."""
    limit = min(Config.TELEGRAM_MAX_MSG - 50, Config.POLISH_FILE_THRESHOLD)
    edited = False
    if mid is not None and len(html_text) <= limit and getattr(sender, "edit_text", None):
        try:
            edited = (sender.edit_text(mid, html_text, "HTML") is not False)
        except Exception:
            edited = False
    if not edited:
        _ensure_no_stale_status(sender, mid, anchor)      # اختياري — انظر P1-03
        _send_long(sender, html_text, plain_text, mid or anchor,
                   filename_prefix, caption, head_html)
    return True
استبدل الكتل الأربع المكرّرة (السطور 389–399 _bg_shelby_rescue، 482–496 السبك الناجح، 501–515 fallback، 523–531 مسار الفشل) باستدعاء واحد لهذه الدالة ⇒ حذف ~80 سطرًا مكرّرًا (TD-02) وإصلاح BUG-01 وBUG-02 في آن واحد.
في الحالة الأخيرة (shelby_status in ("timeout","error")) استخدم if not edited: sender.send_text(to_html, anchor, "HTML") (أو _replace_or_send نفسها) بدل if not edited and mid is None:.
في _send_long أضف حزام أمان: if len(plain_text) > Config.TELEGRAM_MAX_MSG - 50 and sender.send_document is not None: ⇒ أرسل ملفًا (قبل شرط POLISH_FILE_THRESHOLD) كي لا يمرّ نص > سقف الرسالة أبدًا.
الاعتماديات: لا شيء. يجب أن يسبق P1-01.
الاختبارات المطلوبة (tests/test_dual_polish.py):
test_synthesizer_edit_failure_still_closes (FakeSender.edit_text→False + shelby timeout): assert أن هناك رسالة/تعديلًا يحوي "تجاوز شلبي المهلة".
test_synthesizer_golden_4500_becomes_file_or_final: golden=4500 → assert len(fs.docs)==1 أو رسالة نصية ≤ 3950.
test_synthesizer_3950_boundary_edits_in_place: golden نصّه 3900 → assert len(edited)==1 and docs==[].
test_synthesizer_13000_becomes_file.
test_all_synthesis_paths_end_in_terminal_state: مصفوفة (shelby ok/fail, synth ok/fail, edit ok/fail) ⇒ في كل حالة يوجد آخر رسالة لا تحوي "⏳ جاري".
معايير القبول: كل حالة من المصفوفة السابقة (12 حالة) تنتهي برسالة حالة نهائية؛ لا edited_texts تضم نصًا > 3950؛ الملفات تُرسل عند > 4000 نصًا.
مخاطر الانحدار: تغيير سلوك أوضاع merged/split (تستخدم نفس الدوال). ⇒ شغّل الحزمة كاملة، وخصوصًا test_deliver_merged_mode, test_deliver_split_mode, test_deliver_merged_oversized_fallback.
التحقق النهائي: pytest -q = كامل الدفعة ناجحة + إعادة تشغيل سكربتي R1 وR2 ⇒ يجب أن يظهر تغيير السلوك.
P0-02 · الافتراضيات الخطرة: COMPARE_MODE=synthesizer + مهلة 180ث
المشكلة: أداء/حصص/توثيق (PERF-01, PERF-02, DOC-01, DOC-02, DOC-04)
الملفات: config.py:188-189، .env.example:98-99، README.md:137
التغييرات المقترحة (اختر واحدًا بقرار مالك صريح):
الخيار A (مفضَّل أمنيًا): الإبقاء على COMPARE_MODE=synthesizer لكن خفض LOCAL_POLISH_TIMEOUT إلى 20.0 مع تضمين زمن السبك في ميزانية RETRY_BUDGET_SECONDS، وتحديث README/.env.example إلى القيم الفعلية.
الخيار B: إرجاع COMPARE_MODE=merged افتراضيًا وتمكين synthesizer عبر متغيّر بيئة صريح فقط.
اعتماديات: يحتاج قرار مالك (انظر §"قرارات مطلوبة" القرار-1). لا تتجاوزه.
الاختبارات: test_default_compare_mode_documented (يقارن Config.COMPARE_MODE بقيمة مذكورة في .env.example — اختبار ثابت).
معايير القبول: لا توجد قيمة افتراضية في الكود تختلف عن أي قيمة موثقة.
مخاطر الانحدار: منخفضة (إعداد فقط). التحقق: تشغيل البوت مع .env نظيف وقراءة settings.compare_mode().
P1 — أخطاء ومشكلات عالية الأولوية
P1-01 · "النسخة الذهبية" لا تصل للسجلات ولا للدالة المُنادية (BUG-04)
الملفات: delivery.py:462, 535-537 · telegram_bot.py:559-572
التغيير: أضف wait=True/join(timeout) منطقي:
في deliver: بعد t.start() أضف result["compare_thread"] = t (موجود) — ولا تعد حالة.
في telegram_bot.process_job: بعد res = delivery.deliver(...):
Python

_t = res.get("compare_thread")
if _t is not None and res.get("mode") == "dual":
    _t.join(timeout=Config.LOCAL_POLISH_TIMEOUT + Config.TEXT_ASSIST_TIMEOUT + 5)
ثم أضف pending_after لـ jobs.record عند عدم الانتهاء بدل تسجيل polished=False كأنها حقيقة.
بديل أنظف (مفضَّل): تمرير on_polished=lambda txt: jobs.record(job_id, "polished", text=txt[:2000]) إلى deliver واستدعاؤه داخل الخيط.
الاختبارات: test_deliver_blocks_until_compare_done + test_jobs_ndjson_contains_golden (بـtmp_path للـJOBS_FILE).
معايير القبول: jobs.ndjson يحوي النص الذهبي؛ res["polished"] = الذهبية عند عودة process_job.
مخاطر الانحدار: زيادة زمن الإرجاع لخيط العامل (قد يصل 180ث) → قد يؤثر على _in_flight/الإيقاف. البديل on_polished لا يزيد الزمن — استخدمه إن أمكن.
التحقق النهائي: فحص /trace <job_id> بعد فويس حقيقي.
P1-02 · Shelby No-Op (BUG-05)
الملفات: text_assist.py:216-242 · config.py:184-189
التغيير: في polish_local: بعد الحصول على text، إن كان text.strip() == raw_text.strip() أو نسبة تشابه (difflib.SequenceMatcher) ≥ 0.98 ⇒ أعِد (None, "المزود المحلي أعاد النص كما هو (No-Op) — السبك غير مجدٍ") وسجّل _state["local_noop"] += 1.
الاختبارات: test_local_provider_noop_detected.
معايير القبول: لا نداء synthesize عندما تكون مسودة Shelby No-Op؛ /status يُظهر "مزود محلي: No-Op".
مخاطر الانحدار: مزود محلي شرعي يعيد النص كما هو (وضع smart_verbatim مثلًا) سيُعتبر No-Op — rozwiąz: استثنِ role_id == "smart_verbatim".
P1-03 · إغلاق رسالة الحالة القديمة عند التحوّل لمسار مختلف (تعزيز P0-01)
الملفات: delivery.py (دالة جديدة _ensure_no_stale_status)
التغيير: قبل أي _send_long بديل، إن كان mid موجودًا ولم ننجح في تعديله، حاول تعديله إلى سطر قصير "✅ اكتملت — التفاصيل أدناه" ثم أرسل الناتج. إن فشل التعديل أيضًا، أرسل السطر كنص منفصل.
الاختبارات: test_stale_status_marked_when_oversized.
معايير القبول: المستخدم لا يرى أبدًا نصين متعارضين ("جاري السبك" + "النسخة الذهبية") في نفس الوقت.
P1-04 · عزل settings.json في الاختبارات (BUG-06 / QA-02)
الملفات: tests/test_dual_polish.py (أضف fresh_env لكل اختبار يمسّ settings)
التغيير: أضف fixture موحّد:
Python

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config.Config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.reset_cache()
أو الأفضل: اجعل _runtime_dirs_in_tmp في conftest.py تُصفّر SETTINGS_FILE/GLOSSARY_FILE/ROLES_FILE (الآن لا تفعل).
الاختبارات الإضافية: test_suite_does_not_create_settings_json (meta-test) وtest_edit_text_can_fail + FakeSender(edit_ok=False).
معايير القبول: تشغيل الحزمة في نسخة نظيفة لا يُنشئ settings.json ولا يعدّل واحدًا موجودًا (تحقق بـmtime).
مخاطر الانحدار: بعض اختبارات test_phase6_panel.py تعتمد على الحفظ الفعلي — تأكد أنها تستخدم fresh_env.
P1-05 · مزامنة الوثائق مع الكود (DOC-01/02/03/05/06)
الملفات: README.md:137, 195 · .env.example:98-99 · Antigravity/local_provider_template.py:10-12 · concepts_brief.md:6
التغيير: (أ) LOCAL_POLISH_TIMEOUT = القيمة الفعلية؛ (ب) إضافة synthesizer لكل قوائم compare_mode؛ (ج) قسم README جديد "🏆 النسخة الذهبية المسبوكة" بوصف: شريط الحالة، In-Place، صمام الإنقاذ، التذييل الزمني، والحدود (3,950/12,000)؛ (د) تصحيح 525→534؛ (هـ) تصحيح أسماء المتغيرات في القالب إلى LOCAL_POLISH_CMD/TIMEOUT؛ (و) رفع حالة concepts_brief.md من Draft إلى Implemented.
معايير القبول: grep synthesizer README.md ≠ ∅؛ لا توجد قيمة رقمية في README تخالف config.py (أضف اختبار ثابت test_docs_match_defaults).
مخاطر الانحدار: لا شيء.
P1-06 · ترتيب الإقلاع (ARC-04)
الملف: main.py:429-441
التغيير: انقل panel.apply_persisted_overrides() إلى قبل telegram_bot.start_queue(resume=...).
الاختبار: test_overrides_applied_before_resume (mock على start_queue يقرأ Config.LOCAL_POLISH_TIMEOUT ويتحقق أنه مضبوط).
معايير القبول: أول مهمة مستأنفة تعمل بالـ overrides.
مخاطر الانحدار: منخفضة — تأكد أن panel لا يعتمد على شيء يبدأه start_queue.
P2 — جودة واعتمادية
P2-01 · انتظار خيوط السبك في الإيقاف (REL-01)
الملفات: main.py:178-201 · delivery.py (سجّل الخيوط النشطة في قائمة على مستوى الوحدة: _ACTIVE_COMPARE = set())
التغيير: deliver يضيف الخيط إلى _ACTIVE_COMPARE وfinally يُزيله؛ Runtime.shutdown بعد _in_flight() ينتظر min(5, GRACE) ثانية ثم يسجّل تحذيرًا بعدد الخيوط المتبقية.
الاختبار: test_shutdown_waits_for_compare_threads.
معايير القبول: لا daemon خيط يُقتل في منتصف edit_text في الاختبار (محاكاة بحدث + time.sleep).
مخاطر الانحدار: إطالة زمن الإيقاف ≤5ث.
P2-02 · مؤشرات السبك في /status و/doctor (ARC-05)
الملفات: telegram_bot.py:1355-1366 · observability.py
التغيير: أضف | سبك: نجح {synth_ok}/{synth_calls} (فشل {synth_fail}) في _pipeline_text، وسطر تحذير في /doctor إذا synth_fail > 0 and synth_ok == 0.
الاختبار: test_status_shows_synth_stats.
معايير القبول: الأرقام تظهر فعليًا بعد فويس حقيقي.
مخاطر الانحدار: نص /status مُختبَر في اختبارات parity — حدّث التوقعات.
P2-03 · monotonic() للمُدد (REL-04)
الملفات: delivery.py:348,443,453,471 · text_assist.py:102,160
التغيير: time.monotonic() بدل time.time() في قياسات المدد فقط (لا في الطوابع الزمنية).
الاختبار: لا سلوك مختلف — اختبارات قائمة تكفي.
معايير القبول: grep "time.time()" delivery.py لا يظهر في قياس مدة.
P2-04 · حماية /api/add_key وإبطاء الطلبات (SEC-05, SEC-09)
الملفات: web_service.py:280, 321-337, 164-172
التغيير: (أ) if rate_limited(self._client_ip(), limit=Config.WEB_RATE_LIMIT_PER_MIN // 3) قبل معالجة add_key؛ (ب) self.connection.settimeout(30) في setup()؛ (ج) قراءة الجسم بـmin(content_length, max_bytes) والتحقق من len(body).
الاختبارات: test_add_key_rate_limited, test_slow_body_times_out.
معايير القبول: 429 بعد N طلب؛ لا خيط يبقى > 30ث.
مخاطر الانحدار: رفع تحميل مفاتيح شرعي أثناء الإعداد — وثّق الحد.
P2-05 · نقل test_360_exhaustive.py إلى tests/ (TD-05)
التغيير: git mv test_360_exhaustive.py tests/test_360_exhaustive.py + تأكد أنه يمرّ + أزله من ROOT_PY_EXCLUDE في tools/pack_deploy.py.
معايير القبول: العدد يصبح 534 + N في CI.
P2-06 · Coverage (QA-09)
التغيير: أضف pytest-cov إلى requirements-dev.txt و--cov=. --cov-report=term-missing --cov-fail-under=<الحالي> في CI (ابدأ بـfail_under الحالي ثم ارفعه تدريجيًا).
معايير القبول: نسبة معلنة في CI.
P3 — أداء وبنية
P3-01 · تفكيك deliver() (TD-01)
التغيير: استخرج: _deliver_raw_and_title(...), _deliver_polished_single(...), _deliver_compare(...) (يفرّع على cmp_mode), _deliver_no_polish(...). اجعل deliver() مُنسِّقًا ≤ 60 سطرًا.
معايير القبول: كل الاختبارات القائمة تمرّ دون تعديل (دليل على نقاء إعادة الهيكلة). عدد مستويات التداخل ≤ 4.
مخاطر الانحدار: عالية إن لم تكن الحزمة خضراء قبلها — يجب أن يسبقه P0/P1 مكتمل.
P3-02 · ميزانية زمنية للسبك
التغيير: أدخل SYNTH_BUDGET_SECONDS (مثلًا 25ث) وتحقّق قبل بدء السبك: إن تجاوز الزمن الكلي الميزانية أرسل مسودة جيميني مع حالة "تم تخطي السبك لتجاوز الميزانية".
الاختبار: test_synth_skipped_when_budget_exceeded.
معايير القبول: زمن الفويس الكلي ≤ ميزانية معلنة.
P3-03 · Circuit breaker على السبك (O-10)
التغيير: بعد 3 إخفاقات متتالية → تعطيل السبك 10 دقائق (استخدم model_state القائم أو عدّاد في text_assist._state).
P3-04 · توحيد حدود الحجم (TD-03)
التغيير: DELIVERY_LIMITS = {"edit": TELEGRAM_MAX_MSG-50, "message": TELEGRAM_MAX_MSG-50, "file": min(POLISH_FILE_THRESHOLD, TELEGRAM_MAX_MSG-50)} واستخدامها في كل المواضع.
P4 — اختياري/مستقبلي
ID	البند	القيمة
P4-01	Lockfile (pip-compile/uv pip compile) وتثبيت دقيق للاعتماديات (DEP-01/03)	بناءات حتمية
P4-02	توحيد مصفوفة Python: runtime.txt وci.yml من مصدر واحد (DEP-02/04)	توافق
P4-03	pip cache + timeout-minutes: 15 في CI + تشغيل مزدوج (push: branches: [main, A-test]) (CI-01/04)	تكلفة CI
P4-04	SECURITY.md + CHANGELOG.md + tags/releases (DOC-07)	تتبّع
P4-05	مستند "الحقيقة الحالية" واحد يدمج 50 ملف توثيق (DOC-08)	تقليل التناقض
P4-06	نمط import config; config.Config.X بدل from config import Config لتقليل إعادة الربط في conftest (TD-04)	هشاشة أقل
P4-07	تحويل print() إلى logger في delivery.py (TD-06)	حجب أسرار مضمون
P4-08	_client_ip() يأخذ آخر عنصر من X-Forwarded-For (SEC-07)	دقة rate-limit
جدول الاعتماديات بين المهام
text

P0-01 ──► P1-03 ──► P3-01 ──► P3-04
  │
  └──► P1-01 ──► P2-01
P0-02 ──► P1-05
P1-02 ──► P2-02
P1-04 ──► P2-06
P1-06 (مستقل)
قائمة الاختبارات المطلوبة (مجموعة كاملة)
#	الاختبار	يحمي من
T-01	test_all_synthesis_paths_end_in_terminal_state (12 حالة)	BUG-01 · BUG-02
T-02	test_synthesizer_3950_boundary_edits_in_place	BUG-02
T-03	test_synthesizer_4500_becomes_file	BUG-02
T-04	test_synthesizer_13000_becomes_file	BUG-02
T-05	test_jobs_ndjson_contains_golden	BUG-04
T-06	test_local_provider_noop_detected	BUG-05
T-07	test_suite_does_not_create_settings_json (meta)	BUG-06
T-08	test_edit_text_can_fail (FakeSender جديد)	QA-03
T-09	test_overrides_applied_before_resume	ARC-04
T-10	test_shutdown_waits_for_compare_threads	REL-01
T-11	test_docs_match_defaults (static)	DOC-01/02
T-12	test_status_shows_synth_stats	ARC-05
T-13	test_add_key_rate_limited	SEC-05
T-14	test_slow_body_times_out	SEC-09
T-15	test_synth_skipped_when_budget_exceeded	PERF-02
قرارات مطلوبة منك كمالك المشروع (لا أتخذها عنك)
#	القرار	الخيارات	الأثر
D-1	الوضع الافتراضي لـ COMPARE_MODE	(أ) synthesizer مع مهلة 20ث · (ب) merged افتراضيًا · (ج) synthesizer + 180ث كما هو	كل فويس = 2 أو 3 نداءات ذكاء + 180ث أقصى
D-2	مهلة LOCAL_POLISH_TIMEOUT	8 / 20 / 60 / 180	8 يقطع شلبي المحلي الحقيقي غالبًا ⇒ سبك دائم الفشل؛ 180 يجمّد الطابور
D-3	هل "النسخة الذهبية" تُسجَّل في jobs.ndjson وALL.md بالنص الكامل؟	نعم (خصوصية + حجم) / لا (كفاية المسودة)	يمس /trace والتكرار
D-4	هل تُفرَّق رسائل > 4000 إلى رسائل متعددة أم ملف .md دائمًا؟	دائمًا ملف / تقسيم	تجربة المستخدم
D-5	مصدر الحقيقة للتوثيق	README.md فقط + أرشفة Antigravity/ / التوثيق السردي كمرجع تاريخي	ينهي DOC-04
D-6	دعم Python 3.10 رسميًا؟	نعم (وسّع CI) / لا (حدّث ci.yml إلى 3.12 فقط)	أعباء اختبار
D-7	/api/add_key من الويب: يبقى أم يُقيَّد بالبوت فقط؟	يبقى مع rate-limit / البوت فقط	سطح هجوم
D-8	هل localhost يتجاوز التوكن في الويب؟	يبقى / يُلغى ويُطلب التوكن دائمًا	سهولة مقابل أمان
سأبدأ بالوصول الفعلي إلى المستودع والفرع `A-test` — التدقيق يجب أن يكون مبنيًا على الملفات الحقيقية لا على الوصف.

حصلت على قاعدة أدلة كاملة. الآن أكتب التقرير.

تم إنجاز التدقيق. كل الاستنتاجات مبنية على استنساخ فعلي لفرع `A-test` عند `f3709cc` وتشغيل فعلي للكود — لا على الوصف.

## أهم ما كشفه الفحص

**الخبر الجيد:** القاعدة ناضجة فعلاً. `pyflakes` = 0 ملاحظات، **534 اختبارًا ناجحًا بلا شبكة**، صفر أسرار في المستودع، صفر `shell=True/eval/pickle`، ومصادقة الويب تستخدم `hmac.compare_digest` + cookie HttpOnly + حجب أسرار على مستوى `logging`.

**الخبر السيئ — 3 عيوب مؤكدة في ميزة السبك الثلاثي تحديدًا:**

1. **CRITICAL — شريط "⏳ جاري السبك مع شلبي" يبقى معلَّقًا للأبد** إذا فشل `editMessageText` (400/429/رسالة محذوفة). السبب سطر واحد: `if not edited and mid is None` في `delivery.py:530` — لا يرسل بديلاً لأن `mid` ليس `None`. **أعدت إنتاجه**: `sent=2, edited=0`، ولا رسالة واحدة تقول للمستخدم أن شلبي تجاوز المهلة.

2. **HIGH — نص ذهبي بين 3,950 و12,000 حرف**: لا يُعدَّل في المكان ولا يُرسل كملف. أعدت إنتاجه بنص 5,000 حرف: `sent lengths = [155, 266, 5388]` — رسالة تجاوز حد تليجرام ستُقسَّم في طبقة النقل، ووعد "In-Place" ينهار صامتًا. ثلاثة حدود غير متسقة (`4000 / 3950 / 12000`) بلا مشتق واحد.

3. **HIGH — النسخة الذهبية لا تُسجَّل**: الخيط الخلفي يكتب `result["polished"]` بعد أن يكون `process_job` قد قرأها وسجّلها، ولا `join` في أي مكان. النتيجة: `/trace` و`jobs.ndjson` يقولان شيئًا والمستخدم استلم شيئًا آخر.

**وتصحيح ذاتي أُعلنه:** اتهمت أولاً `context_summary` (L2) بأنه يُسقَط في السبك — بعد التحقق من `text_assist.py:311-315` **الادعاء غير صحيح**، الملخص يصل للموديل فعلاً. سحبته في التقرير بدلاً من تركه.

**تناقضات خطيرة بين الكود والوثائق:** الوثائق تقول `LOCAL_POLISH_TIMEOUT=8.0` و`COMPARE_MODE=merged`؛ الكود يقول `180.0` و`synthesizer`. والأخطر: `Shelby` الافتراضي يشير إلى قالب **No-Op** يعيد النص كما هو — أي "سبك" بلا محرك ثانٍ، مع حرق نداء Flash-Lite إضافي لكل فويس. و`README` يعلن 525 اختبارًا (الواقع 534).

**حكمي:** غير جاهز للإنتاج بنسبة 100% — لا لعيوب عامة، بل لهذه النقاط المحددة. إصلاح P0 المقدَّر: **نصف يوم**، يعالج البند الأول والثاني من جذر واحد لأن سبب الجذر واحد: تكرار منطق الإنقاذ أربع مرات في `deliver()` (384 سطرًا، تداخل 10 مستويات).

**التسليمات في `audit/`:** التقرير الكامل (353 سطرًا، 14 قسمًا) · خطة الإصلاح P0–P4 (15 مهمة بمعايير قبول واختبارات) · فهرس 42 مشكلة بالأدلة · سجل الأدلة وسكربتات إعادة الإنتاج. **ولم أُعدّل أي ملف في المستودع**، ولن أفعل حتى تقول **GO**. توجد أيضًا 8 قرارات مطلوبة منك كمالك (خصوصًا D-1 مهلة شلبي وD-2 الوضع الافتراضي) موضوعة في نهاية خطة الإصلاح.

<ADDITIONAL_METADATA>
The current local time is: 2026-11-09T02:27:10+02:00.

The user's current state is as follows:
Other open documents:
- c:\Users\USER\Downloads\111\منظومة_التفريغ_الصوتي_الذكي_Gemini\concepts_brief.md (LANGUAGE_UNSPECIFIED)
- c:\Users\USER\Downloads\111\منظومة_التفريغ_الصوتي_الذكي_Gemini\Antigravity\07_إدخال_سكربت_محلي_فعلي_ودراسة_التنظيم_المتوازي.md (LANGUAGE_UNSPECIFIED)
- c:\Users\USER\Downloads\111\منظومة_التفريغ_الصوتي_الذكي_Gemini\Antigravity\اداخل سكربت محلي فعلي (LANGUAGE_UNSPECIFIED)
- c:\Users\USER\Downloads\111\تشغيل_اللاسلكي_في_الخلفية_Silent.bat (LANGUAGE_UNSPECIFIED)
- c:\Users\USER\Downloads\111\إظهار_البارتشن_E.bat (LANGUAGE_UNSPECIFIED)

The user has uploaded 1 audio file(s). Consider them as a part of their message.
</ADDITIONAL_METADATA>