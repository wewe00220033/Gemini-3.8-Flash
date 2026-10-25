# -*- coding: utf-8 -*-
"""اختبارات محرك التفريغ بـ mock لـ requests.post (P4, سلوك 429، حقول الرد)."""
import importlib
import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def ok_text(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def ok_transcription(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"audioTranscription": {"text": text}}]}}]})


M1, M2 = "gemini-3.6-flash", "gemini-3.5-transcribe"   # الأساسي، الاحتياطي


@pytest.fixture
def engine(fresh_env, monkeypatch):
    import model_state
    importlib.reload(model_state)
    import transcriber
    importlib.reload(transcriber)
    transcriber.key_manager = fresh_env.key_manager
    transcriber.model_circuit = model_state.model_circuit
    monkeypatch.setattr(transcriber.time, "sleep", lambda s: None)   # لا انتظار حقيقي في الاختبارات
    return transcriber


def _key_of(call):
    h = call["headers"].get("x-goog-api-key")
    if h:
        return h
    return call["url"].split("key=")[1] if "key=" in call["url"] else ""


def _install_post(monkeypatch, engine, handler):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        call = {"url": url, "headers": headers or {}, "timeout": timeout, "json": json}
        calls.append(call)
        r = handler(call)
        if isinstance(r, BaseException):
            raise r
        return r

    monkeypatch.setattr(engine.requests, "post", fake_post)
    return calls


def test_success_first_model(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("  مرحبا  "))
    text, model, name = engine.transcribe_audio_bytes(b"abc", "audio/ogg")
    assert (text, model, name) == ("مرحبا", M1, "A")
    assert len(calls) == 1
    assert engine.key_manager.data["keys"][0]["success_count"] == 1


def test_audio_transcription_field_supported(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: ok_transcription("نص"))
    text, _, _ = engine.transcribe_audio_bytes(b"abc")
    assert text == "نص"


def test_429_rotates_to_next_key(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    _install_post(monkeypatch, engine,
                  lambda c: FakeResp(429) if _key_of(c) == VALID_A else ok_text("ok"))
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert text == "ok" and name == "B"
    # بعد المرحلة 2: يُقفل الموديل فقط على المفتاح A، والمفتاح يبقى نشطاً للموديلات الأخرى
    k_a = engine.key_manager.data["keys"][0]
    assert M1 in k_a.get("exhausted_models", {})
    assert k_a["status"] == "active"


def test_no_keys_raises(engine):
    with pytest.raises(RuntimeError):
        engine.transcribe_audio_bytes(b"abc")


def test_empty_audio_raises(engine):
    engine.key_manager.add_key(VALID_A)
    with pytest.raises(ValueError):
        engine.transcribe_audio_bytes(b"")


def test_all_fail_raises_with_last_error(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A)
    _install_post(monkeypatch, engine, lambda c: FakeResp(500, text="boom"))
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert "500" in str(ei.value)


def test_api_key_not_in_url(engine, monkeypatch):
    """P4: المفتاح يجب أن يُرسل في Header وليس في الـ URL."""
    engine.key_manager.add_key(VALID_A)
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("x"))
    engine.transcribe_audio_bytes(b"abc")
    assert VALID_A not in calls[0]["url"], "API key leaked into URL query string"
    assert calls[0]["headers"].get("x-goog-api-key") == VALID_A


# ---------- المرحلة 2 ----------

def test_429_on_one_model_falls_through_to_next_model_same_key(engine, monkeypatch):
    """P15: 429 على الأساسي → نفس المفتاح يُجرَّب على الاحتياطي بدل قفله بالكامل."""
    engine.key_manager.add_key(VALID_A, "A")
    def handler(c):
        return FakeResp(429) if M1 in c["url"] else ok_text("ok")
    calls = _install_post(monkeypatch, engine, handler)
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (text, model, name) == ("ok", M2, "A")
    assert len(calls) == 2
    k = engine.key_manager.data["keys"][0]
    assert M1 in k["exhausted_models"] and k["status"] == "active"


def test_all_exhausted_gives_clear_message_without_requests(engine, monkeypatch):
    """P14: لا طلبات شبكة لما كل المفاتيح مقفولة — رسالة واضحة بالوقت."""
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.mark_key_exhausted(VALID_A)
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("x"))
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert calls == []
    assert "دقيقة" in str(ei.value)


