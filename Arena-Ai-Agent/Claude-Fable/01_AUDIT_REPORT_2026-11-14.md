# تقرير التدقيق الهندسي المستقل — Gemini-3.8-Flash (فرع A-test) — الجزء 1/2

- تاريخ التقرير: 2026-11-14
- المدقق: Claude Fable (وكيل مستقل) — وضع READ-ONLY
- المستودع: wewe00220033/Gemini-3.8-Flash — الفرع: A-test
- HEAD وقت الفحص: `7ebb2ad6219b6e40f0215799e71eb5ce25f10349` (2026-11-14)
- الجزء 2 (خطة الإصلاح + الاختبارات + قرارات المالك + بيان النطاق): `02_REMEDIATION_PLAN_2026-11-14.md`
- هذا الملف إضافة وثائقية فقط. لم يُعدَّل أي كود، لم يُشغَّل أي اختبار.

> تنبيه نطاق: أداة القراءة توقفت قبل اكتمال الجولة وعدة ملفات وصلت مقصوصة. هذا ليس «فحصاً كاملاً للمشروع». كل ما لم يُقرأ مصنّف UNVERIFIED.

---

## 0. هوية المستودع والحالة الفعلية

| البند | القيمة | الدليل |
|---|---|---|
| HEAD على A-test | `7ebb2ad6` (2026-11-14) | شجرة Git + سجل commits |
| عدد commits | 263 | صفحة المستودع |
| الفروع | 30 فرعاً، كلها `protected: false` | GitHub API |
| main | عند `2dac3d17` — متخلّف كثيراً عن A-test | GitHub API |
| Pull Requests | 28 (لا PR مفتوح) | صفحة PRs |
| CI | آخر تشغيل على A-test (run #327، commit 7ebb2ad) — **conclusion: failure** | GitHub Actions API |
| الترخيص | MIT | LICENSE |

---

## A. Executive Summary

بوت تليجرام + واجهة ويب لتفريغ الصوت عبر Gemini مع تدوير مفاتيح، قواطع دائرة، طابور موحّد، جلسات، أرشفة، نسخ احتياطي، مشرف عمليات اختياري، ومسار «سبك» ثلاثي. الهندسة الدفاعية (كتابة ذرية، عزل التالف، Session لكل خيط، watchdog متدرج) ناضجة وأعلى من المعتاد لمشروع فردي.

**أهم النتائج:**
1. **CI أحمر على HEAD الحالي** رغم أن الوثائق تقول «CI أخضر / 636 اختبار». سبب الفشل لم يُقرأ.
2. **أسرار نصّاً واضحاً**: keys.json يحوي مفاتيح Gemini + توكن تليجرام، والسجل يطبع 8+4 أحرف من كل مفتاح.
3. **بيانات شخصية في مستودع عام**: أسماء أفراد العائلة داخل SYSTEM_PROMPT في config.py، وملفات توثيق بعناوين «سجل تفريغ الفويسات».
4. **لا حماية على أي فرع**، وفرع main لا يمثل الكود الفعلي.
5. مخاطر تشغيلية محتملة (غير مُقاسة): استهلاك ذاكرة الويب عند رفع متزامن، وانحراف توقيت تصفير الكوتا على Windows بلا tzdata.

**الحكم المختصر:** صالح للاستخدام الشخصي الحالي؛ غير جاهز كمستودع عام/قابل لإعادة النشر قبل معالجة P0 وP1.

---

## B. Project Inventory

- وحدات Python في الجذر (~37): main, config, key_manager, transcriber, telegram_bot, web_service, voice_queue, netsession, supervisor, store, text_assist, delivery, sessions, archive, backup, audit, observability, logkit, jobs, heartbeat, clock, watchdog, panel, fsm, assistant, prompts, glossary, settings, incidents, interactions_client, md_cache, model_state, preflight, transcribe_tool, smoke_failover.
- اختبارات: tests/ (≈38 ملف + conftest.py + fake_telegram.py)، وملف test_360_exhaustive.py **في الجذر خارج tests/**.
- CI: .github/workflows/ci.yml (596 بايت).
- توثيق ضخم: README.md (~39KB)، DEPLOY.md (~25KB)، architecture-v2/ (00–22)، audit/، Antigravity/، Arena-Ai-Agent/، genspark-review/، consolidated_review_report.md.
- ملفات غريبة في الجذر: «رابط جينيص genspark»، «رابط-تفريغ-شات-شوفو-ضروري»، Arena-Ai-Agent/1 (19 بايت)، ملفا .bat بأسماء عربية.
- لم تُقرأ: requirements.txt, requirements-dev.txt, pytest.ini, runtime.txt, VERSION, static/, tools/, محتوى tests/.

---

## C. Architecture Assessment

**سليم (مدعوم بالكود):** فصل واضح للمسؤوليات (netsession/store/key_manager/voice_queue/supervisor بلا import من المشروع)؛ store.py يطبق tmp→fsync→replace + قفل لكل مسار + عزل *.corrupt-؛ طابور موحّد للويب وتليجرام مع in_flight واحد.

| ID | الخطورة | الوصف | الدليل |
|---|---|---|---|
| ARCH-01 | MEDIUM | حالة عامة على مستوى الوحدة (text_assist._state, telegram_bot.STATE, web_service._rl_hits) تُحدَّث من خيوط متعددة؛ بعضها بلا قفل. قابل للتسابق (لم يُثبت بتشغيل). | text_assist.py, telegram_bot.py |
| ARCH-02 | MEDIUM | text_assist.generate() يستدعي requests.post مباشرة لا netsession — بلا keepalive ولا session.close() عند الخطأ؛ حادثة الـ socket نصف المفتوح ما زالت ممكنة في هذا المسار. | text_assist.py::generate مقابل netsession.py |
| ARCH-03 | LOW | استيرادات دائرية مؤجلة: web_service._transcribe و delivery.finalize يستوردان telegram_bot داخل الدالة — اقتران مخفي. | web_service.py, delivery.py |
| ARCH-04 | LOW | config.py ينفّذ _load_dotenv ويحسب WEB_PORT=int(...) عند الاستيراد — SERVER_PORT غير رقمي يُسقط العملية بـ ValueError. | config.py |
| ARCH-05 | INFO | تعقيد عالٍ في transcriber.transcribe_audio_bytes_ex (closures متداخلة). | transcriber.py |

---

## D. Confirmed Bugs

| ID | الخطورة | الحالة | الملف | الوصف | الحل المقترح |
|---|---|---|---|---|---|
| BUG-01 | MEDIUM (مشروط) | VERIFIED بالقراءة | key_manager.py::_tz | فشل ZoneInfo (Windows بلا tzdata / اسم خاطئ) يسقط بصمت إلى UTC → تصفير الكوتا يُحسب بـ UTC بدل Pacific (انحراف 7–8 ساعات). | تحذير واضح + tzdata في المتطلبات. |
| BUG-02 | LOW | VERIFIED | key_manager.py::_resolve | key.startswith(target): كل المفاتيح تبدأ بـ AIza فـ /remove AIza يصيب أول مفتاح. | حد أدنى لطول البادئة أو رفض التطابق المتعدد. |
| BUG-03 | LOW | VERIFIED | web_service.py::_authorized | التوكن يُقبل عبر ?token= → يبقى في access logs/Referer. | Header/Cookie فقط أو POST /api/login. |
| BUG-04 | MEDIUM | VERIFIED، الأثر غير مُقاس | web_service.py | كل رفع يُقرأ كاملاً في الذاكرة (حتى 25MB) + نسخ في parse_multipart + base64؛ ThreadingHTTPServer بلا سقف اتصالات و30 طلب/دقيقة/IP → OOM محتمل على 512MB. | Semaphore لمسار الرفع + 503 عند الامتلاء. |
| BUG-05 | LOW | VERIFIED | web_service.py::_authorized | تجاوز localhost بالاستدلال: proxy محلي لا يضيف X-Forwarded-For مع TRUST_PROXY=0 → كل الطلبات «محلية» → تجاوز التوكن. | متغير صريح WEB_LOCALHOST_BYPASS. |
| BUG-06 | LOW | VERIFIED | delivery.py::Sender | except TypeError واسع يبتلع أخطاء حقيقية ويعيد التنفيذ (إرسال مزدوج محتمل). | inspect.signature بدل try/except. |

لم يُثبت: race conditions فعلية، تسرب ذاكرة، أخطاء تاريخ — تحتاج تشغيلاً.

---

## E. Security Findings

| ID | الخطورة | الحالة | المرجع | الوصف |
|---|---|---|---|---|
| SEC-01 | HIGH | VERIFIED | key_manager.py::save | مفاتيح Gemini وتوكن تليجرام نصّاً واضحاً في keys.json (0600 + ذري + gitignore تخفيفات جيدة؛ الخطر عبر النسخ/لقطات القرص/اللوحة). |
| SEC-02 | HIGH | VERIFIED | key_manager.py::_merge_env_keys | طباعة k[:8]...k[-4:] في stdout — 8 أحرف إنتروبيا مكشوفة في السجلات. |
| SEC-03 | MEDIUM | VERIFIED | config.py::SYSTEM_PROMPT القاعدة 4 | أسماء أفراد العائلة مضمّنة في كود مستودع عام. |
| SEC-04 | MEDIUM (محتوى UNVERIFIED) | — | Antigravity/03_…, 07a_…, ملفا الروابط في الجذر | أسماء تشير لتفريغات شخصية وروابط شات خاصة في repo عام — لم يُقرأ المحتوى. |
| SEC-05 | MEDIUM | VERIFIED | ci.yml | لا secret scanning، لا pip-audit. |
| SEC-06 | MEDIUM | VERIFIED | GitHub API | كل الفروع بلا حماية. |
| SEC-07 | LOW | VERIFIED | web_service.py::_send_json | الكوكي تحمل التوكن نفسه لا جلسة مشتقة؛ Secure فقط عند اكتشاف HTTPS. |
| SEC-08 | INFO | VERIFIED | main.py::_startup_ping | التوكن المولَّد يُرسل نصّاً لشات المالك. |
| SEC-09 | UNVERIFIED | — | text_assist.py (مقصوص) | تنفيذ LOCAL_POLISH_CMD عبر subprocess — لم يُقرأ الاستدعاء الفعلي. |

لم تُنسخ أي أسرار حقيقية.

---

## F. Testing Assessment

| ID | الخطورة | الحالة | الوصف |
|---|---|---|---|
| QA-01 | HIGH | VERIFIED | آخر CI على A-test فاشل (run #327). السبب UNVERIFIED. |
| QA-02 | MEDIUM | VERIFIED | pytest -v بلا تغطية، بلا --strict-markers، بلا timeout. |
| QA-03 | MEDIUM | UNVERIFIED | test_360_exhaustive.py في الجذر — قد يُجمَع ويلمس الشبكة. |
| QA-04 | MEDIUM | UNVERIFIED | tests/test_live_dual_drafts.py «live» — يجب التأكد من تخطيه في CI. |
| QA-05 | INFO | VERIFIED جزئياً | fake_telegram.py + conftest.py + PYTEST_DISABLE_DOTENV = عزل جيد. |
| QA-06 | MEDIUM | بالاستدلال | لا اختبار لـ BUG-04 ولا BUG-01. |

لم يُشغَّل أي اختبار.

---

## G. Performance & Reliability

**مؤكد تصميمياً (جيد):** Session لكل خيط + max_retries=0 + keepalive + close عند الخطأ؛ سلم قواطع 5xx محفوظ على القرص؛ ميزانية 45ث مع تأجيل؛ المشرف يقتل شجرة العملية + backoff + حد 20/ساعة؛ _idle_loop بنبضة 1ث.

| ID | الخطورة | الوصف |
|---|---|---|
| REL-01 | MEDIUM | voice_queue._next_job في وضع الحجب يوقظ العامل كل ≤1ث طوال التأجيل (قد يكون ساعات). |
| REL-02 | MEDIUM | text_assist.generate بلا netsession (ARCH-02). |
| REL-03 | LOW | rate_limited ينظّف فقط عند >1000 مفتاح. |
| PERF-01 | UNVERIFIED | لا قياسات زمن/ذاكرة — لا حكم بلا benchmark. |

---

## H. Dependencies & CI/CD

| ID | الخطورة | الوصف |
|---|---|---|
| DEP-01 | UNVERIFIED | requirements*/runtime.txt لم تُقرأ. الاعتماديات الظاهرة من الكود: requests + stdlib. |
| CI-01 | MEDIUM | ci.yml: matrix 3.10/3.12، pyflakes+py_compile+pytest؛ لا pin، لا cache، لا بناء حزمة نشر، لا artifact، لا release. |
| CI-02 | MEDIUM | push على كل الفروع بلا concurrency. |
| CI-03 | HIGH | لا branch protection → CI ليس بوابة. |
| DEPLOY-01 | UNVERIFIED | DEPLOY.md و tools/pack_deploy.py لم يُقرآ؛ احتمال غياب tools/shelby_adapter.py من الحزمة (من تدقيق سابق). |

---

## I. Documentation Assessment

| ID | الخطورة | الوصف |
|---|---|---|
| DOC-01 | HIGH | تعارض: commits «636 tests / CI verification» بينما CI الحالي فاشل. |
| DOC-02 | MEDIUM | README ~39KB يخلط الدليل بالقرارات والحوادث؛ خمسة مواقع لتقارير تدقيق متكررة. |
| DOC-03 | LOW | .env.example ممتاز — حافظ عليه. |
| DOC-04 | LOW | أسماء ملفات عربية بمسافات في الجذر تكسر أدوات CLI/packaging. |

---

## J. Positive Findings (حافظ عليها)

1. store.py: كتابة ذرية + قفل لكل مسار + عزل التالف + probe_writable.
2. supervisor.py بلا import من المشروع.
3. netsession.py: Session لكل خيط، max_retries=0، keepalive، reset عند الخطأ.
4. تصنيف الأخطاء في transcriber وفصل قفل الموديل عن المفتاح.
5. key_manager: RLock واحد، حفظ ذري 0600، بصمة SHA-256 للمجموعات التلقائية.
6. web_service: _read_body_bounded صارم، hmac.compare_digest، /api/reset POST فقط.
7. الطابور الموحّد يسجّل كل خطوة في jobs.ndjson ويستأنف بعد إعادة التشغيل.
8. .gitignore شامل للحالة والأسرار والنسخ.
9. .env.example كوثيقة إعداد حقيقية.
10. حارس assert_not_transcription_model يمنع Lite في التفريغ.

---

## K. Technical Debt

- 30 فرعاً بلا حماية، main ميت فعلياً، لا tags/releases.
- خمسة مواقع لتقارير تدقيق + README ضخم.
- ملفات روابط/ملاحظات شخصية في الجذر.
- حالة عامة على مستوى الوحدات + مسار شبكة خارج السياسة.
- تعقيد transcribe_audio_bytes_ex.
- test_360_exhaustive.py خارج tests/.
- دعم keys.txt legacy.

---

## L. Improvement Opportunities (مرتبة)

| # | التحسين | المشكلة | التكلفة | التصنيف |
|---|---|---|---|---|
| 1 | إصلاح CI الأحمر + branch protection على A-test | QA-01, SEC-06 | منخفضة | ضروري |
| 2 | إزالة طباعة أجزاء المفاتيح | SEC-02 | دقائق | ضروري |
| 3 | نقل الأسماء الشخصية من SYSTEM_PROMPT إلى glossary.json غير متتبع | SEC-03 | منخفضة | ضروري |
| 4 | فحص الملفات المشبوهة وإزالتها من التاريخ إن لزم | SEC-04 | متوسطة | قرار مالك |
| 5 | توحيد text_assist على netsession | ARCH-02/REL-02 | منخفضة | ضروري |
| 6 | سقف اتصالات متزامنة لرفع الويب | BUG-04 | منخفضة | ضروري إن كان الويب عاماً |
| 7 | تحذير عند سقوط ZoneInfo + tzdata | BUG-01 | منخفضة | ضروري |
| 8 | pip-audit + gitleaks في CI | SEC-05 | منخفضة | تحسيني |
| 9 | دمج تقارير التدقيق في docs/audits/ | DOC-02 | متوسطة | تحسيني |
| 10 | تفكيك transcribe_audio_bytes_ex | ARCH-05 | عالية | اختياري |

---

## M. Risk Register

| الخطر | الشدة | الاحتمال | الحالة | التخفيف الحالي |
|---|---|---|---|---|
| تسريب مفاتيح/توكن من keys.json أو نسخة | HIGH | متوسط | مفتوح | 0600، gitignore، /backup بلا أسرار |
| نشر كود مكسور لأن CI ليس بوابة | HIGH | مرتفع (يحدث الآن) | مفتوح | لا شيء |
| كشف بيانات أسرية في repo عام | MEDIUM | مؤكد/محتمل | مفتوح | لا شيء |
| OOM من رفع ويب متزامن | MEDIUM | منخفض خلف توكن | نظري | rate limit/IP |
| انحراف تصفير الكوتا (TZ) | MEDIUM | متوسط على Windows | نظري | لا شيء |
| socket معلّق في مسار التنظيم | LOW–MEDIUM | منخفض | نظري | timeout |
| انحراف الفروع (main قديم) | MEDIUM | مؤكد | مفتوح | لا شيء |
| shelby_adapter خارج الحزمة | UNKNOWN | — | UNVERIFIED | — |

---

## N. Final Engineering Verdict

**تم التحقق:** الشجرة الكاملة، الفروع، PRs، آخر CI، وقراءة (جزئية) لـ 16 ملفاً: config, main, key_manager, transcriber, telegram_bot, web_service, voice_queue, netsession, supervisor, store, text_assist, delivery, .env.example, .gitignore, ci.yml, README + تقرير تدقيق سابق مضمّن.

**لم يتم:** تشغيل الاختبارات، لوج CI، requirements*/pytest.ini/runtime.txt/tools/static/tests/*، ومعظم وحدات المرحلة 4–7.

**الحكم:** هندسة الاعتمادية أعلى من المتوسط، لكن حوكمة المستودع (CI أحمر بلا بوابة، فروع بلا حماية، أسرار وبيانات شخصية) هي الحلقة الأضعف. لا حكم «جاهز للإنتاج» قبل: معرفة سبب فشل CI، قراءة الاعتماديات، تشغيل الاختبارات محلياً.

**لا تنفيذ ولا تعديل كود حتى موافقة صريحة GO من المالك.**
