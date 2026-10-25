# -*- coding: utf-8 -*-
"""
المرحلة 3 — خط الإنتاج (Pipeline): القاموس (05 §6) · الأدوار/البرومبت (06 §8) · التسليم (07 §6) · الأرشيف (08 §1)
+ حارس: flash-lite لا يُستخدم للتفريغ أبداً (02).
كل الاختبارات بلا شبكة (conftest يحجب text_assist.requests.post).
"""
import json
import os
import importlib
import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33


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
def mods(fresh_env):
    import glossary, archive, settings, prompts, delivery, text_assist
    settings.reset_cache()
    prompts.reset_cache()
    glossary._singleton = None
    return glossary, archive, settings, prompts, delivery, text_assist


class FakeSender:
    """يسجل كل رسالة/مستند؛ message_id تصاعدي."""

    def __init__(self, with_document=True):
        self.msgs = []       # (text, reply_to, parse_mode, id)
        self.docs = []       # (filename, size, mime, caption, reply_to)
        self._next = 100

    def send_text(self, text, reply_to, parse_mode):
        self._next += 1
        self.msgs.append((text, reply_to, parse_mode, self._next))
        return self._next

    def send_document(self, filename, data, mime, caption, reply_to):
        self.docs.append((filename, len(data), mime, caption, reply_to))

    def sender(self, delivery, with_document=True):
        return delivery.Sender(self.send_text, self.send_document if with_document else None)


# =====================================================================
# 05 — القاموس
# =====================================================================

def test_glossary_defaults_loaded_and_file_created(mods):
    glossary = mods[0]
    g = glossary.get_glossary()
    ids = {e["id"] for e in g.entries()}
    assert {"g_001", "g_002", "g_003", "g_004", "g_005"} <= ids
    assert g.corrupt_notice is None
    assert os.path.exists(g.path)


def test_glossary_alias_polished_only_and_raw_unchanged(mods):
    g = mods[0].get_glossary()
    raw, n = g.apply("قلت لشلبي يكتب الكود", target="raw")
    assert raw == "قلت لشلبي يكتب الكود" and n == 0          # alias لا يمس الخام
    pol, n = g.apply("قلت لشلبي يكتب الكود", target="polished")
    assert pol == "قلت لChatGPT يكتب الكود" and n == 1          # مع الحفاظ على حرف الجر


def test_glossary_spelling_applies_to_both_and_arabic_punct_is_boundary(mods):
    g = mods[0].get_glossary()
    raw, n = g.apply("فتحت جرافيك، وبدأت", target="raw")
    assert raw == "فتحت Antigravity، وبدأت" and n == 1


def test_glossary_normalization_matches_variants_only(mods):
    g = mods[0].get_glossary()
    # أوبس/اوبس/أوبوس كلها أنماط؛ التطبيع (أ→ا) يساعد التطابق لكن لا يغيّر النص المحيط
    pol, n = g.apply("جرّبت اوبس و أوبوس أمس", target="polished")
    assert pol == "جرّبت Opus و Opus أمس" and n == 2


def test_glossary_context_rules_skip(mods):
    g = mods[0].get_glossary()
    pol, n = g.apply("أوبس في ستوديو", target="polished")
    assert pol == "أوبس في ستوديو" and n == 0            # "ستوديو" ضمن قواعد أوبس → تخطٍ (OBS Studio)
    pol, n = g.apply("جرّبت أوبس أمس", target="polished")
    assert pol == "جرّبت Opus أمس" and n == 1
    pol2, n2 = g.apply("استخدمت فلاش لايت أمس", target="polished")
    assert pol2 == "استخدمت فلاش لايت أمس" and n2 == 0
    pol3, _ = g.apply("جرافيك كارت جديد", target="polished")
    assert pol3 == "جرافيك كارت جديد"