def test_retry_delay_parsed_from_google_body(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    body = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}]}}
    r429 = FakeResp(429, body)
    r429.headers = {}
    _install_post(monkeypatch, engine, lambda c: r429 if M1 in c["url"] else ok_text("ok"))
    engine.transcribe_audio_bytes(b"abc")
    until = engine.key_manager.data["keys"][0]["exhausted_models"][M1]
    import time as _t
    assert 0 < until - _t.time() <= 38


def test_daily_usage_recorded_on_success(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: ok_text("ok"))
    engine.transcribe_audio_bytes(b"abc")
    k = engine.key_manager.data["keys"][0]
    assert sum(v.get(M1, 0) for v in k["daily_usage"].values()) == 1


# ---------- المرحلة 3: payload اختياري ----------

def test_default_payload_keeps_prompt_as_text_part(engine, monkeypatch):
    monkeypatch.setattr(engine.Config, "USE_SYSTEM_INSTRUCTION", False)
    monkeypatch.setattr(engine.Config, "TEMPERATURE", None)
    p = engine._build_payload("QUJD", "audio/ogg")
    assert "system_instruction" not in p and "generationConfig" not in p
    assert p["contents"][0]["parts"][1]["text"] == engine.Config.SYSTEM_PROMPT


def test_optional_system_instruction_and_temperature(engine, monkeypatch):
    monkeypatch.setattr(engine.Config, "USE_SYSTEM_INSTRUCTION", True)
    monkeypatch.setattr(engine.Config, "TEMPERATURE", 0.0)
    p = engine._build_payload("QUJD", "audio/ogg")
    assert p["system_instruction"]["parts"][0]["text"] == engine.Config.SYSTEM_PROMPT
    assert p["generationConfig"] == {"temperature": 0.0}
    assert p["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "audio/ogg"


# ======================================================================
# حادثة 01 — Failover سريع: تصنيف، قاطع الدائرة، مهلة ديناميكية، جولات محدودة
# ======================================================================
import requests as _rq


def _err(status, message="", err_status="", details=None):
    body = {"error": {"code": status, "message": message, "status": err_status}}
    if details:
        body["error"]["details"] = details
    return FakeResp(status, body)


# ---------- classify_response: صف لكل حالة في جدول 5.2 ----------
@pytest.mark.parametrize("resp,expected", [
    (ok_text("hi"), "SUCCESS"),
    (FakeResp(200, {"promptFeedback": {"blockReason": "SAFETY"}}), "BLOCKED"),
    (_err(429, "Quota exceeded for metric GenerateRequestsPerDay", "RESOURCE_EXHAUSTED"), "KEY_EXHAUSTED_DAILY"),
    (_err(429, "Resource has been exhausted (e.g. check quota).", "RESOURCE_EXHAUSTED"), "KEY_RATE_LIMITED"),
    (_err(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT"), "KEY_INVALID"),
    (_err(401, "Request had invalid authentication credentials", "UNAUTHENTICATED"), "KEY_INVALID"),
    (_err(403, "Your API key was reported as leaked.", "PERMISSION_DENIED"), "KEY_INVALID"),
    (_err(400, "Unsupported MIME type: audio/x-foo", "INVALID_ARGUMENT"), "BAD_REQUEST"),
    (_err(404, "models/gemini-x is not found", "NOT_FOUND"), "MODEL_NOT_FOUND"),
    (_err(500, "Internal error", "INTERNAL"), "MODEL_DOWN"),
    (_err(503, "This model is currently experiencing high demand.", "UNAVAILABLE"), "MODEL_DOWN"),
    (_err(504, "Deadline expired", "DEADLINE_EXCEEDED"), "MODEL_DOWN"),
    (FakeResp(418, text="teapot"), "BAD_REQUEST"),
])
def test_classify_response_matrix(engine, resp, expected):
    assert engine.classify_response(resp).outcome == expected


def test_classify_block_reason_detail(engine):
    att = engine.classify_response(FakeResp(200, {"candidates": [{"finishReason": "RECITATION", "content": {"parts": []}}]}))
    assert att.outcome == "BLOCKED" and "RECITATION" in att.detail


# ---------- الحادثة نفسها: Timeout على الأساسي بمفتاحين → محاولة واحدة فقط ثم الاحتياطي ----------
def test_timeout_on_primary_does_not_try_other_keys(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    calls = _install_post(monkeypatch, engine,
                          lambda c: _rq.exceptions.ReadTimeout("slow") if M1 in c["url"] else ok_text("ok"))
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (text, model, name) == ("ok", M2, "A")
    urls = [c["url"] for c in calls]
    assert sum(M1 in u for u in urls) == 1, "Timeout يجب ألا يجرّب المفتاح الثاني على نفس الموديل"
    assert engine.model_circuit.is_open(M1) and engine.model_circuit.reason(M1) == "timeout"
    # المفاتيح نفسها لم تُقفل (المشكلة في الموديل)
    for k in engine.key_manager.data["keys"]:
        assert k["status"] == "active" and not k.get("exhausted_models")


def test_503_on_primary_opens_circuit_and_next_request_skips_it(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    r503 = _err(503, "high demand", "UNAVAILABLE")
    calls = _install_post(monkeypatch, engine, lambda c: r503 if M1 in c["url"] else ok_text("ok"))
    engine.transcribe_audio_bytes(b"abc")
    # Failover v2 (04 §2.1): 503 على A → قفزة لـ B (مشروع مختلف) → الكل 503 → قفل الموديل → الموديل التالي
    assert [M1 in c["url"] for c in calls] == [True, True, False]
    assert engine.model_circuit.is_open(M1)
    # الطلب التالي: لا يلمس 3.6 أصلاً
    calls.clear()
    text, model, _, info = engine.transcribe_audio_bytes_ex(b"abc")
    assert model == M2 and all(M2 in c["url"] for c in calls) and len(calls) == 1
    assert info["skipped"] and "503" in info["skipped"][0]


def test_circuit_reopens_after_pause(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.model_circuit.pause(M1, 1, "503")
    engine.model_circuit._paused[M1]["until"] = 0   # انتهت المدة
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("ok"))
    _, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert model == M1 and len(calls) == 1


def test_all_models_down_bounded_rounds_then_clear_message(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    sleeps = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: sleeps.append(s))
    calls = _install_post(monkeypatch, engine, lambda c: _err(503, "busy", "UNAVAILABLE"))
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    # الجولة الأولى: موديلان × مفتاح = طلبان. بعدها القواطع مقفولة 20ث (أول درجة في السلم)
    # Failover v2 (04 §2.3): انتظار ذكي = أقرب فتح قاطع (20) لا backoff أعمى (2)، ثم جولة ثانية تضرب الشبكة
    # لأن sleep وهمي (القاطع لا يزال مقفولاً فعلياً → تُتخطّى كل الموديلات بلا طلبات) والميزانية تمنع المزيد
    assert len(calls) == 2
    # remaining() = int(until - now) + 1 بساعة حقيقية: على لينكس يمر كسر ثانية → 20، وعلى ويندوز (دقة ~15ms)
    # القراءتان في نفس التكة → 21. الاختبار يقبل كليهما (test_failover_v2 يستخدم ساعة وهمية ويثبّت 21).
    ladder0 = engine.Config.MODEL_PAUSE_LADDER_5XX[0]
    assert len(sleeps) == 1 and ladder0 <= sleeps[0] <= ladder0 + 1, sleeps
    assert isinstance(ei.value, engine.DeferredError) and ei.value.kind == "model"
    msg = str(ei.value)
    assert "مزدحمة" in msg and "/reset" in msg and msg.startswith("🤖 Gemini:")


def test_transient_retry_round_succeeds_when_circuit_cleared(engine, monkeypatch):
    """لو الموديل رجع بعد الانتظار (القاطع فُتح) الجولة الثانية تنجح."""
    engine.key_manager.add_key(VALID_A, "A")
    state = {"n": 0}

    def handler(c):
        state["n"] += 1
        return _err(503, "busy", "UNAVAILABLE") if state["n"] <= 2 else ok_text("late ok")

    def fake_sleep(s):
        engine.model_circuit.reset()   # نحاكي انتهاء القفل أثناء الانتظار

    monkeypatch.setattr(engine.time, "sleep", fake_sleep)
    _install_post(monkeypatch, engine, handler)
    text, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert text == "late ok" and model == M1 and state["n"] == 3


def test_invalid_key_marked_and_next_key_used(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    _install_post(monkeypatch, engine,
                  lambda c: _err(400, "API key not valid.", "INVALID_ARGUMENT") if _key_of(c) == VALID_A else ok_text("ok"))
    _, _, name = engine.transcribe_audio_bytes(b"abc")
    assert name == "B"
    assert engine.key_manager.data["keys"][0]["status"] == "invalid"
    assert not engine.model_circuit.is_open(M1)


def test_daily_429_locks_until_reset_and_rpm_429_short(engine, monkeypatch):
    import time as _t
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")

    def handler(c):
        if _key_of(c) == VALID_A:
            return _err(429, "Quota exceeded for metric generate_requests_per_day", "RESOURCE_EXHAUSTED")
        if M1 in c["url"]:
            return _err(429, "rate limited", "RESOURCE_EXHAUSTED")
        return ok_text("ok")

    _install_post(monkeypatch, engine, handler)
    _, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (model, name) == (M2, "B")
    a_until = engine.key_manager.data["keys"][0]["exhausted_models"][M1]
    b_until = engine.key_manager.data["keys"][1]["exhausted_models"][M1]
    assert a_until - _t.time() > 3600 or a_until - _t.time() >= 60   # حتى التصفير (≥ 60 كحد أدنى)
    assert 0 < b_until - _t.time() <= engine.Config.KEY_COOLDOWN_SECONDS + 1


def test_bad_request_does_not_burn_other_keys(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    calls = _install_post(monkeypatch, engine,
                          lambda c: _err(400, "Unsupported MIME", "INVALID_ARGUMENT") if M1 in c["url"] else ok_text("ok"))
    _, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert model == M2 and sum(M1 in c["url"] for c in calls) == 1
    assert all(k["status"] == "active" for k in engine.key_manager.data["keys"])
    assert not engine.model_circuit.is_open(M1)


def test_network_error_retries_once_then_raises_without_touching_state(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: _rq.exceptions.ConnectionError("dns"))
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert len(calls) == 2 and "شبكة" in str(ei.value)
    k = engine.key_manager.data["keys"][0]
    assert k["status"] == "active" and not k.get("exhausted_models")
    assert not engine.model_circuit.is_open(M1) and not engine.model_circuit.is_open(M2)


def test_connect_timeout_is_network_not_model(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: _rq.exceptions.ConnectTimeout("x"))
    with pytest.raises(RuntimeError):
        engine.transcribe_audio_bytes(b"abc")
    assert not engine.model_circuit.is_open(M1)


def test_blocked_response_falls_to_next_model_then_clear_message(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    blocked = FakeResp(200, {"promptFeedback": {"blockReason": "SAFETY"}})
    calls = _install_post(monkeypatch, engine, lambda c: blocked)
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert len(calls) == 2            # موديل واحد لكل محاولة، بدون حرق المفتاح الثاني
    assert "SAFETY" in str(ei.value)


# ---------- المهلة الديناميكية ----------
@pytest.mark.parametrize("size_bytes,expected", [
    (0, 15), (770 * 1024, 21), (5 * 1024 * 1024, 55),
    (8 * 1024 * 1024, 79),                 # على الحد: صغير → 15+64=79 < 90
    (9 * 1024 * 1024, 87),                 # > 8MB: كبير لكن المعادلة أقل من 180
    (15 * 1024 * 1024, 135),               # 22 U3: كان يُقصّ إلى 90 → timeout زائف
    (20 * 1024 * 1024, 175), (100 * 1024 * 1024, 180),   # سقف الكبير 180
])
def test_dynamic_read_timeout(engine, size_bytes, expected):
    assert engine.dynamic_read_timeout(size_bytes) == expected


def test_request_uses_connect_and_dynamic_read_timeout(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("ok"))
    engine.transcribe_audio_bytes(b"x" * (770 * 1024))
    assert calls[0]["timeout"] == (engine.Config.GEMINI_CONNECT_TIMEOUT, 21)


# ---------- thinkingConfig: فقط للموديل المذكور ----------
def test_thinking_level_sent_only_for_configured_model(engine, monkeypatch):
    monkeypatch.setattr(engine.Config, "MODEL_THINKING_LEVEL", {M1: "minimal"})
    p1 = engine._build_payload("QUJD", "audio/ogg", M1)
    p2 = engine._build_payload("QUJD", "audio/ogg", M2)
    assert p1["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert "generationConfig" not in p2


def test_thinking_level_default_empty(engine):
    assert engine.Config.MODEL_THINKING_LEVEL == {}
    assert "generationConfig" not in engine._build_payload("QUJD", "audio/ogg", M1)


# ---------- الشفافية ----------
def test_ex_returns_elapsed_and_attempts(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: ok_text("ok"))
    _, _, _, info = engine.transcribe_audio_bytes_ex(b"abc")
    assert info["attempts"] == 1 and isinstance(info["elapsed"], float) and info["skipped"] == []
    assert engine.model_circuit.recent_attempts()[0]["outcome"] == "SUCCESS"


def test_disabled_model_never_called(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: _err(503, "x", "UNAVAILABLE"))
    with pytest.raises(RuntimeError):
        engine.transcribe_audio_bytes(b"abc")
    assert not any("gemini-3.8-flash" in c["url"] for c in calls)
