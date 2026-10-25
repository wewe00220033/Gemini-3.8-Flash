# -*- coding: utf-8 -*-
"""
المرحلة 4 — الجلسات والتسمية والسياق (08 §7 + 10 §7) + إصلاحات مراجعة المرحلة 3.
كل الاختبارات بلا شبكة: text_assist/requests.post محجوب في conftest، التسمية stub، Gemini mock عبر requests.post.
"""
import importlib
import json
import os
import pytest

VALID_A = "AIzaSy" + "A" * 33
M1, M2 = "gemini-3.6-flash", "gemini-3.5-transcribe"
LONG_RAW = "فتحت Docker و Nginx على السيرفر النهارده وضبطت الإعدادات كلها بعد ما شغلت الحاوية"


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(payload or {})
        self.headers = {}

    def json(self):
        return self._payload


def ok_text(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


@pytest.fixture
def S(fresh_env):
    import sessions
    sessions.reset_cache()
    return sessions


# =====================================================================
# 08 — دورة حياة الجلسة
# =====================================================================

def test_start_creates_files_and_sets_current(S):
    s = S.start()
    assert s["status"] == "active" and S.current_id() == s["id"] and s["name"].startswith("جلسة ")
    assert ":" not in s["name"]                                            # اسم آمن لأنظمة الملفات (ويندوز)
    assert os.path.exists(S.session_file(s["id"])) and os.path.exists(S.index_path())
    head = open(S.session_file(s["id"]), encoding="utf-8").read()
    assert head.startswith("---\n") and f"id: {s['id']}" in head and "Verbatim Transcription" in head


def test_no_session_by_default_and_append_returns_false(S):
    assert S.current() is None and S.current_name() is None
    assert S.append_entry("نص", model="m") is False                       # 08 §2: لا جلسة تلقائية


def test_dual_write_entry_polished_title_and_count(S):
    import archive
    s = S.start("إعداد Nginx")
    assert S.append_entry("خام أول", model=M1, elapsed=2.0, key_name="A")
    assert S.append_polished("منظم أول") and not S.append_polished("منظم ثانٍ")   # بلا تكرار
    assert S.append_title("عنوان أول") and not S.append_title("عنوان ثانٍ")
    assert S.current()["count"] == 1
    e = S.entries(s["id"])
    assert len(e) == 1 and e[0]["raw"] == "خام أول" and e[0]["polished"] == "منظم أول" and e[0]["title"] == "عنوان أول"
    assert "جلسة: «إعداد Nginx»" in e[0]["meta"] and "3.6-flash" in e[0]["meta"]
    archive.append("خام أول", model=M1, session_name="إعداد Nginx")
    assert "جلسة: «إعداد Nginx»" in archive.last()["meta"]                 # نفس الصيغة في ALL.md


def test_end_resume_persist_across_restart(S):
    s = S.start("أ")
    S.append_entry("1", model="m")
    S.reset_cache()                                                         # محاكاة restart (08 §7.2)
    assert S.current_id() == s["id"]
    assert S.append_entry("2", model="m") and S.get(s["id"])["count"] == 2
    e = S.end()
    assert e["status"] == "ended" and e["ended"] and S.current() is None
    assert S.end() is None
    r = S.resume(s["id"][:4])                                              # بادئة المعرّف تكفي
    assert r and r["status"] == "active" and S.current_id() == s["id"]
    S.reset_cache()
    assert S.current_id() == s["id"]


def test_one_active_session_at_a_time(S):
    a = S.start("أ")
    b = S.start("ب")
    assert S.get(a["id"])["status"] == "ended" and S.current_id() == b["id"]
    S.resume(a["id"])
    assert S.get(b["id"])["status"] == "ended" and S.current_id() == a["id"]


def test_rename_sanitizes_and_dedups(S):
    S.start("أ")
    assert S.rename('إعداد/Nginx:*?"<>|' + "x" * 100).startswith("إعداد Nginx")
    assert len(S.current()["name"]) <= S.Config.SESSION_NAME_MAX
    S.end()
    S.start("إعداد Nginx x" + "x" * 99)
    assert S.current()["name"].endswith("(2)")
    assert S.rename("   ") is None


def test_merge_archives_originals_and_orders_chronologically(S):
    a = S.start("أ")
    S.append_entry("نص أ", model="m")
    S.end()
    b = S.start("ب")
    S.append_entry("نص ب", model="m")
    S.end()
    m = S.merge(a["id"], b["id"])
    assert m and m["count"] == 2 and m["status"] == "ended" and m["merged_from"] == [a["id"], b["id"]]
    assert S.get(a["id"])["status"] == "archived" and S.get(b["id"])["status"] == "archived"
    assert [e["raw"] for e in S.entries(m["id"])] == ["نص أ", "نص ب"]
    assert os.path.exists(S.session_file(a["id"]))                        # لا حذف أبداً (08 §6)
    assert S.resume(a["id"]) is None                                       # المؤرشفة لا تُستأنف
    assert S.merge(a["id"], a["id"]) is None and S.merge("zzzz", b["id"]) is None
    names = [s["name"] for s in S.list_sessions()]
    assert m["name"] in names and a["name"] not in names
    assert len(S.list_sessions(include_archived=True)) == 3


def test_index_rebuilt_from_files_when_deleted_or_corrupt(S):
    a = S.start("الأولى")
    S.append_entry("1", model="m")
    S.append_entry("2", model="m")
    S.rename("مُسمّاة")
    S.end()
    b = S.start("الثانية")
    os.remove(S.index_path())
    S.reset_cache()
    lst = {s["id"]: s for s in S.list_sessions()}
    assert lst[a["id"]]["name"] == "مُسمّاة" and lst[a["id"]]["count"] == 2 and lst[a["id"]]["status"] == "ended"
    assert lst[b["id"]]["status"] == "active" and S.current_id() == b["id"]
    with open(S.index_path(), "w", encoding="utf-8") as f:
        f.write("{ broken")
    S.reset_cache()
    assert {s["id"] for s in S.list_sessions()} == {a["id"], b["id"]}
    assert any(fn.startswith("sessions_index.json.") for fn in os.listdir(S.sessions_dir()))   # التالف معزول


def test_export_bytes_and_list_text_and_card(S):
    s = S.start("تصدير")
    for i in range(7):
        S.append_entry(f"نص {i}", model="m")
    data = S.export_bytes(s["id"])
    assert data and data.decode("utf-8").count("## 🎙️ ") == 7
    txt = S.list_text()
    assert "🟢" in txt and "(7 فويس)" in txt and s["id"] in txt
    assert S.summary_card().startswith("📂 تصدير — 7 فويس")
    assert S.export_bytes("nope") is None
    assert [e["raw"] for e in S.last_entries(3)] == ["نص 4", "نص 5", "نص 6"]
    S.end()
    assert "📋" in S.list_text(1, status="ended")
    assert S.list_text(1, status="active").startswith("لا توجد جلسات")


def test_stats_text(S):
    assert "لا جلسة نشطة" in S.stats_text()
    S.start("إحصاء")
    S.append_entry("x", model="m")
    t = S.stats_text()
    assert "«إحصاء»" in t and "السياق: full" in t


# =====================================================================
# 08 §3 — التسمية الذكية
# =====================================================================

def test_propose_accept_silent_and_manual_wins(S):
    s = S.start()
    assert S.needs_title_suggestion() is False                             # لا فويس بعد
    S.append_entry(LONG_RAW, model="m")
    assert S.needs_title_suggestion() is True
    t = S.propose_title(LONG_RAW, generate_fn=lambda sys_t, usr_t: ("«إعداد Nginx: جديد»\nسطر زائد", None))
    assert t == "إعداد Nginx جديد" and S.current()["pending_title"] == t
    assert S.needs_title_suggestion() is False                             # اقتراح معلّق موجود
    assert S.accept_pending_title() == "إعداد Nginx جديد" and S.current()["name"] == "إعداد Nginx جديد"
    assert S.accept_pending_title() is None
    assert S.needs_title_suggestion() is False                             # صارت مسمّاة
    S.propose_title(LONG_RAW, generate_fn=lambda a, b: ("اقتراح ثانٍ", None))
    assert S.rename("يدوي") == "يدوي" and S.current()["pending_title"] is None   # اليدوي يغلب (08 §7.3)
    assert s["id"] == S.current_id()


def test_propose_title_rejects_bad_outputs_and_named_sessions(S):
    S.start()
    S.append_entry(LONG_RAW, model="m")
    assert S.propose_title(LONG_RAW, generate_fn=lambda a, b: (None, "فشل")) is None
    assert S.propose_title(LONG_RAW, generate_fn=lambda a, b: ("كلمة " * 9, None)) is None   # طويل جداً
    assert S.propose_title("قصير جداً", generate_fn=lambda a, b: ("x", None)) is None
    assert S.reject_pending_title() is True and S.current()["pending_title"] is None
    S.start("مسمّاة")
    S.append_entry(LONG_RAW, model="m")
    assert S.needs_title_suggestion() is False


def test_voice_title_is_text_only_and_sanitized(S, monkeypatch):
    import text_assist
    seen = {}

    def fake_generate(system_text, user_text, timeout=None):
        seen["sys"], seen["user"] = system_text, user_text
        return "  «إعداد Nginx على السيرفر»  ", None

    real = importlib.reload(S).voice_title                                 # conftest تضع stub؛ نسترجع الحقيقية
    monkeypatch.setattr(S, "Config", __import__("config").Config)
    monkeypatch.setattr(text_assist, "generate", fake_generate)
    assert real(LONG_RAW) == "إعداد Nginx على السيرفر"
    assert "عنوان" in seen["sys"] and LONG_RAW.split()[0] in seen["user"]
    assert real("قصير") is None
    monkeypatch.setattr(S.Config, "SMART_TITLE_ENABLED", False)
    assert real(LONG_RAW) is None


# =====================================================================
# 10 — السياق L0/L1 + الصدى + 2-strike (وحدة الجلسات)
# =====================================================================

def test_context_block_respects_last_n_and_max_chars(S):
    assert S.context_block() == ""                                         # لا جلسة
    S.start("سياق")
    for i in range(4):
        S.append_entry(f"فويس رقم {i} " + ("كلمة " * 300), model="m")
    l1 = S.context_block()
    assert 0 < len(l1) <= S.Config.CONTEXT_MAX_CHARS                      # 10 §7.4
    assert "فويس رقم 3" in l1 and "فويس رقم 1" not in l1                  # آخر N=2؛ الأحدث له الأولوية
    assert S.context_prompt_text().startswith("سياق سابق")
    assert S.context_mode() == "full"


def test_session_terms_extracted_locally(S):
    S.start("مصطلحات")
    S.append_entry("ضبطت Nginx و Docker و Nginx تاني مع Kubernetes", model="m")
    terms = S.session_terms()
    assert terms[0] == "Nginx" and "Docker" in terms and "Kubernetes" in terms
    assert len(S.session_terms(limit=2)) == 2


def test_echo_detection(S):
    assert S.is_context_echo(LONG_RAW + " وخلاص", LONG_RAW) is True
    assert S.is_context_echo("كلام جديد تماماً عن موضوع آخر خالص لا علاقة له", LONG_RAW) is False
    assert S.is_context_echo("قصير جداً", LONG_RAW) is False                # < 6 كلمات لا تُحتسب
    assert S.is_context_echo(LONG_RAW, "") is False


def test_strikes_simple_mode_and_cooldown(S):
    S.start("ضربات")
    S.append_entry(LONG_RAW, model="m")
    assert S.record_strike("BLOCKED") == 1 and S.context_mode() == "full"
    assert S.record_strike("echo") == 2 and S.context_mode() == "simple" and S.context_block() == ""
    assert S.current()["simple_left"] == S.Config.SIMPLE_MODE_COOLDOWN
    for _ in range(S.Config.SIMPLE_MODE_COOLDOWN):
        S.record_success(used_context=False)
    assert S.context_mode() == "full" and S.current()["strikes"] == 0
    assert S.set_context_enabled(False) and S.context_mode() == "simple" and S.context_block() == ""
    assert S.set_context_enabled(True) and S.context_mode() == "full"
    S.end()
    assert S.set_context_enabled(True) is False and S.context_mode() == "off"


def test_context_disabled_globally(S, monkeypatch):
    S.start("x")
    S.append_entry(LONG_RAW, model="m")
    monkeypatch.setattr(S.Config, "CONTEXT_ENABLED", False)
    assert S.context_block() == "" and S.context_mode() == "off"


def test_backup_includes_sessions_dir(S, monkeypatch):
    import backup
    monkeypatch.setattr(backup, "Config", S.Config)                        # backup يحمل مرجعاً قديماً بعد reload(config)
    S.start("ن")
    assert any(p.endswith("sessions") for p in backup.data_roots())


# =====================================================================
# 10 §7.1-3 — التكامل مع المحرك (2-strike داخل transcribe_audio_bytes_ex)
# =====================================================================

@pytest.fixture
def engine(fresh_env, monkeypatch):
    import model_state
    importlib.reload(model_state)
    import transcriber
    importlib.reload(transcriber)
    transcriber.key_manager = fresh_env.key_manager
    transcriber.model_circuit = model_state.model_circuit
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)
    fresh_env.key_manager.add_key(VALID_A, "A")
    return transcriber