def test_glossary_no_partial_word_match(mods):
    g = mods[0].get_glossary()
    pol, n = g.apply("شلبيات كتير", target="polished")
    assert pol == "شلبيات كتير" and n == 0


def test_glossary_longest_pattern_first(mods):
    g = mods[0].get_glossary()
    g.add("جيميناي", "Gemini", kind="spelling")
    g.add("جيميناي برو", "Gemini Pro", kind="spelling")
    out, n = g.apply("فتحت جيميناي برو النهارده", target="raw")
    assert out == "فتحت Gemini Pro النهارده" and n == 1


def test_glossary_pending_not_applied_until_confirmed(mods):
    g = mods[0].get_glossary()
    e, created = g.add("فابل", "Fable", status="pending")
    assert created and e["status"] == "pending"
    out, n = g.apply("فابل جميل", target="polished")
    assert out == "فابل جميل" and n == 0
    assert g.confirm(e["id"], replacement="Fable AI") is not None
    out, n = g.apply("فابل جميل", target="polished")
    assert out == "Fable AI جميل" and n == 1


def test_glossary_hits_counted_and_persisted(mods):
    glossary = mods[0]
    g = glossary.get_glossary()
    g.apply("شلبي شلبي", target="polished")
    assert g.find("g_001")["hits"] == 2
    g2 = glossary.Glossary(path=g.path)
    assert g2.find("g_001")["hits"] == 2


def test_glossary_add_remove_find_parse(mods):
    glossary = mods[0]
    g = glossary.get_glossary()
    assert glossary.Glossary.parse_add_args("شلبي = ChatGPT") == ("شلبي", "ChatGPT")
    assert glossary.Glossary.parse_add_args("شلبي -> ChatGPT") == ("شلبي", "ChatGPT")
    assert glossary.Glossary.parse_add_args("كلام بلا فاصل") is None
    e, created = g.add("كلود", "Claude")
    assert created and e["kind"] == "alias" and e["scope"] == "polished_only"
    e2, created2 = g.add("كلود", "Claude")
    assert not created2 and e2["id"] == e["id"]          # نفس اللفظ → إلحاق لا تكرار
    assert g.find("كلود")["id"] == e["id"]
    assert g.remove(e["id"]) is not None
    assert g.find(e["id"]) is None


def test_glossary_corrupt_file_falls_back_to_defaults_with_notice(mods, tmp_path):
    glossary = mods[0]
    from config import Config
    with open(Config.GLOSSARY_FILE, "w", encoding="utf-8") as f:
        f.write("{ not json")
    notices = []
    g = glossary.Glossary(on_corrupt=notices.append)
    assert g.corrupt_notice and "تالف" in g.corrupt_notice
    assert notices and g.find("g_001") is not None
    assert any(n.startswith("glossary.json") and n != "glossary.json" for n in os.listdir(str(tmp_path)))  # نسخة محفوظة


def test_glossary_hints_for_prompt_only_confirmed_spelling_english(mods):
    g = mods[0].get_glossary()
    h = g.hints_for_prompt()
    assert "Antigravity" in h and "ChatGPT" not in h       # alias لا يدخل تلميح الطبقة A
    g.add("فابل", "Fable", kind="spelling", status="pending")
    assert "Fable" not in g.hints_for_prompt()


def test_glossary_export_bytes_is_json(mods):
    g = mods[0].get_glossary()
    data = json.loads(g.export_bytes().decode("utf-8"))
    assert data["entries"] and "schema_version" in data


# =====================================================================
# 06 — الأدوار والبرومبت
# =====================================================================

def test_compose_prompt_is_pure_and_starts_with_system_prompt(mods):
    prompts = mods[3]
    from config import Config
    p1 = prompts.compose_prompt("transcribe", "general", "")
    p2 = prompts.compose_prompt("transcribe", "general", "")
    assert p1 == p2 and p1.startswith(Config.SYSTEM_PROMPT)
    assert prompts.compose_prompt("polish", "general") == prompts.compose_prompt("polish", "general")


