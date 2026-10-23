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


@pytest.fixture
def engine(fresh_env):
    import transcriber
    importlib.reload(transcriber)
    transcriber.key_manager = fresh_env.key_manager
    return transcriber


def _key_of(call):
    h = call["headers"].get("x-goog-api-key")
    if h:
        return h
    return call["url"].split("key=")[1] if "key=" in call["url"] else ""


def _install_post(monkeypatch, engine, handler):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        call = {"url": url, "headers": headers or {}}
        calls.append(call)
        return handler(call)

    monkeypatch.setattr(engine.requests, "post", fake_post)
    return calls


def test_success_first_model(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("  مرحبا  "))
    text, model, name = engine.transcribe_audio_bytes(b"abc", "audio/ogg")
    assert (text, model, name) == ("مرحبا", "gemini-3.8-flash", "A")
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
    assert "gemini-3.8-flash" in k_a.get("exhausted_models", {})
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
    """P15: 429 على 3.8 → نفس المفتاح يُجرَّب على 3.6 بدل قفله بالكامل."""
    engine.key_manager.add_key(VALID_A, "A")
    def handler(c):
        return FakeResp(429) if "gemini-3.8-flash" in c["url"] else ok_text("ok")
    calls = _install_post(monkeypatch, engine, handler)
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (text, model, name) == ("ok", "gemini-3.6-flash", "A")
    assert len(calls) == 2
    k = engine.key_manager.data["keys"][0]
    assert "gemini-3.8-flash" in k["exhausted_models"] and k["status"] == "active"


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
    _install_post(monkeypatch, engine, lambda c: r429 if "3.8" in c["url"] else ok_text("ok"))
    engine.transcribe_audio_bytes(b"abc")
    until = engine.key_manager.data["keys"][0]["exhausted_models"]["gemini-3.8-flash"]
    import time as _t
    assert 0 < until - _t.time() <= 38


def test_daily_usage_recorded_on_success(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: ok_text("ok"))
    engine.transcribe_audio_bytes(b"abc")
    k = engine.key_manager.data["keys"][0]
    assert sum(v.get("gemini-3.8-flash", 0) for v in k["daily_usage"].values()) == 1


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