def _install_post(monkeypatch, engine, handler):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        call = {"url": url, "headers": headers or {}, "json": json}
        calls.append(call)
        return handler(call, len(calls))

    monkeypatch.setattr(engine.requests, "post", fake_post)
    return calls


def _prompt_of(call):
    return call["json"]["contents"][0]["parts"][1]["text"]


def _has_ctx(call):
    return "سياق سابق" in _prompt_of(call)


def test_engine_injects_l1_only_inside_active_session(engine, S, monkeypatch):
    calls = _install_post(monkeypatch, engine, lambda c, n: ok_text("نص جديد عن حاجة تانية خالص مختلفة"))
    engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert not _has_ctx(calls[0])
    S.start("س")
    S.append_entry(LONG_RAW, model="m")
    text, model, key, info = engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert _has_ctx(calls[1]) and info["context"] == "full" and info["strikes"] == 0
    assert LONG_RAW.split()[1] in _prompt_of(calls[1])
    assert len(_prompt_of(calls[1])) - len(engine.transcription_prompt()) <= engine.Config.CONTEXT_MAX_CHARS + 120


def test_engine_two_blocked_then_third_without_l1(engine, S, monkeypatch):
    S.start("س")
    S.append_entry(LONG_RAW, model="m")

    def handler(c, n):
        if n <= 2:
            return FakeResp(400, {"error": {"message": "request payload too large"}})
        return ok_text("نص جديد تماماً عن الكوبرنيتس والنشر")

    calls = _install_post(monkeypatch, engine, handler)
    text, model, key, info = engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert text.startswith("نص جديد") and model == M1
    assert [_has_ctx(c) for c in calls] == [True, True, False]              # 10 §7.1: الثالثة بلا L1
    assert info["context"] == "simple" and info["strikes"] == 2
    assert S.current()["strikes"] == 0 and S.context_mode() == "simple"     # نجاح يصفّر؛ الكولداون ساري


