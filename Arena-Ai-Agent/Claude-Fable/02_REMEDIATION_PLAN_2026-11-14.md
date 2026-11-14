# خطة الإصلاح والاختبارات وقرارات المالك — Gemini-3.8-Flash (A-test) — الجزء 2/2

- تاريخ: 2026-11-14 — المدقق: Claude Fable — وضع READ-ONLY
- الجزء 1 (التقرير الكامل A–N): `01_AUDIT_REPORT_2026-11-14.md`
- HEAD وقت الفحص: `7ebb2ad6219b6e40f0215799e71eb5ce25f10349`
- هذه خطة مرتبة **بلا تنفيذ**. لا تعديل كود حتى موافقة صريحة GO من المالك.

---

## 1. MASTER REMEDIATION PLAN

### P0 — حرج

| Task | المشكلة | الملفات | التغيير المقترح | الاعتماد | الاختبار | معايير القبول | خطر التراجع |
|---|---|---|---|---|---|---|---|
| P0-1 | QA-01 (CI فاشل) | حسب اللوج | قراءة لوج run #327 وإصلاح السبب الجذري | — | pytest كامل على 3.10 و3.12 | CI أخضر على HEAD | حسب السبب |
| P0-2 | SEC-02 | key_manager.py::_merge_env_keys | استبدال `k[:8]...k[-4:]` ببصمة sha256[:8] أو الاسم فقط | — | اختبار يلتقط stdout ويتحقق من غياب أي 8 أحرف متتالية من المفتاح | لا جزء من المفتاح في أي print/log | لا شيء |
| P0-3 | SEC-03 | config.py, prompts.py | نقل القاعدة 4 (أسماء العائلة) إلى glossary.json (غير متتبع) وحقنها كتلميح في البرومبت | — | test_pipeline_phase3 + اختبار جديد للحقن | SYSTEM_PROMPT خالٍ من أسماء شخصية | تدهور دقة الأسماء إن لم يُحقن البديل |
| P0-4 | SEC-06 / CI-03 | إعدادات GitHub | حماية A-test: require CI + no force-push + PR للتغييرات | P0-1 | — | push مباشر مرفوض | لا شيء |

### P1 — عالي

| Task | المشكلة | الملفات | التغيير | الاختبار | القبول |
|---|---|---|---|---|---|
| P1-1 | SEC-04 | Antigravity/03_, 07a_, ملفا الروابط في الجذر | فحص المحتوى؛ نقل خارج المستودع أو إعادة كتابة التاريخ (قرار مالك) | — | لا محتوى شخصي/روابط خاصة في الشجرة |
| P1-2 | ARCH-02 / REL-02 | text_assist.py | requests.post → netsession.post بنفس timeout | mock فشل شبكة → netsession.stats()["sessions_closed"] +1 | كل نداءات HTTP عبر netsession (grep) |
| P1-3 | BUG-01 | key_manager.py::_tz, requirements.txt | تحذير صريح عند سقوط ZoneInfo + إضافة tzdata | monkeypatch ZoneInfo يرفع → تحذير مطبوع | — |
| P1-4 | BUG-04 | web_service.py | threading.BoundedSemaphore(N) حول مسار الرفع؛ 503 عند الامتلاء | 10 رفعات متزامنة 20MB (mock transcribe) → ≤N في الذاكرة | لا OOM، 503 مفهوم |
| P1-5 | SEC-05 | ci.yml | pip-audit + gitleaks + pytest --timeout + concurrency | CI | — |

### P2 — جودة واعتمادية

- **P2-1** BUG-02: حد أدنى لطول بادئة /remove (≥8) + رفض التطابق المتعدد. اختبار: بادئة AIza → خطأ «غامض».
- **P2-2** BUG-03 / SEC-07: إلغاء ?token= أو جعله لمرة واحدة بإعادة توجيه 302 بلا query؛ كوكي بقيمة مشتقة (HMAC) لا التوكن نفسه.
- **P2-3** BUG-05: متغير صريح WEB_LOCALHOST_BYPASS=0/1 بدل الاستدلال.
- **P2-4** BUG-06: inspect.signature مرة واحدة بدل except TypeError.
- **P2-5** QA-03: نقل test_360_exhaustive.py إلى tests/ أو تقييده بـ marker live.
- **P2-6** ARCH-01: تغليف telegram_bot.STATE بقفل أو dataclass بقفل داخلي.

### P3 — أداء وبنية

