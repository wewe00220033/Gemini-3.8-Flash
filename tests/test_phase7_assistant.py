# -*- coding: utf-8 -*-
"""
المرحلة 7a — المساعد التفاعلي (12 §1–§2، DoD 12 §5 بنود 1 و3):
 1. /ask يُلحق الجواب بالجلسة ويستخدم TEXT_ASSIST_MODEL فقط (اسم الموديل يُفحص في الـ URL).
 2. السياق: الأحدث أولاً ≤ ASK_MAX_CONTEXT_CHARS + ملخص L2؛ بلا جلسة → الأرشيف؛ لا شيء → رسالة واضحة.
 3. /chat: النص = سؤال متابعة مع آخر 6 تبادلات؛ الأوامر والمفاتيح تمر؛ TTL 10 د → إشعار واحد؛ /endchat.
 4. الفويس أثناء /chat يُفرَّغ عادياً (مسار الصوت لا يمر على handle_text أصلاً) ولا يُفسَّر كسؤال.
 5. Interactions client: نص فقط، تحليل مرن، كل فشل → رسالة لا استثناء؛ /plan يُلحق «### 🧭 خطة Antigravity».
 6. flash-lite و antigravity ليسا في Config.MODELS (ممنوعان للتفريغ).
كله بلا شبكة.
"""
import importlib
import json

import pytest

VALID_A = "AIzaSy" + "A" * 33


class FakeResp:
    def __init__(self, status, payload=None, text=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)
        self.headers = {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def bot(fresh_env, monkeypatch):
    import sessions
    sessions.reset_cache()
    import assistant
    assistant.reset()
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent, docs = [], []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append({"method": url.rsplit("/", 1)[-1], "payload": json})
        return FakeResp(200, {"ok": True, "result": {"message_id": 500 + len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    monkeypatch.setattr(telegram_bot, "_send_document",
                        lambda token, chat_id, filename, data, mime, caption, reply_to_id: docs.append((filename, data, caption)) or 900)
    monkeypatch.setattr(telegram_bot.Config, "PLAN_SYNC_FOR_TESTS", True)
    return telegram_bot, sent, docs


def _texts(sent):
    return [x["payload"].get("text", "") for x in sent if x["method"] == "sendMessage"]


def _cmd(tb, text, reply=None):
    msg = {"message_id": 1}
    if reply:
        msg["reply_to_message"] = {"text": reply}
    tb.handle_text("T", "1", msg, text)


def _fake_generate(calls, answer="الجواب: قررنا استخدام SQLite لاحقاً."):
    def gen(system_text, user_text):
        calls.append((system_text, user_text))
        return answer, None
    return gen


# =====================================================================
# 1–2: /ask — السياق والإلحاق والموديل
# =====================================================================

def test_ask_appends_to_session_and_uses_text_assist_model_only(bot, monkeypatch):
    tb, sent, _ = bot
    import sessions, assistant, text_assist
    from config import Config
    sessions.start("مشروع ألفا")
    sessions.append_entry("أول فويس: ناقشنا قاعدة البيانات", model="gemini-3.6-flash")
    sessions.append_entry("ثاني فويس: قررنا SQLite لاحقاً", model="gemini-3.6-flash")
    # generate الحقيقي يمر بـ requests.post → نلتقط الـ URL ونرد JSON كأن Gemini أجاب
    urls = []

    def fake_requests_post(url, json=None, headers=None, timeout=None):
        urls.append(url)
        return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "قررتم SQLite لاحقاً."}]}}]})

    monkeypatch.setattr(text_assist.requests, "post", fake_requests_post)
    monkeypatch.setattr(text_assist, "key_manager", tb.key_manager)     # fresh_env أعاد تحميل key_manager
    tb.key_manager.add_key(VALID_A, "P1")
    _cmd(tb, "/ask إيه اللي قررناه؟")
    assert urls and Config.TEXT_ASSIST_MODEL in urls[0] and "lite" in urls[0]
    assert all(m not in urls[0] for m in Config.MODELS)            # ليس موديل تفريغ
    out = _texts(sent)[-1]
    assert "قررتم SQLite" in out and "مشروع ألفا" in out and "أُلحق بالجلسة" in out
    data = sessions.export_bytes(sessions.current_id()).decode("utf-8")
    assert assistant.ASK_MARK in data and "إيه اللي قررناه" in data and "قررتم SQLite" in data
    # الإلحاق لا يلوّث مدخلات الجلسة (الخام/المنظم) — المحلل يتجاهل «### 💡»
    ents = sessions.entries(sessions.current_id())
    assert len(ents) == 2 and "قررتم" not in (ents[-1]["raw"] + str(ents[-1]["polished"]))
    assert "قررتم" not in (sessions.last_entries(1)[0]["raw"])