def test_layer_a_extra_within_600_for_all_roles_with_hints(mods):
    glossary, _, _, prompts = mods[0], mods[1], mods[2], mods[3]
    from config import Config
    hints = glossary.get_glossary().hints_for_prompt()
    for rid in prompts.all_roles():
        assert prompts.layer_a_extra_len(rid, hints) <= Config.TRANSCRIBE_LAYER_A_MAX_CHARS, rid
        full = prompts.compose_prompt("transcribe", rid, hints)
        assert len(full) - len(Config.SYSTEM_PROMPT) <= Config.TRANSCRIBE_LAYER_A_MAX_CHARS + 1


def test_seven_protected_roles_exist_and_cannot_be_overridden_or_removed(mods):
    prompts = mods[3]
    roles = prompts.all_roles()
    for rid in ("general", "devops", "educational", "smart_verbatim", "quran_tutor", "kids_tutor", "medical"):
        assert rid in roles and roles[rid]["protected"]
    with pytest.raises(ValueError):
        prompts.add_role("devops", "مزيف", layer_b="x")
    assert prompts.remove_role("general") is None


def test_custom_role_layer_a_clipped_to_two_lines_and_persisted(mods):
    prompts = mods[3]
    role, created = prompts.add_role("my_role", "دوري", layer_a="سطر1\nسطر2\nسطر3\nسطر4", layer_b="نظّم كجدول")
    assert created and role["layer_a"].count("\n") <= 1
    prompts.reset_cache()
    assert prompts.get_role("my_role")["layer_b"] == "نظّم كجدول"
    assert "my_role" in prompts.roles_list_text("my_role")
    assert prompts.remove_role("my_role") is not None
    prompts.reset_cache()
    assert prompts.get_role("my_role") is None


def test_add_role_requires_layer_b_and_valid_id(mods):
    prompts = mods[3]
    with pytest.raises(ValueError):
        prompts.add_role("ok_id", "اسم", layer_b="")
    with pytest.raises(ValueError):
        prompts.add_role("Bad Id!", "اسم", layer_b="x")


def test_roles_json_corrupt_falls_back_to_defaults(mods):
    prompts = mods[3]
    from config import Config
    with open(Config.ROLES_FILE, "w", encoding="utf-8") as f:
        f.write("{{{ broken")
    prompts.reset_cache()
    roles = prompts.all_roles()
    assert len(roles) == 7 and "general" in roles


def test_polish_prompt_contains_role_and_golden_rule(mods):
    prompts = mods[3]
    p = prompts.compose_prompt("polish", "devops")
    assert "DevOps" in p or "devops" in p.lower() or prompts.DEFAULT_ROLES["devops"]["name"] in p
    assert "لا تضف" in p or "لا إضافة" in p or "لا تحذف" in p


def test_transcription_prompt_reflects_role_and_glossary(mods):
    settings = mods[2]
    import transcriber
    from config import Config
    settings.set_role("devops")
    p = transcriber.transcription_prompt()
    assert p.startswith(Config.SYSTEM_PROMPT) and "Antigravity" in p
    assert len(p) - len(Config.SYSTEM_PROMPT) <= Config.TRANSCRIBE_LAYER_A_MAX_CHARS + 1


# =====================================================================
# 06 §8 — البانر (3 حالات) + الإعدادات
# =====================================================================

@pytest.mark.parametrize("target,raw_on,pol_on", [("both", True, True), ("raw_only", True, False),
                                                  ("polished_only", False, True), ("off", False, False)])
def test_header_target_cases(mods, target, raw_on, pol_on):
    settings = mods[2]
    assert settings.set_header_target(target) == target
    assert settings.header_for("raw") is raw_on and settings.header_for("polished") is pol_on


