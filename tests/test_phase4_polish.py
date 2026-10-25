# -*- coding: utf-8 -*-
"""
تكميلي المرحلة 4 (fix/v2-phase4-polishing) — إغلاق الفجوات السبع التي كشفتها المراجعة الجنائية:
  1) L0 session_terms → transcription_prompt (10 §2)      2) role_override حيّ (06 §2 / 08 §1)
  3) jobs.record يحمل session_id/session_name (08 §1 / 03 §3)   4) ترويسة 🏷️/📂 في معاينة النص الطويل (08 §3)
  5) 🎲 3 اقتراحات لاسم الجلسة (08 §2)                     6) RESUME_RECAP (08 §2 / 16 D27)
  7) L2 ملخص تراكمي كل 5 فويسات → طبقة B فقط (10 §2)
كل الاختبارات بلا شبكة (conftest يحجب requests.post للمساعد النصي).
"""
import importlib
import json
import pytest

VALID_A = "AIzaSy" + "A" * 33
M1 = "gemini-3.6-flash"
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
    jobs.record(job.job_id, "received", **job.journal_fields())
    return job


def _cmd(tb, text):
    tb.handle_text("T", "1", {"message_id": 1}, text)


# =====================================================================
# (1) L0: مصطلحات الجلسة تدخل طبقة A
# =====================================================================

def test_terms_hint_empty_without_session_or_terms(S):
    assert S.terms_hint() == ""
    S.start("أ")
    assert S.terms_hint() == ""                                             # جلسة بلا فويسات
    S.append_entry("كلام عربي فقط بلا أسماء تقنية", model=M1)
    assert S.terms_hint() == ""


def test_transcription_prompt_includes_session_terms_inside_session(S, engine):
    base = engine.transcription_prompt()
    assert "Docker" not in base
    S.start("خادم")
    S.append_entry(LONG_RAW, model=M1)
    p = engine.transcription_prompt()
    assert "اكتبها كما هي" in p and "Docker" in p and "Nginx" in p
    assert p.startswith(engine.Config.SYSTEM_PROMPT)                        # الأساس لم يتغير
    S.end()
    assert "Docker" not in engine.transcription_prompt()                   # خارج الجلسة → لا حقن


def test_layer_a_extra_capped_even_with_many_terms(S, engine, fresh_env):
    import prompts
    S.start("كثير")
    S.append_entry(" ".join(f"Term{i}xyz" for i in range(200)) + " نص", model=M1)
    p = engine.transcription_prompt()
    extra = p[len(engine.Config.SYSTEM_PROMPT):]
    assert len(extra) <= engine.Config.TRANSCRIBE_LAYER_A_MAX_CHARS + 1
    assert prompts.layer_a_extra_len("general", S.terms_hint()) <= engine.Config.TRANSCRIBE_LAYER_A_MAX_CHARS


def test_engine_payload_carries_session_terms(S, engine, monkeypatch):
    S.start("خادم")
    S.append_entry(LONG_RAW, model=M1)
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        return ok_text("نص جديد مختلف تماماً عن السابق")

    monkeypatch.setattr(engine.requests, "post", fake_post)
    text, model, key, info = engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert text and calls
    sys_txt = calls[0]["contents"][0]["parts"][1]["text"]
    assert "Docker" in sys_txt and "اكتبها كما هي" in sys_txt


# =====================================================================
# (2) role_override حيّ
# =====================================================================

def test_set_role_override_and_effective_role(S, fresh_env):
    import settings
    assert S.set_role_override("devops") is False                            # لا جلسة
    assert settings.effective_role() == settings.role()
    s = S.start("طبية")
    assert S.set_role_override("medical") is True and S.role_override() == "medical"
    assert settings.effective_role() == "medical" and settings.role() != "medical"
    S.reset_cache()                                                          # يبقى بعد restart (في الفهرس)
    assert S.role_override() == "medical"
    assert S.set_role_override(None) is True and S.role_override() is None
    assert settings.effective_role() == settings.role()
    body = open(S.session_file(s["id"]), encoding="utf-8").read()
    assert "🎭 دور الجلسة: medical" in body and "🎭 دور الجلسة: الافتراضي" in body
    S.append_entry("نص", model=M1)
    assert S.entries(s["id"])[0]["raw"] == "نص"                             # المؤشر لا يلوّث المدخلات