def test_ask_context_newest_first_within_budget_and_includes_summary(bot, monkeypatch):
    tb, sent, _ = bot
    import sessions, assistant
    monkeypatch.setattr(assistant.Config, "ASK_MAX_CONTEXT_CHARS", 1200)
    sid = sessions.start("كبيرة")["id"]
    for i in range(10):
        sessions.append_entry(f"فويس رقم {i} " + ("كلام " * 60), model="m")
    with sessions._lock:
        sessions._find(sid)["summary"] = "ملخص-L2-تجريبي"
        sessions._write_index()
    assert sessions.summary(sid) == "ملخص-L2-تجريبي"
    calls = []
    res = assistant.ask("سؤال", generate_fn=_fake_generate(calls))
    assert res["ok"] and res["source"] == "session"
    user = calls[0][1]
    assert "ملخص-L2-تجريبي" in user
    assert "فويس رقم 9" in user and "فويس رقم 0" not in user          # الأحدث أولاً ضمن الميزانية
    ctx_part = user.split("السؤال:")[0]
    assert len(ctx_part) < 1200 + 400                                 # الميزانية محترمة (+ رؤوس)


def test_ask_without_session_falls_back_to_archive_then_clear_error(bot):
    tb, sent, _ = bot
    import assistant, archive
    calls = []
    res = assistant.ask("سؤال", generate_fn=_fake_generate(calls))
    assert not res["ok"] and "لا يوجد محتوى" in res["error"]
    archive.append("نص أرشيفي عن Docker", model="m")
    res = assistant.ask("سؤال", generate_fn=_fake_generate(calls))
    assert res["ok"] and res["source"] == "archive" and not res["appended"]
    assert "Docker" in calls[-1][1]
    _cmd(tb, "/ask")
    assert "الاستخدام" in _texts(sent)[-1]


def test_ask_failure_reports_reason_no_exception(bot, monkeypatch):
    tb, sent, _ = bot
    import sessions, assistant
    sessions.start("x")
    sessions.append_entry("محتوى", model="m")
    monkeypatch.setattr(assistant.text_assist, "generate", lambda s, u: (None, "حصة المساعد النصي اليومية نفدت"))
    _cmd(tb, "/ask سؤال")
    assert "تعذّر الرد" in _texts(sent)[-1] and "نفدت" in _texts(sent)[-1]


def test_long_answer_goes_as_md_file(bot, monkeypatch):
    tb, sent, docs = bot
    import sessions, assistant
    sessions.start("x")
    sessions.append_entry("محتوى", model="m")
    monkeypatch.setattr(assistant.text_assist, "generate", lambda s, u: ("ج " * 3000, None))
    _cmd(tb, "/ask سؤال طويل")
    assert docs and docs[-1][0].startswith("ask_") and docs[-1][0].endswith(".md")


# =====================================================================
# 3–4: /chat
# =====================================================================

def test_chat_mode_text_is_question_with_history_and_endchat(bot, monkeypatch):
    tb, sent, _ = bot
    import sessions, assistant
    sessions.start("جلسة")
    sessions.append_entry("محتوى الجلسة", model="m")
    calls = []
    n = {"i": 0}

    def gen(s, u):
        calls.append(u)
        n["i"] += 1
        return f"جواب{n['i']}", None

    monkeypatch.setattr(assistant.text_assist, "generate", gen)
    _cmd(tb, "/chat")
    assert "وضع المحادثة مفعّل" in _texts(sent)[-1]
    for i in range(8):
        _cmd(tb, f"سؤال{i}")
    assert len(calls) == 8
    # آخر سؤال يحمل آخر 6 تبادلات فقط
    last = calls[-1]
    assert "س: سؤال6" in last and "ج: جواب7" in last and "س: سؤال0" not in last
    assert assistant.chat_state()["turns"] == 6
    # أمر أثناء المحادثة يمر عادياً ولا يُعامل كسؤال
    _cmd(tb, "/status")
    assert len(calls) == 8 and assistant.chat_active("1")
    # مفتاح ملصوق يمر لمسار المفتاح
    _cmd(tb, VALID_A)
    assert len(calls) == 8 and tb.key_manager.data["keys"]
    _cmd(tb, "/endchat")
    assert "انتهى وضع المحادثة" in _texts(sent)[-1] and not assistant.chat_active("1")
    _cmd(tb, "نص عادي بعد الإنهاء")
    assert len(calls) == 8                                            # لم يُفسَّر كسؤال