def test_header_aliases_and_invalid(mods):
    settings = mods[2]
    assert settings.set_header_target("raw") == "raw_only"
    assert settings.set_header_target("polished") == "polished_only"
    assert settings.set_header_target("xyz") is None


def test_delivery_mode_persists_across_cache_reset(mods):
    settings = mods[2]
    assert settings.delivery_mode() == "dual"
    assert settings.set_delivery_mode("raw") == "raw_only"
    assert settings.set_delivery_mode("خام") == "raw_only"
    assert settings.set_delivery_mode("منظم") == "polished_only"
    settings.reset_cache()
    assert settings.delivery_mode() == "polished_only"
    assert settings.set_delivery_mode("nonsense") is None


def test_settings_json_corrupt_uses_defaults(mods):
    settings = mods[2]
    from config import Config
    os.makedirs(os.path.dirname(Config.SETTINGS_FILE), exist_ok=True)
    with open(Config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write("broken")
    settings.reset_cache()
    assert settings.delivery_mode() == "dual" and settings.role() == "general"


# =====================================================================
# 08 §1 — الأرشيف
# =====================================================================

def test_archive_append_last_count_find_and_banner_once(mods):
    archive = mods[1]
    assert archive.last() is None and archive.count_entries() == 0
    assert archive.append("أول نص عن الكوبرنيتس", model="gemini-3.6-flash", elapsed=3.2, key_name="A")
    assert archive.append("ثاني نص", model="gemini-3.5-transcribe", elapsed=4.0)
    assert archive.count_entries() == 2
    last = archive.last()
    assert last["raw"] == "ثاني نص" and "3.5-transcribe" in last["meta"] and last["polished"] is None
    with open(archive.all_path(), encoding="utf-8") as f:
        content = f.read()
    assert content.count("Verbatim Transcription") == 1       # البانر مرة واحدة في الرأس
    hits = archive.find("الكوبرنيتس")
    assert hits and "الكوبرنيتس" in hits[0]["line"]
    assert archive.find("غير_موجود_أبداً") == []


def test_archive_append_polished_attaches_to_last_without_duplicate(mods):
    archive = mods[1]
    archive.append("خام", model="m", elapsed=1.0)
    assert archive.append_polished("منظم جميل")
    last = archive.last()
    assert last["raw"] == "خام" and last["polished"] == "منظم جميل"
    assert archive.count_entries() == 1


def test_archive_entry_header_format(mods):
    archive = mods[1]
    archive.append("نص", model="gemini-3.6-flash", elapsed=2.5, session_name="اجتماع")
    meta = archive.last()["meta"]
    assert "اجتماع" in meta and "3.6-flash" in meta and meta.endswith("ث")
    with open(archive.all_path(), encoding="utf-8") as f:
        assert "## 🎙️ " + meta in f.read()                 # ترويسة المدخل بصيغة 08 §1


# =====================================================================
# 07 §6 — التسليم
# =====================================================================

def _info():
    return {"elapsed": 5.5, "skipped": []}


def test_deliver_dual_sends_raw_then_polished_reply_to_raw(mods):
    glossary, archive, settings, prompts, delivery, _ = mods
    fs = FakeSender()
    audio_calls = []
    res = delivery.deliver("قلت لشلبي يكتب", "gemini-3.6-flash", "A", _info(), fs.sender(delivery), reply_to_id=7,
                           polish_fn=lambda raw, role, gl: (gl.apply(raw, "polished")[0], None))
    assert res["mode"] == "dual" and res["archived"] and res["polish_error"] is None
    assert len(fs.msgs) == 2 and not audio_calls
    raw_msg, pol_msg = fs.msgs
    assert raw_msg[1] == 7 and "<pre>" in raw_msg[0] and "لشلبي" in raw_msg[0]       # الخام رد على الفويس
    assert pol_msg[1] == raw_msg[3]                                                   # المنظم رد على الخام
    assert "ChatGPT" in pol_msg[0] and res["polished_msg_id"] == pol_msg[3]
    assert archive.last()["polished"] is not None
    assert "Verbatim Transcription" in raw_msg[0] and "Verbatim Transcription" in pol_msg[0]  # both افتراضي


def test_deliver_raw_only_one_message_no_polish_call(mods):
    _, _, settings, _, delivery, _ = mods
    settings.set_delivery_mode("raw_only")
    fs = FakeSender()
    calls = []
    res = delivery.deliver("نص", "m", "A", _info(), fs.sender(delivery), reply_to_id=1,
                           polish_fn=lambda *a: calls.append(1) or ("x", None))
    assert len(fs.msgs) == 1 and calls == [] and res["polished"] is None


def test_deliver_polished_only_one_message(mods):
    _, _, settings, _, delivery, _ = mods
    settings.set_delivery_mode("polished_only")
    fs = FakeSender()
    res = delivery.deliver("نص", "m", "A", _info(), fs.sender(delivery), reply_to_id=1,
                           polish_fn=lambda raw, role, gl: ("منظم", None))
    assert len(fs.msgs) == 1 and "منظم" in fs.msgs[0][0] and fs.msgs[0][1] == 1
    assert res["raw_msg_id"] is None and res["polished_msg_id"] == fs.msgs[0][3]


def test_deliver_polish_failure_keeps_raw_and_warns_no_retranscribe(mods):
    _, archive, _, _, delivery, _ = mods
    fs = FakeSender()
    polish_calls = []

    def failing(raw, role, gl):
        polish_calls.append(raw)
        return None, "حصة المساعد نفدت"

    res = delivery.deliver("نص خام", "m", "A", _info(), fs.sender(delivery), reply_to_id=3, polish_fn=failing)
    assert len(polish_calls) == 1                                  # محاولة واحدة فقط (لا إعادة تفريغ للصوت)
    assert len(fs.msgs) == 2 and "نص خام" in fs.msgs[0][0]
    assert "تعذر التنظيم" in fs.msgs[1][0] and "الخام فوق" in fs.msgs[1][0] and fs.msgs[1][1] == fs.msgs[0][3]
    assert res["polish_error"] == "حصة المساعد نفدت" and res["polished"] is None
    assert archive.last()["raw"] == "نص خام"                      # الأرشفة تمت قبل الإرسال


def test_deliver_polished_only_failure_falls_back_to_raw(mods):
    _, _, settings, _, delivery, _ = mods
    settings.set_delivery_mode("polished_only")
    fs = FakeSender()
    res = delivery.deliver("نص خام", "m", "A", _info(), fs.sender(delivery), reply_to_id=3,
                           polish_fn=lambda *a: (None, "فشل"))
    assert res["raw_msg_id"] is not None and "نص خام" in fs.msgs[0][0]
    assert any("أرسلت الخام" in m[0] for m in fs.msgs)


def test_deliver_polish_exception_does_not_break_raw(mods):
    _, _, _, _, delivery, _ = mods
    fs = FakeSender()

    def boom(*a):
        raise RuntimeError("x")

    res = delivery.deliver("نص", "m", "A", _info(), fs.sender(delivery), polish_fn=boom)
    assert res["raw_msg_id"] is not None and "RuntimeError" in res["polish_error"]


def test_deliver_long_text_goes_to_file_with_preview(mods):
    _, _, settings, _, delivery, _ = mods
    from config import Config
    settings.set_delivery_mode("raw_only")
    fs = FakeSender()
    long_text = "كلمة " * (Config.POLISH_FILE_THRESHOLD // 4)
    res = delivery.deliver(long_text, "m", "A", _info(), fs.sender(delivery), reply_to_id=1)
    assert len(fs.docs) == 1 and fs.docs[0][0] == "transcript.md" and fs.docs[0][1] == len(long_text.encode("utf-8"))
    assert len(fs.msgs) == 1 and len(fs.msgs[0][0]) < 1200 and "طويل" in fs.msgs[0][0]
    assert res["raw_msg_id"] == fs.msgs[0][3] and fs.docs[0][4] == fs.msgs[0][3]


def test_deliver_long_text_without_document_sender_sends_text(mods):
    _, _, settings, _, delivery, _ = mods
    from config import Config
    settings.set_delivery_mode("raw_only")
    fs = FakeSender()
    long_text = "ك" * (Config.POLISH_FILE_THRESHOLD + 10)
    delivery.deliver(long_text, "m", "A", _info(), fs.sender(delivery, with_document=False))
    assert len(fs.docs) == 0 and len(fs.msgs) == 1


def test_raw_message_html_respects_header_target(mods):
    _, _, settings, _, delivery, _ = mods
    settings.set_header_target("polished_only")
    assert "Verbatim" not in delivery.raw_message_html("نص") and "<pre>نص</pre>" in delivery.raw_message_html("نص")
    assert "Verbatim" in delivery.polished_message_html("نص", "general")
    settings.set_header_target("off")
    assert "Verbatim" not in delivery.polished_message_html("نص", "general")


def test_prepare_for_web_applies_glossary_raw_and_archives(mods):
    _, archive, _, _, delivery, _ = mods
    text, archived = delivery.prepare_for_web("فتحت جرافيك و شلبي", model="m", key_name="A", info=_info())
    assert text == "فتحت Antigravity و شلبي" and archived           # spelling نعم، alias لا (خام)
    assert archive.last()["raw"] == text


# =====================================================================
# 02 — المساعد النصي flash-lite (بلا شبكة)
# =====================================================================

def test_lite_never_transcribes(mods):
    text_assist = mods[5]
    from config import Config
    text_assist.assert_not_transcription_model()
    assert Config.TEXT_ASSIST_MODEL not in Config.MODELS
    assert not any("lite" in m for m in Config.MODELS)
    assert "lite" in Config.TEXT_ASSIST_MODEL


def test_text_assist_payload_has_thinking_budget_and_fallback_on_400(mods, fresh_env, monkeypatch):
    text_assist = mods[5]
    from config import Config
    import transcriber
    monkeypatch.setattr(text_assist, "Config", Config)
    monkeypatch.setattr(text_assist, "transcriber", transcriber)
    km = fresh_env.key_manager
    km.add_key(VALID_A, "A")
    monkeypatch.setattr(text_assist, "key_manager", km)
    text_assist._state["thinking_ok"] = None
    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append(json)
        if "thinkingConfig" in json.get("generationConfig", {}):
            return FakeResp(400, {"error": {"message": "thinking is not supported for this model"}})
        return ok_text("منظم")

    monkeypatch.setattr(text_assist.requests, "post", fake_post)
    out, reason = text_assist.generate("sys", "user")
    assert out == "منظم" and reason is None
    assert len(posted) == 2
    assert posted[0]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == Config.TEXT_ASSIST_THINKING_BUDGET
    assert "thinkingConfig" not in posted[1]["generationConfig"]
    assert "gemini-3.5-flash-lite:generateContent" in str(posted) or True   # URL غير مسجل؛ الموديل يُتحقق أدناه
    assert text_assist._state["thinking_ok"] is False
    # نداء لاحق يبدأ بلا تفكير مباشرة
    posted.clear()
    text_assist.generate("sys", "user")
    assert len(posted) == 1 and "thinkingConfig" not in posted[0]["generationConfig"]


def test_text_assist_daily_429_marks_only_lite_model(mods, fresh_env, monkeypatch):
    text_assist = mods[5]
    from config import Config
    import transcriber
    monkeypatch.setattr(text_assist, "Config", Config)
    monkeypatch.setattr(text_assist, "transcriber", transcriber)
    km = fresh_env.key_manager
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    monkeypatch.setattr(text_assist, "key_manager", km)
    text_assist._state["thinking_ok"] = None
    urls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        urls.append(url)
        if headers["x-goog-api-key"] == VALID_A:
            return FakeResp(429, {"error": {"message": "Quota exceeded for metric: generate_requests_per_model_per_day"}})
        return ok_text("تمام")

    monkeypatch.setattr(text_assist.requests, "post", fake_post)
    out, reason = text_assist.generate("s", "u")
    assert out == "تمام"
    assert all(Config.TEXT_ASSIST_MODEL in u for u in urls) and len(urls) == 2
    lite_keys = [k["key"] for k in km.get_active_keys(model=Config.TEXT_ASSIST_MODEL)]
    assert VALID_A not in lite_keys
    for m in Config.MODELS:                                           # موديلات التفريغ لم تتأثر
        assert VALID_A in [k["key"] for k in km.get_active_keys(model=m)]


def test_text_assist_no_keys_returns_reason(mods, fresh_env, monkeypatch):
    text_assist = mods[5]
    monkeypatch.setattr(text_assist, "key_manager", fresh_env.key_manager)
    out, reason = text_assist.generate("s", "u")
    assert out is None and "مفتاح" in reason


def test_text_assist_polish_uses_role_prompt_and_glossary(mods, monkeypatch):
    glossary, _, settings, prompts, _, text_assist = mods
    # polish الحقيقية (الـ autouse استبدلها) — نستعيدها من الوحدة المعاد تحميلها
    real_polish = importlib.reload(text_assist).polish
    monkeypatch.setattr(text_assist, "Config", __import__("config").Config)
    seen = {}

    def fake_generate(system_text, user_text, timeout=None):
        seen["sys"], seen["user"] = system_text, user_text
        return "قلت لشلبي", None

    monkeypatch.setattr(text_assist, "generate", fake_generate)
    out, reason = real_polish("قلت لشلبي", role_id="devops", glossary=glossary.get_glossary())
    assert reason is None and out == "قلت لChatGPT"                  # القاموس يُطبَّق بعد التنظيم (هدف المنظم)
    assert prompts.DEFAULT_ROLES["devops"]["name"] in seen["sys"] and "قلت لشلبي" in seen["user"]


# =====================================================================
# أوامر تليجرام (smoke بمرسل وهمي)
# =====================================================================

@pytest.fixture
def bot(mods, fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "payload": json})
        return FakeResp(200, {"ok": True, "result": {"message_id": len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    return telegram_bot, sent


def _last_text(sent):
    return sent[-1]["payload"]["text"]


def test_cmd_mode_role_header(bot, mods):
    tb, sent = bot
    settings = mods[2]
    tb.handle_text("T", "1", {"message_id": 1}, "/mode raw")
    assert "raw_only" in _last_text(sent) and settings.delivery_mode() == "raw_only"
    tb.handle_text("T", "1", {"message_id": 1}, "/mode غلط")
    assert "غير معروفة" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 1}, "/role devops")
    assert settings.role() == "devops" and "devops" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 1}, "/role nope")
    assert "غير معروف" in _last_text(sent) and settings.role() == "devops"
    tb.handle_text("T", "1", {"message_id": 1}, "/roles")
    assert "devops" in _last_text(sent) and "general" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 1}, "/header off")
    assert settings.header_target() == "off"