- **P3-1** REL-01: في وضع الحجب استخدام cv.wait(ready_at - now) كاملاً مع notify عند skip/stop بدل نبضة 1ث.
- **P3-2** ARCH-05: تفكيك transcribe_audio_bytes_ex إلى state machine صغيرة (كل اختبارات test_failover_v2 يجب أن تبقى خضراء).
- **P3-3** CI-01: خطوة CI تبني deploy_ready.zip عبر tools/pack_deploy.py وتتحقق منه كـ artifact.

### P4 — اختياري

- **P4-1** DOC-02: دمج تقارير التدقيق في docs/audits/<date>-<source>.md مع حالة (ساري/مؤرشف).
- **P4-2** DOC-04: إعادة تسمية ملفات الجذر العربية ذات المسافات.
- **P4-3** حذف دعم keys.txt legacy بعد تأكيد الهجرة.
- **P4-4** حذف الفروع المدمجة (≈25 فرعاً) أو أرشفتها بـ tags.

---

## 2. قائمة الاختبارات المطلوبة (جديدة)

1. `test_no_key_fragments_in_logs` (P0-2)
2. `test_prompt_has_no_personal_names` + `test_glossary_names_injected` (P0-3)
3. `test_text_assist_uses_netsession_and_resets_on_error` (P1-2)
4. `test_tz_fallback_warns` (P1-3)
5. `test_web_upload_concurrency_cap_returns_503` (P1-4)
6. `test_resolve_rejects_short_or_ambiguous_prefix` (P2-1)
7. `test_query_token_not_persisted_in_cookie_plain` (P2-2)
8. `test_sender_typeerror_inside_callable_propagates` (P2-4)

---

## 3. قرارات مطلوبة من المالك

1. **أي فرع هو «الإنتاج»؟** A-test أم main؟ (يحدد أين تُطبَّق الحماية وأي فرع يُحذف).
2. **هل يُعاد كتابة تاريخ Git** لإزالة الأسماء الشخصية/السجلات/الروابط الخاصة، أم يكفي حذفها من HEAD؟ (إعادة الكتابة تكسر أي clone موجود).
3. **هل الويب مكشوف فعلاً على الإنترنت؟** (يحدد أولوية BUG-04 / SEC-07 / BUG-05).
4. **هل تُشغَّل المنظومة على Windows في بيئة إنتاجية؟** (يحدد أولوية BUG-01).
5. هل تقبل سقفاً صريحاً لرفع الويب المتزامن (مثلاً 3) وما القيمة؟
6. هل تريد الاحتفاظ بالـ 25 فرعاً المدمجة أم حذفها بعد وضع tags؟
7. **ما سبب فشل CI #327؟** يحتاج قراءة اللوج أو تشغيلاً محلياً.

---

## 4. شروط اكتمال التدقيق — بيان النطاق الصريح

- **فُحص:** شجرة الملفات الكاملة (API)، الفروع، PRs، آخر 5 تشغيلات CI، و16 ملفاً (config, main, key_manager, transcriber, telegram_bot, web_service, voice_queue, netsession, supervisor, store, text_assist, delivery, .env.example, .gitignore, ci.yml, README) — معظمها وصل مقصوصاً عند ~8–12KB — إضافة إلى Arena-Ai-Agent/audit-report-2026-11-14/AUDIT_REPORT.md.
- **لم يُفحص ولماذا:** requirements*, pytest.ini, runtime.txt, VERSION, tools/*, static/*, محتوى tests/*, لوج CI، وبقية وحدات المرحلة 4–7 (sessions, panel, assistant, backup, observability, logkit, jobs, archive, glossary, md_cache, interactions_client, fsm, incidents, preflight, watchdog, heartbeat, clock, audit, settings, prompts, model_state) — بسبب فشل القراءة الخام ثم بلوغ حد استدعاءات الأداة.
- **اختبارات شُغّلت:** لا شيء.
- **أدوات تعذّر استخدامها:** قراءة لوجات GitHub Actions، أي تنفيذ محلي.
- **نتائج مؤكدة:** كل ما وُسم VERIFIED في الجزء 1.
- **نتائج محتملة:** كل ما وُسم UNVERIFIED أو «الأثر غير مُقاس».
- **يحتاج صلاحيات/بيئة إضافية:** لوج CI، تشغيل pytest، قياس الذاكرة تحت حمل.
- **القيود التي تمنع حكماً نهائياً:** الثلاثة أعلاه.

**لا تنفيذ، لا تعديل كود، لا إصلاح — حتى موافقة صريحة بكلمة GO من المالك.**