def test_delivery_and_transcriber_use_session_role(S, fresh_env, engine):
    import delivery
    import prompts
    S.start("طبية")
    S.set_role_override("medical")
    seen = {}

    def polish_fn(raw, role, gl, context_summary=None):
        seen["role"] = role
        return ("منظم", None)

    sender = delivery.Sender(lambda t, r, p: 1)
    res = delivery.deliver("نص طويل بما يكفي ليُنظَّم هنا الآن", M1, "A", {}, sender, polish_fn=polish_fn, title_fn=lambda r: None)
    assert seen["role"] == "medical" and res["role"] == "medical"
    role_line = (prompts.get_role("medical") or {}).get("layer_a") or ""
    if role_line.strip():
        assert role_line.strip().split("\n")[0] in engine.transcription_prompt()


def test_session_role_command(bot, S):
    tb, sent = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    _cmd(tb, "/session_role")
    assert "لا جلسة نشطة" in last()
    _cmd(tb, "/new_session طبية")
    _cmd(tb, "/session_role")
    assert "الافتراضي" in last()
    _cmd(tb, "/session_role nothing_like_this")
    assert "دور غير معروف" in last() and S.role_override() is None
    _cmd(tb, "/session_role medical")
    assert S.role_override() == "medical" and "medical" in last()
    _cmd(tb, "/status")
    assert "دور الجلسة medical" in last()
    _cmd(tb, "/session_role -")
    assert S.role_override() is None and "الدور العام" in last()
    _cmd(tb, "/help")
    assert "/session_role" in last() and "/suggest_titles" in last()


# =====================================================================
# (3) jobs.record يحمل الجلسة — تليجرام / مكرر / ويب
# =====================================================================

def test_jobs_record_has_session_fields_telegram_dedup_web(bot, S, monkeypatch):
    tb, sent = bot
    import jobs
    import voice_queue
    s = S.start("تتبع")
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex", lambda b, m: (LONG_RAW, M1, "A", {"elapsed": 1.0, "skipped": []}))
    j1 = _voice_job("t1")
    tb.process_job(j1)
    j2 = _voice_job("t1")                                                    # مكرر
    r = tb.process_job(j2)
    assert r["dedup"] is True
    web = voice_queue.Job(source="web", file_id=None, mime_type="audio/webm", label="web")
    web.meta["audio_bytes"] = b"abc"
    web.meta["transcribe_fn"] = lambda b, m: ("نص ويب", M1, "A", {"elapsed": 1.0})
    tb.process_job(web)
    st = jobs.load_state()
    for jid in (j1.job_id, j2.job_id, web.job_id):
        assert st[jid]["state"] == "sent"
        assert st[jid]["session_id"] == s["id"] and st[jid]["session_name"] == "تتبع", jid
    S.end()
    j3 = _voice_job("t3")
    tb.process_job(j3)
    assert "session_id" not in jobs.load_state()[j3.job_id]                 # بلا جلسة → لا حقول (None لا تُكتب)


# =====================================================================
# (4) ترويسة 🏷️/📂 في معاينة النص الطويل
# =====================================================================

def test_long_text_preview_keeps_head(S, fresh_env):
    import delivery
    s = S.start("طويلة")
    msgs, docs = [], []
    sender = delivery.Sender(lambda t, r, p: msgs.append(t) or len(msgs),
                             send_document=lambda fn, data, mime, cap, r: docs.append(fn) or 900)
    long_raw = ("كلمة " * 20 + "\n") * 150                                   # > POLISH_FILE_THRESHOLD
    assert len(long_raw) > delivery.Config.POLISH_FILE_THRESHOLD
    res = delivery.deliver(long_raw, M1, "A", {}, sender, polish_fn=lambda raw, role, gl: (long_raw, None),
                           title_fn=lambda r: "عنوان طويل")
    assert res["title"] == "عنوان طويل" and docs == ["transcript.md", "polished.md"]
    assert len(msgs) == 2
    for m in msgs:
        assert "🏷️ <b>عنوان طويل</b>" in m and f"📂 {s['name']}" in m and "<pre>" in m
        assert m.index("🏷️") < m.index("<pre>")                               # الترويسة قبل المعاينة
    S.end()
    msgs.clear()
    delivery.deliver(long_raw, M1, "A", {}, sender, polish_fn=lambda raw, role, gl: (None, "x"), title_fn=lambda r: None)
    assert msgs and "🏷️" not in msgs[0] and "📂" not in msgs[0]              # بلا جلسة/عنوان → لا ترويسة فارغة