def test_engine_echo_counts_as_strike_and_retries(engine, S, monkeypatch):
    S.start("س")
    S.append_entry(LONG_RAW, model="m")
    calls = _install_post(monkeypatch, engine,
                          lambda c, n: ok_text(LONG_RAW + " وخلاص") if n == 1 else ok_text("كلام جديد مختلف عن الأمس تماماً خالص"))
    text, _m, _k, info = engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert text.startswith("كلام جديد") and len(calls) == 2 and info["strikes"] == 1
    assert _has_ctx(calls[1])                                               # ضربة واحدة → إعادة بالسياق


def test_engine_503_is_not_a_strike(engine, S, monkeypatch):
    S.start("س")
    S.append_entry(LONG_RAW, model="m")
    calls = _install_post(monkeypatch, engine,
                          lambda c, n: FakeResp(503, {"error": {"message": "overloaded"}}) if n == 1 else ok_text("نص عادي جديد مختلف تماماً"))
    text, model, _k, info = engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert model == M2 and info["strikes"] == 0 and S.current()["strikes"] == 0    # 10 §7.2
    assert _has_ctx(calls[1])                                                       # السياق باقٍ على الموديل التالي


def test_engine_saver_mode_disables_l1(engine, S, monkeypatch):
    S.start("س")
    S.append_entry(LONG_RAW, model="m")
    monkeypatch.setattr(engine.key_manager, "quota_mode", lambda: "saver")
    calls = _install_post(monkeypatch, engine, lambda c, n: ok_text("نص عادي جديد مختلف تماماً خالص"))
    engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert not _has_ctx(calls[0])