def test_chat_ttl_expires_with_single_notice(bot, monkeypatch):
    tb, sent, _ = bot
    import clock, assistant, sessions
    sessions.start("x")
    sessions.append_entry("محتوى", model="m")
    fc = clock.FakeClock()
    clock.use(fc)
    try:
        monkeypatch.setattr(assistant.text_assist, "generate", lambda s, u: ("ج", None))
        _cmd(tb, "/chat")
        _cmd(tb, "سؤال")
        fc.advance(10 * 60 + 1)
        before = len(_texts(sent))
        _cmd(tb, "نص بعد المهلة")
        txts = _texts(sent)
        assert len(txts) == before + 1 and "انتهت مهلة وضع المحادثة" in txts[-1]
        _cmd(tb, "نص آخر")
        assert len(_texts(sent)) == before + 1                        # لا إشعار ثانٍ ولا سؤال
        assert not assistant.chat_active("1")
    finally:
        clock.reset()


def test_voice_during_chat_is_transcribed_not_asked(bot, monkeypatch):
    """الصوت لا يمر على handle_text أصلاً؛ وبعده يبقى وضع /chat فعّالاً (12 §1 «ليس تفريغاً»)."""
    tb, sent, _ = bot
    import assistant, sessions
    sessions.start("x")
    sessions.append_entry("محتوى", model="m")
    calls = []
    monkeypatch.setattr(assistant.text_assist, "generate", _fake_generate(calls))
    _cmd(tb, "/chat")
    voice_msg = {"message_id": 7, "chat": {"id": 1}, "voice": {"file_id": "F", "file_unique_id": "U", "mime_type": "audio/ogg", "file_size": 10}}
    assert tb._is_audio_message(voice_msg)                             # مسار الصوت منفصل
    assert not voice_msg.get("text")                                   # لا نص → handle_text لا يُستدعى
    assert assistant.chat_active("1") and calls == []
    _cmd(tb, "سؤال بعد الفويس")
    assert len(calls) == 1


# =====================================================================
# 5: Interactions client + /plan
# =====================================================================

def test_interactions_payload_text_only_and_flexible_parsing(fresh_env, monkeypatch):
    import interactions_client as ic
    from config import Config
    fresh_env.key_manager.add_key(VALID_A, "P1")
    monkeypatch.setattr(ic, "key_manager", fresh_env.key_manager)
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResp(200, {"outputs": [{"type": "message", "content": [{"type": "text", "text": "الخطة: 1) …"}]}]})

    text, reason = ic.run("حوّل ده لخطة", post_fn=post)
    assert text == "الخطة: 1) …" and reason is None
    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["json"] == {"agent": Config.AGENT_NAME, "environment": "remote", "input": "حوّل ده لخطة"}
    assert set(seen["json"]) == {"agent", "environment", "input"}      # نص فقط — لا صوت ولا parts
    assert seen["headers"]["x-goog-api-key"] == VALID_A and seen["timeout"][1] == Config.AGENT_TIMEOUT
    # أشكال أخرى للرد
    assert ic.extract_text({"output_text": "x"}) == "x"
    assert ic.extract_text({"candidates": [{"content": {"parts": [{"text": "y"}]}}]}) == "y"
    assert ic.extract_text({"foo": {"bar": [{"baz": 1}]}}) == ""
    assert ic.extract_text({"error": {"message": "boom"}}) == ""


@pytest.mark.parametrize("status,payload,needle", [
    (200, {"weird": {"shape": 1}}, "غير متوقع"),
    (200, "not json", "غير متوقع"),
    (400, {"error": {"message": "audio input not supported"}}, "400"),
    (401, {"error": {"message": "invalid key"}}, "مرفوض"),
    (404, {}, "404"),
    (429, {"error": {"message": "Quota exceeded for the day"}}, "نفدت"),
    (429, {"error": {"message": "slow down"}}, "429"),
    (503, {}, "مزدحمة"),
])
def test_interactions_every_failure_is_message_not_exception(fresh_env, monkeypatch, status, payload, needle):
    import interactions_client as ic
    fresh_env.key_manager.add_key(VALID_A, "P1")
    monkeypatch.setattr(ic, "key_manager", fresh_env.key_manager)
    if payload == "not json":
        resp = FakeResp(status, ValueError("no json"), text="<html>")
    else:
        resp = FakeResp(status, payload)
    text, reason = ic.run("x", post_fn=lambda *a, **k: resp)
    assert text is None and needle in reason
    assert ic.stats()["fail"] >= 1