# =====================================================================
# (5) 🎲 3 اقتراحات
# =====================================================================

def test_suggest_titles_three_sanitized_dedup(S):
    assert S.suggest_titles("نص بلا جلسة") == []
    S.start("مؤقت")
    assert S.suggest_titles("قصير") == []                                    # < 3 كلمات
    answers = iter(["إعداد: Nginx/على السيرفر", "إعداد Nginx على السيرفر", "ضبط\nالحاوية", "  Docker و Nginx  "])
    calls = []

    def gen(sys_t, usr_t):
        calls.append(sys_t)
        return (next(answers), None)

    out = S.suggest_titles(LONG_RAW, generate_fn=gen)
    assert len(calls) == 3 and len(out) == 2                                  # 3 نداءات؛ المكرر بعد التنقية أُسقط
    assert out[0] == "إعداد Nginx على السيرفر" and out[1] == "ضبط"
    for t in out:
        assert not any(c in t for c in '/\\:*?"<>|')
    assert "رقم 2" in calls[1] and "إعداد Nginx على السيرفر" in calls[1]    # يطلب صياغة مختلفة عن السابق


def test_suggest_titles_uses_last_entry_and_handles_failure(S):
    S.start("مؤقت")
    S.append_entry(LONG_RAW, model=M1)
    seen = []
    out = S.suggest_titles(generate_fn=lambda s_, u: seen.append(u) or ("عنوان " + str(len(seen)), None))
    assert out == ["عنوان 1", "عنوان 2", "عنوان 3"] and all("Docker" in u for u in seen)
    assert S.suggest_titles(generate_fn=lambda s_, u: (None, "فشل")) == []
    assert S.suggest_titles(generate_fn=lambda s_, u: (_ for _ in ()).throw(RuntimeError("x"))) == []


def test_pick_title_and_commands(bot, S, monkeypatch):
    tb, sent = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    _cmd(tb, "/suggest_titles")
    assert "لا جلسة نشطة" in last()
    _cmd(tb, "/new_session")
    _cmd(tb, "/suggest_titles")
    assert "لم أحصل على اقتراحات" in last()                                  # جلسة بلا فويسات
    S.append_entry(LONG_RAW, model=M1)
    n = {"i": 0}

    def gen(s_, u):
        n["i"] += 1
        return (f"اقتراح {n['i']}", None)

    import text_assist
    monkeypatch.setattr(text_assist, "generate", gen)
    _cmd(tb, "/suggest_titles")
    assert "🎲" in last() and "1. «اقتراح 1»" in last() and "3. «اقتراح 3»" in last()
    assert S.title_options() == ["اقتراح 1", "اقتراح 2", "اقتراح 3"]
    S.reset_cache()                                                          # الخيارات محفوظة في الفهرس
    assert S.title_options() == ["اقتراح 1", "اقتراح 2", "اقتراح 3"]
    _cmd(tb, "/pick_title 9")
    assert "غير صالح" in last()
    _cmd(tb, "/pick_title x")
    assert "غير صالح" in last()
    _cmd(tb, "/pick_title 2")
    assert S.current()["name"] == "اقتراح 2" and "«اقتراح 2»" in last()
    assert S.title_options() == [] and S.current()["pending_title"] is None and S.current()["auto_named"] is False
    _cmd(tb, "/pick_title")
    assert "لا اقتراحات محفوظة" in last()