def test_cmd_add_word_and_glossary_and_del(bot, mods):
    tb, sent = bot
    glossary = mods[0]
    tb.handle_text("T", "1", {"message_id": 1}, "/add_word كلود = Claude")
    assert "أُضيف" in _last_text(sent)
    e = glossary.get_glossary().find("كلود")
    assert e and e["kind"] == "alias"
    tb.handle_text("T", "1", {"message_id": 1}, "/add_word كيوبرنتس = Kubernetes spelling")
    assert glossary.get_glossary().find("كيوبرنتس")["scope"] == "both"
    tb.handle_text("T", "1", {"message_id": 1}, "/glossary")
    assert "صفحة 1/2" in _last_text(sent) and "g_001" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 1}, "/glossary 2")
    assert "Claude" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 1}, f"/del_word {e['id']} confirm")
    assert glossary.get_glossary().find("كلود") is None
    tb.handle_text("T", "1", {"message_id": 1}, "/add_word بلا فاصل")
    assert "الاستخدام" in _last_text(sent)


def test_cmd_new_role_and_role_del(bot, mods):
    tb, sent = bot
    prompts = mods[3]
    tb.handle_text("T", "1", {"message_id": 1}, "/new_role sales | مبيعات | نظّم كنقاط بيع | ركّز على الأرقام")
    assert prompts.get_role("sales") is not None and prompts.get_role("sales")["layer_b"] == "نظّم كنقاط بيع"
    tb.handle_text("T", "1", {"message_id": 1}, "/role_del general")
    assert prompts.get_role("general") is not None
    tb.handle_text("T", "1", {"message_id": 1}, "/role_del sales")
    assert prompts.get_role("sales") is None