def test_interactions_network_errors_and_no_keys(fresh_env, monkeypatch):
    import interactions_client as ic
    import requests
    monkeypatch.setattr(ic, "key_manager", fresh_env.key_manager)
    assert ic.run("x")[1].startswith("لا مفتاح")
    fresh_env.key_manager.add_key(VALID_A, "P1")

    def timeout(*a, **k):
        raise requests.exceptions.ReadTimeout("slow")

    def boom(*a, **k):
        raise RuntimeError("weird")

    assert "لم يرد" in ic.run("x", post_fn=timeout)[1]
    assert "غير متوقع" in ic.run("x", post_fn=boom)[1]
    monkeypatch.setattr(ic.Config, "FEATURE_PLAN", False)
    assert "معطّلة" in ic.run("x", post_fn=boom)[1]


def test_plan_command_appends_to_session_and_reports_failure(bot, monkeypatch):
    tb, sent, docs = bot
    import sessions, assistant, interactions_client as ic
    sessions.start("مشروع")
    sessions.append_entry("نحتاج نبني بوت تفريغ مع نسخ احتياطي", model="m")
    prompts = []

    def fake_run(prompt, timeout=None, post_fn=None):
        prompts.append(prompt)
        return "## خطة\n1. البوت\n2. النسخ", None

    monkeypatch.setattr(ic, "run", fake_run)
    _cmd(tb, "/plan ركّز على النسخ الاحتياطي")
    txts = _texts(sent)
    assert any("Antigravity شغّال" in t for t in txts)
    assert "خطة Antigravity" in txts[-1] and "أُلحقت بالجلسة" in txts[-1]
    assert "نسخ احتياطي" in prompts[0] and "طلب المالك: ركّز" in prompts[0]
    data = sessions.export_bytes(sessions.current_id()).decode("utf-8")
    assert assistant.PLAN_MARK in data and "1. البوت" in data
    assert len(sessions.entries(sessions.current_id())) == 1            # لا يُعدّ مدخلاً
    # رد على نص → النص هو المدخل
    _cmd(tb, "/plan", reply="نص مردود عليه عن CI")
    assert "نص مردود عليه عن CI" in prompts[-1]
    # فشل → رسالة واضحة
    monkeypatch.setattr(ic, "run", lambda p, timeout=None, post_fn=None: (None, "Google مزدحمة (503)"))
    _cmd(tb, "/plan")
    assert "/plan: Google مزدحمة" in _texts(sent)[-1]
    # استثناء داخل الخيط لا يهرب
    monkeypatch.setattr(ic, "run", lambda p, timeout=None, post_fn=None: (_ for _ in ()).throw(RuntimeError("x")))
    _cmd(tb, "/plan")
    assert "خطأ غير متوقع" in _texts(sent)[-1]


def test_plan_without_content_and_feature_flag(bot, monkeypatch):
    tb, sent, _ = bot
    _cmd(tb, "/plan")
    assert "لا محتوى للتخطيط" in _texts(sent)[-1]
    monkeypatch.setattr(tb.Config, "FEATURE_PLAN", False)
    _cmd(tb, "/plan")
    assert "معطّلة" in _texts(sent)[-1]


# =====================================================================
# 6: ممنوعان للتفريغ + القائمة والمساعدة
# =====================================================================

def test_lite_and_antigravity_never_transcribe(fresh_env):
    from config import Config
    import interactions_client as ic
    import text_assist
    text_assist.assert_not_transcription_model()
    ic.assert_not_transcription()
    assert Config.AGENT_NAME not in Config.MODELS and "antigravity" not in " ".join(Config.MODELS)
    assert not any("lite" in m for m in Config.MODELS)
    import transcriber
    src = open(transcriber.__file__, encoding="utf-8").read()
    assert "interactions" not in src.lower() and "antigravity" not in src.lower()


def test_bot_commands_include_ask_and_help_mentions_new_commands(bot):
    tb, sent, _ = bot
    names = [c[0] for c in tb.BOT_COMMANDS]
    assert "ask" in names and len(names) <= 12
    _cmd(tb, "/help")
    h = _texts(sent)[-1]
    assert "/ask" in h and "/chat" in h and "/endchat" in h and "/plan" in h