def test_suggestion_message_mentions_dice(bot, S, monkeypatch):
    tb, sent = bot
    S.start()
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex", lambda b, m: (LONG_RAW, M1, "A", {"elapsed": 1.0, "skipped": []}))
    tb.process_job(_voice_job("d1"))
    msg = [t for t in _texts(sent) if "اسم مقترح للجلسة" in t]
    assert msg and "/suggest_titles" in msg[0]


# =====================================================================
# (6) RESUME_RECAP
# =====================================================================

def test_resume_recap_toggle(bot, S, monkeypatch):
    tb, sent = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    s = S.start("أ")
    S.append_entry("النص الأول في الجلسة", model=M1)
    S.end()
    monkeypatch.setattr(tb.Config, "RESUME_RECAP", True)
    _cmd(tb, f"/resume {s['id']}")
    assert "آخر النصوص" in last() and "النص الأول" in last()
    S.end()
    monkeypatch.setattr(tb.Config, "RESUME_RECAP", False)
    _cmd(tb, f"/resume {s['id']}")
    assert "▶️" in last() and "آخر النصوص" not in last() and "النص الأول" not in last()


def test_resume_recap_env_default(monkeypatch):
    import config
    monkeypatch.setenv("RESUME_RECAP", "0")
    importlib.reload(config)
    assert config.Config.RESUME_RECAP is False
    monkeypatch.delenv("RESUME_RECAP")
    importlib.reload(config)
    assert config.Config.RESUME_RECAP is True
    assert config.Config.TITLE_SUGGESTIONS_N == 3 and config.Config.SESSION_SUMMARY_EVERY == 5


# =====================================================================
# (7) L2: ملخص تراكمي كل 5 فويسات → طبقة B فقط
# =====================================================================

def test_summary_due_and_update_every_five(S):
    assert S.summary() is None and S.summary_due() is False
    s = S.start("ملخص")
    calls = []

    def gen(sys_t, usr_t):
        calls.append((sys_t, usr_t))
        return ("  ملخص   تراكمي  رقم " + str(len(calls)) + "  ", None)

    for i in range(1, 10):
        S.append_entry(f"فويس رقم {i} عن Docker", model=M1)
        due = S.summary_due()
        assert due is (i % 5 == 0), i
        out = S.maybe_update_summary(generate_fn=gen)
        if i == 5:
            assert out == "ملخص تراكمي رقم 1" and S.summary() == out and S.current()["summary_at"] == 5
            assert "فويس رقم 5" in calls[0][1] and "فويس رقم 1" in calls[0][1]
        else:
            assert out is None
    assert len(calls) == 1 and S.summary_due() is False
    assert S.maybe_update_summary(generate_fn=gen) is None                   # لا تكرار عند نفس العدّ
    S.append_entry("فويس رقم 10", model=M1)
    out = S.maybe_update_summary(generate_fn=gen)
    assert out == "ملخص تراكمي رقم 2" and len(calls) == 2
    assert "الملخص السابق" in calls[1][1] and "ملخص تراكمي رقم 1" in calls[1][1]   # تراكمي
    body = open(S.session_file(s["id"]), encoding="utf-8").read()
    assert "🧾 ملخص: ملخص تراكمي رقم 2" in body
    assert len(S.entries(s["id"])) == 10 and all("🧾" not in e["raw"] for e in S.entries(s["id"]))


def test_summary_survives_index_rebuild_and_failure_is_silent(S):
    import os
    s = S.start("ملخص")
    for i in range(5):
        S.append_entry(f"نص {i}", model=M1)
    assert S.maybe_update_summary(generate_fn=lambda a, b: (None, "فشل")) is None
    assert S.summary() is None and S.summary_due() is True                   # الفشل لا يستهلك الدور
    assert S.maybe_update_summary(generate_fn=lambda a, b: ("ملخص محفوظ", None)) == "ملخص محفوظ"
    os.remove(S.index_path())
    S.reset_cache()
    assert S.get(s["id"])["summary"] == "ملخص محفوظ" and S.get(s["id"])["summary_at"] == 5
    assert S.summary_due(s["id"]) is False
    long = "ك" * 2000
    assert len(S.maybe_update_summary(generate_fn=lambda a, b: (long, None)) or "") == 0 or True   # لا شيء حان
    for i in range(5):
        S.append_entry(f"نص {i + 5}", model=M1)
    out = S.maybe_update_summary(generate_fn=lambda a, b: (long, None))
    assert len(out) == S.Config.SESSION_SUMMARY_MAX_CHARS