# =====================================================================
# التكامل مع delivery + تليجرام (+ إصلاحات مراجعة م3)
# =====================================================================

@pytest.fixture
def bot(fresh_env, S, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent = []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append({"method": url.rsplit("/", 1)[-1], "payload": json, "kw": kw})
        return FakeResp(200, {"ok": True, "result": {"message_id": 500 + len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    monkeypatch.setattr(telegram_bot, "_download_file", lambda t, f: b"abc")
    return telegram_bot, sent


def _texts(sent):
    return [x["payload"].get("text", "") for x in sent if x["method"] == "sendMessage"]


def _voice_job(uid):
    import voice_queue
    import jobs
    msg = {"message_id": 5, "chat": {"id": 1}, "date": 1, "voice": {"file_id": "f" + uid, "file_unique_id": uid}}
    job = voice_queue.new_telegram_job("1", msg, "f" + uid, "audio/ogg", "voice")
    job.meta["token"] = "T"
    jobs.record(job.job_id, "received", **job.journal_fields())            # ما يكتبه submit() عادة (للـ dedup)
    return job


def test_deliver_writes_session_and_title_and_suggests_name(S, fresh_env):
    import delivery
    import archive
    s = S.start()
    msgs = []
    sender = delivery.Sender(lambda t, r, p: msgs.append((t, r)) or len(msgs))
    res = delivery.deliver(LONG_RAW, M1, "A", {"elapsed": 1.0}, sender, reply_to_id=1,
                           polish_fn=lambda raw, role, gl: ("منظم", None), title_fn=lambda raw: "إعداد Nginx")
    assert res["session"] == s["name"] and res["session_written"] and res["title"] == "إعداد Nginx"
    assert res["title_suggestion"] == "إعداد Nginx" and S.current()["pending_title"] == "إعداد Nginx"
    e = S.entries(s["id"])[0]
    assert e["title"] == "إعداد Nginx" and e["polished"] == "منظم" and e["raw"] == LONG_RAW
    assert archive.last()["title"] == "إعداد Nginx" and "جلسة: «" in archive.last()["meta"]
    assert "🏷️ <b>إعداد Nginx</b>" in msgs[0][0] and f"📂 {s['name']}" in msgs[0][0]
    S.end()                                                                 # بلا جلسة → ALL.md فقط (08 §7.1)
    res2 = delivery.deliver("نص آخر طويل بما يكفي ليحمل عنواناً مفيداً هنا", M1, "A", {}, sender,
                            polish_fn=lambda *a: (None, "x"), title_fn=lambda raw: None)
    assert res2["session"] is None and res2["session_written"] is False and res2["title"] is None
    assert "بدون جلسة" in archive.last()["meta"]


def test_process_job_silent_accept_then_dedup_via_pipeline(bot, S, monkeypatch):
    tb, sent = bot
    import jobs
    S.start()
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex", lambda b, m: (LONG_RAW, M1, "A", {"elapsed": 1.5, "skipped": []}))
    monkeypatch.setattr(S, "voice_title", lambda raw, generate_fn=None: "عنوان الجلسة")
    tb.process_job(_voice_job("u1"))
    assert any("اسم مقترح للجلسة" in t for t in _texts(sent))
    assert S.current()["pending_title"] == "عنوان الجلسة"
    tb.process_job(_voice_job("u2"))                                       # فويس تالٍ → قبول صامت
    assert S.current()["name"] == "عنوان الجلسة" and S.current()["count"] == 2
    n = len(sent)
    r = tb.process_job(_voice_job("u2"))                                   # مكرر → خط الإنتاج بلا أرشفة ثانية
    assert r["dedup"] is True and r["polished"]
    new = _texts(sent[n:])
    assert any("♻️ مكرر" in t and "<pre>" in t for t in new) and any("[منظم-اختبار]" in t for t in new)
    assert S.current()["count"] == 2                                       # لا مدخل جديد للمكرر
    st = [j for j in jobs.load_state().values() if j.get("dedup_of")]
    assert st and st[0]["polished_text"].startswith("[منظم-اختبار]")


def test_process_job_simple_mode_footer(bot, S, monkeypatch):
    tb, sent = bot
    S.start("x")
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex",
                        lambda b, m: ("نص", M1, "A", {"elapsed": 1.0, "skipped": [], "context": "simple", "strikes": 2}))
    tb.process_job(_voice_job("u9"))
    assert any("⚙️ بسيط" in t for t in _texts(sent))


def test_polish_reads_replied_document(bot, monkeypatch):
    tb, sent = bot
    monkeypatch.setattr(tb, "_download_file", lambda t, f: "نص من الملف الطويل".encode("utf-8"))
    tb.handle_text("T", "1", {"message_id": 3, "reply_to_message": {"document": {"file_id": "d1"}}}, "/polish")
    assert any("[منظم-اختبار] نص من الملف الطويل" in t for t in _texts(sent))


def test_session_commands_smoke(bot, S):
    tb, sent = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    tb.handle_text("T", "1", {"message_id": 1}, "/end")
    assert "لا جلسة نشطة" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/new_session")
    assert "بدأت جلسة" in last() and "اسم مؤقت" in last()
    sid = S.current_id()
    S.append_entry("نص الفويس الأول", model=M1)
    tb.handle_text("T", "1", {"message_id": 1}, "/rename_session إعداد Nginx")
    assert S.current()["name"] == "إعداد Nginx"
    tb.handle_text("T", "1", {"message_id": 1}, "/stats")
    assert "«إعداد Nginx»" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/context")
    assert "full" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/context off")
    assert S.context_mode() == "simple"
    tb.handle_text("T", "1", {"message_id": 1}, "/status")
    assert "الجلسة النشطة" in last() and "simple" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/end")
    assert "اتحفظت" in last() and f"/export {sid}" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/sessions")
    assert "(1 فويس)" in last()
    tb.handle_text("T", "1", {"message_id": 1}, f"/resume {sid}")
    assert "نص الفويس الأول" in last() and S.current_id() == sid
    tb.handle_text("T", "1", {"message_id": 1}, "/resume zzzz")
    assert "غير موجودة" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/export")
    doc = [x for x in sent if x["method"] == "sendDocument"]
    assert doc and doc[-1]["kw"]["files"]["document"][0] == f"session_{sid}.md"
    tb.handle_text("T", "1", {"message_id": 1}, "/new_session ثانية")
    sid2 = S.current_id()
    tb.handle_text("T", "1", {"message_id": 1}, f"/merge_sessions {sid} {sid2} مدموجة")
    assert "دُمجتا في «مدموجة»" in last()
    tb.handle_text("T", "1", {"message_id": 1}, "/help")
    assert "/new_session" in last() and "/context" in last()


def test_export_zips_large_session(bot, S, monkeypatch):
    tb, sent = bot
    s = S.start("كبيرة")
    monkeypatch.setattr(tb.Config, "SESSION_EXPORT_ZIP_MB", 0)
    S.append_entry("x" * 2000, model="m")
    tb.handle_text("T", "1", {"message_id": 1}, f"/export {s['id']}")
    doc = [x for x in sent if x["method"] == "sendDocument"][-1]
    assert doc["kw"]["files"]["document"][0].endswith(".zip")


def test_web_path_dual_writes_to_session(bot, S):
    tb, sent = bot
    import voice_queue
    s = S.start("ويب")
    job = voice_queue.Job(source="web", file_id=None, mime_type="audio/webm", label="web")
    job.meta["audio_bytes"] = b"abc"
    job.meta["transcribe_fn"] = lambda b, m: ("نص من الويب", M1, "A", {"elapsed": 1.0})
    r = tb.process_job(job)
    assert r["archived"] and S.get(s["id"])["count"] == 1 and S.entries(s["id"])[0]["raw"] == "نص من الويب"
    assert not sent