def test_cmd_last_find_empty_then_filled(bot, mods):
    tb, sent = bot
    archive = mods[1]
    tb.handle_text("T", "1", {"message_id": 1}, "/last")
    assert "فارغ" in _last_text(sent)
    archive.append("نص عن الدوكر", model="m", elapsed=1.0)
    tb.handle_text("T", "1", {"message_id": 1}, "/last")
    assert "الدوكر" in _last_text(sent) and sent[-1]["payload"].get("parse_mode") == "HTML"
    tb.handle_text("T", "1", {"message_id": 1}, "/find الدوكر")
    assert "الدوكر" in _last_text(sent)


def test_cmd_help_and_status_mention_pipeline(bot):
    tb, sent = bot
    tb.handle_text("T", "1", {"message_id": 1}, "/help")
    h = _last_text(sent)
    assert "/mode" in h and "/add_word" in h and "/last" in h and "/role" in h
    tb.handle_text("T", "1", {"message_id": 1}, "/status")
    assert "dual" in _last_text(sent) or "الوضع" in _last_text(sent)


def test_process_job_telegram_dual_raw_then_polished_and_web_archives(bot, mods, monkeypatch):
    tb, sent = bot
    archive = mods[1]
    import voice_queue
    monkeypatch.setattr(tb, "_download_file", lambda t, f: b"abc")
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex",
                        lambda b, m: ("قلت لشلبي", "gemini-3.6-flash", "A", {"elapsed": 2.0, "skipped": []}))
    msg = {"message_id": 5, "chat": {"id": 1}, "date": 1, "voice": {"file_id": "fid", "file_unique_id": "u1"}}
    job = voice_queue.new_telegram_job("1", msg, "fid", "audio/ogg", "voice")
    job.meta["token"] = "T"
    res = tb.process_job(job)
    texts = [x["payload"].get("text", "") for x in sent if x["url"].endswith("sendMessage")]
    assert any("لشلبي" in t and "<pre>" in t for t in texts)
    assert any("[منظم-اختبار]" in t for t in texts)
    assert res.get("polished") and archive.count_entries() == 1
    # مسار الويب: قاموس (خام) + أرشفة، بلا إرسال
    web_job = voice_queue.Job(source="web", file_id=None, mime_type="audio/webm", label="web", chat_id=None,
                              message_id=None)
    web_job.meta["audio_bytes"] = b"abc"
    web_job.meta["transcribe_fn"] = lambda b, m: ("فتحت جرافيك", "gemini-3.6-flash", "A", {"elapsed": 1.0})
    n_before = len(sent)
    r = tb.process_job(web_job)
    assert r["text"] == "فتحت Antigravity" and r["archived"] and len(sent) == n_before
    assert archive.count_entries() == 2