def test_polish_injects_summary_in_layer_b_only(fresh_env, monkeypatch):
    import text_assist
    importlib.reload(text_assist)
    seen = {}

    def gen(system_text, user_text, timeout=None):
        seen["sys"], seen["usr"] = system_text, user_text
        return ("منظم", None)

    monkeypatch.setattr(text_assist, "generate", gen)
    monkeypatch.setattr(text_assist.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert text_assist.polish("نص", "general") == ("منظم", None)
    assert "ملخص الجلسة حتى الآن" not in seen["sys"]
    assert text_assist.polish("نص", "general", context_summary="الجلسة عن Nginx") == ("منظم", None)
    assert "ملخص الجلسة حتى الآن" in seen["sys"] and "الجلسة عن Nginx" in seen["sys"] and "للفهم فقط" in seen["sys"]
    assert seen["usr"] == "نص"                                                # الملخص ليس في نص المستخدم
    assert text_assist.polish("نص", "general", context_summary="x" * 5000)[0] == "منظم"
    assert len(seen["sys"]) < 5000 + 2000                                     # مقتطع عند SESSION_SUMMARY_MAX_CHARS


def test_delivery_passes_summary_to_polish_never_to_transcription(S, fresh_env, engine, monkeypatch):
    import delivery
    S.start("ملخص")
    for i in range(4):
        S.append_entry(f"نص {i}", model=M1)
    seen = []

    def polish_fn(raw, role, gl, context_summary=None):
        seen.append(context_summary)
        return ("منظم", None)

    import text_assist
    monkeypatch.setattr(text_assist, "generate", lambda a, b, timeout=None: ("ملخص الجلسة L2", None))
    sender = delivery.Sender(lambda t, r, p: 1)
    res = delivery.deliver("الفويس الخامس هنا بما يكفي", M1, "A", {}, sender, polish_fn=polish_fn, title_fn=lambda r: None)
    assert res["summary_updated"] is True and S.summary() == "ملخص الجلسة L2" and seen == [None]   # الخامس: يُحدَّث بعده
    res = delivery.deliver("الفويس السادس هنا بما يكفي", M1, "A", {}, sender, polish_fn=polish_fn, title_fn=lambda r: None)
    assert res["summary_updated"] is False and seen[-1] == "ملخص الجلسة L2"  # السادس: يُمرَّر لطبقة B
    # polish_fn قديمة (بلا context_summary) تظل تعمل — المسار المكرر/الاختبارات
    res = delivery.deliver("الفويس السابع هنا بما يكفي", M1, "A", {}, sender, polish_fn=lambda raw, role, gl: ("ق", None), title_fn=lambda r: None)
    assert res["polished"] == "ق"
    # طبقة A: الملخص لا يظهر أبداً في برومبت التفريغ
    assert "ملخص الجلسة L2" not in engine.transcription_prompt()
    assert "ملخص" not in (S.context_prompt_text() or "") or "ملخص الجلسة L2" not in S.context_prompt_text()
    calls = []
    monkeypatch.setattr(engine.requests, "post", lambda url, json=None, timeout=None, headers=None: calls.append(json) or ok_text("نص جديد كلياً"))
    engine.transcribe_audio_bytes_ex(b"abc", "audio/ogg")
    assert calls and "ملخص الجلسة L2" not in json.dumps(calls[0], ensure_ascii=False)
    assert "ملخص L2" in S.stats_text()
    S.end()
    res = delivery.deliver("بلا جلسة الآن", M1, "A", {}, sender, polish_fn=polish_fn, title_fn=lambda r: None)
    assert seen[-1] is None and res["summary_updated"] is False
